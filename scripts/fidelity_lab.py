"""Fidelity lab: method experiments for visually-faithful alternate art.

Runs a matrix of generation methods over a fixed 12-item test set (gems + a size
spread) and scores each END-TO-END: master render -> fit to cell -> palette
quantize -> DC6 -> render, compared against the MPQ original with the same
metrics as fidelity_audit (IoU / Oklab EMD / SSIM / composite).

Methods (round 1):
  m0_base         current production behaviour: Qwen enhance, house style, no prompt
  m1_anchor       + identity anchor ("the item is <physical description>")
  m2_catstyle     identity + category-aware style (gems: vivid/glossy/glow, not
                  the house 'muted palette, no glow'), anti-hallucination negative
  m3_anchor_ct    m1 master + Oklab quantile color-transfer from the original
  m4_catstyle_ct  m2 master + color transfer
  m5_pad          m0 but with a transparent margin forced around the sprite so
                  full-cell objects (Sapphire) keep an object-on-background
                  composition and rembg has an edge to find
  m6_combo        pad + identity + category style (generation-side combo)
  m7_combo_ct     m6 master + color transfer  (the expected production recipe)

Results: _fidelity_out/lab/manifest.json + per-item PNGs (master + fitted).
Resumable: existing (item, method) rows are skipped unless --force.

    python scripts/fidelity_lab.py                     # full round
    python scripts/fidelity_lab.py --items invgsrc,invtes --methods m2_catstyle
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import assets  # noqa: E402
import catalog  # noqa: E402
import comfy  # noqa: E402
from fidelity_audit import score_pair, srgb_to_oklab  # noqa: E402

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_fidelity_out"))
LAB = os.path.join(OUT, "lab")
SEED = 7

# ---- test set --------------------------------------------------------------
# identity: literal physical description of the ORIGINAL sprite (never the D2 name).
# cat: gem (vivid crystal), glow (luminous magical), control (house style is fine).
# The hand-reviewed 12-item seed below is merged with _fidelity_out/testset50.json
# (auto-captioned + reviewed); JSON rows without an identity are skipped.
LEGACY_SET = [
	{"inv": "invgsbc", "identity": "a deep blue sapphire gemstone, rectangular step-cut, sparkling facets", "cat": "gem"},
	{"inv": "invgsrc", "identity": "a blood-red ruby gemstone, rough angular cut, sparkling facets", "cat": "gem"},
	{"inv": "invgsvc", "identity": "a wide chunky purple amethyst crystal cluster with bright white sparkling highlights", "cat": "gem"},
	{"inv": "invgsgc", "identity": "a bright green emerald gemstone, jagged angular cut crystal", "cat": "gem"},
	{"inv": "invjw2", "identity": "a bright blue faceted crystal gem set in an ornate gold mount", "cat": "gem"},
	{"inv": "invtes", "identity": "a small pile of glowing blue crystalline dust with blue sparkles floating above it", "cat": "glow"},
	{"inv": "invrEl", "identity": "a gray carved stone rune tablet with an engraved sigil", "cat": "control"},
	{"inv": "invrin1", "identity": "a silver ring with a dark round gemstone on a chunky beaded band", "cat": "control"},
	{"inv": "invhp5", "identity": "a ribbed glass potion bottle of red liquid with a brass cap", "cat": "control"},
	{"inv": "invdgr", "identity": "a steel dagger with a straight blade, gold crossguard and dark grip", "cat": "control"},
	{"inv": "invcap", "identity": "a plain olive-brown leather cap hood with drooping ear flaps", "cat": "control"},
	{"inv": "invqlt", "identity": "a brown quilted padded cloth armor tunic with diamond stitching", "cat": "control"},
]

TESTSET_JSON = os.path.join(OUT, "testset50.json")


def load_test_set() -> list[dict]:
	merged = {t["inv"]: t for t in LEGACY_SET}
	if os.path.exists(TESTSET_JSON):
		with open(TESTSET_JSON, encoding="utf-8") as f:
			for r in json.load(f):
				if (r.get("identity") or "").strip():
					merged[r["inv"]] = {"inv": r["inv"], "identity": r["identity"].strip(),
					                    "cat": r.get("cat", "control")}
	return list(merged.values())


TEST_SET = load_test_set()

GEM_STYLE = ("hand-painted dark-fantasy RPG inventory icon, vivid saturated colours, glossy "
             "translucent faceted crystal, bright specular highlights, preserve the inner glow "
             "and sparkle, crisp sharp edges")
GLOW_STYLE = ("hand-painted dark-fantasy RPG inventory icon, vivid saturated colours, luminous "
              "magical glow preserved, crisp sharp edges")
KEEP = ", keep the exact shape, silhouette, materials and colours of the original"
ANTI_HALLUCINATE = ("treasure chest, box, container, letter, text, logo, face, "
                    "creature, blurry, low quality, watermark, frame, border")


# ---- helpers ---------------------------------------------------------------

def oklab_to_srgb(lab: np.ndarray) -> np.ndarray:
	l_ = lab[:, 0] + 0.3963377774 * lab[:, 1] + 0.2158037573 * lab[:, 2]
	m_ = lab[:, 0] - 0.1055613458 * lab[:, 1] - 0.0638541728 * lab[:, 2]
	s_ = lab[:, 0] - 0.0894841775 * lab[:, 1] - 1.2914855480 * lab[:, 2]
	l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
	rgb = np.stack([
		+4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
		-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
		-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
	], axis=1)
	rgb = np.clip(rgb, 0.0, 1.0)
	return np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055)


def color_transfer(gen_png: bytes, orig_png: bytes, strength: float = 1.0) -> bytes:
	"""Histogram-match the generated object's Oklab channels to the original's object
	pixels (exact per-channel quantile mapping), preserving alpha and structure."""
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
	matched = np.empty_like(glab)
	n = len(glab)
	for c in range(3):
		order = np.argsort(glab[:, c], kind="stable")
		ranks = np.empty(n, dtype=np.float64)
		ranks[order] = np.arange(n, dtype=np.float64) / max(1, n - 1)
		osorted = np.sort(olab[:, c])
		matched[:, c] = np.interp(ranks, np.linspace(0.0, 1.0, len(osorted)), osorted)
	out_lab = glab + (matched - glab) * strength
	rgb = (oklab_to_srgb(out_lab) * 255.0).round().astype(np.uint8)
	ga[gm, 0], ga[gm, 1], ga[gm, 2] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
	buf = io.BytesIO()
	Image.fromarray(ga, "RGBA").save(buf, "PNG")
	return buf.getvalue()


def pad_sprite(png: bytes, frac: float = 0.18) -> bytes:
	"""Force a transparent margin around the sprite. comfy.matte_and_size crops to the
	alpha bbox, so the margin is held open with 4 near-invisible (alpha=2) corner px."""
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


def cat_instruction(it: dict) -> str:
	style = {"gem": GEM_STYLE, "glow": GLOW_STYLE}.get(it["cat"])
	if style:
		return f"the item is {it['identity']}. {style}{KEEP}"
	return f"the item is {it['identity']}. " + comfy.qwen_enhance_instruction()


# ---- methods ---------------------------------------------------------------
# gen methods: fn(item, orig_png) -> (master_png, meta). ct methods: (parent, strength).

def m0_base(it, orig):
	instr = comfy.qwen_enhance_instruction()
	m, _ = comfy.generate_qwen_edit(orig, instruction=instr, seed=SEED)
	return m, {"instruction": instr}


def m1_anchor(it, orig):
	instr = f"the item is {it['identity']}. " + comfy.qwen_enhance_instruction()
	m, _ = comfy.generate_qwen_edit(orig, instruction=instr, seed=SEED)
	return m, {"instruction": instr}


def m2_catstyle(it, orig):
	instr = cat_instruction(it)
	m, _ = comfy.generate_qwen_edit(orig, instruction=instr, seed=SEED, negative=ANTI_HALLUCINATE)
	return m, {"instruction": instr, "negative": ANTI_HALLUCINATE}


def m5_pad(it, orig):
	instr = comfy.qwen_enhance_instruction()
	m, _ = comfy.generate_qwen_edit(pad_sprite(orig), instruction=instr, seed=SEED)
	return m, {"instruction": instr, "pad": 0.18}


def m6_combo(it, orig):
	instr = cat_instruction(it)
	m, _ = comfy.generate_qwen_edit(pad_sprite(orig), instruction=instr, seed=SEED,
	                                negative=ANTI_HALLUCINATE)
	return m, {"instruction": instr, "negative": ANTI_HALLUCINATE, "pad": 0.18}


def m8_sdxl(it, orig):
	"""SDXL tile lane: the tile ControlNet pins internal structure to the original."""
	pos = cat_instruction(it)
	m, _ = comfy.upscale_faithful(pad_sprite(orig), positive=pos, negative=ANTI_HALLUCINATE,
	                              seed=SEED, denoise=0.30, cn_strength=0.85)
	return m, {"positive": pos, "denoise": 0.30, "cn_strength": 0.85, "pad": 0.18}


GEN_METHODS = {"m0_base": m0_base, "m1_anchor": m1_anchor, "m2_catstyle": m2_catstyle,
               "m5_pad": m5_pad, "m6_combo": m6_combo, "m8_sdxl": m8_sdxl}

# ---- upscaler-family methods (from upscale4x_lab: locals / GAN / SeedVR2) --------
# These answer "enhance the existing art" rather than "re-imagine it": output is a 4x
# master with the original's exact silhouette re-cut on. Scored through the same
# end-to-end pipe for a fidelity floor/ceiling comparison against the diffusion lanes.
import upscale4x_lab as u4  # noqa: E402

U_METHODS = {
	"u_lanczos": "lanczos",
	"u_scale4x": "scale4x",
	"u_hq4x": "hq4x",
	"u_ultrasharp": "gan:4x-UltraSharp.pth",
	"u_remacri": "gan:4x_foolhardy_Remacri.pth",
	"u_animesharp": "gan:4x-AnimeSharp.pth",
	"u_siax": "gan:4x_NMKD-Siax_200k.pth",
	"u_rleanime": "gan:RealESRGAN_x4plus_anime_6B.pth",
	"u_hybrid": "hybrid_scale4x_remacri",
	"u_seedvr2": "seedvr2",
}


def _make_u_method(u4name: str):
	def fn(it, orig):
		orig_im = Image.open(io.BytesIO(orig)).convert("RGBA")
		up = u4.run_method(u4name, orig_im, it)
		buf = io.BytesIO()
		up.save(buf, "PNG")
		return buf.getvalue(), {"u4": u4name}
	return fn


for _n, _u in U_METHODS.items():
	GEN_METHODS[_n] = _make_u_method(_u)
CT_METHODS = {"m3_anchor_ct": ("m1_anchor", 1.0), "m4_catstyle_ct": ("m2_catstyle", 1.0),
              "m7_combo_ct": ("m6_combo", 1.0), "m9_sdxl_ct": ("m8_sdxl", 1.0)}
BESTOF_METHOD = "m11_bestof3"   # m6+CT over seeds {SEED, 8, 9}, keep the best-scoring
ALL_METHODS = list(GEN_METHODS) + list(CT_METHODS) + [BESTOF_METHOD]


# ---- run -------------------------------------------------------------------

def _items_by_inv():
	items, _ = catalog.build_catalog()
	byinv = {}
	for it in items:
		byinv.setdefault(it["invfile"], it)
	return byinv


def _paths(inv, method):
	d = os.path.join(LAB, inv)
	os.makedirs(d, exist_ok=True)
	return (os.path.join(d, f"{method}.master.png"),
	        os.path.join(d, f"{method}.fitted.png"))


def end_to_end(master_png: bytes, cat_item: dict, orig_img: Image.Image):
	"""master -> fit -> quantize -> DC6 -> render; return (scores, fitted_render_png)."""
	dc6_bytes = assets.png_to_item_dc6(master_png, cat_item["invwidth"], cat_item["invheight"])
	fitted_png = assets.dc6_to_png_bytes(dc6_bytes)
	fitted = Image.open(io.BytesIO(fitted_png)).convert("RGBA")
	return score_pair(orig_img, fitted), fitted_png


def load_manifest():
	p = os.path.join(LAB, "manifest.json")
	if os.path.exists(p):
		with open(p, encoding="utf-8") as f:
			return json.load(f)
	return {"rows": []}


def save_manifest(m):
	os.makedirs(LAB, exist_ok=True)
	with open(os.path.join(LAB, "manifest.json"), "w", encoding="utf-8") as f:
		json.dump(m, f, indent=1)


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--items", default="")
	ap.add_argument("--methods", default="")
	ap.add_argument("--force", action="store_true")
	args = ap.parse_args()
	sel_items = set(filter(None, args.items.split(",")))
	sel_methods = set(filter(None, args.methods.split(","))) or set(ALL_METHODS)

	byinv = _items_by_inv()
	manifest = load_manifest()
	done = {(r["inv"], r["method"]) for r in manifest["rows"]}

	todo = [t for t in TEST_SET if not sel_items or t["inv"] in sel_items]
	for t in todo:
		inv = t["inv"]
		cat_item = byinv.get(inv)
		if not cat_item:
			print(f"{inv}: not in catalog, skipped")
			continue
		orig_png = assets.dc6_to_png_bytes(assets.read_original_dc6(inv))
		orig_img = Image.open(io.BytesIO(orig_png)).convert("RGBA")

		# generation methods first (masters on disk), then CT variants over those masters
		for name in [m for m in GEN_METHODS if m in sel_methods]:
			if (inv, name) in done and not args.force:
				continue
			mp, fp = _paths(inv, name)
			t0 = time.time()
			try:
				master, meta = GEN_METHODS[name](t, orig_png)
			except Exception as e:  # noqa: BLE001
				print(f"  {inv} {name}: GEN FAILED {e}")
				manifest["rows"].append({"inv": inv, "method": name, "error": str(e)})
				save_manifest(manifest)
				continue
			with open(mp, "wb") as f:
				f.write(master)
			scores, fitted_png = end_to_end(master, cat_item, orig_img)
			with open(fp, "wb") as f:
				f.write(fitted_png)
			row = {"inv": inv, "method": name, "cat": t["cat"], "secs": round(time.time() - t0, 1),
			       **scores, **{f"meta_{k}": v for k, v in meta.items()}}
			manifest["rows"] = [r for r in manifest["rows"]
			                    if not (r["inv"] == inv and r["method"] == name)]
			manifest["rows"].append(row)
			save_manifest(manifest)
			print(f"  {inv} {name}: score={scores['score']:.3f} iou={scores['iou']:.2f} "
			      f"emd={scores['emd']:.3f} ssim={scores['ssim']:.2f} ({row['secs']}s)")

		for name, (parent, strength) in CT_METHODS.items():
			if name not in sel_methods or ((inv, name) in done and not args.force):
				continue
			pmp, _ = _paths(inv, parent)
			if not os.path.exists(pmp):
				continue
			with open(pmp, "rb") as f:
				parent_master = f.read()
			master = color_transfer(parent_master, orig_png, strength)
			mp, fp = _paths(inv, name)
			with open(mp, "wb") as f:
				f.write(master)
			scores, fitted_png = end_to_end(master, cat_item, orig_img)
			with open(fp, "wb") as f:
				f.write(fitted_png)
			row = {"inv": inv, "method": name, "cat": t["cat"], "secs": 0.0,
			       "meta_parent": parent, "meta_ct_strength": strength, **scores}
			manifest["rows"] = [r for r in manifest["rows"]
			                    if not (r["inv"] == inv and r["method"] == name)]
			manifest["rows"].append(row)
			save_manifest(manifest)
			print(f"  {inv} {name}: score={scores['score']:.3f} iou={scores['iou']:.2f} "
			      f"emd={scores['emd']:.3f} ssim={scores['ssim']:.2f}")

		# best-of-3 seeds: the production auto-QA shape — generate, score, keep the winner
		if BESTOF_METHOD in sel_methods and t["cat"] in ("gem", "glow") \
				and ((inv, BESTOF_METHOD) not in done or args.force):
			candidates = []
			m7 = next((r for r in manifest["rows"]
			                    if r["inv"] == inv and r["method"] == "m7_combo_ct" and "score" in r), None)
			if m7:
				mp7, _ = _paths(inv, "m7_combo_ct")
				if os.path.exists(mp7):
					with open(mp7, "rb") as f:
						candidates.append((m7["score"], f.read(), SEED))
			instr = cat_instruction(t)
			for seed in (SEED + 1, SEED + 2):
				try:
					master, _ = comfy.generate_qwen_edit(pad_sprite(orig_png), instruction=instr,
					                                     seed=seed, negative=ANTI_HALLUCINATE)
				except Exception as e:  # noqa: BLE001
					print(f"  {inv} bestof seed{seed}: GEN FAILED {e}")
					continue
				master = color_transfer(master, orig_png, 1.0)
				scores, _fp = end_to_end(master, cat_item, orig_img)
				candidates.append((scores["score"], master, seed))
				print(f"  {inv} bestof seed{seed}: score={scores['score']:.3f}")
			if candidates:
				candidates.sort(key=lambda c: c[0], reverse=True)
				_, best_master, best_seed = candidates[0]
				mp, fp = _paths(inv, BESTOF_METHOD)
				with open(mp, "wb") as f:
					f.write(best_master)
				scores, fitted_png = end_to_end(best_master, cat_item, orig_img)
				with open(fp, "wb") as f:
					f.write(fitted_png)
				row = {"inv": inv, "method": BESTOF_METHOD, "cat": t["cat"], "secs": 0.0,
				       "meta_seed": best_seed, "meta_pool": len(candidates), **scores}
				manifest["rows"] = [r for r in manifest["rows"]
				                    if not (r["inv"] == inv and r["method"] == BESTOF_METHOD)]
				manifest["rows"].append(row)
				save_manifest(manifest)
				print(f"  {inv} {BESTOF_METHOD}: score={scores['score']:.3f} (seed {best_seed})")

	# summary table
	rows = [r for r in manifest["rows"] if "score" in r]
	print("\nMean composite score per method:")
	for m in ALL_METHODS:
		sub = [r["score"] for r in rows if r["method"] == m]
		gem = [r["score"] for r in rows if r["method"] == m and r.get("cat") in ("gem", "glow")]
		if sub:
			print(f"  {m:<16} all={sum(sub) / len(sub):.3f} (n={len(sub)})  "
			      f"gems={sum(gem) / len(gem):.3f} (n={len(gem)})" if gem else
			      f"  {m:<16} all={sum(sub) / len(sub):.3f} (n={len(sub)})")


if __name__ == "__main__":
	main()
