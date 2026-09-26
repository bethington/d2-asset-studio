"""Item catalog — parse PD2's item tables (read live from the MPQs) into a browsable list.

Base items come from weapons/armor/misc.txt (they carry invfile + invwidth/invheight).
Uniques/sets come from uniqueitems/setitems.txt; when their own invfile is blank they
inherit the base item's invfile (resolved via the base 'code').
"""

from __future__ import annotations

import csv
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyd2.mpq import read_effective  # noqa: E402

EXCEL = "data\\global\\excel\\{}.txt"
BASE_TABLES = ("weapons", "armor", "misc")


def _read_table(name: str):
	data, _src = read_effective(EXCEL.format(name))
	text = data.decode("latin-1")
	return list(csv.DictReader(io.StringIO(text), delimiter="\t"))


def _clean(s: str) -> str:
	return (s or "").strip()


_VARGFX_CACHE = None


def clear_caches():
	"""Drop what was derived from the MPQs (called when the game's files change)."""
	global _VARGFX_CACHE
	_VARGFX_CACHE = None


def var_inv_gfx():
	"""itemtype code -> [invfile, ...] for every type with VarInvGfx > 0.

	D2 has a SECOND art-resolution path that overrides the item row: when an item's TYPE carries
	VarInvGfx=N (itemtypes.txt), the game ignores misc/weapons/armor.txt `invfile` and picks one of
	that type's InvGfx1..N at random per item instance (Item.nInvGfxIdx; see D2Common
	ITEMS_GetVarInvGfxCount / ITEMS_GetVarInvGfxString). Rings, amulets, charms and jewels all use
	it -- e.g. misc.txt claims Charm Medium is `invwnd` (a WAND) but the game draws invch2/5/8.
	Trusting the row invfile for these items maps art onto files the game never reads for them.
	"""
	global _VARGFX_CACHE
	if _VARGFX_CACHE is None:
		out = {}
		try:
			for r in _read_table("itemtypes"):
				code = _clean(r.get("Code") or r.get("code"))
				try:
					n = int(_clean(r.get("VarInvGfx") or r.get("varinvgfx")) or 0)
				except ValueError:
					n = 0
				if not code or n <= 0:
					continue
				gfx, seen = [], set()
				for i in range(1, 7):
					v = _clean(r.get(f"InvGfx{i}") or r.get(f"invgfx{i}")).lower()
					if v and v not in seen:      # PVP types repeat one file N times
						seen.add(v)
						gfx.append(v)
				if gfx:
					out[code] = gfx
		except FileNotFoundError:
			pass
		_VARGFX_CACHE = out
	return _VARGFX_CACHE


def set_pieces(set_query: str):
	"""Return [{row, index, base, set}] for every setitems.txt row whose set name (or piece
	index) matches set_query (case-insensitive substring). `row` is the 0-based setitems.txt
	row, `base` is the piece's base item code -- both needed to force the exact set item via
	the /showcase/item {code, setRow} verb (setRow is the row; base must be the piece's base).
	"""
	rows = _read_table("setitems")
	q = set_query.strip().lower()
	out = []
	# The game's setitems array (from the compiled .bin) is 0-based over the rows the
	# compiler KEEPS -- it drops the "Expansion" section separator (a row with no index),
	# so the raw CSV row number is off. Use a running counter of kept rows = the game index.
	game_idx = 0
	for r in rows:
		index = _clean(r.get("index"))
		base = _clean(r.get("item"))
		if not index or index.lower() == "expansion" or not base:
			continue  # a dropped row -- does NOT advance the game index
		if q in _clean(r.get("set")).lower() or q in index.lower():
			out.append({"row": game_idx, "index": index, "set": _clean(r.get("set")),
			            "base": base, "lvl": _clean(r.get("lvl"))})
		game_idx += 1
	return out


