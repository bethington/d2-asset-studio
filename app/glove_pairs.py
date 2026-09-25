"""Compose a two-handed glove sprite from single-hand 3D models.

D2 shows gloves as a PAIR in one inventory sprite, but the re-imagined art library
splits them into single hands (`invtgl-l4.png` / `invtgl-r4.png`) because a 3D model
of one hand is what Meshy can actually generate. This module puts them back together.

Why a hand-tuned template rather than deriving placement from the original: the two
gloves in the original DC6 OVERLAP, so its alpha is a single merged blob — connected-
component analysis on all five glove sprites returns exactly one region each. There is
nothing to split, so each art file carries a saved layout instead (tuned once, reused
across every variant pair: l1+r1, l4+r4, lj2+rj2 ...).

Placement is stored as NEUTRAL-BASED adjustments so every control has an obvious zero:
`dx`/`dy` are offsets from the hand's default anchor (0 = where it naturally sits),
`scale` is a multiplier on the default fit (1 = natural size), `rot` is degrees
clockwise (0 = unrotated). Units are fractions of the canvas, so a template survives
any change of cell size or render resolution.
"""
from __future__ import annotations

import io
import json
import math
import os
import re

from PIL import Image, ImageChops, ImageFilter

import app.assets as assets
from pyd2 import dc6

TEMPLATES_PATH = os.path.join(assets.WORKSPACE, "pair_templates.json")

# Where each hand sits with everything at neutral: side by side, vertically centred.
# Tuning moves a hand RELATIVE to this, so dx=dy=0 / scale=1 / rot=0 is a sane sprite
# on its own rather than both hands stacked in the middle.
BASE_ANCHOR = {"left": (0.34, 0.50), "right": (0.66, 0.50)}
BASE_FIT = 0.52          # hand's longest side as a fraction of canvas width at scale 1

# `flip` mirrors the source art before placing. Two independent uses, and they combine:
# a hand with no generation is mirrored from the other, and art that is oriented
# opposite to its filename is normalised by flipping (measured: 8 of 68 library files
# are, and since every -r is an exact flip of its -l, flipping is lossless and yields
# the true opposite hand -- so a mis-oriented file self-corrects instead of erroring).
NEUTRAL_HAND = {"dx": 0.0, "dy": 0.0, "scale": 1.0, "rot": 0.0, "flip": False}

# Pose for the 3D pair render. yaw turns each hand inward, gap separates them and depth
# pushes one toward the camera; gap/depth are fractions of the model's own width so the
# pose is scale-independent. Mirrors make_pair()'s contract and pair3d.js's defaults.
# yaw is a ROLL in the view plane (0 = hands dead upright); azim 0 / elev 0 looks
# straight at the back of the hand, which is how the original sprites read. A non-zero
# azimuth is what swung the glove into a profile view.
DEFAULT_POSE3D = {"yaw": 0.0, "gap": 0.55, "depth": 0.0,
                  "azim": 0.0, "elev": 0.0, "margin": 1.06}
DEFAULT_TEMPLATE = {
	"left":  dict(NEUTRAL_HAND),
	"right": dict(NEUTRAL_HAND),
	"front": "right",
	# included here too, so the no-saved-template early return still carries a pose
	"pose3d": dict(DEFAULT_POSE3D),
}

# `-l4` = LEFT hand, variant 4.  `-rj2` = RIGHT hand, variant j2 (the `j` series is a
# second run of redraws).  `-L` / `-R` are the original unnumbered pair.
VISIBLE_ALPHA = 16      # below this a pixel reads as nothing once quantised to the palette

_HAND_RE = re.compile(r"-(?P<hand>[lr])(?P<series>j?)(?P<num>\d*)$", re.IGNORECASE)


def hand_of(art_file: str | None) -> str | None:
	"""'invtgl-l4.png' -> 'left'; 'invtgl-rj2.png' -> 'right'; 'invtgl.png' -> None.

	The library's own filenames carry the hand, so this never has to be inferred from
	the picture. The `j` series (lj1..lj4 / rj1..rj4) is a second set of redraws.
	"""
	if not art_file:
		return None
	stem = os.path.splitext(os.path.basename(art_file))[0]
	m = _HAND_RE.search(stem)
	if not m:
		return None
	return "left" if m.group("hand").lower() == "l" else "right"


