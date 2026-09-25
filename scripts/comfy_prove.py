"""Phase-A proof harness for the ComfyUI upscale lanes.

Runs sample sprites through Lane A (SDXL tile creative-upscale) and writes a side-by-side sheet
(original @ 4x nearest | upscaled) so we can eyeball fidelity vs. the design's exit criterion.

    python scripts/comfy_prove.py <sprite.png> "<positive prompt>" [--denoise .45 --cn .7 --seed N]

Or the built-in trio:
    python scripts/comfy_prove.py --trio <dir_with helm_ulm.png glove_xtg.png boot_xtb.png>
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import comfy  # noqa: E402
from PIL import Image  # noqa: E402

NEG = "blurry, pixelated, jpeg artifacts, low quality, text, watermark, signature, extra objects, frame, border"

TRIO = {
	"helm_ulm": "detailed hand-painted fantasy plate helmet, closed great helm, dark fantasy armor, "
	            "intricate steel, battle-worn metal, dramatic rim lighting, Diablo II inventory item art",
	"glove_xtg": "detailed hand-painted fantasy plated gauntlets, articulated steel finger armor, "
	             "dark fantasy, battle-worn metal, dramatic lighting, Diablo II inventory item art",
	"boot_xtb": "detailed hand-painted fantasy armored greaves boots, plated sabatons, dark fantasy, "
	            "battle-worn leather and steel, dramatic lighting, Diablo II inventory item art",
}


def sheet(orig_png: bytes, up_png: bytes, out_path: str, scale_orig: int = 8):
	o = Image.open(io.BytesIO(orig_png)).convert("RGBA")
	# trim to content so the comparison isn't mostly empty canvas
	bb = o.split()[-1].getbbox()
	if bb:
		o = o.crop(bb)
	o = o.resize((o.width * scale_orig, o.height * scale_orig), Image.NEAREST)
	u = Image.open(io.BytesIO(up_png)).convert("RGBA")
	ub = u.split()[-1].getbbox()
	if ub:
		u = u.crop(ub)
	H = max(o.height, u.height)
	def fit(im):
		if im.height != H:
			im = im.resize((int(im.width * H / im.height), H), Image.LANCZOS)
		return im
	o, u = fit(o), fit(u)
	gap = 24
	W = o.width + gap + u.width
	# checker background so alpha is visible
	canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
	c = 16
	for y in range(0, H, c):
		for x in range(0, W, c):
			if (x // c + y // c) % 2:
				canvas.paste((40, 40, 46, 255), (x, y, min(x + c, W), min(y + c, H)))
	canvas.alpha_composite(o, (0, 0))
	canvas.alpha_composite(u, (o.width + gap, 0))
	canvas.convert("RGBA").save(out_path)
	print("  sheet ->", out_path)


def run_one(sprite_path: str, prompt: str, out_dir: str, *, denoise: float, cn: float, seed: int):
	name = os.path.splitext(os.path.basename(sprite_path))[0]
	with open(sprite_path, "rb") as f:
		orig = f.read()
	print(f"[{name}] denoise={denoise} cn={cn} seed={seed}")
	t = time.time()
	up, size = comfy.upscale_faithful(orig, positive=prompt, negative=NEG,
	                                  seed=seed, denoise=denoise, cn_strength=cn)
	dt = time.time() - t
	up_path = os.path.join(out_dir, f"{name}_up.png")
	with open(up_path, "wb") as f:
		f.write(up)
	print(f"  gen {dt:.1f}s  {size}  -> {up_path}")
	sheet(orig, up, os.path.join(out_dir, f"{name}_sheet.png"))


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("sprite", nargs="?")
	ap.add_argument("prompt", nargs="?")
	ap.add_argument("--trio")
	ap.add_argument("--denoise", type=float, default=0.45)
	ap.add_argument("--cn", type=float, default=0.7)
	ap.add_argument("--seed", type=int, default=12345)
	ap.add_argument("--out", default=None)
	a = ap.parse_args()
	print("COMFY_URL =", comfy.COMFY_URL, "  ckpt =", comfy.CKPT)
	if a.trio:
		out = a.out or a.trio
		for name, prompt in TRIO.items():
			p = os.path.join(a.trio, name + ".png")
			if os.path.exists(p):
				run_one(p, prompt, out, denoise=a.denoise, cn=a.cn, seed=a.seed)
			else:
				print("missing", p)
		return
	out = a.out or os.path.dirname(os.path.abspath(a.sprite))
	run_one(a.sprite, a.prompt, out, denoise=a.denoise, cn=a.cn, seed=a.seed)


if __name__ == "__main__":
	main()
