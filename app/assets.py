"""Asset conversion + overlay management for the Asset Studio app.

- Render an item's inv DC6 (from the MPQs) to a PNG for the browser.
- Import a PNG as an alternate: fit to the item's cell grid, quantize to the act
  palette, encode DC6.
- Activate original / an alternate: mirror the chosen DC6 into the overlay tree.
- Build patch.mpq from the overlay (v1 + PKWARE) for the in-game override channel.
"""

from __future__ import annotations

import io
import json
import os
import sys
from functools import lru_cache

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyd2 import dc6  # noqa: E402
from pyd2.mpq import read_effective, build_archive  # noqa: E402
from pyd2.palette import load_pal_dat, frame_to_rgba  # noqa: E402

WORKSPACE = os.environ.get("ASSET_STUDIO_WS", r"C:\Diablo2\AssetStudio")
OVERLAY = os.path.join(WORKSPACE, "overlay")
ALTERNATES = os.path.join(WORKSPACE, "alternates")
EXPORT_MPQ = os.path.join(WORKSPACE, "export", "patch.mpq")
MANIFEST = os.path.join(WORKSPACE, "manifest.json")
CELL_PX = 29  # inventory grid cell size in pixels

_PAL_CACHE = {}


def _palette(act: int = 1):
	if act not in _PAL_CACHE:
		data, _ = read_effective(f"data\\global\\palette\\ACT{act}\\pal.dat")
		_PAL_CACHE[act] = load_pal_dat(data)
	return _PAL_CACHE[act]


def item_dc6_path(invfile: str) -> str:
	return f"data\\global\\items\\{invfile}.dc6"


def read_original_dc6(invfile: str) -> bytes:
	data, _src = read_effective(item_dc6_path(invfile))
	return data


def try_read_effective(rel_path: str):
	"""read_effective, but None instead of raising when the file doesn't resolve."""
	try:
		data, _src = read_effective(rel_path)
		return data
	except Exception:  # noqa: BLE001
		return None


def dc6_to_png_bytes(dc6_bytes: bytes, frame_index: int = 0, palette=None) -> bytes:
	sprite = dc6.decode(dc6_bytes)
	frame = sprite.frames[frame_index]
	w, h, rgba = frame_to_rgba(frame, palette or _palette())
	buf = io.BytesIO()
	Image.frombytes("RGBA", (w, h), rgba).save(buf, format="PNG")
	return buf.getvalue()


# --- item colour transform (unique/set inventory recolour, e.g. Twitchthroe -> green) ----------
# The game recolours an item's INVENTORY sprite via D2CMP_MixPalette(base.nInvTrans, item.nInvTransform)
# (D2Common ITEMS_GetColor, Items.cpp:3841). MixPalette returns a 256-entry index->index remap read
# straight from Data\Global\Items\Palette\<transform>.dat; we apply it to the act palette so the
# rendered PNG matches what the game draws. pyd2.colortransform is a pixel-exact port of that path.
_XFORM_PAL_CACHE = {}


def _color_index(color):
	"""uniqueitems/setitems `invtransform` cell -> nColor (0..20) or None. Accepts a colour code
	string ('lgrn') or a bare number."""
	from pyd2 import colortransform as ct
	s = str(color or "").strip().lower()
	if not s:
		return None
	if s in ct.COLOR_INDEX:
		return ct.COLOR_INDEX[s]
	if s.lstrip("-").isdigit():
		v = int(s)
		return v if 0 <= v < 21 else None
	return None


def item_transform_palette(inv_trans, invtransform):
	"""256-entry (r,g,b) palette recoloured by the item's inventory colour transform, or None when
	the item has no valid transform (then the caller uses the base palette). `inv_trans` = base
	item's InvTrans (1..8); `invtransform` = the unique/set colour code."""
	try:
		t = int(inv_trans or 0)
	except (TypeError, ValueError):
		return None
	ci = _color_index(invtransform)
	if not (1 <= t <= 8) or ci is None:
		return None
	key = (t, ci)
	if key not in _XFORM_PAL_CACHE:
		from pyd2 import colortransform as ct
		try:
			remap = ct.mix_palette(t, ci, reader=try_read_effective)
		except Exception:  # noqa: BLE001 -- missing .dat etc. -> fall back to base palette
			return None
		base = _palette()
		_XFORM_PAL_CACHE[key] = [base[remap[i]] for i in range(256)]
	return _XFORM_PAL_CACHE[key]


# Semi-transparent edge pixels are matted onto this near-black rim before quantization.
# DC6 has no alpha: the old "alpha>=128 -> keep RGB fully opaque" hardened anti-aliasing halos
# (warm bloom/background-blended edge pixels) into full-brightness cream specks along
# silhouettes. Matting them dark reads like vanilla D2's dark item outlines instead.
RIM_RGB = (10, 10, 10)


def _srgb_to_oklab(rgb01):
	"""sRGB in 0..1 (N,3) -> Oklab (N,3). Björn Ottosson's reference constants."""
	import numpy as np
	c = np.where(rgb01 <= 0.04045, rgb01 / 12.92, ((rgb01 + 0.055) / 1.055) ** 2.4)
	r, g, b = c[:, 0], c[:, 1], c[:, 2]
	l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
	m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
	s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
	l, m, s = np.cbrt(l), np.cbrt(m), np.cbrt(s)
	return np.stack([
		0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
		1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
		0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
	], axis=1)