def variant_of(art_file: str | None) -> str | None:
	"""'invtgl-l4.png' -> '4';  'invtgl-rj2.png' -> 'j2';  'invlgl-L.png' -> ''.

	The number is the VARIANT, and it is what makes two files a set: `-l4` and `-r4`
	are the same redraw's two hands and belong together. Pairing across variants
	(l4 with r7) would put two different designs on one pair of hands.
	"""
	if not art_file:
		return None
	stem = os.path.splitext(os.path.basename(art_file))[0]
	m = _HAND_RE.search(stem)
	if not m:
		return None
	return (m.group("series") or "").lower() + (m.group("num") or "")


def variant_label(v: str | None) -> str:
	if v is None:
		return "?"
	return f"variant {v}" if v else "base pair"


def is_split_art(art_file: str | None) -> bool:
	return hand_of(art_file) is not None


# ---- templates ------------------------------------------------------------

def load_templates() -> dict:
	try:
		with open(TEMPLATES_PATH, encoding="utf-8") as f:
			return json.load(f)
	except (FileNotFoundError, ValueError):
		return {}


def _from_legacy(hand: str, src: dict) -> dict:
	"""Convert a pre-2026-07-19 absolute entry (cx/cy/absolute scale) to the neutral
	form, so templates saved before the controls were re-zeroed still load."""
	ax, ay = BASE_ANCHOR[hand]
	return {
		"dx": float(src.get("cx", ax)) - ax,
		"dy": float(src.get("cy", ay)) - ay,
		"scale": float(src.get("scale", BASE_FIT)) / BASE_FIT,
		"rot": float(src.get("rot", 0.0)),
	}


def get_template(invfile: str) -> dict:
	t = load_templates().get((invfile or "").lower())
	out = json.loads(json.dumps(DEFAULT_TEMPLATE))
	if not t:
		return out
	for hand in ("left", "right"):
		src = t.get(hand) or {}
		if "cx" in src or "cy" in src:
			out[hand].update(_from_legacy(hand, src))
		else:
			out[hand].update({k: float(v) for k, v in src.items()
			                  if k in ("dx", "dy", "scale", "rot")})
			out[hand]["flip"] = bool(src.get("flip", False))
	out["front"] = t.get("front", out["front"])
	out["pose3d"] = dict(DEFAULT_POSE3D)
	out["pose3d"].update({k: float(v) for k, v in (t.get("pose3d") or {}).items()
	                      if k in DEFAULT_POSE3D})
	return out


def save_template(invfile: str, tpl: dict) -> dict:
	all_t = load_templates()
	clean = {}
	for hand in ("left", "right"):
		src = tpl.get(hand) or {}
		if "cx" in src or "cy" in src:
			src = _from_legacy(hand, src)
		clean[hand] = {
			"dx": max(-1.0, min(1.0, float(src.get("dx", 0.0)))),
			"dy": max(-1.0, min(1.0, float(src.get("dy", 0.0)))),
			# auto-fit legitimately needs >3x: a heavily rotated glove filling the
			# height measures small on its unrotated longest side (invtgl wants 3.37)
			"scale": max(0.1, min(6.0, float(src.get("scale", 1.0)))),
			"rot": max(-180.0, min(180.0, float(src.get("rot", 0.0)))),
			"flip": bool(src.get("flip", False)),
		}
	clean["front"] = "left" if tpl.get("front") == "left" else "right"
	pose = dict(DEFAULT_POSE3D)
	pose.update({k: float(v) for k, v in (tpl.get("pose3d") or {}).items()
	             if k in DEFAULT_POSE3D})
	pose["yaw"] = max(-60.0, min(60.0, pose["yaw"]))
	pose["gap"] = max(0.0, min(2.0, pose["gap"]))
	pose["depth"] = max(-2.0, min(2.0, pose["depth"]))
	pose["elev"] = max(-89.0, min(89.0, pose["elev"]))
	pose["margin"] = max(1.0, min(2.0, pose["margin"]))
	clean["pose3d"] = pose
	all_t[(invfile or "").lower()] = clean
	tmp = TEMPLATES_PATH + ".tmp"
	with open(tmp, "w", encoding="utf-8") as f:
		json.dump(all_t, f, indent=1)
	os.replace(tmp, TEMPLATES_PATH)
	return clean


# ---- image helpers --------------------------------------------------------