def unique_row(index_name: str, base: str | None = None):
	"""Return the game's compiled uniqueitems row index for a unique by its `index` (name), or None.

	Like set_pieces(), the game's uniqueitems array (from the .bin) is 0-based over the rows the
	compiler KEEPS -- it drops the "Expansion" section separator (a row with no index) -- so the
	raw CSV row number is off past that point. We count a running index over kept rows to get the
	game row, which is what the /showcase/item {code, uniqueRow} verb forces (uniqueRow = that row;
	`code` must be the unique's base item code).

	A name can repeat on different base items (Azurewrath, Crackleshot): pass the catalog item's
	base `code` to get the first row of that name AND base; without it, the first row of the name.
	"""
	q = (index_name or "").strip().lower()
	want_base = (base or "").strip().lower()
	game_idx = 0
	for r in _read_table("uniqueitems"):
		index = _clean(r.get("index"))
		if not index or index.lower() == "expansion":
			continue  # a dropped separator row -- does NOT advance the game index
		if index.lower() == q and (not want_base or _clean(r.get("code")).lower() == want_base):
			return {"row": game_idx, "index": index, "base": _clean(r.get("code")),
			        "lvl": _clean(r.get("lvl"))}
		game_idx += 1
	return None


def _base_art(binfo, quality_key):
	"""Inventory art a unique/set draws when its own row has no invfile: the base item's
	`uniqueinvfile` / `setinvfile` (quality_key) if set, else the base `invfile`. Base `cap` is the
	case that matters: unique/set Caps draw `invcapu` (the feathered war-bonnet), plain Caps and
	the War Hat/Shako family (blank unique/set art) draw `invcap`."""
	if not binfo:
		return ""
	return binfo.get(quality_key) or binfo["invfile"]


class _IdBook:
	"""Hands out catalog ids for unique/set rows, some of which share an `index` name.

	Item ids key stored data (workspace/upscales, gen_prompts.json, meshy_links.json), so the FIRST
	row with a name keeps the bare id. A later row with the same base item, art and tint is an exact
	repeat of an earlier one and gets no item (claim() -> None). A later row that differs gets
	`<bare>#<base code>` (`-2`, `-3`... if that id is taken too). Txt row order keeps it stable."""

	def __init__(self):
		self._looks = {}     # bare id -> [(code, invfile, invtransform), ...] already given an item
		self._taken = set()

	def claim(self, bare: str, code: str, invfile: str, invtransform: str):
		look = (code.lower(), invfile.lower(), invtransform.lower())
		looks = self._looks.setdefault(bare, [])
		if look in looks:
			return None
		looks.append(look)
		item_id = bare if len(looks) == 1 else f"{bare}#{code}"
		n = 1
		while item_id in self._taken:
			n += 1
			item_id = f"{bare}#{code}-{n}"
		self._taken.add(item_id)
		return item_id


