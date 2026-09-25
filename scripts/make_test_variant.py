"""Produce a visibly-different variant of a DC6 for the Phase 0 go/no-go test.

Stamps thick diagonal stripes across every frame using the palette index
closest to pure magenta (magenta is invariant under RGB<->BGR ambiguity).

Usage: python scripts/make_test_variant.py in.dc6 pal.dat out.dc6
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyd2 import dc6  # noqa: E402
from pyd2.palette import load_pal_dat  # noqa: E402


def closest_index(palette, target):
	tr, tg, tb = target
	best, best_d = 0, 1 << 30
	for i in range(1, 256):  # skip 0 (transparent by convention)
		r, g, b = palette[i]
		d = (r - tr) ** 2 + (g - tg) ** 2 + (b - tb) ** 2
		if d < best_d:
			best, best_d = i, d
	return best


def main():
	in_path, pal_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
	with open(in_path, "rb") as f:
		sprite = dc6.decode(f.read())
	with open(pal_path, "rb") as f:
		palette = load_pal_dat(f.read())
	target = (0, 255, 0)
	if len(sys.argv) > 4:
		target = tuple(int(v) for v in sys.argv[4].split(","))
	stripe = closest_index(palette, target)
	print(f"stripe index {stripe} rgb={palette[stripe]}")
	for frame in sprite.frames:
		for y in range(frame.height):
			for x in range(frame.width):
				if frame.pixels[y][x] != dc6.TRANSPARENT and ((x + y) // 3) % 2 == 0:
					frame.pixels[y][x] = stripe
	os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
	with open(out_path, "wb") as f:
		f.write(dc6.encode(sprite))
	print(f"wrote {out_path}")


if __name__ == "__main__":
	main()