def drop_flat_background(img: Image.Image, thresh: int = 26) -> Image.Image:
	"""Cut the flat black backdrop off a re-imagined hand image.

	Only removes background REACHABLE FROM THE IMAGE BORDER. The naive version deleted
	every pixel darker than `thresh` anywhere, which on a dark glove punched holes
	straight through its own shadows and crevices: invvgl came out only 10% fully
	opaque with 32% of pixels partially transparent (interior alpha averaging 196 of
	255), so the checkerboard showed through the artwork and the built sprite was
	genuinely see-through.

	Backdrop is contiguous with the edges; a shadow inside the glove is not.
	"""
	img = img.convert("RGBA")
	if img.split()[-1].getextrema()[0] < 250:
		return img          # already carries real transparency

	try:
		import numpy as np
		from scipy import ndimage
	except ImportError:      # keep working without them, holes and all
		return _drop_flat_background_naive(img, thresh)

	a = np.array(img)
	dark = (a[:, :, 0] < thresh) & (a[:, :, 1] < thresh) & (a[:, :, 2] < thresh)
	if not dark.any():
		return img
	# label the dark regions, then keep only those touching an edge
	lab, n = ndimage.label(dark)
	if n == 0:
		return img
	edge = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
	edge.discard(0)
	if not edge:
		return img
	background = np.isin(lab, list(edge))
	a[:, :, 3] = np.where(background, 0, 255).astype(np.uint8)
	return Image.fromarray(a, "RGBA")


def _drop_flat_background_naive(img: Image.Image, thresh: int) -> Image.Image:
	px = img.load()
	w, h = img.size
	for y in range(h):
		for x in range(w):
			r, g, b, _a = px[x, y]
			if r < thresh and g < thresh and b < thresh:
				px[x, y] = (r, g, b, 0)
	return img


def _crop_to_content(img: Image.Image) -> Image.Image:
	bb = img.split()[-1].getbbox()
	return img.crop(bb) if bb else img


def place_hand(canvas: Image.Image, hand_png: bytes | Image.Image, spec: dict,
               mirror: bool = False, hand: str = "left") -> Image.Image:
	"""Scale/rotate/position one hand onto the canvas per its template entry.

	`spec` is neutral-based: scale multiplies BASE_FIT, dx/dy offset BASE_ANCHOR, so
	{dx:0, dy:0, scale:1, rot:0} places the hand at its natural spot and size.
	"""
	img = hand_png if isinstance(hand_png, Image.Image) else Image.open(io.BytesIO(hand_png))
	img = drop_flat_background(img)
	img = _crop_to_content(img)
	# XOR: mirroring for a missing hand and normalising mis-oriented art can cancel out
	if bool(mirror) != bool(spec.get("flip", False)):
		img = img.transpose(Image.FLIP_LEFT_RIGHT)
	W, H = canvas.size
	target = max(1, round(BASE_FIT * float(spec.get("scale", 1.0)) * W))
	k = target / max(img.width, img.height)
	img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)
	rot = float(spec.get("rot", 0.0))
	if rot:
		img = img.rotate(-rot, resample=Image.BICUBIC, expand=True)
	ax, ay = BASE_ANCHOR.get(hand, (0.5, 0.5))
	cx = (ax + float(spec.get("dx", 0.0))) * W
	cy = (ay + float(spec.get("dy", 0.0))) * H
	canvas.alpha_composite(img, (round(cx - img.width / 2), round(cy - img.height / 2)))
	return canvas


_OUTLINE_CACHE = {}


def silhouette_points(img: Image.Image, samples: int = 48) -> list:
	"""Sample the opaque outline as normalised points in the image's own 0..1 box.

	Per column we take the topmost and bottommost opaque pixel, which traces the real
	shape closely enough to clamp against while staying tiny to ship and cheap to
	rotate on every drag frame (vs. testing thousands of pixels).
	"""
	img = img.convert("RGBA")
	bb = img.split()[-1].getbbox()
	if not bb:
		return []
	img = img.crop(bb)
	w, h = img.size
	small = img.resize((min(samples, w), min(samples, h)), Image.NEAREST)
	sw, sh = small.size
	a = small.split()[-1].load()
	pts = []
	for x in range(sw):
		# same visibility threshold the flush snap uses -- tracing the faint rotation
		# fringe instead made the clamp think a flush glove was 2% outside the border
		col = [y for y in range(sh) if a[x, y] >= VISIBLE_ALPHA]
		if not col:
			continue
		for y in (col[0], col[-1]):
			pts.append([round(x / max(1, sw - 1), 4), round(y / max(1, sh - 1), 4)])
	return pts


