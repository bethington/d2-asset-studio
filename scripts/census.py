"""Phase 0 checklist #7: enumerate every file in every PD2/base MPQ.

Writes census.json (per-archive file lists + extension counts) into the
workspace and prints a summary table. Also answers checklist #8 by showing
whether pd2data.mpq ships .txt, .bin, or both.

Usage: python scripts/census.py [workspace_dir]
"""

import glob
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyd2.mpq import MpqArchive, MpqError  # noqa: E402

ARCHIVE_GLOBS = [r"C:\Diablo2\*.mpq", r"C:\Diablo2\ProjectD2\*.mpq"]


def main():
	workspace = sys.argv[1] if len(sys.argv) > 1 else r"C:\Diablo2\AssetStudio"
	out_dir = os.path.join(workspace, "census")
	os.makedirs(out_dir, exist_ok=True)

	census = {}
	for pattern in ARCHIVE_GLOBS:
		for arc_path in sorted(glob.glob(pattern)):
			if arc_path.lower().endswith(".bak"):
				continue
			try:
				with MpqArchive(arc_path) as arc:
					files = []
					for name, size in arc.list_files():
						files.append((name, size))
						if len(files) > 500000:  # runaway guard
							break
			except (MpqError, OSError, MemoryError) as e:
				census[arc_path] = {"error": type(e).__name__ + ": " + str(e)}
				print(f"{os.path.basename(arc_path):24} ERROR {type(e).__name__}")
				continue
			exts = Counter(os.path.splitext(n)[1].lower() or "<none>" for n, _ in files)
			census[arc_path] = {
				"file_count": len(files),
				"extensions": dict(exts.most_common()),
				"files": [{"name": n, "size": s} for n, s in files],
			}
			top = ", ".join(f"{e}:{c}" for e, c in exts.most_common(6))
			print(f"{os.path.basename(arc_path):24} {len(files):6} files   {top}")

	with open(os.path.join(out_dir, "census.json"), "w", encoding="utf-8") as f:
		json.dump(census, f, indent=1)
	print(f"\nwrote {os.path.join(out_dir, 'census.json')}")

	# checklist #8: .txt vs .bin in the PD2 data archive
	for key in census:
		if key.lower().endswith("pd2data.mpq") and "extensions" in census[key]:
			ext = census[key]["extensions"]
			print(f"pd2data.mpq: .txt={ext.get('.txt', 0)}  .bin={ext.get('.bin', 0)}")


if __name__ == "__main__":
	main()
