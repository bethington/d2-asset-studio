"""Bulk-caption the item catalog with Florence-2 (GPU via ComfyUI) into descriptions.json.

Fills the workflow panel's "From description" tab for every item ahead of time, and builds the
description corpus for generating wholly-new items later. Resumable: items that already have a
description are skipped (user-edited ones are never touched).

    python scripts/caption_catalog.py [--limit N] [--category base|unique|set] [--dry]

Runs against the local studio server for the item list + art (server must be up on :5001) and
talks to ComfyUI directly for the captions. ~6s/item warm on the 3090 => full 1400-item catalog
is ~2.5h of GPU time; the studio's own generations share that GPU, so run it when you're not
actively upscaling.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app.describe as describe  # noqa: E402

SERVER = os.environ.get("STUDIO_URL", "http://127.0.0.1:5001")


def fetch_json(path):
	with urllib.request.urlopen(f"{SERVER}{path}", timeout=30) as r:
		return json.loads(r.read())


def fetch_png(path):
	with urllib.request.urlopen(f"{SERVER}{path}", timeout=30) as r:
		return r.read()


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--limit", type=int, default=0, help="stop after N new captions")
	ap.add_argument("--category", default=None, help="only base / unique / set")
	ap.add_argument("--dry", action="store_true", help="list what would be captioned")
	a = ap.parse_args()

	data = fetch_json("/api/items")
	items = data if isinstance(data, list) else data.get("items", [])
	if a.category:
		items = [i for i in items if i.get("category") == a.category]
	todo = [i for i in items if not describe.get(i["id"])]
	print(f"{len(items)} items, {len(todo)} without descriptions")
	if a.dry:
		for i in todo[:40]:
			print(" ", i["id"], "-", i["name"])
		return

	done = errs = 0
	t0 = time.time()
	for it in todo:
		if a.limit and done >= a.limit:
			break
		try:
			png = fetch_png(f"/api/item/{urllib.request.quote(it['id'], safe='')}/original.png")
			t = time.time()
			describe.caption_and_store(it["id"], png, ts=time.time(),
			                           item_name=it.get("name"), item_kind=it.get("type"))
			done += 1
			print(f"[{done}/{len(todo)}] {it['id']} ({time.time() - t:.1f}s)")
		except KeyboardInterrupt:
			print("interrupted — progress is saved, rerun to resume")
			return
		except Exception as e:  # noqa: BLE001
			errs += 1
			print(f"  ERR {it['id']}: {e}")
			if errs > 10 and errs > done:
				print("too many consecutive errors — is ComfyUI up?")
				return
	print(f"done: {done} captioned, {errs} errors, {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
	main()