def outline_for(path: str) -> list:
	if path not in _OUTLINE_CACHE:
		try:
			with open(path, "rb") as f:
				img = drop_flat_background(Image.open(io.BytesIO(f.read())))
			_OUTLINE_CACHE[path] = silhouette_points(img)
		except Exception:  # noqa: BLE001
			_OUTLINE_CACHE[path] = []
	return _OUTLINE_CACHE[path]


def aspect_for(path: str) -> float:
	"""Cropped content aspect (w/h) -- the client needs it to size the outline box."""
	try:
		with open(path, "rb") as f:
			img = drop_flat_background(Image.open(io.BytesIO(f.read())))
		bb = img.split()[-1].getbbox()
		if not bb:
			return 1.0
		return (bb[2] - bb[0]) / max(1, (bb[3] - bb[1]))
	except Exception:  # noqa: BLE001
		return 1.0


def visible_bbox(img: Image.Image, thr: int = VISIBLE_ALPHA):
	"""Bounds of the pixels that actually READ. Rotation feathers a 1-2px fringe of
	alpha 1..8 around a glove; treating that as content snapped the fringe to the
	border and left the visible cuff a couple of pixels inside it."""
	return img.split()[-1].point(lambda v: 255 if v >= thr else 0).getbbox()


def original_size(invfile: str) -> tuple[int, int] | None:
	"""Pixel size of the game's own sprite, so a replacement matches it exactly."""
	try:
		png = assets.dc6_to_png_bytes(assets.read_original_dc6(invfile))
		im = Image.open(io.BytesIO(png))
		return im.size
	except Exception:  # noqa: BLE001
		return None


# Contact shadow: how far the front hand's shadow falls onto the back one, and how dark.
# The original sprites carry a dark interior seam on 58-87% of their rows, which is what
# stops two same-coloured gloves reading as one merged shape.
SHADOW_SPREAD_PX = 2.0      # at 56px; scaled with the canvas
SHADOW_STRENGTH = 0.62      # 1.0 = black at the seam
SHADOW_FEATHER = 1.2


def _contact_shadow(back: Image.Image, front_alpha: Image.Image,
                    canvas_w: int) -> Image.Image:
    """Darken `back` where the FRONT hand looms over it, fading with distance.

    Only the region the front hand actually covers or abuts is affected, so both gloves
    keep their own colour -- the separation comes from the seam, not from dimming a whole
    hand.
    """
    spread = max(1.0, SHADOW_SPREAD_PX * canvas_w / 56.0)
    # grow the front silhouette, then blur: a band that is darkest against the edge
    grown = front_alpha.filter(ImageFilter.MaxFilter(_odd(int(spread * 2) + 1)))
    band = grown.filter(ImageFilter.GaussianBlur(SHADOW_FEATHER * spread))
    # the front hand covers its own pixels anyway; shade only what remains visible
    band = ImageChops.subtract(band, front_alpha)
    r, g, b, a = back.split()
    shade = band.point(lambda v: int(255 - v * SHADOW_STRENGTH))
    return Image.merge("RGBA", (
        ImageChops.multiply(r, shade), ImageChops.multiply(g, shade),
        ImageChops.multiply(b, shade), a))


def _odd(n: int) -> int:
    return n if n % 2 else n + 1


def composite(left_png, right_png, template: dict, invwidth: int, invheight: int,
              mirror_left: bool = False, mirror_right: bool = False,
              size: tuple[int, int] | None = None,
              contact_shadow: bool = True) -> Image.Image:
	"""Build the paired sprite.

	Sized to the ORIGINAL sprite's pixel dimensions when known (invtgl is 56x56, not the
	2*29=58 the cell grid implies) so the tuning ghost overlays the result 1:1 and the
	replacement drops in at the same scale as every other item. Falls back to the cell
	grid for art no original could be read for.

	A missing hand is mirrored from the other so the result is ALWAYS a pair — the game
	never shows a one-handed glove.
	"""
	if size:
		W, H = size
	else:
		W = max(1, invwidth) * assets.CELL_PX
		H = max(1, invheight) * assets.CELL_PX
	front = template.get("front", "right")
	order = ["left", "right"] if front == "right" else ["right", "left"]
	src = {"left": (left_png, mirror_left), "right": (right_png, mirror_right)}

	# Each hand onto its own layer so the front one's silhouette can cast onto the back.
	layers = {}
	for hand in order:
		png, mir = src[hand]
		if png is None:
			continue
		layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
		place_hand(layer, png, template.get(hand) or DEFAULT_TEMPLATE[hand],
		           mirror=mir, hand=hand)
		layers[hand] = layer

	back_hand, front_hand = order[0], order[1]
	if contact_shadow and back_hand in layers and front_hand in layers:
		layers[back_hand] = _contact_shadow(layers[back_hand],
		                                    layers[front_hand].split()[-1], W)

	canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
	for hand in order:  # back first, front last
		if hand in layers:
			canvas.alpha_composite(layers[hand])
	return canvas


