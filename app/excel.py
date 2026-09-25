"""uniqueitems.bin cell editor — the plan's §7.3 "txt sliver".

The game always loads the compiled .bin (DATATBLS_LoadFromBin = TRUE,
D2Common DataTbls.cpp:24), and the .bin format is simply
[int32 recordCount][recordCount x UniqueItemsTxt] (DATATBLS_CompileTxt,
DataTbls.cpp:607).  UniqueItemsTxt is 0x14C bytes (ItemsTbls.h:121); the
cells we edit are fixed char[32] strings inside the record, so a targeted
edit is a direct byte-patch of the row — no txt->bin recompile needed.

Edited bins are written to the overlay tree (atomic temp+rename); the
patch.mpq build picks them up automatically.  When every edit is reverted
the overlay bin is removed so the stock file resolves again.
"""

from __future__ import annotations

import os
import re
import struct

from app.assets import OVERLAY, _load_manifest, _save_manifest
from pyd2.mpq import read_effective

BIN_REL = "data\\global\\excel\\uniqueitems.bin"

REC_SIZE = 0x14C  # sizeof(UniqueItemsTxt)
OFF_NAME = 0x02  # char[32]  szName        ("index" column)
OFF_FLIPPY = 0x3A  # char[32]  szFlippyFile
OFF_INVFILE = 0x5A  # char[32]  szInvFile
STR_LEN = 32  # 31 chars + NUL

FIELDS = {"invfile": OFF_INVFILE, "flippyfile": OFF_FLIPPY}

_VALID_VALUE = re.compile(r"^[A-Za-z0-9_\-]{0,31}$")


def _overlay_bin_path() -> str:
	return os.path.join(OVERLAY, *BIN_REL.split("\\"))


def stock_bin() -> bytes:
	data, _src = read_effective(BIN_REL)
	return data


def load_bin() -> bytes:
	"""Overlay copy if present (so edits stack), else the stock MPQ file."""
	p = _overlay_bin_path()
	if os.path.exists(p):
		with open(p, "rb") as f:
			return f.read()
	return stock_bin()


def _check(data: bytes) -> int:
	n = struct.unpack_from("<i", data, 0)[0]
	if len(data) != 4 + n * REC_SIZE:
		raise ValueError(f"uniqueitems.bin size mismatch: {len(data)} bytes for {n} records "
		                 f"(expected {4 + n * REC_SIZE}) — schema drift?")
	return n


def _cstr(data: bytes, off: int, length: int = STR_LEN) -> str:
	return data[off:off + length].split(b"\x00", 1)[0].decode("latin-1")


def find_row(data: bytes, unique_name: str) -> int:
	n = _check(data)
	want = unique_name.strip()
	for i in range(n):
		if _cstr(data, 4 + i * REC_SIZE + OFF_NAME) == want:
			return i
	# tolerate case drift between txt and bin
	want_l = want.lower()
	for i in range(n):
		if _cstr(data, 4 + i * REC_SIZE + OFF_NAME).lower() == want_l:
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


def set_unique_field(unique_name: str, field: str, value: str) -> dict:
	"""Patch one string cell for one unique row and write the overlay bin.

	value '' reverts the cell to its stock value.  When the whole bin equals
	stock again the overlay copy is removed.  Returns the new get_unique().
	"""
	if field not in FIELDS:
		raise ValueError(f"field must be one of {sorted(FIELDS)}")
	if not _VALID_VALUE.match(value or ""):
		raise ValueError("value must be 0-31 chars of [A-Za-z0-9_-]")

	data = bytearray(load_bin())
	row = find_row(bytes(data), unique_name)
	if row < 0:
		raise KeyError(f"unique {unique_name!r} not found in uniqueitems.bin")

	stock = stock_bin()
	if len(stock) != len(data):
		raise ValueError("overlay bin and stock bin disagree on size — stale overlay? "
		                 "delete the overlay excel file and re-apply edits")

	off = 4 + row * REC_SIZE + FIELDS[field]
	cell = (value or _cstr(stock, off)).encode("latin-1")
	data[off:off + STR_LEN] = cell.ljust(STR_LEN, b"\x00")

	m = _load_manifest()
	owned = set(m.get("owned_overlay_files", []))
	edits = m.setdefault("txt_edits", {}).setdefault("uniqueitems", {})
	p = _overlay_bin_path()

	if bytes(data) == stock:
		# fully reverted — drop the overlay copy
		if os.path.exists(p):
			os.remove(p)
		owned.discard(BIN_REL)
		edits.pop(unique_name, None)
		if not edits:
			m["txt_edits"].pop("uniqueitems", None)
	else:
		os.makedirs(os.path.dirname(p), exist_ok=True)
		tmp = p + ".tmp"
		with open(tmp, "wb") as f:
			f.write(data)
		os.replace(tmp, p)  # atomic — the game may read mid-frame
		owned.add(BIN_REL)
		e = edits.setdefault(unique_name, {})
		if value:
			e[field] = value
		else:
			e.pop(field, None)
		if not e:
			edits.pop(unique_name, None)

	m["owned_overlay_files"] = sorted(owned)
	_save_manifest(m)
	return get_unique(unique_name)


def unique_overrides() -> dict:
	"""{unique name: {field: value}} — the manifest's record of active bin edits."""
	return dict(_load_manifest().get("txt_edits", {}).get("uniqueitems", {}))
