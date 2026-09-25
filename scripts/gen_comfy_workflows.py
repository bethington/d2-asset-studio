"""Generate importable ComfyUI workflow files for lab methods m0-m7.

Each file is a UI-format workflow (drag into the ComfyUI web UI) of the production
Qwen-Image-Edit core exactly as app/comfy.build_qwen_edit_graph builds it (GGUF unet +
Lightning LoRA + AuraFlow shift 3.0 + CFGNorm + GAN pre-upscale + 4-step euler/simple
cfg1), with the method's instruction / negative pre-filled and a Note node explaining
the pipeline steps that run in Python OUTSIDE the graph (margin pad, BiRefNet re-cut,
Oklab color transfer, cell fit + palette quantize).

Output: workflows/m0_qwen_baseline.json ... m7_qwen_combo_ct.json
        + README.md

    python scripts/gen_comfy_workflows.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import comfy  # noqa: E402

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "workflows"))

HOUSE = comfy.qwen_enhance_instruction()
GEM_STYLE = ("hand-painted dark-fantasy RPG inventory icon, vivid saturated colours, glossy "
             "translucent faceted crystal, bright specular highlights, preserve the inner glow "
             "and sparkle, crisp sharp edges")
KEEP = ", keep the exact shape, silhouette, materials and colours of the original"
ANTI = ("treasure chest, box, container, letter, text, logo, face, creature, blurry, "
        "low quality, watermark, frame, border")
IDENT = "the item is <IDENTITY — one literal sentence describing the ORIGINAL sprite>. "

COMMON_NOTE = (
	"Python-side steps around this graph (app/comfy.py + scripts/fidelity_lab.py):\n"
	"1. INPUT: the sprite is bbox-cropped and matted onto neutral gray at near-native size\n"
	"   so the GAN (not LANCZOS) does the enlargement (matte_and_size + _native_long_side).\n"
	"2. OUTPUT: background re-cut with BiRefNet/rembg clamped to the original alpha\n"
	"   (recut_alpha_rembg), then fit to the item's cell grid, palette-quantized (Oklab\n"
	"   nearest, indices 1-224), edge-outlined, and DC6-encoded (assets.png_to_item_dc6).\n"
)

METHODS = [
	("m0_qwen_baseline", "m0 — Qwen enhance, house style, NO prompt (the old failure mode)",
	 HOUSE, "", ""),
	("m1_qwen_anchor", "m1 — identity anchor + house style",
	 IDENT + HOUSE, "", ""),
	("m2_qwen_catstyle", "m2 — identity + category style (gem shown) + anti-hallucination negative",
	 IDENT + GEM_STYLE + KEEP, ANTI, ""),
	("m3_qwen_anchor_ct", "m3 — m1 + Oklab color transfer (Python post-pass)",
	 IDENT + HOUSE, "",
	 "m3 = the m1 graph result + color transfer: the generated object's Oklab channels are\n"
	 "histogram-matched to the ORIGINAL's object pixels (fidelity_lab.color_transfer).\n"),
	("m4_qwen_catstyle_ct", "m4 — m2 + Oklab color transfer (Python post-pass)",
	 IDENT + GEM_STYLE + KEEP, ANTI,
	 "m4 = the m2 graph result + color transfer (see m3 note).\n"),
	("m5_qwen_pad", "m5 — house style + margin pad (Python pre-pass)",
	 HOUSE, "",
	 "m5 = m0 graph, but the sprite is padded with a transparent margin (~18%) BEFORE the\n"
	 "matte (fidelity_lab.pad_sprite) so cell-filling sprites keep an object-on-background\n"
	 "composition and the re-cut has an edge to find.\n"),
	("m6_qwen_combo", "m6 — pad + identity + category style + negative",
	 IDENT + GEM_STYLE + KEEP, ANTI,
	 "m6 = the m2 graph + the m5 margin pad. Generation-side production recipe.\n"),
	("m7_qwen_combo_ct", "m7 — m6 + Oklab color transfer (the default production recipe)",
	 IDENT + GEM_STYLE + KEEP, ANTI,
	 "m7 = the m6 graph + margin pad (pre) + Oklab color transfer (post). This is the\n"
	 "recommended default: identity fixes hallucination, pad fixes cell-filling sprites,\n"
	 "the category style keeps gems vivid, and the color transfer locks the palette.\n"),
]


def wf(title: str, prompt: str, negative: str, extra_note: str) -> dict:
	"""UI-format workflow: the qwen-edit core with GAN pre-upscale, laid out left->right."""
	nodes, links = [], []

	def node(nid, typ, pos, size, inputs, outputs, widgets, title_=None):
		n = {"id": nid, "type": typ, "pos": list(pos), "size": list(size), "flags": {},
		     "order": nid, "mode": 0, "inputs": inputs, "outputs": outputs,
		     "properties": {"Node name for S&R": typ}, "widgets_values": widgets}
		if title_:
			n["title"] = title_
		nodes.append(n)

	def link(lid, fn, fs, tn, ts, typ):
		links.append([lid, fn, fs, tn, ts, typ])

	def inp(name, typ, lid):
		return {"name": name, "type": typ, "link": lid}

	def outp(name, typ, lids, slot=0):
		return {"name": name, "type": typ, "links": lids, "slot_index": slot}

	# 1 unet -> 2 lora -> 3 ms -> 4 cfgn ; 5 clip ; 6 vae ; 7 img -> 9 gan(8 upmodel) -> 10 scale
	# 11 pos / 12 neg / 13 enc -> 14 ks -> 15 dec -> 16 save ; 17 note
	node(1, "UnetLoaderGGUF", (40, 40), (340, 60), [],
	     [outp("MODEL", "MODEL", [1])], [comfy.QWEN_GGUF])
	node(2, "LoraLoaderModelOnly", (420, 40), (340, 82),
	     [inp("model", "MODEL", 1)], [outp("MODEL", "MODEL", [2])], [comfy.QWEN_LORA, 1.0])
	node(3, "ModelSamplingAuraFlow", (800, 40), (280, 58),
	     [inp("model", "MODEL", 2)], [outp("MODEL", "MODEL", [3])], [3.0])
	node(4, "CFGNorm", (1120, 40), (240, 58),
	     [inp("model", "MODEL", 3)], [outp("MODEL", "MODEL", [4])], [1.0])
	node(5, "CLIPLoader", (40, 160), (340, 82), [],
	     [outp("CLIP", "CLIP", [5, 6])], [comfy.QWEN_CLIP, "qwen_image", "default"])
	node(6, "VAELoader", (40, 290), (340, 58), [],
	     [outp("VAE", "VAE", [7, 8, 9, 10])], [comfy.QWEN_VAE])
	node(7, "LoadImage", (40, 400), (340, 314), [],
	     [outp("IMAGE", "IMAGE", [11]), outp("MASK", "MASK", None, 1)],
	     ["sprite_matted_gray.png", "image"])
	node(8, "UpscaleModelLoader", (40, 760), (340, 58), [],
	     [outp("UPSCALE_MODEL", "UPSCALE_MODEL", [12])], [comfy.UPSCALE_MODEL])
	node(9, "ImageUpscaleWithModel", (420, 620), (280, 78),
	     [inp("upscale_model", "UPSCALE_MODEL", 12), inp("image", "IMAGE", 11)],
	     [outp("IMAGE", "IMAGE", [13])], [], "GAN pre-upscale (4x-UltraSharp)")
	node(10, "ImageScale", (740, 620), (300, 130),
	     [inp("image", "IMAGE", 13)], [outp("IMAGE", "IMAGE", [14, 15, 16])],
	     ["lanczos", 1024, 1024, "disabled"])
	node(11, "TextEncodeQwenImageEditPlus", (1080, 380), (420, 200),
	     [inp("clip", "CLIP", 5), inp("vae", "VAE", 7), inp("image1", "IMAGE", 14)],
	     [outp("CONDITIONING", "CONDITIONING", [17])], [prompt], "POSITIVE instruction")
	node(12, "TextEncodeQwenImageEditPlus", (1080, 630), (420, 160),
	     [inp("clip", "CLIP", 6), inp("vae", "VAE", 8), inp("image1", "IMAGE", 15)],
	     [outp("CONDITIONING", "CONDITIONING", [18])], [negative], "NEGATIVE")
	node(13, "VAEEncode", (1080, 840), (240, 78),
	     [inp("pixels", "IMAGE", 16), inp("vae", "VAE", 9)],
	     [outp("LATENT", "LATENT", [19])], [])
	node(14, "KSampler", (1560, 380), (320, 262),
	     [inp("model", "MODEL", 4), inp("positive", "CONDITIONING", 17),
	      inp("negative", "CONDITIONING", 18), inp("latent_image", "LATENT", 19)],
	     [outp("LATENT", "LATENT", [20])], [7, "fixed", 4, 1.0, "euler", "simple", 1.0])
	node(15, "VAEDecode", (1920, 380), (220, 78),
	     [inp("samples", "LATENT", 20), inp("vae", "VAE", 10)],
	     [outp("IMAGE", "IMAGE", [21])], [])
	node(16, "SaveImage", (2180, 380), (320, 290),
	     [inp("images", "IMAGE", 21)], [], ["d2qwen"])
	nodes.append({"id": 17, "type": "Note", "pos": [1560, 720], "size": [560, 260],
	              "flags": {}, "order": 17, "mode": 0, "inputs": [], "outputs": [],
	              "properties": {}, "widgets_values": [title + "\n\n" + COMMON_NOTE + extra_note],
	              "color": "#432", "bgcolor": "#653"})

	link(1, 1, 0, 2, 0, "MODEL")
	link(2, 2, 0, 3, 0, "MODEL")
	link(3, 3, 0, 4, 0, "MODEL")
	link(4, 4, 0, 14, 0, "MODEL")
	link(5, 5, 0, 11, 0, "CLIP")
	link(6, 5, 0, 12, 0, "CLIP")
	link(7, 6, 0, 11, 1, "VAE")
	link(8, 6, 0, 12, 1, "VAE")
	link(9, 6, 0, 13, 1, "VAE")
	link(10, 6, 0, 15, 1, "VAE")
	link(11, 7, 0, 9, 1, "IMAGE")
	link(12, 8, 0, 9, 0, "UPSCALE_MODEL")
	link(13, 9, 0, 10, 0, "IMAGE")
	link(14, 10, 0, 11, 2, "IMAGE")
	link(15, 10, 0, 12, 2, "IMAGE")
	link(16, 10, 0, 13, 0, "IMAGE")
	link(17, 11, 0, 14, 1, "CONDITIONING")
	link(18, 12, 0, 14, 2, "CONDITIONING")
	link(19, 13, 0, 14, 3, "LATENT")
	link(20, 14, 0, 15, 0, "LATENT")
	link(21, 15, 0, 16, 0, "IMAGE")

	return {"last_node_id": 17, "last_link_id": 21, "nodes": nodes, "links": links,
	        "groups": [], "config": {}, "extra": {"note": title}, "version": 0.4}


README = """# ComfyUI workflows — fidelity-lab methods m0–m7