# ---- auto-fit -------------------------------------------------------------
#
# A glove's OUTER (pinky) edge is a long straight run -- measured across the real
# library it holds for 57-73% of the glove's height, and it mirrors cleanly between
# hands (invlgl-l2: right edge 73% at +27.2 deg; invlgl-r2: left edge 73% at -27.2).
# So: rotate that edge parallel to the border, grow to fill the height, snap to the
# side. Left hand goes to the RIGHT border, right hand to the LEFT.

STRAIGHT_TOL_PX = 2.0        # rms deviation allowed when calling a run "straight"
EDGE_SAMPLE_ROWS = 200       # working height for edge tracing

# Measured over all 68 single-hand glove files: edge detection alone agrees with the
# filename only 60/68 (88%). Every disagreement is a SYMMETRIC MIRROR PAIR
# (invtgl-l7/r7, lj2/rj2, lj3/rj3, invvgl-l4/r4) at a margin of 0.05-0.08 -- the
# filenames are self-consistent and the detector is what flips, on designs like the
# clawed invtgl whose cuff makes both edges similarly straight. So the filename
# decides, and detection only raises a flag when it disagrees DECISIVELY. Every real
# disagreement measured so far is below 0.10, so 0.15 gives no false alarms while
# still catching genuinely mirrored art.
DECISIVE_MARGIN = 0.15


def _outer_edge_points(img: Image.Image, side: str) -> list:
	"""Outermost opaque pixel per row on `side` ('L' or 'R')."""
	a = img.split()[-1].load()
	w, h = img.size
	pts = []
	for y in range(h):
		rng = range(w) if side == "L" else range(w - 1, -1, -1)
		for x in rng:
			if a[x, y] > 8:
				pts.append((float(x), float(y)))
				break
	return pts


def longest_straight_run(pts: list, tol: float = STRAIGHT_TOL_PX) -> dict:
	"""Longest contiguous stretch of edge points fitting a line within `tol` rms.

	Deliberately a longest-RUN fit rather than two extreme points or a convex hull:
	a cuff flare, a thumb or a claw tip simply isn't part of the longest straight
	stretch, so it cannot drag the angle off.
	"""
	n = len(pts)
	best = {"count": 0, "tilt": 0.0, "rms": 999.0, "frac": 0.0}
	if n < 10:
		return best
	for start in range(0, n - 9):
		for end in range(n - 1, start + 8, -1):
			if (end - start + 1) <= best["count"]:
				break
			seg = pts[start:end + 1]
			mx = sum(q[0] for q in seg) / len(seg)
			my = sum(q[1] for q in seg) / len(seg)
			syy = sum((q[1] - my) ** 2 for q in seg)
			if syy == 0:
				continue
			slope = sum((q[0] - mx) * (q[1] - my) for q in seg) / syy   # dx per dy
			rms = math.sqrt(sum(((q[0] - mx) - slope * (q[1] - my)) ** 2 for q in seg) / len(seg))
			if rms <= tol:
				best = {"count": len(seg), "tilt": math.degrees(math.atan(slope)),
				        "rms": rms, "frac": len(seg) / n}
				break          # longest fitting run from this start; move to the next
			# too curved -- shrink the segment from the far end and retry
	return best


