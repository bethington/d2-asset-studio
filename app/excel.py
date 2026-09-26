"""uniqueitems.bin / setitems.bin cell editor — the plan's §7.3 "txt sliver".

The game always loads the compiled .bin (DATATBLS_LoadFromBin = TRUE,
D2Common DataTbls.cpp:24), and the .bin format is simply
[int32 recordCount][recordCount x record] (DATATBLS_CompileTxt,
DataTbls.cpp:607).  UniqueItemsTxt is 0x14C bytes and SetItemsTxt 0x1B8
(ItemsTbls.h); the cells we edit are fixed char[32] strings or single
signed colour bytes inside the record, so a targeted edit is a direct
byte-patch of the row — no txt->bin recompile needed.

String cells (uniques only): invfile / flippyfile.
Colour cells (uniques + sets): nChrTransform / nInvTransform — a colors.txt
row index 0..20, or -1 for "no tint".

Edited bins are written to the overlay tree (atomic temp+rename); the
patch.mpq build picks them up automatically.  When every edit is reverted
the overlay bin is removed so the stock file resolves again.
"""

from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass

from app.assets import OVERLAY, _load_manifest, _save_manifest
from pyd2 import colortransform
from pyd2.mpq import read_effective


@dataclass(frozen=True)
class Table:
	name: str      # txt table name; also the manifest `txt_edits` key
	bin_rel: str
	rec_size: int
	off_chr: int   # int8 nChrTransform (on-character colour)
	off_inv: int   # int8 nInvTransform (inventory colour)


UNIQUE_TABLE = Table("uniqueitems", "data\\global\\excel\\uniqueitems.bin", 0x14C, 0x38, 0x39)
SET_TABLE = Table("setitems", "data\\global\\excel\\setitems.bin", 0x1B8, 0x40, 0x41)
TABLES = {t.name: t for t in (UNIQUE_TABLE, SET_TABLE)}

BIN_REL = UNIQUE_TABLE.bin_rel
REC_SIZE = UNIQUE_TABLE.rec_size  # sizeof(UniqueItemsTxt)
OFF_NAME = 0x02  # char[32]  szName        ("index" column) — same offset in both records
OFF_FLIPPY = 0x3A  # char[32]  szFlippyFile (unique)
OFF_INVFILE = 0x5A  # char[32]  szInvFile   (unique)
STR_LEN = 32  # 31 chars + NUL

FIELDS = {"invfile": OFF_INVFILE, "flippyfile": OFF_FLIPPY}

_VALID_VALUE = re.compile(r"^[A-Za-z0-9_\-]{0,31}$")

# tint side -> (Table offset attribute, manifest key)
_TINT_SIDES = {"inv": ("off_inv", "invtransform"), "chr": ("off_chr", "chrtransform")}
_NO_TINT = 0xFF  # int8 -1


def _overlay_bin_path(tbl: Table = UNIQUE_TABLE) -> str:
	return os.path.join(OVERLAY, *tbl.bin_rel.split("\\"))


def stock_bin(tbl: Table = UNIQUE_TABLE) -> bytes:
	data, _src = read_effective(tbl.bin_rel)
	return data


def load_bin(tbl: Table = UNIQUE_TABLE) -> bytes:
	"""Overlay copy if present (so edits stack), else the stock MPQ file."""
	p = _overlay_bin_path(tbl)
	if os.path.exists(p):
		with open(p, "rb") as f:
			return f.read()
	return stock_bin(tbl)


def _check(data: bytes, tbl: Table = UNIQUE_TABLE) -> int:
	n = struct.unpack_from("<i", data, 0)[0]
	if len(data) != 4 + n * tbl.rec_size:
		raise ValueError(f"{os.path.basename(tbl.bin_rel)} size mismatch: {len(data)} bytes for {n} records "
		                 f"(expected {4 + n * tbl.rec_size}) — schema drift?")
	return n


def _cstr(data: bytes, off: int, length: int = STR_LEN) -> str:
	return data[off:off + length].split(b"\x00", 1)[0].decode("latin-1")


