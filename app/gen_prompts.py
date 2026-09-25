"""Per-item generation prompts for the Enhance picker: each item remembers its own restyle
text, enhance 'nudge', negative-prompt override, and last-used method -- fixing the old shared
`GEN` object (workflow.js) that bled state across items when you switched and was lost on
every page reload.

Storage: <workspace>/gen_prompts.json keyed by item id -> {restyle, nudge, negative, last_method}.
"""

from __future__ import annotations

import json
import os

import app.assets as assets

STORE = os.path.join(assets.WORKSPACE, "gen_prompts.json")

DEFAULTS = {"restyle": "", "nudge": "", "negative": "", "last_method": ""}


def _load() -> dict:
	try:
		with open(STORE, encoding="utf-8") as f:
			return json.load(f)
	except (OSError, ValueError):
		return {}


def _save(d: dict) -> None:
	os.makedirs(os.path.dirname(STORE), exist_ok=True)
	tmp = STORE + ".tmp"
	with open(tmp, "w", encoding="utf-8") as f:
		json.dump(d, f, indent=2, ensure_ascii=False)
	os.replace(tmp, STORE)


def get(item_id: str) -> dict:
	"""DEFAULTS merged with the saved record -- never None; the UI always needs something to
	bind its textboxes/radios to."""
	rec = _load().get(item_id) or {}
	return {**DEFAULTS, **{k: v for k, v in rec.items() if k in DEFAULTS}}


def update(item_id: str, patch: dict) -> dict:
	"""Patch-merge (not full-replace) so a nudge-only edit never clobbers a restyle text saved
	from a different field/debounce tick."""
	d = _load()
	cur = get(item_id)
	for k, v in (patch or {}).items():
		if k in DEFAULTS:
			cur[k] = str(v)
	d[item_id] = cur
	_save(d)
	return cur


def reset(item_id: str) -> dict:
	d = _load()
	d.pop(item_id, None)
	_save(d)
	return dict(DEFAULTS)