def analyse_hand_art(path: str, hand: str) -> dict:
	"""Measure both outer edges and decide the pinky side.

	The filename decides (left hand -> right edge, right hand -> left edge) and the
	measurement cross-checks it. Disagreement means the art is probably mirrored or
	mislabelled, so it is reported rather than silently fitted backwards.
	"""
	img = drop_flat_background(Image.open(path))
	bb = img.split()[-1].getbbox()
	if bb:
		img = img.crop(bb)
	if img.height > EDGE_SAMPLE_ROWS:
		img = img.resize((max(1, round(img.width * EDGE_SAMPLE_ROWS / img.height)),
		                  EDGE_SAMPLE_ROWS), Image.LANCZOS)
	runs = {side: longest_straight_run(_outer_edge_points(img, side)) for side in ("L", "R")}
	expected = "R" if hand == "left" else "L"        # pinky faces the border it snaps to
	measured = "L" if runs["L"]["frac"] >= runs["R"]["frac"] else "R"
	margin = abs(runs["L"]["frac"] - runs["R"]["frac"])
	return {
		"expected_side": expected,
		"measured_side": measured,
		"agrees": expected == measured,
		"margin": round(margin, 3),
		# only a confident contradiction is worth interrupting for
		"suspect_mirrored": (expected != measured) and margin >= DECISIVE_MARGIN,
		"tilt": runs[expected]["tilt"],
		"run_frac": runs[expected]["frac"],
		"rms": runs[expected]["rms"],
		"runs": {k: {"frac": round(v["frac"], 3), "tilt": round(v["tilt"], 1)}
		         for k, v in runs.items()},
	}


def autofit_hand(path: str, hand: str, out_w: int, out_h: int,
                 fallback_mirror: bool = False) -> dict:
	"""Template entry that stands the pinky edge parallel to its border, fills the
	height and snaps to the side. Returns the entry plus the diagnosis.

	`fallback_mirror` says the compositor will ALREADY mirror this source (because the
	hand has no generation of its own and borrows the other's art). place_hand XORs the
	two, so the stored flip is pre-compensated -- otherwise both hands end up flipped
	the same way and the pair reads as two identical hands instead of a mirrored pair.
	"""
	info = analyse_hand_art(path, hand)
	# Art oriented opposite to its filename is normalised by flipping rather than
	# rejected: flipping is exact (every -r in the library is a pixel-perfect flip of
	# its -l) and yields the true opposite hand.
	flip = not info["agrees"]

	img = drop_flat_background(Image.open(path))
	bb = img.split()[-1].getbbox()
	if bb:
		img = img.crop(bb)
	if flip:
		img = img.transpose(Image.FLIP_LEFT_RIGHT)
		info = dict(info, flipped_to_match_name=True)

	# Re-measure on the normalised art so the angle belongs to the pinky edge as it
	# will actually be placed.
	meas = img.copy()
	if meas.height > EDGE_SAMPLE_ROWS:
		meas = meas.resize((max(1, round(meas.width * EDGE_SAMPLE_ROWS / meas.height)),
		                    EDGE_SAMPLE_ROWS), Image.LANCZOS)
	side = "R" if hand == "left" else "L"
	run = longest_straight_run(_outer_edge_points(meas, side))
	# Stand the pinky edge up. `tilt` is dx-per-dy in degrees: +27 means the edge leans
	# right as it descends, so it needs a CLOCKWISE rotation to become vertical.
	# place_hand applies rotate(-rot), and PIL rotates counter-clockwise, so rot must
	# carry the tilt's own sign.
	rot = run["tilt"]

	# Measure the ACTUAL placed silhouette rather than predicting it. Resizing then
	# rotating does not scale the alpha bbox exactly linearly (resampling + rounding),
	# and predicting it left a 7% overshoot that the build -- which does not run the
	# browser's clamp -- would have silently clipped.
	def placed_bbox(scale):
		"""Size of the ACTUAL rendered silhouette, including the anti-aliased fringe
		rotation adds -- which is why auto-fit can sit flush with no safety margin."""
		target = max(1, round(BASE_FIT * scale * out_w))
		k = target / max(img.width, img.height)
		sim = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))),
		                 Image.LANCZOS)
		if rot:
			sim = sim.rotate(-rot, resample=Image.BICUBIC, expand=True)
		b = visible_bbox(sim)
		return (0, 0) if not b else (b[2] - b[0], b[3] - b[1])

	# Fill the FULL height and sit FLUSH against the side. No safety inset here: the
	# scale and offset are derived from placed_bbox(), a measurement of the real render,
	# so the fringe is already accounted for -- the outermost cuff pixel lands exactly on
	# the border column rather than a pixel inside it.
	target_h_px = float(out_h)

	# Largest size whose rendered height still fits. place_hand rounds the pixel size to
	# an integer, so the achievable heights are DISCRETE -- a multiplicative solver
	# oscillates between two of them and settles low (invtgl filled 54 of 56). Binary
	# search the integer size directly and take the biggest one that fits: exact fill,
	# and never over.
	def height_at(px_size):
		return placed_bbox(px_size / (BASE_FIT * out_w))[1]

	lo, hi = 1, max(4, int(out_w * 4))
	while lo < hi:
		mid = (lo + hi + 1) // 2
		if height_at(mid) <= target_h_px:
			lo = mid
		else:
			hi = mid - 1
	scale = lo / (BASE_FIT * out_w)
	pw, ph = placed_bbox(scale)

	# Snap flush by MEASUREMENT, not arithmetic. place_hand centres the whole rotated
	# IMAGE, and that image's alpha bbox is not perfectly centred inside it (rotation
	# pads asymmetrically), so computing the offset from the silhouette width alone left
	# a 1px gap. Instead: place it, measure where the silhouette actually landed, and
	# correct. Integer rounding in the paste means two passes settle it.
	ax, ay = BASE_ANCHOR[hand]

	def landed(dx, dy):
		canvas = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
		place_hand(canvas, img, {"dx": dx, "dy": dy, "scale": scale, "rot": rot,
		                         "flip": False}, mirror=False, hand=hand)
		return visible_bbox(canvas)

	dx = (1.0 - (pw / 2) / out_w - ax) if hand == "left" else ((pw / 2) / out_w - ax)
	dy = 0.5 - ay
	for _ in range(4):
		b = landed(dx, dy)
		if not b:
			break
		# horizontal: the outer edge should sit ON the border
		err_x = (out_w - b[2]) if hand == "left" else (0 - b[0])
		# vertical: equal space above and below
		err_y = ((out_h - b[3]) - b[1]) / 2.0
		if abs(err_x) < 0.5 and abs(err_y) < 0.5:
			break
		dx += err_x / out_w
		dy += err_y / out_h

	entry = {"dx": round(dx, 4), "dy": round(dy, 4),
	         "scale": round(scale, 4), "rot": round(rot, 2),
	         "flip": bool(flip) != bool(fallback_mirror)}   # XOR-compensated
	info = dict(info, placed_px=[pw, ph], target_h_px=round(target_h_px, 1))
	return {"entry": entry, "info": info}


