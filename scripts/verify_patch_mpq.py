"""Read every file back out of a built patch.mpq through StormLib (sanity check)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyd2.mpq import MpqArchive  # noqa: E402

MPQ = sys.argv[1] if len(sys.argv) > 1 else r"C:\Diablo2\AssetStudio\export\patch.mpq"
ITEMS = r"C:\Diablo2\AssetStudio\overlay\data\global\items"


def main():
	n = 0
	with MpqArchive(MPQ) as a:
		for f in os.listdir(ITEMS):
			if not f.endswith(".dc6"):
				continue
			name = "data\\global\\items\\" + f
			d = a.read_file(name)
			assert d and d[:4] == b"\x06\x00\x00\x00", f"{name}: not a DC6 ({len(d)}B)"
			n += 1
	print(f"read back {n} DC6 files OK from {os.path.basename(MPQ)}")


if __name__ == "__main__":
	main()
