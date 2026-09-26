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
		rec[0x28:0x2C] = r.get("code", "").encode("latin-1").ljust(4, b" ")   # base item code (both tables)
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


@pytest.fixture
def w_live():
	"""The real server + live PD2 tables (read-only) against the throw-away workspace."""
	if not os.path.abspath(assets.WORKSPACE).startswith(os.path.abspath(tempfile.gettempdir())):
		pytest.skip("workspace is not a throw-away temp dir; refusing to reset its manifest")
	try:
		import app.server as server
		server.catalog()
	except Exception as e:  # noqa: BLE001 - no MPQs / StormLib on this machine
		pytest.skip(f"PD2 MPQs unavailable: {e}")

	def reset():
		for t in (U, S):
			p = excel._overlay_bin_path(t)
			if os.path.exists(p):
				os.remove(p)
		assets._save_manifest({"version": 1, "assets": {}, "owned_overlay_files": []})
		server.invalidate_catalog()
	reset()
	yield server, server.flask_app.test_client()
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


# ---- rows that share a name: an edit must hit the row it was made for --------------------------
# uniqueitems has names on several rows (Azurewrath and Crackleshot each exist on TWO base items).
# Edits are keyed by the catalog item's `edit_key`: the bare name = the FIRST row of that name (so
# existing manifest edits keep working untouched), `Name#code` = the first row of that name on that
# base item.

TWINS = [
	{"name": "Twin", "code": "crs", "invfile": "invcrsu", "flippy": "flpa", "chr": 0xFF, "inv": 0xFF},
	{"name": "Other", "code": "cap", "invfile": "invo", "flippy": "flpo", "chr": 0xFF, "inv": 0xFF},
	{"name": "Twin", "code": "7cr", "invfile": "invcrs", "flippy": "flpb", "chr": 0xFF, "inv": 0xFF}]


def _twins(w):
	w.stock[U.name] = make_bin(U, TWINS)


def _changed_rows(stock, cur, n):
	sz = U.rec_size
	return [i for i in range(n) if stock[4 + i * sz:4 + (i + 1) * sz] != cur[4 + i * sz:4 + (i + 1) * sz]]


def test_find_row_resolves_a_name_and_a_name_hash_code(w):
	_twins(w)
	data = w.stock[U.name]
	assert excel.find_row(data, "Twin") == 0                    # bare name: the FIRST row
	assert excel.find_row(data, "Twin#crs") == 0
	assert excel.find_row(data, "Twin#7cr") == 2                # ...or the row on that base
	assert excel.find_row(data, "twin#7CR") == 2                # case-insensitive like a bare name
	assert excel.find_row(data, "Twin#zzz") == -1
	assert excel.find_row(data, "Other#7cr") == -1              # right code, wrong name


def test_a_name_that_literally_contains_hash_code_still_wins(w):
	w.stock[U.name] = make_bin(U, [{"name": "Odd#abc", "code": "cap"}, {"name": "Odd", "code": "abc"}])
	assert excel.find_row(w.stock[U.name], "Odd#abc") == 0


def test_an_edit_keyed_by_hash_code_touches_only_that_row(w):
	_twins(w)
	excel.set_unique_field("Twin#7cr", "invfile", "mine")
	assert excel.get_unique("Twin#7cr")["invfile"] == "mine"
	assert excel.get_unique("Twin")["invfile"] == "invcrsu"      # the first Twin is untouched
	assert excel.unique_overrides() == {"Twin#7cr": {"invfile": "mine"}}
	assert _changed_rows(w.stock[U.name], w.overlay(U), 3) == [2], "only the 7cr row may differ"


def test_a_bare_name_edit_keeps_targeting_the_first_row(w):
	"""Existing manifests only have bare names: they must mean exactly what they always meant."""
	_twins(w)
	excel.set_unique_field("Twin", "invfile", "mine")
	assert _changed_rows(w.stock[U.name], w.overlay(U), 3) == [0]


def test_tint_edits_use_the_same_keys(w):
	_twins(w)
	excel.set_tint(U.name, "Twin#7cr", inv="cred")
	assert excel.get_tint(U.name, "Twin#7cr")["inv"] == "cred"
	assert excel.get_tint(U.name, "Twin")["inv"] == ""
	assert excel.tint_overrides(U.name) == {"Twin#7cr": {"invtransform": "cred"}}


def test_a_hash_code_edit_follows_its_row_when_the_game_reorders_the_table(w):
	_twins(w)
	excel.set_unique_field("Twin#7cr", "invfile", "mine")
	w.stock[U.name] = make_bin(U, [TWINS[2], TWINS[1], TWINS[0], {"name": "New", "code": "cap"}])  # update
	rep = excel.rebuild_overlay_bins()
	assert rep[U.name]["applied"] == ["Twin#7cr"] and rep[U.name]["missing"] == []
	cur = w.overlay(U)
	assert excel._cstr(cur, 4 + 0 * U.rec_size + excel.OFF_INVFILE) == "mine"    # the 7cr row, now first
	assert excel._cstr(cur, 4 + 2 * U.rec_size + excel.OFF_INVFILE) == "invcrsu"  # the crs row, untouched
	assert excel.edit_report() == {}


def test_a_hash_code_edit_for_a_vanished_base_is_reported_not_misapplied(w):
	_twins(w)
	excel.set_unique_field("Twin#7cr", "invfile", "mine")
	w.stock[U.name] = make_bin(U, [TWINS[0], TWINS[1]])                  # the 7cr row is gone
	rep = excel.rebuild_overlay_bins()
	assert rep[U.name]["missing"] == ["Twin#7cr"] and rep[U.name]["applied"] == []
	assert not os.path.exists(excel._overlay_bin_path(U)), "must NOT fall back to editing the first Twin"


def test_the_server_edits_the_picked_azurewrath_not_the_first(w_live):
	"""Live PD2 data: Azurewrath is on two base items (crs, 7cr). A tint edit on the 7cr item must
	patch that row only and show on that item only."""
	from urllib.parse import quote
	server, client = w_live
	first, second = "unique/Azurewrath", "unique/Azurewrath#7cr"
	by = server.catalog()["by_id"]
	assert by[first]["code"] != by[second]["code"]
	assert by[second]["edit_key"] == "Azurewrath#" + by[second]["code"] and by[first]["edit_key"] == "Azurewrath"
	r = client.post(f"/api/item/{quote(second, safe='')}/txt", json={"field": "tint", "inv": "cred"})
	assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
	by = server.catalog()["by_id"]
	assert by[second]["invtransform"] == "cred", "the picked item shows its edit"
	assert by[first]["invtransform"] != "cred", "the other Azurewrath must not"
	stock, cur = excel.stock_bin(), excel.load_bin()
	n = int.from_bytes(stock[:4], "little")
	target = excel.find_row(stock, "Azurewrath#" + by[second]["code"])
	assert _changed_rows(stock, cur, n) == [target] and target != excel.find_row(stock, "Azurewrath")