_AUTOFIT_CACHE = {}


def autofit_template(left_path: str | None, right_path: str | None,
                     out_w: int, out_h: int) -> dict:
	"""Auto-fit both hands. A missing hand mirrors the other, so it is fitted from the
	same art with the opposite snap."""
	key = (left_path, right_path, out_w, out_h)
	if key in _AUTOFIT_CACHE:
		return json.loads(json.dumps(_AUTOFIT_CACHE[key]))
	tpl = json.loads(json.dumps(DEFAULT_TEMPLATE))
	notes = {}
	for hand, path in (("left", left_path), ("right", right_path)):
		src = path or (right_path if hand == "left" else left_path)
		borrowed = path is None and src is not None    # compositor will mirror this one
		if not src or not os.path.exists(src):
			continue
		try:
			r = autofit_hand(src, hand, out_w, out_h, fallback_mirror=borrowed)
		except Exception as e:  # noqa: BLE001
			notes[hand] = {"error": str(e)[:120]}
			continue
		tpl[hand] = r["entry"]
		notes[hand] = r["info"]
	tpl["front"] = "right"
	tpl["pose3d"] = dict(DEFAULT_POSE3D)
	result = {"template": tpl, "notes": notes}
	_AUTOFIT_CACHE[key] = json.loads(json.dumps(result))
	return result


def canvas_to_dc6(canvas: Image.Image) -> bytes:
	"""Encode an already-positioned canvas straight to DC6.

	Deliberately NOT assets.png_to_item_dc6: that crops to the alpha bbox and re-fills
	the cell, which would undo the placement this whole module exists to get right.
	"""
	rows = assets._quantize_to_palette(canvas, assets._palette())
	frame = dc6.Dc6Frame(flip=0, width=canvas.width, height=canvas.height,
	                     offset_x=0, offset_y=0, pixels=rows)
	sprite = dc6.Dc6File(directions=1, frames_per_direction=1,
	                     termination=b"\xee\xee\xee\xee", frames=[frame])
	return dc6.encode(sprite)