def find_row(data: bytes, unique_name: str, tbl: Table = UNIQUE_TABLE) -> int:
	n = _check(data, tbl)
	want = unique_name.strip()
	for i in range(n):
		if _cstr(data, 4 + i * tbl.rec_size + OFF_NAME) == want:
			return i
	# tolerate case drift between txt and bin
	want_l = want.lower()
	for i in range(n):
		if _cstr(data, 4 + i * tbl.rec_size + OFF_NAME).lower() == want_l:
			return i
	return -1


def get_unique(unique_name: str) -> dict | None:
	"""Current (possibly edited) string cells for one unique, plus stock values."""
	data = load_bin()
	row = find_row(data, unique_name)
	if row < 0:
		return None
	base = 4 + row * REC_SIZE
	stock = stock_bin()
	sbase = 4 + row * REC_SIZE
	return {
		"row": row,
		"invfile": _cstr(data, base + OFF_INVFILE),
		"flippyfile": _cstr(data, base + OFF_FLIPPY),
		"stock_invfile": _cstr(stock, sbase + OFF_INVFILE),
		"stock_flippyfile": _cstr(stock, sbase + OFF_FLIPPY),
		"edited": os.path.exists(_overlay_bin_path()),
	}


def _patch(tbl: Table, name: str, cells: list[tuple[int, bytes]], record: dict) -> None:
	"""Write `cells` ([(offset within the record, bytes)]) into one row of the overlay bin.

	`record` maps manifest keys to the value to remember for this row's edit (None = no longer
	edited, drop the key).  When the whole bin equals stock again the overlay copy is removed.
	"""
	data = bytearray(load_bin(tbl))
	row = find_row(bytes(data), name, tbl)
	if row < 0:
		raise KeyError(f"{tbl.name} entry {name!r} not found in {os.path.basename(tbl.bin_rel)}")

	stock = stock_bin(tbl)
	if len(stock) != len(data):
		raise ValueError("overlay bin and stock bin disagree on size — stale overlay? "
		                 "delete the overlay excel file and re-apply edits")

	rec = 4 + row * tbl.rec_size
	for off, blob in cells:
		data[rec + off:rec + off + len(blob)] = blob

	m = _load_manifest()
	owned = set(m.get("owned_overlay_files", []))
	edits = m.setdefault("txt_edits", {}).setdefault(tbl.name, {})
	p = _overlay_bin_path(tbl)

	if bytes(data) == stock:
		# fully reverted — drop the overlay copy
		if os.path.exists(p):
			os.remove(p)
		owned.discard(tbl.bin_rel)
		edits.pop(name, None)
		if not edits:
			m["txt_edits"].pop(tbl.name, None)
	else:
		os.makedirs(os.path.dirname(p), exist_ok=True)
		tmp = p + ".tmp"
		with open(tmp, "wb") as f:
			f.write(data)
		os.replace(tmp, p)  # atomic — the game may read mid-frame
		owned.add(tbl.bin_rel)
		e = edits.setdefault(name, {})
		for key, value in record.items():
			if value:
				e[key] = value
			else:
				e.pop(key, None)
		if not e:
			edits.pop(name, None)

	m["owned_overlay_files"] = sorted(owned)
	_save_manifest(m)


def set_unique_field(unique_name: str, field: str, value: str) -> dict:
	"""Patch one string cell for one unique row and write the overlay bin.

	value '' reverts the cell to its stock value.  When the whole bin equals
	stock again the overlay copy is removed.  Returns the new get_unique().
	"""
	if field not in FIELDS:
		raise ValueError(f"field must be one of {sorted(FIELDS)}")
	if not _VALID_VALUE.match(value or ""):
		raise ValueError("value must be 0-31 chars of [A-Za-z0-9_-]")

	row = find_row(load_bin(), unique_name)
	if row < 0:
		raise KeyError(f"unique {unique_name!r} not found in uniqueitems.bin")
	off = FIELDS[field]
	stock = stock_bin()
	cell = (value or _cstr(stock, 4 + row * REC_SIZE + off)).encode("latin-1")
	_patch(UNIQUE_TABLE, unique_name, [(off, cell.ljust(STR_LEN, b"\x00"))], {field: value})
	return get_unique(unique_name)