Importable ComfyUI workflows for the Qwen-Image-Edit generation core of each lab
method (drag the .json into the ComfyUI web UI at http://10.0.10.30:8188).

All eight share the identical production graph (GGUF Qwen-Image-Edit-2509 + 4-step
Lightning LoRA, AuraFlow shift 3.0, CFGNorm, 4x-UltraSharp GAN pre-upscale, euler/simple,
cfg 1.0, denoise 1.0) — the METHOD lives in the prompt/negative text and in Python
pre/post steps that cannot run inside ComfyUI:

| method | prompt | negative | Python pre | Python post |
|---|---|---|---|---|
| m0 baseline   | house style only        | —    | —          | re-cut + fit + quantize |
| m1 anchor     | identity + house        | —    | —          | 〃 |
| m2 catstyle   | identity + gem/glow     | anti | —          | 〃 |
| m3 anchor+CT  | identity + house        | —    | —          | + Oklab color transfer |
| m4 cat+CT     | identity + gem/glow     | anti | —          | + Oklab color transfer |
| m5 pad        | house style only        | —    | margin pad | 〃 (no CT) |
| m6 combo      | identity + gem/glow     | anti | margin pad | 〃 (no CT) |
| m7 combo+CT   | identity + gem/glow     | anti | margin pad | + Oklab color transfer |

Replace `<IDENTITY …>` in the positive prompt with one literal sentence describing the
original sprite (auto-captioned by Florence-2 in the studio pipeline). The LoadImage
node expects the gray-matted sprite the studio uploads (app/comfy.matte_and_size); when
experimenting by hand, any small item image on a plain gray background behaves the same.

The Note node inside each workflow documents the exact Python steps around the graph.
Production entry points: app/comfy.generate_qwen_edit (graph) + scripts/fidelity_lab.py
(pad_sprite, color_transfer, end_to_end scoring).
"""


def main():
	os.makedirs(OUT, exist_ok=True)
	for fname, title, prompt, negative, extra in METHODS:
		with open(os.path.join(OUT, fname + ".json"), "w", encoding="utf-8") as f:
			json.dump(wf(title, prompt, negative, extra), f, indent=1)
		print(f"  {fname}.json")
	with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as f:
		f.write(README)
	print(f"-> {OUT}")


if __name__ == "__main__":
	main()
