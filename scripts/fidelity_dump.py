"""Dump side-by-side original-vs-alternates strips (4x nearest zoom) for named buckets
so failures can be inspected by eye. Writes _fidelity_out/img/<invfile>.png.

    python scripts/fidelity_dump.py invgsbc invgsvc invgsgc
    python scripts/fidelity_dump.py --worst 15      # worst N from audit.json
"""

from __future__ import annotations

import io
import json
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import assets  # noqa: E402

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_fidelity_out"))
ALT_DC6 = os.path.join(assets.WORKSPACE, "alternates", "dc6")
ZOOM = 4
BG = (40, 40, 48, 255)


def _tile(img: Image.Image, label: str) -> Image.Image:
	z = img.resize((img.size[0] * ZOOM, img.size[1] * ZOOM), Image.NEAREST)
	canvas = Image.new("RGBA", (z.size[0] + 8, z.size[1] + 22), BG)
	canvas.paste(z, (4, 18), z)
	ImageDraw.Draw(canvas).text((4, 3), label, fill=(230, 220, 180, 255))
	return canvas


def dump(invfile: str) -> str | None:
	orig_bytes = assets.try_read_effective(assets.item_dc6_path(invfile))
	if orig_bytes is None:
		print(f"  {invfile}: no original")
		return None
	png = assets.dc6_to_png_bytes(orig_bytes)
	tiles = [_tile(Image.open(io.BytesIO(png)).convert("RGBA"), "ORIG")]
	bdir = os.path.join(ALT_DC6, invfile)
	for fn in sorted(os.listdir(bdir)):
		if not fn.endswith(".dc6"):
			continue
		with open(os.path.join(bdir, fn), "rb") as f:
			apng = assets.dc6_to_png_bytes(f.read())
		tiles.append(_tile(Image.open(io.BytesIO(apng)).convert("RGBA"), fn[:-4]))
	w = sum(t.size[0] for t in tiles) + 4 * (len(tiles) - 1)
	h = max(t.size[1] for t in tiles)
	strip = Image.new("RGBA", (w, h), BG)
	x = 0
	for t in tiles:
		strip.paste(t, (x, 0))
		x += t.size[0] + 4
	os.makedirs(os.path.join(OUT, "img"), exist_ok=True)
	path = os.path.join(OUT, "img", f"{invfile}.png")
	strip.convert("RGB").save(path)
	return path


def main():
	args = sys.argv[1:]
	if args and args[0] == "--worst":
		n = int(args[1]) if len(args) > 1 else 15
		with open(os.path.join(OUT, "audit.json"), encoding="utf-8") as f:
			buckets = [r["invfile"] for r in json.load(f)["best_per_bucket"][:n]]
	else:
		buckets = args
	for b in buckets:
		p = dump(b)
		if p:
			print(p)


if __name__ == "__main__":
	main()
