"""Re-run the PNG->DC6 conversion for every alternate from its stored provenance render,
using the fixed quantization pipeline (dark-rim matte + Oklab match + ringing clamp +
firefly despeckle; see UPSCALE_3D_PANEL_DESIGN.md).

No Meshy/Blender re-runs — only the conversion step. Originals are backed up next to each
file as <alt>.dc6.pre_specfix (first run only; reruns keep the FIRST backup). A before/after
contact sheet is written for eyeballing.

    python scripts/reconvert_alternates.py [--dry] [--sheet OUT.png]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app.assets as assets  # noqa: E402
from PIL import Image  # noqa: E402
from pyd2 import dc6  # noqa: E402
from pyd2.palette import frame_to_rgba  # noqa: E402

ALTS = os.path.join(assets.WORKSPACE, "alternates")

# sources whose DC6 was produced by png_to_item_dc6 (safe to re-run with stored params) —
# verified against the actual save sites in server.py. 2D glove composites ("glove-pair",
# canvas_to_dc6) are laid out differently and are skipped.
SAFE_SOURCES = {"studio", "pairing-single", "glove-pair-3d", "png-import", "meshy+blender", None}


def dc6_to_img(data: bytes, pal) -> Image.Image:
	f = dc6.decode(data).frames[0]
	w, h, rgba = frame_to_rgba(f, pal)
	return Image.frombytes("RGBA", (w, h), rgba)


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--dry", action="store_true")
	ap.add_argument("--sheet", default=os.path.join(assets.WORKSPACE, "reconvert_sheet.png"))
	a = ap.parse_args()
	pal = assets._palette()
	rows = []
	done = skipped = 0
	for dc6_path in sorted(glob.glob(os.path.join(ALTS, "**", "*.dc6"), recursive=True)):
		if "_backup" in dc6_path or dc6_path.endswith(".pre_specfix"):
			continue
		base = dc6_path[:-4]
		render = base + ".render.png"
		meta_path = base + ".meta.json"
		rel = os.path.relpath(dc6_path, ALTS)
		if not os.path.exists(render):
			skipped += 1
			print(f"SKIP (no render.png)   {rel}")
			continue
		meta = {}
		if os.path.exists(meta_path):
			try:
				with open(meta_path, encoding="utf-8") as f:
					meta = json.load(f)
			except ValueError:
				pass
		source = meta.get("source")
		if source is not None and source not in SAFE_SOURCES:
			skipped += 1
			print(f"SKIP (source={source})  {rel}")
			continue
		with open(dc6_path, "rb") as f:
			old_bytes = f.read()
		f0 = dc6.decode(old_bytes).frames[0]
		iw, ih = max(1, f0.width // assets.CELL_PX), max(1, f0.height // assets.CELL_PX)
		fill = 1.0 if source == "glove-pair-3d" else float(meta.get("fill", 0.94))
		try:
			with open(render, "rb") as f:
				render_bytes = f.read()
			new_bytes = assets.png_to_item_dc6(render_bytes, iw, ih, fill=fill,
			                                   dx=float(meta.get("dx", 0)),
			                                   dy=float(meta.get("dy", 0)),
			                                   grade=meta.get("grade"))
		except Exception as e:  # noqa: BLE001
			skipped += 1
			print(f"SKIP (convert error: {e})  {rel}")
			continue
		print(f"{'DRY ' if a.dry else ''}reconvert  {rel}  ({iw}x{ih}, fill={fill})")
		rows.append((rel, dc6_to_img(old_bytes, pal), dc6_to_img(new_bytes, pal)))
		if not a.dry:
			bak = dc6_path + ".pre_specfix"
			if not os.path.exists(bak):
				shutil.copy2(dc6_path, bak)
			with open(dc6_path, "wb") as f:
				f.write(new_bytes)
		done += 1

	print(f"\n{done} reconverted, {skipped} skipped")
	if not a.dry:
		# CRITICAL: the game reads overlay/, not alternates/ — re-activate every active choice
		# so the rebuilt bytes actually reach the game (learned 2026-07-22: Victor's Silk kept
		# showing pre-fix specks in game because the overlay was never refreshed).
		# Prefer the live catalog for the EFFECTIVE invfile: txt-edited uniques (own art file,
		# e.g. Azurewrath's invcrsu) have a manifest entry that still names the base invfile,
		# which would leave the own-file overlay copy stale (bitten 2026-07-22).
		actives = []
		try:
			import urllib.request
			d = json.loads(urllib.request.urlopen("http://127.0.0.1:5001/api/items", timeout=20).read())
			items = d if isinstance(d, list) else d.get("items", [])
			actives = [(i["id"], i["invfile"], i["active"]) for i in items
			           if i.get("active") and i["active"] != "original"]
			src = "catalog"
		except Exception:  # server down — manifest fallback
			m = assets._load_manifest()
			actives = [(k, v.get("invfile"), v.get("active"))
			           for k, v in (m.get("assets") or {}).items()
			           if isinstance(v, dict) and v.get("active") and v["active"] != "original"
			           and v.get("invfile")]
			src = "manifest (server down — txt-edited own-invfiles may stay stale)"
		n = 0
		for item_id, invfile, choice in actives:
			try:
				assets.activate(item_id, invfile, choice)
				n += 1
			except Exception as e:  # noqa: BLE001
				print(f"  overlay resync FAIL {item_id}: {e}")
		print(f"overlay resynced for {n} active alternates via {src} "
		      "(Push to game + Full reload to see)")
	if rows:
		# contact sheet: old | new per row, 3x zoom, chunked into columns of 20
		Z, GAP, PER_COL = 3, 8, 20
		cell_w = max(r[1].width for r in rows) * Z * 2 + GAP * 3
		cell_h = max(r[1].height for r in rows) * Z + GAP
		cols = (len(rows) + PER_COL - 1) // PER_COL
		sheet = Image.new("RGBA", (cell_w * cols, cell_h * PER_COL), (46, 42, 36, 255))
		for i, (rel, old, new) in enumerate(rows):
			cx, cy = (i // PER_COL) * cell_w, (i % PER_COL) * cell_h
			sheet.alpha_composite(old.resize((old.width * Z, old.height * Z), Image.NEAREST), (cx + GAP, cy))
			sheet.alpha_composite(new.resize((new.width * Z, new.height * Z), Image.NEAREST),
			                      (cx + GAP * 2 + old.width * Z, cy))
		sheet.save(a.sheet)
		print(f"before/after sheet -> {a.sheet}")


if __name__ == "__main__":
	main()
