"""uniqueitems/setitems .bin edits must survive -- and never shadow -- a game update.

Edits (Set invfile / tint) used to live as a frozen full copy of the stock .bin in the overlay. After
a game update that copy would replace the game's NEW table with the old one. The manifest already
records each edit as (row name, field, value); the overlay .bin is now re-derived from the CURRENT
stock bin + that record (excel.rebuild_overlay_bins), at push time, at startup and on a detected
game update. Edits whose row vanished are reported and kept (never written); a table whose record
layout changed is left stock rather than shipping a wrong-format copy.

Synthetic stock bins; writes go to a throw-away workspace (the fixture refuses to run against any
other workspace, because it resets the manifest).
Usage: python -m pytest tests/test_bin_rebuild.py
"""

import os
import struct
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_TMP = tempfile.mkdtemp(prefix="asset_studio_test_")
os.environ["ASSET_STUDIO_WS"] = _TMP  # must be set before app.assets import

import app.assets as assets  # noqa: E402
import app.excel as excel  # noqa: E402

U, S = excel.UNIQUE_TABLE, excel.SET_TABLE


def make_bin(tbl, rows):
	"""rows: dicts {name, invfile, flippy, chr, inv} -> a compiled-bin image."""
	out = bytearray(struct.pack("<i", len(rows)))
	for r in rows:
		rec = bytearray(tbl.rec_size)
		rec[excel.OFF_NAME:excel.OFF_NAME + 32] = r["name"].encode("latin-1").ljust(32, b"\0")
		if tbl is U:
			rec[excel.OFF_FLIPPY:excel.OFF_FLIPPY + 32] = r.get("flippy", "").encode().ljust(32, b"\0")
			rec[excel.OFF_INVFILE:excel.OFF_INVFILE + 32] = r.get("invfile", "").encode().ljust(32, b"\0")
		rec[tbl.off_chr] = r.get("chr", 0xFF) & 0xFF
		rec[tbl.off_inv] = r.get("inv", 0xFF) & 0xFF
		out += rec
	return bytes(out)


def rows_of(tbl, data):
	n = struct.unpack_from("<i", data, 0)[0]
	out = {}
	for i in range(n):
		b = 4 + i * tbl.rec_size
		out[excel._cstr(data, b + excel.OFF_NAME)] = {
			"invfile": excel._cstr(data, b + excel.OFF_INVFILE) if tbl is U else None,
			"flippy": excel._cstr(data, b + excel.OFF_FLIPPY) if tbl is U else None,
			"chr": data[b + tbl.off_chr], "inv": data[b + tbl.off_inv]}
	return out


class World:
	def __init__(self):
		self.stock = {}

	def overlay(self, tbl):
		with open(excel._overlay_bin_path(tbl), "rb") as f:
			return f.read()


@pytest.fixture
def w(monkeypatch):
	if not os.path.abspath(assets.WORKSPACE).startswith(os.path.abspath(tempfile.gettempdir())):
		pytest.skip("workspace is not a throw-away temp dir; refusing to reset its manifest")
	world = World()
	world.stock[U.name] = make_bin(U, [
		{"name": "Alpha", "invfile": "invalpha", "flippy": "flpalpha", "chr": 3, "inv": 3},
		{"name": "Beta", "invfile": "invbeta", "flippy": "flpbeta", "chr": 0xFF, "inv": 0xFF}])
	world.stock[S.name] = make_bin(S, [{"name": "Gamma", "chr": 5, "inv": 5}])
	monkeypatch.setattr(excel, "stock_bin", lambda tbl=U: world.stock[tbl.name])

	def reset():
		for t in (U, S):
			p = excel._overlay_bin_path(t)
			if os.path.exists(p):
				os.remove(p)
		assets._save_manifest({"version": 1, "assets": {}, "owned_overlay_files": []})
	reset()
	yield world
	reset()


def test_update_keeps_the_games_changes_and_reapplies_our_edits(w):
	excel.set_unique_field("Alpha", "invfile", "mine")
	excel.set_tint(U.name, "Beta", inv="cred")
	frozen = w.overlay(U)                                   # the old frozen copy (2 rows)
	# game update: Alpha's flippy changes, a new unique is inserted first, Beta moves
	w.stock[U.name] = make_bin(U, [
		{"name": "Newcomer", "invfile": "invnew", "flippy": "flpnew", "chr": 1, "inv": 1},
		{"name": "Beta", "invfile": "invbeta2", "flippy": "flpbeta", "chr": 0xFF, "inv": 0xFF},
		{"name": "Alpha", "invfile": "invalpha", "flippy": "flpalpha_v2", "chr": 3, "inv": 3}])
	rep = excel.rebuild_overlay_bins()
	rows = rows_of(U, w.overlay(U))
	assert w.overlay(U) != frozen and len(rows) == 3, "the frozen old table must not shadow the update"
	assert rows["Alpha"]["invfile"] == "mine"               # our edit re-applied
	assert rows["Alpha"]["flippy"] == "flpalpha_v2"         # the game's change to another cell survives
	assert rows["Beta"]["invfile"] == "invbeta2"            # ...and to a cell we never touched
	assert rows["Beta"]["inv"] == excel._encode_tint("cred")
	assert rows["Newcomer"]["invfile"] == "invnew"
	assert sorted(rep[U.name]["applied"]) == ["Alpha", "Beta"] and rep[U.name]["missing"] == []


