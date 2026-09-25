"""Phase 0 experiment: inject overlay item DC6s into patch_d2.mpq (backed up first).

Reversible high-priority-archive test — patch_d2.mpq is loaded at priority 5000.
Usage: python scripts/inject_patch_d2_test.py
"""

import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyd2.mpq import add_files, MpqArchive  # noqa: E402

OVERLAY = r"C:\Diablo2\AssetStudio\overlay\data\global\items"
PATCH = r"C:\Diablo2\ProjectD2\patch_d2.mpq"


def main():
	files = {}
	for f in glob.glob(os.path.join(OVERLAY, "*.dc6")):
		archived = "data\\global\\items\\" + os.path.basename(f)
		files[archived] = f
	add_files(PATCH, files)
	print(f"added {len(files)} green potions to patch_d2.mpq")
	with MpqArchive(PATCH) as a:
		probe = "data\\global\\items\\invmp3.dc6"
		d = a.read_file(probe)
		print(f"verify {probe} in patch_d2: {len(d)} bytes")


if __name__ == "__main__":
	main()
