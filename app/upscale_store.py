"""Storage for AI-upscale variants (the history strip; see UPSCALE_3D_PANEL_DESIGN §4).

Layout under <workspace>/upscales/<item_key>/ :
  <vid>-master.png    hi-res ~1024 RGBA master (fed to Meshy; kept internally)
  <vid>.png           canonical art = EXACTLY 2x the original art canvas (displayed/stored)
  index.json          {variants:[record...], selected: vid|None}

A record: {id, seed, engine, fidelity, creativity, denoise, cn, prompt, negative, size, ts}.
Item ids contain slashes (e.g. "base/armor/xtg"); keyed to a filesystem-safe folder name.
"""

from __future__ import annotations

import json
import os
import re

import app.assets as assets

ROOT = os.path.join(assets.WORKSPACE, "upscales")


def _key(item_id: str) -> str:
	return re.sub(r"[^A-Za-z0-9_.-]", "__", item_id)


def _dir(item_id: str) -> str:
	return os.path.join(ROOT, _key(item_id))


def _index_path(item_id: str) -> str:
	return os.path.join(_dir(item_id), "index.json")


def load(item_id: str) -> dict:
	try:
		with open(_index_path(item_id), encoding="utf-8") as f:
			return json.load(f)
	except (OSError, ValueError):
		return {"variants": [], "selected": None}


def _write(item_id: str, idx: dict) -> None:
	os.makedirs(_dir(item_id), exist_ok=True)
	tmp = _index_path(item_id) + ".tmp"
	with open(tmp, "w", encoding="utf-8") as f:
		json.dump(idx, f, indent=2)
	os.replace(tmp, _index_path(item_id))


def add_variant(item_id: str, *, master_png: bytes, canonical_png: bytes, meta: dict) -> dict:
	idx = load(item_id)
	n = 1 + max([int(re.sub(r"\D", "", v["id"]) or 0) for v in idx["variants"]], default=0)
	vid = f"v{n}"
	os.makedirs(_dir(item_id), exist_ok=True)
	with open(os.path.join(_dir(item_id), f"{vid}-master.png"), "wb") as f:
		f.write(master_png)
	with open(os.path.join(_dir(item_id), f"{vid}.png"), "wb") as f:
		f.write(canonical_png)
	rec = {"id": vid, **meta}
	idx["variants"].append(rec)
	idx["selected"] = vid  # newest becomes current selection by default
	_write(item_id, idx)
	return rec


def list_variants(item_id: str) -> dict:
	return load(item_id)


def variant_png(item_id: str, vid: str, which: str = "canonical") -> bytes | None:
	name = f"{vid}-master.png" if which == "master" else f"{vid}.png"
	p = os.path.join(_dir(item_id), name)
	if not os.path.exists(p):
		return None
	with open(p, "rb") as f:
		return f.read()


def delete_variant(item_id: str, vid: str) -> dict:
	idx = load(item_id)
	idx["variants"] = [v for v in idx["variants"] if v["id"] != vid]
	if idx.get("selected") == vid:
		idx["selected"] = idx["variants"][-1]["id"] if idx["variants"] else None
	for suffix in (f"{vid}.png", f"{vid}-master.png"):
		try:
			os.remove(os.path.join(_dir(item_id), suffix))
		except OSError:
			pass
	_write(item_id, idx)
	return idx


def select_variant(item_id: str, vid: str) -> dict:
	idx = load(item_id)
	if any(v["id"] == vid for v in idx["variants"]):
		idx["selected"] = vid
		_write(item_id, idx)
	return idx


def selected_record(item_id: str) -> dict | None:
	idx = load(item_id)
	vid = idx.get("selected")
	return next((v for v in idx["variants"] if v["id"] == vid), None)