def _quantize_to_palette(img: Image.Image, palette, alpha_cliff: int = 128):
	"""Nearest-palette-index quantize an RGBA image; alpha<alpha_cliff -> transparent.

	Perceptual (Oklab) nearest match — plain RGB distance snapped grays to olive/cream
	neighbors. Partially-transparent pixels (alpha alpha_cliff..254) are alpha-matted onto RIM_RGB
	first, so edge halos darken like vanilla outlines instead of becoming light specks.

	`alpha_cliff` (default 128) is lowered to ~96 for thin items (swords) so a downscaled ~1px
	blade whose alpha lands in the 96..127 band survives as a dark rim pixel instead of vanishing;
	those pixels are matted onto RIM_RGB anyway, so they read as outline, never light specks."""
	import numpy as np

	rgba = img.convert("RGBA")
	arr = np.asarray(rgba, dtype=np.float64)  # H,W,4
	h, w = arr.shape[0], arr.shape[1]
	rgb = arr[:, :, :3].reshape(-1, 3)
	alpha = arr[:, :, 3].reshape(-1)
	# matte semi-transparent pixels onto the dark rim
	a = (alpha / 255.0)[:, None]
	rim = np.array(RIM_RGB, dtype=np.float64)[None, :]
	rgb = rgb * a + rim * (1.0 - a)
	# Oklab nearest — candidates restricted to indexes 1..224.
	# Index 0 is the transparent slot. Indexes 225..255 are act-variable/reserved: their ACT1
	# colors (dark greens/browns) tempt the matcher for dark pixels, but the GAME renders them
	# through act palettes / colormaps where they differ — every such pixel shows in-game as a
	# wrong-colored speck ("fireflies") while looking perfect in our own ACT1 preview.
	# Verified 2026-07-22: stock item DC6s use ZERO pixels >=225; our speckled-in-game items
	# used hundreds (Victor's Silk 379); the one clean-in-game item used almost none.
	SAFE_MAX = 224
	pal = np.array([palette[i] for i in range(256)], dtype=np.float64)  # 256,3
	pix_lab = _srgb_to_oklab(rgb / 255.0)                       # N,3
	pal_lab = _srgb_to_oklab(pal[1:SAFE_MAX + 1] / 255.0)       # 224,3 (indexes 1..224)
	d = ((pix_lab[:, None, :] - pal_lab[None, :, :]) ** 2).sum(axis=2)  # N,224
	idx = (d.argmin(axis=1) + 1).reshape(h, w)                  # 1..224
	amask = (alpha < alpha_cliff).reshape(h, w)
	rows = []
	for y in range(h):
		row = []
		for x in range(w):
			row.append(dc6.TRANSPARENT if amask[y, x] else int(idx[y, x]))
		rows.append(row)
	return rows