def build_catalog():
	"""Return (items, by_code). items: list of dicts; by_code: base code -> base item."""
	items = []
	by_code = {}
	ids = _IdBook()

	# --- base items ---
	for table in BASE_TABLES:
		try:
			rows = _read_table(table)
		except FileNotFoundError:
			continue
		for r in rows:
			code = _clean(r.get("code"))
			invfile = _clean(r.get("invfile"))
			name = _clean(r.get("name"))
			if not code or not invfile or not name or name.lower() in ("expansion", "name"):
				continue
			# tier chain (weapons/armor carry all three codes on every row; misc has none)
			norm = _clean(r.get("normcode"))
			uber = _clean(r.get("ubercode"))
			ultra = _clean(r.get("ultracode"))
			tier = 2 if (ultra and code == ultra) else 1 if (uber and code == uber) else 0
			item = {
				"id": f"base/{table}/{code}",
				"category": "base",
				"table": table,
				"name": name,
				"code": code,
				"invfile": invfile,
				"flippyfile": _clean(r.get("flippyfile")),
				"invwidth": int(r.get("invwidth") or 1),
				"invheight": int(r.get("invheight") or 1),
				"invtransform": "",
				"type": _clean(r.get("type")),
				"family": norm or code,  # normcode names the family across all 3 tiers
				"tier": tier,
				# The base row's art for its unique / set quality (blank = same as invfile). A
				# unique/set whose own row has no invfile draws THESE, not `invfile`.
				"unique_invfile": _clean(r.get("uniqueinvfile")),
				"set_invfile": _clean(r.get("setinvfile")),
				# InvTrans = the item-palette transform-file selector (1..8) the game feeds to
				# D2CMP_MixPalette for the INVENTORY sprite recolor (Items.cpp:3841). Uniques/sets
				# inherit it from their base; combined with their own `invtransform` colour code it
				# is what tints e.g. Twitchthroe green.
				"inv_trans": int((r.get("InvTrans") or r.get("invtrans") or "0").strip() or 0),
			}
			# VarInvGfx override: the game draws this item from its TYPE's graphic list, not the
			# row's invfile. Point the item at the first real graphic and expose the whole set; the
			# extra graphics also get their own catalog entries below so each can be enhanced and
			# accepted independently (the game rolls one at random, so ALL must be covered for an
			# item to look consistently re-arted).
			vgfx = var_inv_gfx().get(item["type"])
			if vgfx:
				item["row_invfile"] = invfile      # keep what the txt claimed, for reference
				item["invfile"] = vgfx[0]
				item["var_invfiles"] = list(vgfx)
				# A NON-blank uniqueinvfile/setinvfile still beats the type's list (kept from the row
				# above); a blank one falls through to it. Verified live 2026-09-26: Amulet of the
				# Viper (uniqueinvfile=invvip) draws invvip, while uniques with blank unique art
				# (Nagelring, Mara's Kaleidoscope, Rainbow Facet) draw a per-instance pick from the
				# type's list. Only the Viper has such a base today; sets on these bases are by symmetry.
			items.append(item)
			by_code.setdefault(code, item)
			if vgfx:
				for gi, g in enumerate(vgfx[1:], start=2):
					alt = dict(item)
					alt["id"] = f"base/{table}/{code}#g{gi}"
					alt["name"] = f"{name} (gfx {gi})"
					alt["invfile"] = g
					alt["var_gfx_index"] = gi
					alt["var_gfx_of"] = item["id"]
					items.append(alt)

	# --- uniques ---
	try:
		for r in _read_table("uniqueitems"):
			index = _clean(r.get("index"))
			base = _clean(r.get("code"))
			if not index or not base or index.lower() in ("expansion", "index"):
				continue
			binfo = by_code.get(base)
			invfile = _clean(r.get("invfile")) or _base_art(binfo, "unique_invfile")
			if not invfile:
				continue
			invtransform = _clean(r.get("invtransform"))
			item_id = ids.claim(f"unique/{index}", base, invfile, invtransform)
			if item_id is None:      # exact repeat of an earlier row of the same name
				continue
			items.append({
				"id": item_id,
				# what the .bin edit paths (app/excel.py) key this row by: the bare name for the first
				# row of a name (as it always was), `Name#code` for a later row that differs
				"edit_key": item_id.split("/", 1)[1],
				"category": "unique",
				"table": "uniqueitems",
				"name": index,
				"code": base,
				"invfile": invfile,
				"flippyfile": binfo["flippyfile"] if binfo else "",
				"invwidth": binfo["invwidth"] if binfo else 2,
				"invheight": binfo["invheight"] if binfo else 2,
				"invtransform": invtransform,
				"type": binfo["type"] if binfo else "",
				"family": binfo["family"] if binfo else base,
				"tier": binfo["tier"] if binfo else 0,
				"inv_trans": binfo["inv_trans"] if binfo else 0,
			})
	except FileNotFoundError:
		pass

	# --- sets ---
	try:
		for r in _read_table("setitems"):
			index = _clean(r.get("index"))
			base = _clean(r.get("item"))
			if not index or not base:
				continue
			binfo = by_code.get(base)
			invfile = _clean(r.get("invfile")) or _base_art(binfo, "set_invfile")
			if not invfile:
				continue
			invtransform = _clean(r.get("invtransform"))
			item_id = ids.claim(f"set/{index}", base, invfile, invtransform)
			if item_id is None:      # exact repeat of an earlier row of the same name
				continue
			items.append({
				"id": item_id,
				"edit_key": item_id.split("/", 1)[1],
				"category": "set",
				"table": "setitems",
				"name": index,
				"code": base,
				"invfile": invfile,
				"flippyfile": binfo["flippyfile"] if binfo else "",
				"invwidth": binfo["invwidth"] if binfo else 2,
				"invheight": binfo["invheight"] if binfo else 2,
				"invtransform": invtransform,
				"type": binfo["type"] if binfo else "",
				"family": binfo["family"] if binfo else base,
				"tier": binfo["tier"] if binfo else 0,
				"inv_trans": binfo["inv_trans"] if binfo else 0,
			})
	except FileNotFoundError:
		pass

	return items, by_code
