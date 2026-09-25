"""Phase-0 diagnosis: WHERE does a thin metallic blade (Long Sword) lose pixels in the
background-cut + fit + quantize pipeline?

Compares the two rembg cutters (isnet-general-use = the wrongly-shipped default that the
design doc rejected, vs birefnet-general = the intended one) and replays the downstream
alpha stages, emitting side-by-side strips + per-stage opaque-pixel counts so the loss point
is visible. Read-only w.r.t. app data; writes only under _fidelity_out/_bgdiag/.

    python scripts/diagnose_bgcut.py                 # Long Sword (invlsd) + a helm control
    python scripts/diagnose_bgcut.py invlsd invfhl
"""

from __future__ import annotations

import io
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # app package root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))
from app import assets, comfy  # noqa: E402
import catalog  # noqa: E402

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_fidelity_out", "_bgdiag"))
LAB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_fidelity_out", "lab"))
MODELS = ["isnet-general-use", "birefnet-general"]


def opaque(png_or_img) -> int:
	im = png_or_img if isinstance(png_or_img, Image.Image) else Image.open(io.BytesIO(png_or_img))
	a = np.asarray(im.convert("RGBA"))[:, :, 3]
	return int((a >= 128).sum())


def as_img(x) -> Image.Image:
	return x if isinstance(x, Image.Image) else Image.open(io.BytesIO(x)).convert("RGBA")


def alpha_vis(img: Image.Image) -> Image.Image:
	"""White-on-checker view of an alpha channel for the strip."""
	a = img.convert("RGBA").split()[-1]
	vis = Image.new("RGBA", a.size, (0, 0, 0, 255))
	vis.paste((255, 255, 255, 255), (0, 0), a)
	return vis


def _dc6_thin_prototype(png_bytes: bytes, invwidth: int, invheight: int) -> bytes:
	"""Prototype the planned Phase-1 thin-item path (proves the fix before editing assets.py):
	fit -> SKIP the size-3 cell despeckle -> outline -> quantize with alpha_cliff=96."""
	from pyd2 import dc6
	canvas = assets.fit_png_to_cell(png_bytes, invwidth, invheight, fill=0.94)
	# (thin) skip _despeckle_fireflies(size=3)
	canvas = assets.add_edge_outline(canvas)
	rows = _quantize_cliff(canvas, assets._palette(), alpha_cliff=96)
	frame = dc6.Dc6Frame(flip=0, width=canvas.width, height=canvas.height,
	                     offset_x=0, offset_y=0, pixels=rows)
	sprite = dc6.Dc6File(directions=1, frames_per_direction=1,
	                     termination=b"\xee\xee\xee\xee", frames=[frame])
	return dc6.encode(sprite)


def _quantize_cliff(img, palette, *, alpha_cliff: int):
	"""Port of assets._quantize_to_palette with a parameterized alpha cliff."""
	from pyd2 import dc6
	rgba = img.convert("RGBA")
	arr = np.asarray(rgba, dtype=np.float64)
	h, w = arr.shape[0], arr.shape[1]
	rgb = arr[:, :, :3].reshape(-1, 3)
	alpha = arr[:, :, 3].reshape(-1)
	a = (alpha / 255.0)[:, None]
	rim = np.array(assets.RIM_RGB, dtype=np.float64)[None, :]
	rgb = rgb * a + rim * (1.0 - a)
	SAFE_MAX = 224
	pal = np.array([palette[i] for i in range(256)], dtype=np.float64)
	pix_lab = assets._srgb_to_oklab(rgb / 255.0)
	pal_lab = assets._srgb_to_oklab(pal[1:SAFE_MAX + 1] / 255.0)
	d = ((pix_lab[:, None, :] - pal_lab[None, :, :]) ** 2).sum(axis=2)
	idx = (d.argmin(axis=1) + 1).reshape(h, w)
	amask = (alpha < alpha_cliff).reshape(h, w)
	return [[dc6.TRANSPARENT if amask[y, x] else int(idx[y, x]) for x in range(w)] for y in range(h)]


def item_for(invfile: str):
	items, _ = catalog.build_catalog()
	for it in items:
		if it["invfile"] == invfile and it["category"] == "base":
			return it
	for it in items:
		if it["invfile"] == invfile:
			return it
	return None


def rembg_mask(gen_png: bytes, model: str) -> bytes:
	from rembg import new_session, remove
	sess = new_session(model)
	return remove(gen_png, session=sess)


def reject_clamp(cut_png: bytes, guide_alpha_png: bytes) -> Image.Image:
	"""Exact port of recut_alpha_rembg's reject-only guide clamp (np.minimum)."""
	cut = Image.open(io.BytesIO(cut_png)).convert("RGBA")
	a = cut.split()[-1]
	g = Image.open(io.BytesIO(guide_alpha_png)).convert("L").resize(a.size, Image.LANCZOS)
	g = g.point(lambda v: 255 if v > 8 else 0).filter(ImageFilter.MaxFilter(9))
	g = g.filter(ImageFilter.GaussianBlur(24)).point(lambda v: 255 if v > 24 else 0)
	a2 = Image.fromarray(np.minimum(np.asarray(a), np.asarray(g)).astype("uint8"), "L")
	rgb = Image.open(io.BytesIO(cut_png)).convert("RGB").convert("RGBA")
	rgb.putalpha(a2)
	return rgb


