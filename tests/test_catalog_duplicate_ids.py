"""Catalog item ids must be unique -- several txt rows share one `index` name.

uniqueitems.txt (and in principle setitems.txt) repeats an `index` on purpose: Azurewrath and
Crackleshot each exist twice on DIFFERENT base items (own invfile / base code), while Magefist,
Rainbow Facet, Arm of King Leoric, Cyclopean Roar and Akarats Devotion repeat the SAME row (same
base, art and tint; usually all but one disabled). Ids were built as f"unique/{index}", so
server.catalog()["by_id"] (a dict keyed by id) silently kept one row per name: on the live data
1448 catalog items but only 1433 distinct ids, and the lost rows could not be enhanced, accepted
or spawned.

Rules under test:
  * the FIRST row with a name keeps the bare id (workspace/upscales/<id-key>, gen_prompts.json and
    meshy_links.json are keyed by item id, so existing ids must not move);
  * a later row that is genuinely different (other base code, art or tint) gets `#<base code>`,
    or `#<base code>-2`, `-3`... if that is taken too -- deterministic, in txt row order;
  * a later row identical to an earlier one adds nothing and is dropped;
  * unique_row() / the drop route resolve the row of the item that was picked, not the first
    row with that name.

Fake tables only for the rule tests; the last test reads the live PD2 data (skipped if the MPQs
are unavailable). All writes go to a throw-away workspace.
Usage: python -m pytest tests/test_catalog_duplicate_ids.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_TMP = tempfile.mkdtemp(prefix="asset_studio_test_")
os.environ["ASSET_STUDIO_WS"] = _TMP  # must be set before app.assets import

import pytest  # noqa: E402

import app.catalog as catalog  # noqa: E402
import app.server as server  # noqa: E402


def _base(name, code, invfile, typ="misc"):
	return {"name": name, "code": code, "invfile": invfile, "uniqueinvfile": "", "setinvfile": "",
	        "type": typ, "invwidth": "1", "invheight": "1", "normcode": code, "ubercode": "",
	        "ultracode": "", "flippyfile": ""}


def _u(index, code, invfile="", tint=""):
	return {"index": index, "code": code, "invfile": invfile, "invtransform": tint}


def _s(index, item, invfile="", tint=""):
	return {"index": index, "item": item, "invfile": invfile, "invtransform": tint}


FAKE = {
	"weapons": [_base("Cryptic Sword", "crs", "invcrs"), _base("Phase Blade", "7cr", "inv7cr"),
	            _base("Hydra Bow", "6hb", "inv6hb"), _base("Siege Bow", "6sb", "invsbw")],
	"armor": [_base("Light Gauntlets", "tgl", "invtgl")],
	"misc": [_base("Jewel", "jew", "invjw0", typ="jewl")],
	"itemtypes": [{"Code": "jewl", "VarInvGfx": "2", "InvGfx1": "invjw1", "InvGfx2": "invjw2"}],
	# game row = position among rows the game KEEPS (the "Expansion" separator is dropped)
	"uniqueitems": [
		_u("Azurewrath", "crs", invfile="invcrsu"),                       # game row 0
		_u("Magefist", "tgl", tint="lgry"),                               # 1
		{"index": "Expansion", "code": "", "invfile": "", "invtransform": ""},   # dropped
		_u("Azurewrath", "7cr", invfile="invcrs"),                        # 2  other base + art
		_u("Magefist", "tgl", tint="lgry"),                               # 3  repeats row 1
		_u("Magefist", "tgl", tint="lgry"),                               # 4  repeats row 1
		_u("Crackleshot", "6hb", invfile="invlightningbow", tint="lyel"),  # 5
		_u("Crackleshot", "6sb", tint="dgrn"),                            # 6  own invfile blank
		_u("Rainbow Facet", "jew"),                                       # 7
		_u("Rainbow Facet", "jew"),                                       # 8  repeats row 7
		_u("Twin", "tgl"),                                                # 9
		_u("Twin", "tgl", tint="lgry"),                                   # 10 same base, other tint
		_u("Twin", "tgl", tint="dgrn"),                                   # 11 ... and another
		_u("Solo", "crs", invfile="invsolo"),                             # 12
	],
	"setitems": [
		_s("Dupe Set", "tgl"), _s("Dupe Set", "tgl"),
		_s("Split Set", "tgl"), _s("Split Set", "crs", invfile="invcrsu"),
		_s("Lone Set", "tgl"),
	],
}


@pytest.fixture
def fake_tables(monkeypatch):
	monkeypatch.setattr(catalog, "_read_table", lambda name: FAKE[name])
	monkeypatch.setattr(catalog, "_VARGFX_CACHE", None)
	yield
	catalog._VARGFX_CACHE = None


def _items():
	return catalog.build_catalog()[0]


def _by_id(items):
	"""id -> [items]; a plain dict comprehension would hide the very collisions under test."""
	out = {}
	for it in items:
		out.setdefault(it["id"], []).append(it)
	return out


def _one(items, item_id):
	rows = _by_id(items).get(item_id, [])
	assert len(rows) == 1, f"{item_id!r}: expected exactly one catalog item, got {len(rows)}"
	return rows[0]


def test_every_catalog_id_is_unique(fake_tables):
	ids = [it["id"] for it in _items()]
	dups = sorted({i for i in ids if ids.count(i) > 1})
	assert not dups, f"duplicate ids: {dups}"


def test_first_row_keeps_the_bare_id(fake_tables):
	items = _items()
	az = _one(items, "unique/Azurewrath")
	assert (az["code"], az["invfile"]) == ("crs", "invcrsu")
	cr = _one(items, "unique/Crackleshot")
	assert (cr["code"], cr["invfile"]) == ("6hb", "invlightningbow")


def test_later_distinct_row_gets_the_base_code_suffix(fake_tables):
	items = _items()
	az = _one(items, "unique/Azurewrath#7cr")
	assert (az["code"], az["invfile"], az["name"]) == ("7cr", "invcrs", "Azurewrath")
	cr = _one(items, "unique/Crackleshot#6sb")
	assert (cr["code"], cr["invfile"], cr["invtransform"]) == ("6sb", "invsbw", "dgrn")


def test_identical_rows_collapse_into_one_item(fake_tables):
	items = _items()
	assert len([it for it in items if it["name"] == "Magefist"]) == 1
	assert len([it for it in items if it["name"] == "Rainbow Facet"]) == 1
	_one(items, "unique/Magefist")
	_one(items, "unique/Rainbow Facet")


def test_same_base_but_different_tint_is_distinct_and_still_unique(fake_tables):
	items = _items()
	assert _one(items, "unique/Twin")["invtransform"] == ""
	assert _one(items, "unique/Twin#tgl")["invtransform"] == "lgry"
	assert _one(items, "unique/Twin#tgl-2")["invtransform"] == "dgrn"


def test_sets_follow_the_same_rules(fake_tables):
	items = _items()
	_one(items, "set/Dupe Set")
	assert len([it for it in items if it["name"] == "Dupe Set"]) == 1
	assert _one(items, "set/Split Set")["code"] == "tgl"
	assert _one(items, "set/Split Set#crs")["code"] == "crs"


def test_ids_that_never_collided_do_not_change(fake_tables):
	"""Stored data (upscales, gen_prompts, meshy links) is keyed by id: only colliding ids may move."""
	ids = {it["id"] for it in _items()}
	assert {"unique/Solo", "set/Lone Set", "base/weapons/crs", "base/armor/tgl",
	        "base/misc/jew", "base/misc/jew#g2"} <= ids


def test_id_assignment_is_deterministic(fake_tables):
	assert [it["id"] for it in _items()] == [it["id"] for it in _items()]


# --- spawn path: unique_row() / _drop_body_for() / the drop route --------------------------------

def test_unique_row_without_a_base_still_returns_the_first_row(fake_tables):
	row = catalog.unique_row("Azurewrath")
	assert (row["row"], row["base"]) == (0, "crs")


def test_unique_row_with_a_base_picks_that_rows_game_index(fake_tables):
	row = catalog.unique_row("Azurewrath", "7cr")
	# txt row 3, but the Expansion separator before it is not a game row -> game row 2
	assert (row["row"], row["base"]) == (2, "7cr")
	assert catalog.unique_row("azurewrath", "7CR")["row"] == 2
	assert catalog.unique_row("Crackleshot", "6sb")["row"] == 6
	assert catalog.unique_row("Twin", "tgl")["row"] == 9        # repeated base -> first such row


def test_unique_row_with_an_unknown_base_finds_nothing(fake_tables):
	assert catalog.unique_row("Azurewrath", "zzz") is None


@pytest.fixture
def server_catalog(fake_tables, monkeypatch):
	items = _items()
	monkeypatch.setitem(server._CATALOG, "items", items)
	monkeypatch.setitem(server._CATALOG, "by_id", {it["id"]: it for it in items})
	return items


def test_drop_body_forces_the_row_of_the_picked_item(server_catalog):
	by_id = {it["id"]: it for it in server_catalog}
	body, _label = server._drop_body_for(by_id["unique/Azurewrath"], None)
	assert (body["code"], body["uniqueRow"]) == ("crs", 0)
	body, _label = server._drop_body_for(by_id["unique/Azurewrath#7cr"], None)
	assert (body["code"], body["uniqueRow"]) == ("7cr", 2)
	body, _label = server._drop_body_for(by_id["unique/Crackleshot#6sb"], None)
	assert (body["code"], body["uniqueRow"]) == ("6sb", 6)


def test_drop_body_forces_the_setitems_row_of_the_picked_piece(server_catalog):
	by_id = {it["id"]: it for it in server_catalog}
	body, _label = server._drop_body_for(by_id["set/Split Set"], None)
	assert (body["code"], body["setRow"]) == ("tgl", 2)
	body, _label = server._drop_body_for(by_id["set/Split Set#crs"], None)
	assert (body["code"], body["setRow"]) == ("crs", 3)


def test_drop_route_accepts_an_encoded_suffixed_id(server_catalog, monkeypatch):
	sent = []
	monkeypatch.setattr(server, "_dbg", lambda method, path, body=None, timeout=12.0:
	                    (sent.append(body) or {"ok": True}, None))
	r = server.flask_app.test_client().post("/api/item/unique%2FAzurewrath%237cr/drop")
	assert r.status_code == 200, r.get_data(as_text=True)
	assert (sent[0]["code"], sent[0]["uniqueRow"]) == ("7cr", 2)


# --- live data ----------------------------------------------------------------------------------

def test_live_pd2_catalog_ids_are_unique():
	"""1448 items / 1433 distinct ids before the fix (2026-09-26)."""
	try:
		items, _ = catalog.build_catalog()
	except Exception as e:  # noqa: BLE001 - no MPQs / StormLib on this machine
		pytest.skip(f"PD2 MPQs unavailable: {e}")
	if not items:
		pytest.skip("PD2 MPQs unavailable: empty catalog")
	ids = [it["id"] for it in items]
	assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
	by_id = {it["id"]: it for it in items}
	# genuinely different items on different bases keep both entries...
	assert by_id["unique/Azurewrath"]["code"] != by_id["unique/Azurewrath#7cr"]["code"]
	assert by_id["unique/Crackleshot"]["code"] != by_id["unique/Crackleshot#6sb"]["code"]
	# ...and exact repeats collapse to the id they always had
	for name in ("Magefist", "Rainbow Facet", "Arm of King Leoric", "Cyclopean Roar", "Akarats Devotion"):
		assert len([it for it in items if it["name"] == name]) == 1, name
		assert f"unique/{name}" in by_id