def test_edit_for_a_vanished_row_is_reported_kept_and_not_written(w):
	excel.set_unique_field("Alpha", "invfile", "mine")
	excel.set_unique_field("Beta", "invfile", "mine2")
	w.stock[U.name] = make_bin(U, [{"name": "Alpha", "invfile": "invalpha", "flippy": "f"}])
	rep = excel.rebuild_overlay_bins()
	assert rep[U.name]["missing"] == ["Beta"] and rep[U.name]["applied"] == ["Alpha"]
	assert list(rows_of(U, w.overlay(U))) == ["Alpha"]
	assert set(excel.unique_overrides()) == {"Alpha", "Beta"}, "the record of the edit is never discarded"
	assert excel.edit_report() == {U.name: {"missing": ["Beta"], "invalid": [], "schema_drift": False}}


def test_record_layout_change_falls_back_to_stock_never_a_wrong_format_copy(w):
	excel.set_unique_field("Alpha", "invfile", "mine")
	w.stock[U.name] = make_bin(U, [{"name": "Alpha"}]) + b"\0" * 7        # size no longer 4 + n*rec
	rep = excel.rebuild_overlay_bins()
	assert rep[U.name]["schema_drift"] is True
	assert not os.path.exists(excel._overlay_bin_path(U)), "no overlay -> the game's own table is used"
	assert U.bin_rel not in assets._load_manifest()["owned_overlay_files"]
	assert excel.unique_overrides() == {"Alpha": {"invfile": "mine"}}
	assert excel.edit_report()[U.name]["schema_drift"] is True


def test_edits_the_game_adopted_leave_no_overlay(w):
	excel.set_unique_field("Alpha", "invfile", "mine")
	w.stock[U.name] = make_bin(U, [
		{"name": "Alpha", "invfile": "mine", "flippy": "flpalpha", "chr": 3, "inv": 3}])
	excel.rebuild_overlay_bins()
	assert not os.path.exists(excel._overlay_bin_path(U))


def test_owned_overlay_without_edits_is_removed(w):
	excel.set_unique_field("Alpha", "invfile", "mine")
	m = assets._load_manifest()
	m["txt_edits"] = {}                                                    # record gone, file left behind
	assets._save_manifest(m)
	excel.rebuild_overlay_bins()
	assert not os.path.exists(excel._overlay_bin_path(U))


def test_an_unowned_overlay_file_is_left_alone(w):
	os.makedirs(os.path.dirname(excel._overlay_bin_path(U)), exist_ok=True)
	with open(excel._overlay_bin_path(U), "wb") as f:
		f.write(b"someone else's file")
	assert excel.rebuild_overlay_bins() == {}
	assert w.overlay(U) == b"someone else's file"


def test_rebuild_is_idempotent_and_covers_both_tables(w):
	excel.set_unique_field("Alpha", "invfile", "mine")
	excel.set_tint(S.name, "Gamma", inv="none")
	a = (w.overlay(U), w.overlay(S))
	excel.rebuild_overlay_bins()
	excel.rebuild_overlay_bins()
	assert (w.overlay(U), w.overlay(S)) == a
	assert rows_of(S, w.overlay(S))["Gamma"]["inv"] == 0xFF and rows_of(S, w.overlay(S))["Gamma"]["chr"] == 5


def test_hand_edited_bad_values_are_skipped_and_reported(w):
	excel.set_unique_field("Alpha", "invfile", "mine")
	m = assets._load_manifest()
	m["txt_edits"][U.name]["Alpha"]["invfile"] = "bad name!"               # fails the same validation as a write
	m["txt_edits"][U.name]["Alpha"]["invtransform"] = "notacolour"
	assets._save_manifest(m)
	rep = excel.rebuild_overlay_bins()
	assert sorted(rep[U.name]["invalid"]) == ["Alpha.invfile", "Alpha.invtransform"]
	assert not os.path.exists(excel._overlay_bin_path(U)), "nothing valid to apply -> stock table"


def test_build_patch_mpq_rederives_bins_before_packing(w, monkeypatch):
	calls = []
	monkeypatch.setattr(excel, "rebuild_overlay_bins", lambda: calls.append("rebuilt") or {})
	monkeypatch.setattr(assets, "build_archive", lambda *a, **k: calls.append("packed"))
	assets.build_patch_mpq()
	assert calls == ["rebuilt", "packed"]