def tile(img: Image.Image, label: str, cell=200) -> Image.Image:
	im = img.convert("RGBA")
	sc = min(cell / max(im.size), 4.0)
	im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))),
	               Image.NEAREST if sc >= 1 else Image.LANCZOS)
	c = Image.new("RGBA", (cell + 8, cell + 26), (32, 32, 38, 255))
	# checker under the tile so transparency reads
	chk = Image.new("RGBA", im.size, (60, 60, 68, 255))
	c.paste(chk, (4 + (cell - im.width) // 2, 22 + (cell - im.height) // 2))
	c.alpha_composite(im, (4 + (cell - im.width) // 2, 22 + (cell - im.height) // 2))
	ImageDraw.Draw(c).text((5, 5), label, fill=(235, 225, 180, 255))
	return c


def strip(tiles: list[Image.Image], path: str):
	w = sum(t.width for t in tiles) + 6 * len(tiles)
	h = max(t.height for t in tiles)
	s = Image.new("RGBA", (w, h), (24, 24, 28, 255))
	x = 0
	for t in tiles:
		s.paste(t, (x, 0))
		x += t.width + 6
	s.convert("RGB").save(path)


def diagnose(invfile: str):
	it = item_for(invfile)
	if not it:
		print(f"{invfile}: not in catalog")
		return
	os.makedirs(OUT, exist_ok=True)
	w, h = it["invwidth"], it["invheight"]
	orig_png = assets.dc6_to_png_bytes(assets.read_original_dc6(invfile))
	orig = Image.open(io.BytesIO(orig_png)).convert("RGBA")

	# source "generated" master: prefer an existing lab master (already went through isnet),
	# but for the cutter A/B we need the RAW matted RGB (pre-cut). Rebuild the matte from the
	# original so both models see the identical gray-matted input.
	rgb_png, alpha_png, (mw, mh) = comfy.matte_and_size(orig_png, 1024)
	matted = Image.open(io.BytesIO(rgb_png)).convert("RGBA")

	print(f"\n=== {invfile} ({it['id']}, {w}x{h}, type={it['type']}) ===")
	print(f"original opaque px: {opaque(orig)}")

	summary = []
	for model in MODELS:
		try:
			mask_png = rembg_mask(rgb_png, model)
		except Exception as e:  # noqa: BLE001
			print(f"  {model}: FAILED {e}")
			continue
		mask_img = as_img(mask_png)
		clamped = reject_clamp(mask_png, alpha_png)
		# downstream: fit to cell + quantize via the real accept path (no grade)
		clamped_buf = io.BytesIO()
		clamped.save(clamped_buf, "PNG")
		dc6 = assets.png_to_item_dc6(clamped_buf.getvalue(), w, h, fill=0.94)
		fitted = Image.open(io.BytesIO(assets.dc6_to_png_bytes(dc6))).convert("RGBA")

		# prototype of the planned Phase-1 thin fix: skip the size-3 cell despeckle + lower the
		# quantize alpha cliff to 96. Reach into png_to_item_dc6 by monkeypatching for the A/B.
		dc6_thin = _dc6_thin_prototype(clamped_buf.getvalue(), w, h)
		fitted_thin = Image.open(io.BytesIO(assets.dc6_to_png_bytes(dc6_thin))).convert("RGBA")

		counts = {"rembg_mask": opaque(mask_img), "clamped": opaque(clamped),
		          "post_quantize": opaque(fitted), "post_thinfix": opaque(fitted_thin)}
		summary.append((model, counts))
		print(f"  {model}: mask={counts['rembg_mask']}  clamped={counts['clamped']}  "
		      f"post_quantize={counts['post_quantize']}  post_THINFIX={counts['post_thinfix']}")

		tiles = [
			tile(orig, "original"),
			tile(matted, "matted gen (gray-128)"),
			tile(alpha_vis(mask_img), f"{model} mask"),
			tile(alpha_vis(clamped), "reject-clamped alpha"),
			tile(fitted, "post fit+quantize (CURRENT)"),
			tile(fitted_thin, "post fit+quantize (THIN FIX)"),
		]
		strip(tiles, os.path.join(OUT, f"{invfile}__{model}.png"))
		print(f"    -> {os.path.join(OUT, f'{invfile}__{model}.png')}")

	# verdict
	if len(summary) == 2:
		iso = dict(summary)["isnet-general-use"]["post_quantize"]
		bir = dict(summary)["birefnet-general"]["post_quantize"]
		print(f"  VERDICT: post-quantize opaque  isnet={iso}  birefnet={bir}  "
		      f"delta={bir - iso:+d} ({'birefnet keeps more' if bir > iso else 'similar'})")


def main():
	invs = [a for a in sys.argv[1:] if not a.startswith("-")] or ["invlsd", "invfhl"]
	for inv in invs:
		diagnose(inv)


if __name__ == "__main__":
	main()
