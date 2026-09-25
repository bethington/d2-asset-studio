"""Fill every non-transparent pixel of a DC6 with one palette color (unambiguous test)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyd2 import dc6  # noqa: E402
from pyd2.palette import load_pal_dat  # noqa: E402
from scripts.make_test_variant import closest_index  # noqa: E402


def main():
	in_path, pal_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
	target = tuple(int(v) for v in (sys.argv[4] if len(sys.argv) > 4 else "0,255,0").split(","))
	with open(in_path, "rb") as f:
		sprite = dc6.decode(f.read())
	with open(pal_path, "rb") as f:
		palette = load_pal_dat(f.read())
	idx = closest_index(palette, target)
	for frame in sprite.frames:
		for y in range(frame.height):
			for x in range(frame.width):
				if frame.pixels[y][x] != dc6.TRANSPARENT:
					frame.pixels[y][x] = idx
	os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
	with open(out_path, "wb") as f:
		f.write(dc6.encode(sprite))
	print(f"solid idx {idx} rgb={palette[idx]} -> {out_path}")


if __name__ == "__main__":
	main()