def prep_image_for_meshy(png_bytes: bytes, size: int | None = None, margin: float = 0.9,
                         max_size: int = 1024) -> bytes:
	"""Prepare a sprite as Meshy image-to-3D input WITHOUT distorting its aspect ratio.

	The old code did `resize((512, 512))`, which stretches a non-square sprite to a square --
	e.g. the tall plate mail (object aspect 0.95) became 1.41 (48% too wide), so Meshy generated
	a wide dome. Here we crop to the object, keep its aspect, and center it on a square canvas.

	`size=None` (default) = TIDY NATIVE: the object stays at its native resolution and the canvas is
	clamped to the source master's own size (never expanded), so a 759px helm on a 768 master feeds
	Meshy a clean 768 canvas -- not a 512 downscale (which cost real 3D detail: it made Meshy
	hallucinate a spurious front ornament, verified 2026-07-22) and not the old 10%-margin 843
	expansion (margin tested as a non-factor for Meshy geometry, so we keep it tidy). Pass an
	explicit `size` to force a fixed canvas (legacy fit-with-margin behaviour).
	"""
	img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
	src_max = max(img.width, img.height)  # source master canvas dim (e.g. 768), before crop
	bb = _alpha_bbox(img)
	if bb:
		img = img.crop(bb)  # drop existing padding so the object fills the frame consistently
	nw, nh = img.width, img.height
	if size is None:
		# tidy native: canvas = source size (clamped), object kept at full res (only shrink if it
		# somehow exceeds the clamp); the small transparent margin emerges from canvas - object.
		size = min(max_size, max(nw, nh, src_max))
		if max(nw, nh) > size:  # object bigger than the clamp -> fit it in
			scale = size / max(nw, nh)
			nw, nh = max(1, round(nw * scale)), max(1, round(nh * scale))
			img = img.resize((nw, nh), Image.LANCZOS)
	else:
		# forced canvas: fit the object within size*margin (used by callers that pass a size)
		scale = min(size * margin / nw, size * margin / nh)
		nw, nh = max(1, round(nw * scale)), max(1, round(nh * scale))
		img = img.resize((nw, nh), Image.LANCZOS)
	canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
	canvas.alpha_composite(img, ((size - nw) // 2, (size - nh) // 2))
	buf = io.BytesIO()
	canvas.save(buf, format="PNG")
	return buf.getvalue()


def _alpha_bbox(img: Image.Image, thresh: int = 16):
	"""Bounding box of the non-transparent pixels, or None if fully transparent."""
	import numpy as np
	a = np.asarray(img.convert("RGBA"))[:, :, 3]
	ys, xs = np.where(a > thresh)
	if len(xs) == 0:
		return None
	return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


@lru_cache(maxsize=1024)
def original_footprint(invfile: str, invwidth: int, invheight: int) -> tuple:
	"""(fill, dx, dy) that reproduce the ORIGINAL art's size and position in its cell grid, so an
	accepted alternate matches how much of the cell the stock sprite actually occupied (a Chipped
	gem stays chip-sized instead of being blown up to a Perfect gem's footprint).

	Measures frame 0 of the original DC6 (alpha-bbox vs the invw*29 x invh*29 cell). `fill` follows
	fit_png_to_cell's constraining-dimension semantics: max(bbox_w/cell_w, bbox_h/cell_h). dx/dy map
	the bbox's position to fit_png_to_cell's [-1,1] free-space nudge. Falls back to the legacy
	(0.94, 0, 0) on any read/decode failure — a preview must never 500 because an original is odd."""
	try:
		png = dc6_to_png_bytes(read_original_dc6(invfile))
		img = Image.open(io.BytesIO(png)).convert("RGBA")
		bb = _alpha_bbox(img)
		if not bb:
			return (0.94, 0.0, 0.0)
		x0, y0, x1, y1 = bb
		bw, bh = x1 - x0, y1 - y0
		tw, th = invwidth * CELL_PX, invheight * CELL_PX
		fill = max(0.1, min(1.5, max(bw / tw, bh / th)))
		# with fill = max(bw/tw, bh/th), fit_png_to_cell's scale works out to 1.0, so the object
		# lands at its own bbox size (bw x bh) — its true footprint. Place its top-left where the
		# original's was by inverting fit's ox = free_x*(0.5+0.5*dx).
		free_x, free_y = tw - bw, th - bh
		dx = (2.0 * x0 / free_x - 1.0) if free_x > 1e-6 else 0.0
		dy = (2.0 * y0 / free_y - 1.0) if free_y > 1e-6 else 0.0
		return (round(fill, 4), round(max(-1.0, min(1.0, dx)), 4), round(max(-1.0, min(1.0, dy)), 4))
	except Exception:  # noqa: BLE001
		return (0.94, 0.0, 0.0)


def _despeckle_fireflies(img: Image.Image, thresh: int = 60, size: int = 5) -> Image.Image:
	"""Suppress isolated 'firefly' sparkle pixels.

	Old browser-engine captures (1.45 tone gain), low-sample Cycles renders and glinty PBR
	metal all carry lone near-white pixels on dark areas; at DC6 scale they quantize into
	cream specks. Any pixel whose luminance exceeds the local size x size median by `thresh`
	is pulled back to the median-filtered color. Real highlights span multiple pixels, so
	their cores raise the local median and survive; only isolated outliers get replaced.

	Runs twice in the pipeline: on the SOURCE render (size=5, catches render sparkles), and
	again on the FITTED cell canvas (size=3 — the scale the player actually sees; verified
	2026-07-22 on Ancient Armor, whose ornate metal texture re-created glints on downsize)."""
	from PIL import ImageFilter
	import numpy as np
	rgba = img.convert("RGBA")
	arr = np.asarray(rgba).astype(np.int16)
	med = np.asarray(rgba.filter(ImageFilter.MedianFilter(size))).astype(np.int16)
	lum = arr[:, :, :3].mean(axis=2)
	med_lum = med[:, :, :3].mean(axis=2)
	fire = (arr[:, :, 3] > 0) & (lum - med_lum > thresh)
	out = arr.copy()
	out[fire, 0:3] = med[fire, 0:3]
	return Image.fromarray(np.clip(out, 0, 255).astype("uint8"), "RGBA")


def _resize_clamped(img: Image.Image, size: tuple[int, int]) -> Image.Image:
	"""LANCZOS downsize with the overshoot clamped away.

	LANCZOS ringing creates isolated over-bright/over-dark pixels next to hard highlights,
	which the palette quantizer then turns into visible specks. Clamp each output pixel's RGB
	to the local min/max of the source neighborhood it came from (min/max-filtered source,
	nearest-resized) — sharpness is untouched wherever there's no ringing."""
	if size[0] >= img.width and size[1] >= img.height:
		return img.resize(size, Image.LANCZOS)  # upscales don't ring visibly
	import numpy as np
	out = img.resize(size, Image.LANCZOS)
	scale = max(img.width / size[0], img.height / size[1])
	k = int(scale) * 2 + 1  # odd kernel covering one output pixel's source footprint
	rgb_src = np.asarray(img.convert("RGB"))
	# min/max envelope of each output pixel's source footprint. PIL's MinFilter/MaxFilter are
	# O(pixels * k^2) and dominate the whole accept (~3s at k~21); scipy's separable rank
	# filters give the IDENTICAL result (mode='nearest' matches PIL's edge extension, verified
	# bit-exact) ~65x faster. Fall back to PIL if scipy is unavailable.
	try:
		from scipy.ndimage import minimum_filter, maximum_filter
		lo_a = np.dstack([minimum_filter(rgb_src[:, :, c], size=k, mode="nearest") for c in range(3)])
		hi_a = np.dstack([maximum_filter(rgb_src[:, :, c], size=k, mode="nearest") for c in range(3)])
		lo = np.asarray(Image.fromarray(lo_a).resize(size, Image.NEAREST), dtype=np.int16)
		hi = np.asarray(Image.fromarray(hi_a).resize(size, Image.NEAREST), dtype=np.int16)
	except ImportError:
		from PIL import ImageFilter
		src = img.convert("RGB")
		lo = np.asarray(src.filter(ImageFilter.MinFilter(k)).resize(size, Image.NEAREST), dtype=np.int16)
		hi = np.asarray(src.filter(ImageFilter.MaxFilter(k)).resize(size, Image.NEAREST), dtype=np.int16)
	o = np.asarray(out).astype(np.int16)
	rgb = np.clip(o[:, :, :3], np.asarray(lo, dtype=np.int16), np.asarray(hi, dtype=np.int16))
	o[:, :, :3] = rgb
	return Image.fromarray(np.clip(o, 0, 255).astype("uint8"), "RGBA")


def fit_png_to_cell(png_bytes: bytes, invwidth: int, invheight: int, *,
                    fill: float = 0.94, dx: float = 0.0, dy: float = 0.0) -> Image.Image:
	"""Crop a render to its object and scale it to FILL the item's cell grid.

	The render frames the object with lots of transparent padding (and is usually square),
	so the OLD "thumbnail the whole image, center it" made the object tiny and left vertical
	gaps (the squished look).  Instead: crop to the object's alpha bbox, then scale it up so
	the constraining dimension reaches `fill` of the cell (aspect preserved -- no distortion),
	and center it with an optional dx/dy nudge (fractions of the free space, -1..1).

	Returns an RGBA canvas exactly (invwidth*29, invheight*29).
	"""
	target_w = invwidth * CELL_PX
	target_h = invheight * CELL_PX
	img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
	img = _despeckle_fireflies(img)
	bb = _alpha_bbox(img)
	if bb:
		img = img.crop(bb)  # drop the transparent padding -> just the object
	fill = max(0.1, min(1.5, fill))
	avail_w = target_w * fill
	avail_h = target_h * fill
	scale = min(avail_w / img.width, avail_h / img.height)  # fill, aspect-preserved
	new_w = max(1, round(img.width * scale))
	new_h = max(1, round(img.height * scale))
	img = _resize_clamped(img, (new_w, new_h))
	canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
	free_x = target_w - new_w
	free_y = target_h - new_h
	# center, then nudge: dx/dy in [-1,1] move within the remaining free space
	ox = round(free_x * (0.5 + 0.5 * max(-1.0, min(1.0, dx))))
	oy = round(free_y * (0.5 + 0.5 * max(-1.0, min(1.0, dy))))
	canvas.alpha_composite(img, (max(0, min(free_x, ox)), max(0, min(free_y, oy))))
	return canvas


def color_grade(png_bytes: bytes, *, brightness: float = 1.0, warmth: float = 1.0,
                saturation: float = 1.0, contrast: float = 1.0, hue: float = 0.0) -> bytes:
	"""Tone an RGBA sprite (alpha preserved). AI renders often come out bright/neutral; this pulls
	them toward a target metal/material tone. brightness<1 darkens; warmth>1 pushes red up + blue
	down (toward bronze/gold), <1 the reverse (toward steel/cool); saturation/contrast as usual;
	hue in degrees (-180..180, 0 = no-op) rotates the whole palette. All neutral = no-op. Reusable
	across the pipeline and exposed as studio color controls.
	"""
	import numpy as np
	from PIL import ImageEnhance
	img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
	if brightness != 1.0 or warmth != 1.0:
		a = np.asarray(img).astype(np.float32)
		alpha = a[:, :, 3:4]
		rgb = a[:, :, :3] * brightness
		if warmth != 1.0:
			rgb[:, :, 0] *= warmth              # red up
			rgb[:, :, 2] *= (2.0 - warmth)      # blue down by the same amount
		img = Image.fromarray(np.clip(np.concatenate([rgb, alpha], 2), 0, 255).astype(np.uint8))
	if saturation != 1.0:
		img = ImageEnhance.Color(img).enhance(saturation)
	if contrast != 1.0:
		img = ImageEnhance.Contrast(img).enhance(contrast)
	if hue:
		# rotate the H channel in HSV space; PIL's HSV H is 0..255, so scale degrees accordingly.
		alpha_ch = img.split()[3]
		hsv = np.asarray(img.convert("RGB").convert("HSV")).astype(np.int16)
		hsv[:, :, 0] = (hsv[:, :, 0] + round(hue / 360.0 * 256)) % 256
		img = Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGB").convert("RGBA")
		img.putalpha(alpha_ch)
	buf = io.BytesIO()
	img.save(buf, format="PNG")
	return buf.getvalue()


# The near-black edge weight measured on real D2 item sprites (outermost opaque ring at
# luminance ~16 vs ~60 one pixel in). Reproducing it is what makes an item read against
# any background, the way every vanilla sprite does.
OUTLINE_RGB = (14, 12, 10)


def add_edge_outline(canvas: "Image.Image", rgb=OUTLINE_RGB, even: bool = False) -> "Image.Image":
	"""Add a near-black 1px rim to the silhouette, in place on the RGBA canvas.

	Applied at FINAL sprite resolution (1px == 1 sprite pixel) so the rim is exactly the
	1px weight vanilla uses, and BEFORE quantise so it survives to the DC6.

	Default (even=False): darken the outermost opaque RING inward — recolours the edge pixels,
	so the silhouette never grows (matches vanilla, where the outline is part of the sprite).

	even=True (optional per-item toggle): grow the rim OUTWARD instead — colour the transparent
	1px ring just OUTSIDE the silhouette (8-connectivity, so it's continuous around diagonals).
	This gives a clean, continuous border WITHOUT recolouring any art pixels, so it never cuts
	into the coloured artwork (the inward version eats diagonal art pixels on staircased edges,
	e.g. the Jewel). Cost: the silhouette is 1px larger; where the art already touches the cell
	edge the outward ring is simply clipped there.
	"""
	import numpy as np
	from scipy import ndimage

	arr = np.array(canvas.convert("RGBA"))
	solid = arr[:, :, 3] >= 128
	if not solid.any():
		return canvas
	if even:
		# outward: the previously-transparent pixels 8-adjacent to the silhouette
		ring = ndimage.binary_dilation(solid, structure=np.ones((3, 3), bool), border_value=0) & ~solid
	else:
		# inward: the outermost opaque pixels (4-connectivity, recoloured — art edge is consumed)
		ring = solid & ~ndimage.binary_erosion(solid, iterations=1, border_value=0)
	arr[ring, 0], arr[ring, 1], arr[ring, 2] = rgb
	arr[ring, 3] = 255                       # hard edge, like the palettised original
	return Image.fromarray(arr, "RGBA")


_STACK_BADGE = None


def stack_badge():
	"""The gold '+' glyph the game composites on the top-right of a STACKED rune/gem sprite (10x10
	on a 28px cell, anchored ~1px from the top-right corner). Extracted once from the original stack
	DC6s (identical across all of them) and cached under <ws>/assets/stack_badge.png. Returns a PIL
	RGBA image, or None if the asset is missing."""
	global _STACK_BADGE
	if _STACK_BADGE is None:
		p = os.path.join(WORKSPACE, "assets", "stack_badge.png")
		_STACK_BADGE = Image.open(p).convert("RGBA") if os.path.exists(p) else False
	return _STACK_BADGE or None


# badge geometry on the reference 28px cell it was extracted from
_BADGE_REF_CELL = 28
_BADGE_MARGIN = 1


def _overlay_stack_badge(canvas: Image.Image) -> Image.Image:
	"""Alpha-composite the stack '+' badge onto the top-right of a fitted cell canvas, scaled to the
	canvas so it matches the game at any cell size (all stackables are 1x1, but this stays correct)."""
	badge = stack_badge()
	if badge is None:
		return canvas
	s = canvas.width / _BADGE_REF_CELL
	bw, bh = max(1, round(badge.width * s)), max(1, round(badge.height * s))
	b = badge.resize((bw, bh), Image.NEAREST if s >= 1 else Image.LANCZOS)
	m = max(1, round(_BADGE_MARGIN * s))
	out = canvas.copy()
	out.alpha_composite(b, (canvas.width - bw - m, m))
	return out


def rotate_png(png_bytes: bytes, degrees: float) -> bytes:
	"""Rotate RGBA art about its centre, expanding the canvas so nothing is clipped.

	Applied BEFORE fit_png_to_cell, which crops to the alpha bbox -- so the transparent corners
	the rotation introduces are discarded and the art is re-framed to the cell at its new angle.
	Bicubic keeps a diagonal blade edge clean; the expanded corners stay fully transparent.
	"""
	if not degrees or abs(degrees) < 0.01:
		return png_bytes
	im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
	out = im.rotate(float(degrees), resample=Image.BICUBIC, expand=True,
	                fillcolor=(0, 0, 0, 0))
	buf = io.BytesIO()
	out.save(buf, "PNG")
	return buf.getvalue()


def png_to_item_dc6(png_bytes: bytes, invwidth: int, invheight: int, *,
                    fill: float = 0.94, dx: float = 0.0, dy: float = 0.0, rot: float = 0.0,
                    grade: dict | None = None, outline: bool = True, stack: bool = False,
                    thin: bool = False, even_border: bool = False) -> bytes:
	"""Crop-to-fill a PNG into the item's cell grid, quantize, and encode a 1-frame DC6.
	`grade` (optional): {brightness, warmth, saturation, contrast, hue} applied before fitting.
	`rot` (optional): degrees counter-clockwise, applied before the fit so the cell crop
	re-frames around the rotated art.
	`outline`: bake the vanilla 1px near-black edge rim (on by default).
	`stack`: overlay the gold '+' stack badge (top-right) — used to derive a rune/gem STACK sprite
	from the same enhanced base art, so the stack always matches its base.
	`thin`: thin/elongated item (sword/staff/…). Skips the cell-scale despeckle (which flattens a
	~1px blade highlight to the dark median) and lowers the quantize alpha cliff to 96 so a
	downscaled blade edge survives as a dark rim pixel instead of breaking into a dashed line."""
	if grade:
		png_bytes = color_grade(png_bytes, **{k: float(v) for k, v in grade.items()
		                                      if k in ("brightness", "warmth", "saturation", "contrast", "hue")})
	png_bytes = rotate_png(png_bytes, rot)
	canvas = fit_png_to_cell(png_bytes, invwidth, invheight, fill=fill, dx=dx, dy=dy)
	if not thin:
		# second despeckle at CELL scale: glinty PBR metal re-creates isolated bright pixels on
		# downsize even from clean renders (Ancient Armor case) — kill them where the player looks.
		# Skipped for thin items: a 1px blade highlight IS an isolated bright pixel and gets erased.
		canvas = _despeckle_fireflies(canvas, thresh=50, size=3)
	if outline:
		canvas = add_edge_outline(canvas, even=even_border)
	if stack:                                   # badge on TOP of the outline, so it isn't rimmed
		canvas = _overlay_stack_badge(canvas)
	target_w, target_h = canvas.width, canvas.height
	rows = _quantize_to_palette(canvas, _palette(), alpha_cliff=96 if thin else 128)
	frame = dc6.Dc6Frame(flip=0, width=target_w, height=target_h, offset_x=0, offset_y=0, pixels=rows)
	sprite = dc6.Dc6File(directions=1, frames_per_direction=1, termination=b"\xee\xee\xee\xee", frames=[frame])
	return dc6.encode(sprite)


# ---- flippy (animated ground-drop DC6) ----------------------------------

def pngs_to_flippy_dc6(png_frames: list[bytes], ref_dc6_bytes: bytes) -> bytes:
	"""Encode a tumble-frame sequence as a flippy DC6 matched to the original's geometry.

	The original flippy's per-frame offsets trace the item's fall arc (offset_y climbs
	from ~-140 back to 0) and its frame sizes set the on-ground visual scale.  We keep
	the frame count, reuse each original frame's offsets (center-corrected for our
	uniform box), and size the box just above the original's largest frame so the drop
	reads at the same scale as every other item.
	"""
	ref = dc6.decode(ref_dc6_bytes)
	if len(png_frames) != len(ref.frames):
		raise ValueError(f"need {len(ref.frames)} frames to match the original flippy, "
		                 f"got {len(png_frames)}")
	box = max(max(f.width, f.height) for f in ref.frames) + 4
	pal = _palette()
	frames = []
	for png, orig in zip(png_frames, ref.frames):
		img = Image.open(io.BytesIO(png)).convert("RGBA")
		img.thumbnail((box, box), Image.LANCZOS)
		canvas = Image.new("RGBA", (box, box), (0, 0, 0, 0))
		canvas.paste(img, ((box - img.width) // 2, (box - img.height) // 2))
		rows = _quantize_to_palette(canvas, pal)
		ox = orig.offset_x - (box - orig.width) // 2  # keep the original frame's center
		oy = orig.offset_y  # keep the fall-arc anchor (bottom-up draw)
		frames.append(dc6.Dc6Frame(flip=0, width=box, height=box,
		                           offset_x=ox, offset_y=oy, pixels=rows))
	sprite = dc6.Dc6File(directions=ref.directions,
	                     frames_per_direction=ref.frames_per_direction,
	                     termination=b"\xee\xee\xee\xee", frames=frames)
	return dc6.encode(sprite)


def dc6_to_gif_bytes(dc6_bytes: bytes, bg=(48, 48, 56)) -> bytes:
	"""Animated-GIF preview of a multi-frame DC6 (frames composited on a dark bg)."""
	sprite = dc6.decode(dc6_bytes)
	pal = _palette()
	# common canvas that fits every frame at its anchor
	w = max(f.width for f in sprite.frames)
	h = max(f.height for f in sprite.frames)
	ims = []
	for f in sprite.frames:
		fw, fh, rgba = frame_to_rgba(f, pal)
		fr = Image.frombytes("RGBA", (fw, fh), rgba)
		canvas = Image.new("RGBA", (w, h), bg + (255,))
		canvas.paste(fr, ((w - fw) // 2, (h - fh) // 2), fr)
		ims.append(canvas.convert("P", palette=Image.ADAPTIVE))
	buf = io.BytesIO()
	ims[0].save(buf, format="GIF", save_all=True, append_images=ims[1:],
	            duration=80, loop=0, disposal=2)
	return buf.getvalue()


# ---- overlay + manifest -------------------------------------------------

def _load_manifest():
	if os.path.exists(MANIFEST):
		try:
			with open(MANIFEST, encoding="utf-8-sig") as f:  # tolerate a BOM
				return json.load(f)
		except (json.JSONDecodeError, OSError):
			pass
	return {"version": 1, "assets": {}, "owned_overlay_files": []}


def _save_manifest(m):
	os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
	with open(MANIFEST, "w", encoding="utf-8") as f:
		json.dump(m, f, indent=1)


# ---- shared-by-DC6 bucketing -------------------------------------------------
# Alternates are stored per *DC6 file*, not per item: every item whose inventory
# graphic (invfile) resolves to the same .dc6 shares one pool of alternates AND one
# active choice. The render overlay is already one physical file per invfile
# (see activate()), so this only aligns the studio's storage with what the game
# already does -- items sharing a DC6 cannot render differently in-game anyway.
# Flippy (ground-drop) alternates bucket by the flippyfile the same way. Giving an
# item its own invfile (the "own invfile" split) moves it to a different bucket, so
# it gets a private pool -- the escape hatch for making one item look distinct.

_ITEM_RESOLVER = None


def register_item_resolver(fn) -> None:
	"""Install a callback item_id -> item dict (carrying 'invfile'/'flippyfile'), or
	None. Wired from the server's live catalog so bucketing follows studio invfile
	edits. When unset (tests / standalone), storage falls back to per-item folders."""
	global _ITEM_RESOLVER
	_ITEM_RESOLVER = fn


def _resolve_item(item_id: str) -> dict:
	if _ITEM_RESOLVER:
		try:
			return _ITEM_RESOLVER(item_id) or {}
		except Exception:  # noqa: BLE001
			return {}
	return {}


def _inv_bucket(item_id: str) -> str:
	"""Storage/manifest key for an item's inventory alternates: its invfile's DC6
	bucket ('dc6/<invfile>'), or the item_id itself when no resolver is wired."""
	inv = (_resolve_item(item_id).get("invfile") or "").strip().lower()
	return f"dc6/{inv}" if inv else item_id


def _flip_bucket(item_id: str) -> str:
	"""Storage/manifest key for an item's flippy (ground-drop) alternates:
	'flip/<flippyfile>', or the legacy '<item_id>/flippy' when no resolver is wired."""
	flp = (_resolve_item(item_id).get("flippyfile") or "").strip().lower()
	return f"flip/{flp}" if flp else f"{item_id}/flippy"


def alt_dir(item_id: str) -> str:
	return os.path.join(ALTERNATES, _inv_bucket(item_id).replace("/", os.sep))


def flippy_dir(item_id: str) -> str:
	return os.path.join(ALTERNATES, _flip_bucket(item_id).replace("/", os.sep))


def list_alternates(item_id: str):
	d = alt_dir(item_id)
	if not os.path.isdir(d):
		return []
	out = []
	for name in sorted(os.listdir(d)):
		if name.endswith(".dc6"):
			out.append(name[:-4])
	return out


def save_alternate_dc6(item_id: str, alt_id: str, dc6_bytes: bytes):
	d = alt_dir(item_id)
	os.makedirs(d, exist_ok=True)
	with open(os.path.join(d, alt_id + ".dc6"), "wb") as f:
		f.write(dc6_bytes)


def alt_dc6_bytes(item_id: str, alt_id: str) -> bytes:
	with open(os.path.join(alt_dir(item_id), alt_id + ".dc6"), "rb") as f:
		return f.read()


# ---- alternate provenance (keep GLB/source/render/meta for later Blender work) ----------

def alt_asset(item_id: str, alt_id: str, ext: str) -> str:
	"""Path to a sidecar asset for an alternate (e.g. '.glb', '.source.png', '.render.png',
	'.meta.json'), kept next to the alternate's .dc6 so nothing is thrown away."""
	return os.path.join(alt_dir(item_id), alt_id + ext)


def save_alt_provenance(item_id: str, alt_id: str, *, glb: bytes | None = None,
                        source_png: bytes | None = None, render_png: bytes | None = None,
                        meta: dict | None = None):
	os.makedirs(alt_dir(item_id), exist_ok=True)
	if glb is not None:
		with open(alt_asset(item_id, alt_id, ".glb"), "wb") as f:
			f.write(glb)
	if source_png is not None:
		with open(alt_asset(item_id, alt_id, ".source.png"), "wb") as f:
			f.write(source_png)
	if render_png is not None:
		with open(alt_asset(item_id, alt_id, ".render.png"), "wb") as f:
			f.write(render_png)
	if meta is not None:
		with open(alt_asset(item_id, alt_id, ".meta.json"), "w", encoding="utf-8") as f:
			json.dump(meta, f, indent=1)


def alt_meta(item_id: str, alt_id: str) -> dict:
	p = alt_asset(item_id, alt_id, ".meta.json")
	if os.path.exists(p):
		try:
			with open(p, encoding="utf-8") as f:
				return json.load(f)
		except (json.JSONDecodeError, OSError):
			pass
	return {}


def alt_render_png(item_id: str, alt_id: str) -> bytes | None:
	"""The saved Blender/Meshy render for an alt (before DC6) -- the source for re-fitting."""
	p = alt_asset(item_id, alt_id, ".render.png")
	if os.path.exists(p):
		with open(p, "rb") as f:
			return f.read()
	return None


def refit_alt(item_id: str, alt_id: str, invwidth: int, invheight: int,
              fill: float, dx: float, dy: float, grade: dict | None = None,
              thin: bool = False, fit_auto: bool = False, even_border: bool = False,
              rot: float = 0.0, outline: bool = True) -> bool:
	"""Re-run crop-to-fill (+ optional rotation and color grade) on an alt's saved render and
	rewrite its DC6. Instant (no Blender). Always works from the SAVED RENDER, never the current
	DC6, so rotation is non-destructive and non-cumulative: dragging the slider to 30 then 10
	gives a 10-degree result, not 40.
	Updates the alt's meta. Returns False if no saved render exists."""
	render = alt_render_png(item_id, alt_id)
	if render is None:
		return False
	dc6_bytes = png_to_item_dc6(render, invwidth, invheight, fill=fill, dx=dx, dy=dy, rot=rot,
	                            grade=grade, thin=thin, even_border=even_border, outline=outline)
	save_alternate_dc6(item_id, alt_id, dc6_bytes)
	m = alt_meta(item_id, alt_id)
	m.update({"fill": fill, "dx": dx, "dy": dy, "rot": rot, "outline": outline,
	          "fit_auto": fit_auto, "even_border": even_border})
	if grade:
		m["grade"] = grade
	save_alt_provenance(item_id, alt_id, meta=m)
	return True


def cell_preview_png(png_bytes: bytes, invwidth: int, invheight: int, *,
                     fill: float, dx: float, dy: float, scale: int = 4,
                     grade: dict | None = None, even_border: bool = False,
                     rot: float = 0.0, outline: bool = True) -> bytes:
	"""Composite a render into its actual inventory cell (reddish bg + grid) at the given
	fill/dx/dy/rot (+ optional color grade), scaled up for a crisp UI preview. `even_border` shows
	the continuous-rim toggle live (the framing preview otherwise carries no outline).
	Rotation happens in the same order as png_to_item_dc6 (grade -> rotate -> fit) so the preview
	matches what accepting actually produces."""
	from PIL import ImageDraw
	if grade:
		png_bytes = color_grade(png_bytes, **{k: float(v) for k, v in grade.items()
		                                     if k in ("brightness", "warmth", "saturation", "contrast", "hue")})
	png_bytes = rotate_png(png_bytes, rot)
	fitted = fit_png_to_cell(png_bytes, invwidth, invheight, fill=fill, dx=dx, dy=dy)
	if outline:
		fitted = add_edge_outline(fitted, even=even_border)
	cell = fitted.resize((fitted.width * scale, fitted.height * scale), Image.NEAREST)
	bg = Image.new("RGBA", cell.size, (46, 20, 20, 255))
	d = ImageDraw.Draw(bg)
	for gx in range(invwidth + 1):  # cell grid lines
		x = min(gx * CELL_PX * scale, cell.width - 1)
		d.line([(x, 0), (x, cell.height)], fill=(90, 60, 60, 255))
	for gy in range(invheight + 1):
		y = min(gy * CELL_PX * scale, cell.height - 1)
		d.line([(0, y), (cell.width, y)], fill=(90, 60, 60, 255))
	bg.alpha_composite(cell)
	buf = io.BytesIO()
	bg.save(buf, format="PNG")
	return buf.getvalue()


def _write_overlay(rel: str, payload: bytes):
	dest = os.path.join(OVERLAY, *rel.split("\\"))
	os.makedirs(os.path.dirname(dest), exist_ok=True)
	tmp = dest + ".tmp"
	with open(tmp, "wb") as f:
		f.write(payload)
	os.replace(tmp, dest)


def _remove_overlay(rel: str):
	dest = os.path.join(OVERLAY, *rel.split("\\"))
	if os.path.exists(dest):
		os.remove(dest)


def activate(item_id: str, invfile: str, choice: str):
	"""choice = 'original' or an alt_id. Writes/removes the overlay DC6 and updates manifest."""
	m = _load_manifest()
	rel = item_dc6_path(invfile)
	key = _inv_bucket(item_id)  # shared per invfile: activating for one item activates for all
	owned = set(m.get("owned_overlay_files", []))
	entry = m["assets"].get(key, {})
	if choice == "original":
		_remove_overlay(rel)
		owned.discard(rel)
		entry.pop("active", None)
		entry.pop("invfile", None)
	else:
		_write_overlay(rel, alt_dc6_bytes(item_id, choice))
		owned.add(rel)
		entry["active"] = choice
		entry["invfile"] = invfile
	if entry:
		m["assets"][key] = entry
	else:
		m["assets"].pop(key, None)
	m["owned_overlay_files"] = sorted(owned)
	_save_manifest(m)
	return m


def activate_flippy(item_id: str, flippyfile: str, choice: str):
	"""Like activate(), for the item's animated ground-drop DC6 (flippyfile)."""
	m = _load_manifest()
	rel = item_dc6_path(flippyfile)  # flippies live in the same items\ dir
	key = _flip_bucket(item_id)  # shared per flippyfile
	owned = set(m.get("owned_overlay_files", []))
	entry = m["assets"].get(key, {})
	if choice == "original":
		_remove_overlay(rel)
		owned.discard(rel)
		entry.pop("flippy_active", None)
		entry.pop("flippyfile", None)
	else:
		_write_overlay(rel, flippy_alt_dc6_bytes(item_id, choice))
		owned.add(rel)
		entry["flippy_active"] = choice
		entry["flippyfile"] = flippyfile
	if entry:
		m["assets"][key] = entry
	else:
		m["assets"].pop(key, None)
	m["owned_overlay_files"] = sorted(owned)
	_save_manifest(m)
	return m


def active_choice(item_id: str) -> str:
	m = _load_manifest()
	return m.get("assets", {}).get(_inv_bucket(item_id), {}).get("active", "original")


def active_flippy_choice(item_id: str) -> str:
	m = _load_manifest()
	return m.get("assets", {}).get(_flip_bucket(item_id), {}).get("flippy_active", "original")


def list_flippy_alternates(item_id: str):
	d = flippy_dir(item_id)
	if not os.path.isdir(d):
		return []
	return sorted(n[:-4] for n in os.listdir(d) if n.endswith(".dc6"))


def save_flippy_alternate_dc6(item_id: str, alt_id: str, dc6_bytes: bytes):
	d = flippy_dir(item_id)
	os.makedirs(d, exist_ok=True)
	with open(os.path.join(d, alt_id + ".dc6"), "wb") as f:
		f.write(dc6_bytes)


def flippy_alt_dc6_bytes(item_id: str, alt_id: str) -> bytes:
	with open(os.path.join(flippy_dir(item_id), alt_id + ".dc6"), "rb") as f:
		return f.read()


def _alt_files(item_id: str, alt_id: str, *, flippy: bool = False):
	"""Every file belonging to an alternate: the .dc6 plus its provenance sidecars
	(.glb / .source.png / .render.png / .meta.json / ...), all named '<alt_id>.<ext>'."""
	d = flippy_dir(item_id) if flippy else alt_dir(item_id)
	if not os.path.isdir(d):
		return []
	prefix = alt_id + "."
	return [os.path.join(d, n) for n in os.listdir(d)
	        if n.startswith(prefix) and os.path.isfile(os.path.join(d, n))]


def delete_alternate(item_id: str, alt_id: str, *, flippy: bool = False):
	"""Remove an alternate and all its sidecars. Caller reverts the overlay first if active."""
	for p in _alt_files(item_id, alt_id, flippy=flippy):
		os.remove(p)


def rename_alternate(item_id: str, old_id: str, new_id: str, *, flippy: bool = False):
	"""Rename an alternate (dc6 + sidecars) and keep the manifest's active choice with it."""
	for p in _alt_files(item_id, old_id, flippy=flippy):
		d, n = os.path.split(p)
		os.rename(p, os.path.join(d, new_id + n[len(old_id):]))
	m = _load_manifest()
	bucket = _flip_bucket(item_id) if flippy else _inv_bucket(item_id)
	entry = m.get("assets", {}).get(bucket)
	field = "flippy_active" if flippy else "active"
	if entry and entry.get(field) == old_id:
		entry[field] = new_id
		_save_manifest(m)


EXPORT_DIR = os.path.join(WORKSPACE, "export")


def build_patch_mpq(out_path: str | None = None) -> tuple[str, int]:
	"""Author a patch.mpq from every file in the overlay tree. Returns (path, count).

	Writes to a fresh rotating filename (patch_<n>.mpq) so a copy the running game still
	holds open never blocks the rebuild; the game closes the old one when it registers
	the new. Stale, now-unlocked patch_*.mpq are pruned.
	"""
	files = {}
	for root, _dirs, names in os.walk(OVERLAY):
		for n in names:
			disk = os.path.join(root, n)
			arc = os.path.relpath(disk, OVERLAY).replace("/", "\\")
			files[arc] = disk
	os.makedirs(EXPORT_DIR, exist_ok=True)
	if out_path is None:
		# pick the next free patch_<n>.mpq
		n = 0
		while True:
			cand = os.path.join(EXPORT_DIR, f"patch_{n}.mpq")
			if not os.path.exists(cand):
				out_path = cand
				break
			try:  # an old one we can overwrite (not locked) is fine to reuse
				os.remove(cand)
				out_path = cand
				break
			except OSError:
				n += 1
	build_archive(out_path, files)  # v1 + PKWARE (D2-compatible)
	write_autoload(out_path)
	# prune other now-unlocked patch_*.mpq (best-effort)
	for name in os.listdir(EXPORT_DIR):
		p = os.path.join(EXPORT_DIR, name)
		if name.startswith("patch_") and name.endswith(".mpq") and p != out_path:
			try:
				os.remove(p)
			except OSError:
				pass
	return out_path, len(files)


AUTOLOAD = os.path.join(WORKSPACE, "autoload.txt")


def write_autoload(mpq_path: str, priority: int = 9000):
	"""Point the D2Debugger early-registration hook at the freshest patch.mpq.

	The hook (DATATBLS_LoadAllTxts detour) reads this file at process startup and
	registers the archive BEFORE data tables load, so excel-bin edits (uniqueitems
	invfile etc.) take effect on a plain full reload.  Written on every build.
	"""
	tmp = AUTOLOAD + ".tmp"
	with open(tmp, "w", encoding="ascii") as f:
		f.write(os.path.abspath(mpq_path) + "\n" + str(int(priority)) + "\n")
	os.replace(tmp, AUTOLOAD)
