"""Enhance-picker generation recipes: one entry point for every method behind the studio's
Enhance tab (m0-m8, the faithful GAN/upscaler lane, and Flux structure-lock).

Ported from the fidelity-lab investigation (scripts/fidelity_lab.py, scripts/upscale4x_lab.py,
_fidelity_out/) that measured these recipes across 57 items against their
MPQ originals. m7 (identity anchor + category style + margin pad + Oklab colour transfer) is
the best all-round recipe; m3 (identity + colour transfer only, no pad/style) wins on thin
weapons, where the pad + gem/glow style over-loosens a blade's silhouette.

This module owns the RECIPE (prompt/pad/colour-transfer/backend choice); it never touches
Flask, the catalog, or on-disk storage -- those stay in server.py.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageFilter

import app.comfy as comfy
import app.gen_settings as gen_settings
from app.fidelity_score import oklab_to_srgb, score_pair, srgb_to_oklab

# ---- style constants (identical to the proven fidelity-lab recipes) --------

GEM_STYLE = ("hand-painted dark-fantasy RPG inventory icon, vivid saturated colours, glossy "
             "translucent faceted crystal, bright specular highlights, preserve the inner glow "
             "and sparkle, crisp sharp edges")
GLOW_STYLE = ("hand-painted dark-fantasy RPG inventory icon, vivid saturated colours, luminous "
              "magical glow preserved, crisp sharp edges")
KEEP = ", keep the exact shape, silhouette, materials and colours of the original"
# Text is the most common hallucination on these sprites -- the model reads a small dense icon as
# a label/scroll and writes on it. Covered thoroughly (words/letters/numbers/captions), but
# deliberately NOT "symbol", "glyph" or "rune": a rune's carved mark is real art we must keep.
ANTI_HALLUCINATE = ("text, words, letters, lettering, writing, handwriting, script, caption, "
                    "label, title, signature, watermark, numbers, digits, typography, "
                    "inscription, subtitle, treasure chest, box, container, face, creature, "
                    "blurry, low quality, frame, border")

FAITHFUL_MODEL = "4x_foolhardy_Remacri.pth"   # best all-round GAN for the "faithful upscale" lane
MASTER_LONG_SIDE = 1024                        # the ~1024px convention every other method's master follows

# gems are chunky/convex with no thin parts for isnet to tear (birefnet's whole reason for
# existing) -- isnet is 10-70x faster and matched birefnet's cutout quality in testing.
GEM_REMBG_MODEL = "isnet-general-use"

SEED = 7  # fidelity-lab's proven seed; caller may override via run(seed=...)

# Oklab-L standard deviation below which an object counts as "flat" -- no real item art is this
# featureless, so it means the generation collapsed (bare silhouette / solid fill).
FLAT_OBJECT_L_STD = 0.02


# ---- picker-option -> method-id mapping -------------------------------------

METHOD_LABELS = {
	# Fidelity-lab suffix naming (scripts/fidelity_lab.py) -- all 10 exposed directly in the
	# picker, no primary/advanced split.
	"m0": "m0_base",
	"m1": "m1_anchor",
	"m2": "m2_catstyle",
	"m3": "m3_anchor_ct",
	"m5": "m5_pad",
	"m6": "m6_combo",
	"m7": "m7_combo_ct",
	# "faithful_upscale" REMOVED from the picker 2026-07-30. It is a GAN upscale with NO sampler
	# (LoadImage -> UpscaleModelWithModel -> Save, the d2enhance_* outputs), so it can only enlarge
	# the pixels that already exist -- a 29px sprite becomes a 1024px smear. Judged terrible on
	# every item it was shown on, versus every Qwen (d2qwen_*) result being acceptable or better.
	# The implementation stays below for the stack-badge/derive paths that call it directly.
	"sdxl_lock": "Structure-locked (SDXL)",
	"flux_lock": "Structure-locked (Flux)",
}
DEFAULT_METHOD = "m7"
THIN_WEAPON_DEFAULT_METHOD = "m3"


class RecipeError(RuntimeError):
	pass


# ---- pad / colour-transfer helpers (ported from scripts/fidelity_lab.py) ---

def pad_sprite(png: bytes, frac: float = 0.18) -> bytes:
	"""Force a transparent margin around the sprite. comfy.matte_and_size crops to the alpha
	bbox, so the margin is held open with 4 near-invisible (alpha=2) corner px. Fixes cell-
	filling sprites (e.g. Sapphire) that otherwise leave the background-cutter nothing to find."""
	im = Image.open(io.BytesIO(png)).convert("RGBA")
	bb = im.split()[-1].getbbox()
	if bb:
		im = im.crop(bb)
	w, h = im.size
	mw, mh = max(4, int(w * frac)), max(4, int(h * frac))
	canvas = Image.new("RGBA", (w + 2 * mw, h + 2 * mh), (0, 0, 0, 0))
	canvas.alpha_composite(im, (mw, mh))
	px = canvas.load()
	cw, ch = canvas.size
	for x, y in ((0, 0), (cw - 1, 0), (0, ch - 1), (cw - 1, ch - 1)):
		px[x, y] = (128, 128, 128, 2)
	buf = io.BytesIO()
	canvas.save(buf, "PNG")
	return buf.getvalue()


def color_transfer(gen_png: bytes, orig_png: bytes, strength: float = 1.0) -> bytes:
	"""Histogram-match the generated object's Oklab channels to the original's object pixels
	(exact per-channel quantile mapping), preserving alpha and structure. Deterministic, ~free,
	and pins the palette exactly to the source so the model can't drift the hue.

	Two guards keep this from INVENTING structure when the generation failed (2026-07-29):
	ties get one shared (averaged) rank, and an essentially flat object is passed through
	untouched. Without them a uniform fill -- what the model returns when it draws a bare
	silhouette -- was ranked in raster order by the stable argsort, so the original's sorted
	luminance got painted top-to-bottom as a smooth gradient. That made a dead generation look
	deliberately shaded AND pinned its colour metrics to ~1.0, hiding the failure from the gate.
	"""
	gen = Image.open(io.BytesIO(gen_png)).convert("RGBA")
	orig = Image.open(io.BytesIO(orig_png)).convert("RGBA")
	ga = np.asarray(gen).copy()
	oa = np.asarray(orig)
	gm = ga[:, :, 3] > 40
	om = oa[:, :, 3] > 40
	if not gm.any() or not om.any():
		return gen_png
	glab = srgb_to_oklab(ga[gm][:, :3].astype(np.float64) / 255.0)
	olab = srgb_to_oklab(oa[om][:, :3].astype(np.float64) / 255.0)
	# A generation with no tonal range left carries no structure to recolour; matching it would
	# only manufacture one. Leave it as-is and let the QA gate see it for what it is.
	if float(np.std(glab[:, 0])) < FLAT_OBJECT_L_STD:
		return gen_png
	matched = np.empty_like(glab)
	n = len(glab)
	for c in range(3):
		# Average rank over tied values (scipy rankdata 'average' semantics, numpy-only): equal
		# input pixels MUST map to one equal output value, never to a spread ordered by position.
		uniq, inv, counts = np.unique(glab[:, c], return_inverse=True, return_counts=True)
		starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
		ranks = (starts + (counts - 1) / 2.0)[inv] / max(1, n - 1)
		osorted = np.sort(olab[:, c])
		matched[:, c] = np.interp(ranks, np.linspace(0.0, 1.0, len(osorted)), osorted)
	out_lab = glab + (matched - glab) * strength
	rgb = (oklab_to_srgb(out_lab) * 255.0).round().astype(np.uint8)
	ga[gm, 0], ga[gm, 1], ga[gm, 2] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
	buf = io.BytesIO()
	Image.fromarray(ga, "RGBA").save(buf, "PNG")
	return buf.getvalue()


def cat_instruction(identity: str, cat: str) -> str:
	style = {"gem": GEM_STYLE, "glow": GLOW_STYLE}.get(cat)
	if style:
		return f"the item is {identity}. {style}{KEEP}"
	return f"the item is {identity}. " + gen_settings.enhance_instruction()


# At cfg 1.0 (every distilled lane: Lightning/DMD2/schnell) classifier-free guidance reduces to
# the conditional branch, so the NEGATIVE PROMPT IS MATHEMATICALLY IGNORED -- proven 2026-07-30 by
# two runs with opposite negatives returning byte-identical output. Anything we need suppressed
# must therefore be stated in the POSITIVE prompt, which is the only channel the model reads.
# Deliberately says "no writing/letters/numbers", never "no symbols" -- a rune's carved mark is
# real art and must survive.
NO_TEXT = ", plain surface with no writing, no letters and no numbers added"


def _qwen_kw() -> dict:
	"""Sampler settings shared by every Qwen-Edit lane, from the user-editable settings."""
	s = gen_settings.get()
	return {"steps": int(s.get("steps", 10)), "denoise": float(s.get("edit_denoise", 0.45))}


def _with_nudge(instr: str, nudge: str) -> str:
	return f"{nudge}, {instr}" if nudge else instr


def _size_of(png: bytes) -> list[int]:
	return list(Image.open(io.BytesIO(png)).size)


# ---- Qwen-Image-Edit methods (m0-m7) ----------------------------------------

def _m0(sprite_png: bytes, *, nudge: str, negative: str, seed: int, protect_silhouette: bool,
        rembg_model: str | None, **_kw) -> tuple[bytes, dict]:
	instr = _with_nudge(gen_settings.enhance_instruction(), nudge)
	neg = negative or ANTI_HALLUCINATE
	master, _ = comfy.generate_qwen_edit(sprite_png, instruction=instr + NO_TEXT, seed=seed,
	                                     negative=neg, protect_silhouette=protect_silhouette,
	                                     rembg_model=rembg_model, **_qwen_kw())
	return master, {"engine": "qwen", "instruction": instr, "negative": neg, **_qwen_kw()}


def _m1(sprite_png: bytes, *, identity: str, nudge: str, negative: str, seed: int,
        protect_silhouette: bool, rembg_model: str | None, **_kw) -> tuple[bytes, dict]:
	instr = _with_nudge(f"the item is {identity}. " + gen_settings.enhance_instruction(), nudge)
	neg = negative or ANTI_HALLUCINATE
	master, _ = comfy.generate_qwen_edit(sprite_png, instruction=instr + NO_TEXT, seed=seed,
	                                     negative=neg, protect_silhouette=protect_silhouette,
	                                     rembg_model=rembg_model, **_qwen_kw())
	return master, {"engine": "qwen", "instruction": instr, "negative": neg, **_qwen_kw()}


def _m2(sprite_png: bytes, *, identity: str, cat: str, nudge: str, negative: str, seed: int,
        protect_silhouette: bool, rembg_model: str | None, **_kw) -> tuple[bytes, dict]:
	instr = _with_nudge(cat_instruction(identity, cat), nudge)
	neg = negative or ANTI_HALLUCINATE
	master, _ = comfy.generate_qwen_edit(sprite_png, instruction=instr + NO_TEXT, seed=seed,
	                                     negative=neg, protect_silhouette=protect_silhouette,
	                                     rembg_model=rembg_model, **_qwen_kw())
	return master, {"engine": "qwen", "instruction": instr, "negative": neg, **_qwen_kw()}


def _m3(sprite_png: bytes, *, identity: str, nudge: str, seed: int, **kw) -> tuple[bytes, dict]:
	master, meta = _m1(sprite_png, identity=identity, nudge=nudge, seed=seed, **kw)
	master = color_transfer(master, sprite_png)
	meta["color_transfer"] = True
	return master, meta


def _m5(sprite_png: bytes, *, nudge: str, negative: str, seed: int, protect_silhouette: bool,
        rembg_model: str | None, **_kw) -> tuple[bytes, dict]:
	instr = _with_nudge(gen_settings.enhance_instruction(), nudge)
	neg = negative or ANTI_HALLUCINATE
	master, _ = comfy.generate_qwen_edit(pad_sprite(sprite_png), instruction=instr + NO_TEXT,
	                                     seed=seed, negative=neg,
	                                     protect_silhouette=protect_silhouette,
	                                     rembg_model=rembg_model, **_qwen_kw())
	return master, {"engine": "qwen", "instruction": instr, "negative": neg, "pad": 0.18,
	                **_qwen_kw()}


def _m6(sprite_png: bytes, *, identity: str, cat: str, nudge: str, negative: str, seed: int,
        protect_silhouette: bool, rembg_model: str | None, **_kw) -> tuple[bytes, dict]:
	instr = _with_nudge(cat_instruction(identity, cat), nudge)
	neg = negative or ANTI_HALLUCINATE
	master, _ = comfy.generate_qwen_edit(pad_sprite(sprite_png), instruction=instr + NO_TEXT,
	                                     seed=seed, negative=neg,
	                                     protect_silhouette=protect_silhouette,
	                                     rembg_model=rembg_model, **_qwen_kw())
	return master, {"engine": "qwen", "instruction": instr, "negative": neg, "pad": 0.18,
	                **_qwen_kw()}


def _m7(sprite_png: bytes, *, identity: str, cat: str, nudge: str, negative: str, seed: int,
        **kw) -> tuple[bytes, dict]:
	master, meta = _m6(sprite_png, identity=identity, cat=cat, nudge=nudge, negative=negative,
	                   seed=seed, **kw)
	master = color_transfer(master, sprite_png)
	meta["color_transfer"] = True
	return master, meta


# ---- SDXL structure-locked (m8) ---------------------------------------------

def _sdxl_lock(sprite_png: bytes, *, identity: str, cat: str, restyle: str, negative: str,
               seed: int, **_kw) -> tuple[bytes, dict]:
	pos = cat_instruction(identity, cat)
	if restyle:
		pos = f"{restyle}, {pos}"
	neg = negative or ANTI_HALLUCINATE
	master, _ = comfy.upscale_faithful(pad_sprite(sprite_png), positive=pos, negative=neg,
	                                   seed=seed, denoise=0.30, cn_strength=0.85)
	return master, {"engine": "sdxl-tile", "positive": pos, "negative": neg,
	                "denoise": 0.30, "cn_strength": 0.85, "pad": 0.18}


# ---- Flux structure-locked (existing backend, new dispatch entry only) -----

def _flux_lock(sprite_png: bytes, *, restyle: str, shape_strength: float, seed: int,
               **_kw) -> tuple[bytes, dict]:
	styled = f"{restyle}, {comfy.QWEN_STYLE}"
	master, _ = comfy.generate_flux_locked(sprite_png, description=styled, seed=seed,
	                                       shape_strength=shape_strength)
	return master, {"engine": "flux-lock", "description": styled, "shape_strength": shape_strength}


# ---- Faithful upscale: GAN/hybrid lane (ported from scripts/upscale4x_lab.py) ----
# Re-cuts the ORIGINAL silhouette back on rather than re-imagining -- "make the current art
# crisper," never a restyle. Consulted params: sprite_png only.

def _epx2x(a: np.ndarray) -> np.ndarray:
	"""One EPX/Scale2x pass on an (H,W,C) uint8 array."""
	h, w, c = a.shape
	up = np.vstack([a[:1], a[:-1]])
	dn = np.vstack([a[1:], a[-1:]])
	lf = np.hstack([a[:, :1], a[:, :-1]])
	rt = np.hstack([a[:, 1:], a[:, -1:]])

	def eq(x, y):
		return (x == y).all(axis=-1)

	out = np.empty((h * 2, w * 2, c), dtype=a.dtype)
	e0 = np.where((eq(up, lf) & ~eq(up, rt) & ~eq(lf, dn))[..., None], lf, a)
	e1 = np.where((eq(up, rt) & ~eq(up, lf) & ~eq(rt, dn))[..., None], rt, a)
	e2 = np.where((eq(dn, lf) & ~eq(dn, rt) & ~eq(lf, up))[..., None], lf, a)
	e3 = np.where((eq(dn, rt) & ~eq(dn, lf) & ~eq(rt, up))[..., None], rt, a)
	out[0::2, 0::2] = e0
	out[0::2, 1::2] = e1
	out[1::2, 0::2] = e2
	out[1::2, 1::2] = e3
	return out


def _scale4x_rgba(im: Image.Image) -> Image.Image:
	a = np.asarray(im.convert("RGBA"))
	return Image.fromarray(_epx2x(_epx2x(a)), "RGBA")


def _premult_resize(im: Image.Image, size: tuple[int, int], resample) -> Image.Image:
	"""Resize with premultiplied alpha so transparent-pixel RGB garbage never bleeds in."""
	arr = np.asarray(im.convert("RGBA")).astype(np.float64)
	al = arr[..., 3:4] / 255.0
	pm = np.concatenate([arr[..., :3] * al, arr[..., 3:4]], axis=-1)
	pim = Image.fromarray(pm.round().astype(np.uint8), "RGBA").resize(size, resample)
	out = np.asarray(pim).astype(np.float64)
	al2 = np.maximum(out[..., 3:4], 1e-6)
	rgb = np.clip(out[..., :3] / (al2 / 255.0), 0, 255)
	res = np.concatenate([rgb, out[..., 3:4]], axis=-1).round().astype(np.uint8)
	return Image.fromarray(res, "RGBA")


def _alpha4x(im: Image.Image) -> Image.Image:
	"""Crisp 4x of the (binary) sprite alpha: scale4x for shape-aware corners + tiny blur."""
	a = np.asarray(im.convert("RGBA"))[..., 3:]
	big = _epx2x(_epx2x(a))
	return Image.fromarray(big[..., 0], "L").filter(ImageFilter.GaussianBlur(0.5))


def _matte_gray(im: Image.Image) -> Image.Image:
	bg = Image.new("RGBA", im.size, (*comfy.GRAY, 255))
	bg.alpha_composite(im.convert("RGBA"))
	return bg.convert("RGB")


def _recut(rgb_im: Image.Image, orig: Image.Image) -> Image.Image:
	"""Put the crisp 4x alpha onto a generated 4x RGB image + defringe the gray matte."""
	out = rgb_im.convert("RGBA")
	out.putalpha(_alpha4x(orig).resize(out.size, Image.LANCZOS))
	return comfy._defringe(out)


def _gan_graph(image_name: str, model: str) -> dict:
	return {
		"img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
		"upmodel": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
		"gan": {"class_type": "ImageUpscaleWithModel", "inputs": {
			"upscale_model": ["upmodel", 0], "image": ["img", 0]}},
		"save": {"class_type": "SaveImage", "inputs": {"images": ["gan", 0],
		                                               "filename_prefix": "d2enhance"}},
	}


def _hybrid_scale4x_gan(orig: Image.Image, model: str = FAITHFUL_MODEL) -> Image.Image:
	"""Pixel-art 4x first (crisp geometry), then the GAN re-paints texture at scale --
	the best all-round faithful-enhance recipe from the upscale4x investigation."""
	pre = _scale4x_rgba(orig)
	buf = io.BytesIO()
	_matte_gray(pre).save(buf, "PNG")
	name = comfy.upload_image(buf.getvalue())
	png = comfy.run(_gan_graph(name, model), timeout=600)
	big = Image.open(io.BytesIO(png)).convert("RGB")
	target = (orig.width * 4, orig.height * 4)
	if big.size != target:
		big = big.resize(target, Image.LANCZOS)
	return _recut(big, orig)


def _faithful_upscale(sprite_png: bytes, **_kw) -> tuple[bytes, dict]:
	orig = Image.open(io.BytesIO(sprite_png)).convert("RGBA")
	up = _hybrid_scale4x_gan(orig)
	# normalize onto the ~1024px master convention every other method's output follows, so
	# this is a drop-in master for upscale_store/to_canonical_2x/Meshy like any other result.
	scale = MASTER_LONG_SIDE / max(up.size)
	if scale > 1.0:
		up = _premult_resize(up, (round(up.width * scale), round(up.height * scale)), Image.LANCZOS)
	buf = io.BytesIO()
	up.save(buf, "PNG")
	return buf.getvalue(), {"engine": "gan-upscale", "model": FAITHFUL_MODEL}


# ---- dispatch ---------------------------------------------------------------

_DISPATCH = {
	"m0": _m0, "m1": _m1, "m2": _m2, "m3": _m3, "m5": _m5, "m6": _m6, "m7": _m7,
	"sdxl_lock": _sdxl_lock, "flux_lock": _flux_lock, "faithful_upscale": _faithful_upscale,
}


def run(method: str, *, sprite_png: bytes, identity: str = "", cat: str = "control",
        nudge: str = "", restyle: str = "", negative: str = "", seed: int = 0,
        shape_strength: float = 0.65, protect_silhouette: bool = False) -> tuple[bytes, dict]:
	"""Generate one enhanced master PNG using picker `method`. `sprite_png` doubles as both the
	edit source and the pad/colour-transfer reference (they're always the same bytes in
	production -- the item's current original/active art).

	`protect_silhouette` (off by default): union the original sprite's silhouette back onto the
	rembg cutout so nothing interior can be deleted -- needed for a thin weapon blade the cutter
	chops in half. Off by default: for most items (esp. full-cell shapes like gems) the original
	silhouette doesn't line up tightly with the generated art, and the floor union re-introduces a
	halo/shadow around the object instead of protecting it. Pass True per-call for an item that
	demonstrably needs it.

	Returns (master_png, meta); meta always carries size=[w,h], method, method_label, engine,
	plus whatever method-specific fields (instruction/positive/negative/...) apply.

	Raises ValueError for an unknown method; RecipeError for a recipe-level validation failure
	(e.g. flux_lock/sdxl_lock called with no restyle text); propagates comfy.* errors as-is on
	backend failure."""
	fn = _DISPATCH.get(method)
	if fn is None:
		raise ValueError(f"unknown method {method!r}")
	if method in ("sdxl_lock", "flux_lock") and not restyle.strip():
		raise RecipeError("describe the new look first")
	seed = seed or SEED
	rembg_model = GEM_REMBG_MODEL if cat == "gem" else None
	master, meta = fn(sprite_png, identity=identity, cat=cat, nudge=nudge, restyle=restyle,
	                  negative=negative, seed=seed, shape_strength=shape_strength,
	                  protect_silhouette=protect_silhouette, rembg_model=rembg_model)
	meta["method"] = method
	meta["method_label"] = METHOD_LABELS.get(method, method)
	meta["protect_silhouette"] = protect_silhouette
	if rembg_model:
		meta["rembg_model"] = rembg_model
	meta.setdefault("seed", seed)
	meta.setdefault("size", _size_of(master))
	return master, meta


def score(orig_png: bytes, alt_png: bytes) -> dict:
	"""Fidelity score of `alt_png` against `orig_png` -- the same composite metric the
	fidelity-lab investigation used (0.35*iou + 0.35*colour + 0.30*ssim)."""
	orig = Image.open(io.BytesIO(orig_png)).convert("RGBA")
	alt = Image.open(io.BytesIO(alt_png)).convert("RGBA")
	return score_pair(orig, alt)
