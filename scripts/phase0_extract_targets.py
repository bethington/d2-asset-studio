"""Phase 0: extract the go/no-go experiment targets into the workspace."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyd2.mpq import read_effective  # noqa: E402

WS = r"C:\Diablo2\AssetStudio\originals"

TARGETS = [
	"data\\global\\excel\\misc.txt",
	"data\\global\\excel\\armor.txt",
	"data\\global\\excel\\uniqueitems.txt",
	"data\\global\\palette\\ACT1\\pal.dat",
	"data\\global\\palette\\ACT2\\pal.dat",
]


def main():
	extra = sys.argv[1:]
	for t in TARGETS + extra:
		try:
			data, src = read_effective(t)
		except FileNotFoundError:
			print(f"MISS {t}")
			continue
		out = os.path.join(WS, *t.split("\\"))
		os.makedirs(os.path.dirname(out), exist_ok=True)
		with open(out, "wb") as f:
			f.write(data)
		print(f"OK   {t}  {len(data)}B  <- {os.path.basename(src)}")


if __name__ == "__main__":
	main()
