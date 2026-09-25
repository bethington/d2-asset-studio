"""Phase 0: verify pal.dat channel order using known-red healing potions."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyd2 import dc6  # noqa: E402
from pyd2.palette import load_pal_dat, save_frame_png  # noqa: E402

ITEMS = r"C:\Diablo2\AssetStudio\originals\data\global\items"
PAL = r"C:\Diablo2\AssetStudio\originals\data\global\palette\ACT1\pal.dat"


def avg_color(frame, palette):
	r = g = b = n = 0
	for row in frame.pixels:
		for p in row:
			if p != dc6.TRANSPARENT:
				cr, cg, cb = palette[p]
				r, g, b, n = r + cr, g + cg, b + cb, n + 1
	return (r // n, g // n, b // n) if n else (0, 0, 0)


def main():
	with open(PAL, "rb") as f:
		raw = f.read()
	for order in ("rgb", "bgr"):
		palette = load_pal_dat(raw, order)
		for name in ("invhp3", "invmp3"):
			with open(os.path.join(ITEMS, name + ".dc6"), "rb") as f:
				sprite = dc6.decode(f.read())
			print(f"order={order} {name}: avg RGB = {avg_color(sprite.frames[0], palette)}")

	# save PNGs with both orders for eyeball check
	palette = load_pal_dat(raw, "rgb")
	out = r"C:\Diablo2\AssetStudio\census"
	for name in ("invhp3", "invmp3", "invcap"):
		with open(os.path.join(ITEMS, name + ".dc6"), "rb") as f:
			sprite = dc6.decode(f.read())
		save_frame_png(sprite.frames[0], palette, os.path.join(out, f"{name}_rgb.png"))


if __name__ == "__main__":
	main()
