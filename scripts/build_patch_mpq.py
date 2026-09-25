"""Author patch.mpq from the workspace overlay tree.

Usage: python scripts/build_patch_mpq.py [overlay_dir] [out.mpq]
Walks overlay_dir, adds every file under it with its relative path (backslashes)
as the archived name, then verifies by reading one entry back.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyd2.mpq import build_archive, MpqArchive  # noqa: E402

OVERLAY = sys.argv[1] if len(sys.argv) > 1 else r"C:\Diablo2\AssetStudio\overlay"
OUT = sys.argv[2] if len(sys.argv) > 2 else r"C:\Diablo2\AssetStudio\export\patch.mpq"


def main():
	files = {}
	for root, _dirs, names in os.walk(OVERLAY):
		for n in names:
			disk = os.path.join(root, n)
			rel = os.path.relpath(disk, OVERLAY).replace("/", "\\")
			files[rel] = disk
	if not files:
		print(f"no files under {OVERLAY}")
		return
	os.makedirs(os.path.dirname(OUT), exist_ok=True)
	build_archive(OUT, files)
	print(f"built {OUT} with {len(files)} files:")
	for rel in sorted(files):
		print(f"  {rel}")
	# verify: reopen and read one back
	with MpqArchive(OUT) as arc:
		probe = sorted(files)[0]
		data = arc.read_file(probe)
		print(f"verify: read '{probe}' back = {len(data)} bytes  ({'OK' if data else 'EMPTY'})")


if __name__ == "__main__":
	main()