def unique_overrides() -> dict:
	"""{unique name: {field: value}} — the manifest's record of active bin edits."""
	return dict(_load_manifest().get("txt_edits", {}).get("uniqueitems", {}))


# ---- tint cells (uniques + sets) ----------------------------------------

def _table(table: str) -> Table:
	try:
		return TABLES[table]
	except KeyError:
		raise ValueError(f"table must be one of {sorted(TABLES)}") from None


def _decode_tint(byte: int) -> str:
	"""Signed colour byte -> colour code; '' for no tint (-1 / out of range)."""
	codes = colortransform.COLOR_CODES
	return codes[byte] if byte < len(codes) else ""


def _encode_tint(value: str) -> int:
	v = (value or "").strip().lower()
	if v in ("", "none"):
		return _NO_TINT
	if v not in colortransform.COLOR_INDEX:
		raise ValueError(f"unknown colour code {value!r} (expected one of "
		                 f"{', '.join(colortransform.COLOR_CODES)}, 'none' or 'stock')")
	return colortransform.COLOR_INDEX[v]


def get_tint(table: str, name: str) -> dict | None:
	"""Current (possibly edited) tint of one unique/set row plus its stock values.

	inv/chr are colour codes, '' = no tint.  `linked` = both sides currently match, i.e. one
	colour picker can drive them together without clobbering a deliberate difference.
	"""
	tbl = _table(table)
	data = load_bin(tbl)
	row = find_row(data, name, tbl)
	if row < 0:
		return None
	stock = stock_bin(tbl)
	base = 4 + row * tbl.rec_size
	cur = {s: _decode_tint(data[base + getattr(tbl, attr)]) for s, (attr, _k) in _TINT_SIDES.items()}
	stk = {s: _decode_tint(stock[base + getattr(tbl, attr)]) for s, (attr, _k) in _TINT_SIDES.items()}
	return {
		"row": row,
		"inv": cur["inv"], "chr": cur["chr"],
		"stock_inv": stk["inv"], "stock_chr": stk["chr"],
		"linked": cur["inv"] == cur["chr"],
		"edited": cur != stk,
	}


def set_tint(table: str, name: str, inv: str | None = None, chr: str | None = None) -> dict:  # noqa: A002
	"""Set the inventory and/or on-character tint of one unique/set row.

	Each side: None = leave alone, 'stock' = restore the stock byte, ''/'none' = no tint
	(-1), else a colour code.  Everything is validated before anything is written.
	Returns the new get_tint().
	"""
	tbl = _table(table)
	want = {"inv": inv, "chr": chr}
	cur = get_tint(table, name)
	if cur is None:
		raise KeyError(f"{tbl.name} entry {name!r} not found in {os.path.basename(tbl.bin_rel)}")
	stock = stock_bin(tbl)
	base = 4 + cur["row"] * tbl.rec_size

	cells, record = [], {}
	for side, value in want.items():
		if value is None:
			continue
		attr, key = _TINT_SIDES[side]
		off = getattr(tbl, attr)
		stock_byte = stock[base + off]
		new_byte = stock_byte if value == "stock" else _encode_tint(value)
		cells.append((off, bytes([new_byte])))
		# remembered only while it differs from stock; 'none' spells -1 in the manifest
		record[key] = None if new_byte == stock_byte else (_decode_tint(new_byte) or "none")
	if cells:
		_patch(tbl, name, cells, record)
	return get_tint(table, name)


def tint_overrides(table: str) -> dict:
	"""{name: {'invtransform'/'chrtransform': code or 'none'}} — active tint edits in the manifest."""
	tbl = _table(table)
	out = {}
	for name, e in _load_manifest().get("txt_edits", {}).get(tbl.name, {}).items():
		t = {k: e[k] for k in ("invtransform", "chrtransform") if k in e}
		if t:
			out[name] = t
	return out
