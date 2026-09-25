"""4x upscale lab: which upscaler best reconstructs 'what the original hi-res gem art
would have been'?

Runs a matrix of 4x upscaling methods over the 7 Perfect-tier gem sprites and saves
every result at exactly 4x the original DC6 frame size, plus a round-trip fidelity
score (downscale the 4x result back to 1x and compare to the MPQ original with the
fidelity_audit metrics). Detail quality is judged visually in the report; the
round-trip score catches methods that drift off the original.

Method families:
  local        nearest / bicubic / lanczos (premultiplied) / scale4x (EPX x2) / hq4x
  gan:<model>  single ImageUpscaleWithModel pass on the ComfyUI box (native-res
               gray-matte input, alpha re-cut from the original binary alpha)
  diffusion    qwen_enhance / sdxl_tile / seedvr2 -- run with --phase diffusion when
               the box is idle (they load multi-GB checkpoints; grouped by family so
               resident models are never thrashed while other work runs)

GPU etiquette: never calls /free; jobs queue behind whatever else is running.

    python scripts/upscale4x_lab.py                      # local + GAN methods
    python scripts/upscale4x_lab.py --phase diffusion    # heavy lanes, box idle only
    python scripts/upscale4x_lab.py --methods lanczos,gan:4x-UltraSharp.pth
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import types

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))
import assets  # noqa: E402
import comfy  # noqa: E402
from fidelity_audit import score_pair  # noqa: E402

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_fidelity_out", "up4x"))
SEED = 7
SCALE = 4

# Perfect-tier gems: most detailed art of each family + the organic-shaped skull.
GEMS = [
	{"inv": "invgsre", "name": "Perfect Ruby",
	 "identity": "a blood-red ruby gemstone, brilliant multi-faceted cut, sparkling facets"},
	{"inv": "invgsbe", "name": "Perfect Sapphire",
	 "identity": "a deep blue sapphire gemstone, brilliant multi-faceted cut, sparkling facets"},
	{"inv": "invgsge", "name": "Perfect Emerald",
	 "identity": "a bright green emerald gemstone, brilliant multi-faceted cut crystal"},
	{"inv": "invgsve", "name": "Perfect Amethyst",
	 "identity": "a purple amethyst gemstone, brilliant multi-faceted cut crystal"},
	{"inv": "invgsye", "name": "Perfect Topaz",
	 "identity": "a golden yellow topaz gemstone, brilliant multi-faceted cut, sparkling facets"},
	{"inv": "invgswe", "name": "Perfect Diamond",
	 "identity": "a clear white diamond gemstone, brilliant multi-faceted cut, sparkling facets"},
	{"inv": "invskz", "name": "Perfect Skull",
	 "identity": "a small ivory human skull with dark hollow eye sockets"},
]

GEM_STYLE = ("hand-painted dark-fantasy RPG inventory icon, vivid saturated colours, glossy "
             "translucent faceted crystal, bright specular highlights, preserve the inner glow "
             "and sparkle, crisp sharp edges, keep the exact shape, silhouette and colours of "
             "the original")
ANTI_HALLUCINATE = ("treasure chest, box, container, letter, text, logo, face, "
                    "creature, blurry, low quality, watermark, frame, border")

GAN_MODELS = ["4x-UltraSharp.pth", "4x_foolhardy_Remacri.pth", "4x-AnimeSharp.pth",
              "4x_NMKD-Siax_200k.pth", "RealESRGAN_x4plus_anime_6B.pth"]

SEEDVR2_MODEL = "seedvr2_3b_fp16.safetensors"
SEEDVR2_VAE = "seedvr2_ema_vae_fp16.safetensors"


# ---- alpha helpers ---------------------------------------------------------

def _epx2x(a: np.ndarray) -> np.ndarray:
	"""One EPX/Scale2x pass on an (H,W,C) uint8 array (C channels compared as tuples)."""
	h, w, c = a.shape
	# neighbours with edge clamping
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


def scale4x_rgba(im: Image.Image) -> Image.Image:
	a = np.asarray(im.convert("RGBA"))
	return Image.fromarray(_epx2x(_epx2x(a)), "RGBA")


def premult_resize(im: Image.Image, size, resample) -> Image.Image:
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


def alpha4x(im: Image.Image) -> Image.Image:
	"""Crisp 4x of the (binary) sprite alpha: scale4x for shape-aware corners + tiny blur."""
	a = np.asarray(im.convert("RGBA"))[..., 3:]
	big = _epx2x(_epx2x(a))
	return Image.fromarray(big[..., 0], "L").filter(ImageFilter.GaussianBlur(0.5))


def matte_gray(im: Image.Image) -> Image.Image:
	bg = Image.new("RGBA", im.size, (*comfy.GRAY, 255))
	bg.alpha_composite(im.convert("RGBA"))
	return bg.convert("RGB")


def recut(rgb_im: Image.Image, orig: Image.Image) -> Image.Image:
	"""Put the crisp 4x alpha onto a generated 4x RGB image + defringe the gray matte."""
	out = rgb_im.convert("RGBA")
	out.putalpha(alpha4x(orig).resize(out.size, Image.LANCZOS))
	return comfy._defringe(out)


def fit_master_to_4x(master_png: bytes, orig: Image.Image) -> Image.Image:
	"""Fit a ~1024 diffusion master back onto the 4x frame canvas at the original's
	content position, so diffusion results are geometrically comparable."""
	m = Image.open(io.BytesIO(master_png)).convert("RGBA")
	bb_m = m.split()[-1].getbbox()
	if bb_m:
		m = m.crop(bb_m)
	tw, th = orig.width * SCALE, orig.height * SCALE
	bb_o = orig.split()[-1].getbbox() or (0, 0, orig.width, orig.height)
	cw, ch = (bb_o[2] - bb_o[0]) * SCALE, (bb_o[3] - bb_o[1]) * SCALE
	m = premult_resize(m, (max(1, cw), max(1, ch)), Image.LANCZOS)
	canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
	canvas.alpha_composite(m, (bb_o[0] * SCALE, bb_o[1] * SCALE))
	return canvas


# ---- local methods ---------------------------------------------------------

def m_nearest(orig):
	return orig.resize((orig.width * SCALE, orig.height * SCALE), Image.NEAREST)


def m_bicubic(orig):
	return premult_resize(orig, (orig.width * SCALE, orig.height * SCALE), Image.BICUBIC)


def m_lanczos(orig):
	return premult_resize(orig, (orig.width * SCALE, orig.height * SCALE), Image.LANCZOS)


def m_scale4x(orig):
	return scale4x_rgba(orig)


def m_hq4x(orig):
	stub = types.ModuleType("PIL.PyAccess")
	stub.PyAccess = object
	sys.modules.setdefault("PIL.PyAccess", stub)
	import hqx
	big = hqx.hq4x(matte_gray(orig))
	return recut(big, orig)


LOCAL_METHODS = {"nearest": m_nearest, "bicubic": m_bicubic, "lanczos": m_lanczos,
                 "scale4x": m_scale4x, "hq4x": m_hq4x}


# ---- GAN methods (ComfyUI, tiny jobs, queue-polite: no /free ever) ---------

def gan_graph(image_name: str, model: str) -> dict:
	return {
		"img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
		"upmodel": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
		"gan": {"class_type": "ImageUpscaleWithModel", "inputs": {
			"upscale_model": ["upmodel", 0], "image": ["img", 0]}},
		"save": {"class_type": "SaveImage", "inputs": {"images": ["gan", 0],
		                                               "filename_prefix": "d2lab4x"}},
	}


def run_gan(orig: Image.Image, model: str) -> Image.Image:
	buf = io.BytesIO()
	matte_gray(orig).save(buf, "PNG")
	name = comfy.upload_image(buf.getvalue())
	png = comfy.run(gan_graph(name, model), timeout=600)
	big = Image.open(io.BytesIO(png)).convert("RGB")
	if big.size != (orig.width * SCALE, orig.height * SCALE):  # non-4x model scale
		big = big.resize((orig.width * SCALE, orig.height * SCALE), Image.LANCZOS)
	return recut(big, orig)


def m_hybrid_scale4x_gan(orig, model="4x_foolhardy_Remacri.pth"):
	"""Community recipe for tiny sprites: pixel-art 4x first (crisp geometry), then the
	GAN re-paints texture at scale, downsampled back to 4x."""
	pre = scale4x_rgba(orig)
	buf = io.BytesIO()
	matte_gray(pre).save(buf, "PNG")
	name = comfy.upload_image(buf.getvalue())
	png = comfy.run(gan_graph(name, model), timeout=600)
	big = Image.open(io.BytesIO(png)).convert("RGB")
	big = big.resize((orig.width * SCALE, orig.height * SCALE), Image.LANCZOS)
	return recut(big, orig)


# ---- diffusion methods (heavy; --phase diffusion only) ---------------------

def m_qwen_enhance(orig: Image.Image, it: dict) -> Image.Image:
	instr = f"the item is {it['identity']}. " + GEM_STYLE
	buf = io.BytesIO()
	orig.save(buf, "PNG")
	# NOTE: comfy.generate_qwen_edit calls free_memory() -- rebuilt here without it so
	# other work's resident models are never evicted by us.
	sprite = buf.getvalue()
	_rgb1k, alpha_png, (w, h) = comfy.matte_and_size(sprite, 1024)
	rgb_png, _a, _sz = comfy.matte_and_size(sprite, comfy._native_long_side(sprite))
	name = comfy.upload_image(rgb_png)
	graph = comfy.build_qwen_edit_graph(image_name=name, prompt=instr, seed=SEED, px=1024,
	                                    gan=True, steps=4, negative=ANTI_HALLUCINATE)
	gen = comfy.run(graph, timeout=900)
	master = comfy.recut_alpha_rembg(gen, guide_alpha_png=alpha_png)
	return fit_master_to_4x(master, orig)


def m_sdxl_tile(orig: Image.Image, it: dict) -> Image.Image:
	pos = f"the item is {it['identity']}. " + GEM_STYLE
	buf = io.BytesIO()
	orig.save(buf, "PNG")
	master, _sz = comfy.upscale_faithful(buf.getvalue(), positive=pos,
	                                     negative=ANTI_HALLUCINATE, seed=SEED,
	                                     denoise=0.30, cn_strength=0.85)
	return fit_master_to_4x(master, orig)


def seedvr2_graph(image_name: str, width: int, height: int, seed: int) -> dict:
	"""Core-ComfyUI SeedVR2: resize -> Preprocess -> VAEEncode -> Conditioning ->
	KSampler(1 step, cfg 1) -> VAEDecode -> PostProcess(lab)."""
	return {
		"img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
		"resize": {"class_type": "ImageScale", "inputs": {
			"image": ["img", 0], "upscale_method": "lanczos",
			"width": width, "height": height, "crop": "disabled"}},
		"model": {"class_type": "UNETLoader", "inputs": {
			"unet_name": SEEDVR2_MODEL, "weight_dtype": "default"}},
		"vae": {"class_type": "VAELoader", "inputs": {"vae_name": SEEDVR2_VAE}},
		"pre": {"class_type": "SeedVR2Preprocess", "inputs": {"resized_images": ["resize", 0]}},
		"enc": {"class_type": "VAEEncode", "inputs": {"pixels": ["pre", 0], "vae": ["vae", 0]}},
		"cond": {"class_type": "SeedVR2Conditioning", "inputs": {
			"model": ["model", 0], "vae_conditioning": ["enc", 0]}},
		"ks": {"class_type": "KSampler", "inputs": {
			"model": ["model", 0], "positive": ["cond", 0], "negative": ["cond", 1],
			"latent_image": ["enc", 0], "seed": seed, "steps": 1, "cfg": 1.0,
			"sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
		"dec": {"class_type": "VAEDecode", "inputs": {"samples": ["ks", 0], "vae": ["vae", 0]}},
		"post": {"class_type": "SeedVR2PostProcessing", "inputs": {
			"images": ["dec", 0], "original_resized_images": ["resize", 0],
			"color_correction_method": "lab"}},
		"save": {"class_type": "SaveImage", "inputs": {"images": ["post", 0],
		                                               "filename_prefix": "d2seedvr"}},
	}


def m_seedvr2(orig: Image.Image, it: dict) -> Image.Image:
	buf = io.BytesIO()
	matte_gray(orig).save(buf, "PNG")
	name = comfy.upload_image(buf.getvalue())
	w, h = orig.width * SCALE, orig.height * SCALE
	png = comfy.run(seedvr2_graph(name, w, h, SEED), timeout=900)
	big = Image.open(io.BytesIO(png)).convert("RGB")
	if big.size != (w, h):
		big = big.resize((w, h), Image.LANCZOS)
	return recut(big, orig)


# ---- scoring / manifest ----------------------------------------------------

def roundtrip_score(up: Image.Image, orig: Image.Image) -> dict:
	down = premult_resize(up, orig.size, Image.LANCZOS)
	return score_pair(orig, down)


def load_manifest():
	p = os.path.join(OUT, "manifest.json")
	if os.path.exists(p):
		with open(p, encoding="utf-8") as f:
			return json.load(f)
	return {"rows": []}


def save_manifest(m):
	os.makedirs(OUT, exist_ok=True)
	with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
		json.dump(m, f, indent=1)


def method_list(phase: str) -> list[str]:
	if phase == "diffusion":
		return ["qwen_enhance", "sdxl_tile", "seedvr2"]
	return (list(LOCAL_METHODS)
	        + [f"gan:{m}" for m in GAN_MODELS]
	        + ["hybrid_scale4x_remacri"])


def run_method(name: str, orig: Image.Image, it: dict) -> Image.Image:
	if name in LOCAL_METHODS:
		return LOCAL_METHODS[name](orig)
	if name.startswith("gan:"):
		return run_gan(orig, name.split(":", 1)[1])
	if name == "hybrid_scale4x_remacri":
		return m_hybrid_scale4x_gan(orig)
	if name == "qwen_enhance":
		return m_qwen_enhance(orig, it)
	if name == "sdxl_tile":
		return m_sdxl_tile(orig, it)
	if name == "seedvr2":
		return m_seedvr2(orig, it)
	raise ValueError(name)


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--phase", default="fast", choices=["fast", "diffusion"])
	ap.add_argument("--items", default="")
	ap.add_argument("--methods", default="")
	ap.add_argument("--force", action="store_true")
	args = ap.parse_args()
	sel_items = set(filter(None, args.items.split(",")))
	methods = list(filter(None, args.methods.split(","))) or method_list(args.phase)

	manifest = load_manifest()
	done = {(r["inv"], r["method"]) for r in manifest["rows"] if "error" not in r}

	for it in GEMS:
		inv = it["inv"]
		if sel_items and inv not in sel_items:
			continue
		orig_png = assets.dc6_to_png_bytes(assets.read_original_dc6(inv))
		orig = Image.open(io.BytesIO(orig_png)).convert("RGBA")
		d = os.path.join(OUT, inv)
		os.makedirs(d, exist_ok=True)
		with open(os.path.join(d, "original.png"), "wb") as f:
			f.write(orig_png)
		for name in methods:
			if (inv, name) in done and not args.force:
				continue
			safe = name.replace(":", "_").replace(".pth", "")
			t0 = time.time()
			try:
				up = run_method(name, orig, it)
			except Exception as e:  # noqa: BLE001
				print(f"  {inv} {name}: FAILED {e}", flush=True)
				manifest["rows"] = [r for r in manifest["rows"]
				                    if not (r["inv"] == inv and r["method"] == name)]
				manifest["rows"].append({"inv": inv, "method": name, "error": str(e)})
				save_manifest(manifest)
				continue
			secs = round(time.time() - t0, 2)
			up.save(os.path.join(d, f"{safe}.png"))
			scores = roundtrip_score(up, orig)
			manifest["rows"] = [r for r in manifest["rows"]
			                    if not (r["inv"] == inv and r["method"] == name)]
			manifest["rows"].append({"inv": inv, "name": it["name"], "method": name,
			                         "file": f"{safe}.png", "secs": secs, **scores})
			save_manifest(manifest)
			print(f"  {inv} {name}: rt={scores['score']:.3f} ({secs}s)", flush=True)

	rows = [r for r in manifest["rows"] if "score" in r]
	print("\nMean round-trip fidelity per method:")
	for m in sorted({r["method"] for r in rows}):
		sub = [r["score"] for r in rows if r["method"] == m]
		print(f"  {m:<28} {sum(sub) / len(sub):.3f} (n={len(sub)})")


if __name__ == "__main__":
	main()
