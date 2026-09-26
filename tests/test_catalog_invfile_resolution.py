"""Unique/set inventory art must follow the game's resolution order, not just the base invfile.

The game draws a unique/set item's inventory sprite from:
  1. the uniqueitems/setitems row's own `invfile`, if non-blank;
  2. else the BASE item's `uniqueinvfile` (uniques) / `setinvfile` (sets), if non-blank;
  3. else the base item's `invfile`.
The catalog used to skip step 2, so e.g. Infernal Cranium (a set Cap) was mapped to `invcap` while
the game draws `invcapu` -- Studio's preview and any art accepted for the item then targeted a DC6
the game never reads for it. Verified in-game (pixel-exact) against Infernal Cranium / Sander's
Paragon; Harlequin Crest and the War Hat items correctly stay on `invcap`.

Usage: python -m pytest tests/test_catalog_invfile_resolution.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.catalog as catalog  # noqa: E402


def _base(name, code, invfile, uinv="", sinv="", typ="helm"):
	return {"name": name, "code": code, "invfile": invfile, "uniqueinvfile": uinv,
	        "setinvfile": sinv, "type": typ, "invwidth": "2", "invheight": "2",
	        "normcode": code, "ubercode": "", "ultracode": "", "flippyfile": ""}


FAKE = {
	"armor": [
		_base("Cap/hat", "cap", "invcap", "invcapu", "invcapu"),
		_base("War Hat", "xap", "invcap"),                       # blank unique/set art -> invcap
		_base("Odd Helm", "odd", "invodd", "invoddu", ""),       # unique art only; sets fall back
	],
	"weapons": [],
	# amulet: VarInvGfx type -> art comes from the TYPE's graphic list; base carries a uniqueinvfile
	"misc": [_base("Amulet of the Viper", "vip", "invvip0", "invvip", "", typ="amul"),
	         _base("Amulet", "amu", "invamu", "", "", typ="amul")],   # no unique art of its own
	"itemtypes": [{"Code": "amul", "VarInvGfx": "2", "InvGfx1": "invamu1", "InvGfx2": "invamu2"}],
	"uniqueitems": [
		{"index": "Biggin", "code": "cap", "invfile": "", "invtransform": ""},
		{"index": "Own Art", "code": "cap", "invfile": "invown", "invtransform": ""},
		{"index": "Peasant", "code": "xap", "invfile": "", "invtransform": ""},
		{"index": "Odd Unique", "code": "odd", "invfile": "", "invtransform": ""},
		{"index": "Viper", "code": "vip", "invfile": "", "invtransform": ""},
		{"index": "Plain Amulet", "code": "amu", "invfile": "", "invtransform": ""},
	],
	"setitems": [
		{"index": "Infernal", "item": "cap", "invfile": "", "invtransform": ""},
		{"index": "Horns", "item": "xap", "invfile": "", "invtransform": ""},
		{"index": "Odd Set", "item": "odd", "invfile": "", "invtransform": ""},
	],
}


@pytest.fixture
def fake_tables(monkeypatch):
	monkeypatch.setattr(catalog, "_read_table", lambda name: FAKE[name])
	monkeypatch.setattr(catalog, "_VARGFX_CACHE", None)
	yield
	catalog._VARGFX_CACHE = None


def _inv(items):
	return {it["id"]: it["invfile"] for it in items}


def test_unique_uses_base_uniqueinvfile(fake_tables):
	inv = _inv(catalog.build_catalog()[0])
	assert inv["unique/Biggin"] == "invcapu"


def test_set_uses_base_setinvfile(fake_tables):
	inv = _inv(catalog.build_catalog()[0])
	assert inv["set/Infernal"] == "invcapu"


def test_row_invfile_still_wins(fake_tables):
	assert _inv(catalog.build_catalog()[0])["unique/Own Art"] == "invown"


def test_blank_base_unique_and_set_art_falls_back_to_invfile(fake_tables):
	inv = _inv(catalog.build_catalog()[0])
	assert inv["unique/Peasant"] == "invcap"
	assert inv["set/Horns"] == "invcap"


def test_unique_and_set_fall_back_independently(fake_tables):
	inv = _inv(catalog.build_catalog()[0])
	assert inv["unique/Odd Unique"] == "invoddu"
	assert inv["set/Odd Set"] == "invodd"       # setinvfile blank -> base invfile, not uniqueinvfile


def test_var_inv_gfx_base_with_its_own_unique_art_uses_it(fake_tables):
	"""Verified in the live game 2026-09-26: Amulet of the Viper (an amul-type base whose row has
	uniqueinvfile=invvip) draws invvip, NOT one of the amulet type's invamu1..3."""
	assert _inv(catalog.build_catalog()[0])["unique/Viper"] == "invvip"


def test_var_inv_gfx_base_without_unique_art_uses_the_type_list(fake_tables):
	"""Uniques on VarInvGfx bases with blank unique art (Nagelring, Mara's Kaleidoscope, ...) draw a
	sprite from the type's list -- the game picks per instance (two Nagelrings drew invrin4 and
	invrin5), so the catalog can only show the first graphic."""
	assert _inv(catalog.build_catalog()[0])["unique/Plain Amulet"] == "invamu1"


def test_base_items_keep_their_own_invfile(fake_tables):
	inv = _inv(catalog.build_catalog()[0])
	assert inv["base/armor/cap"] == "invcap"
	assert inv["base/armor/xap"] == "invcap"


def test_live_pd2_data_matches_what_the_game_draws():
	"""Ground truth from the running game (2026-09-26, pixel-matched inventory screenshot)."""
	try:
		items, _ = catalog.build_catalog()
	except Exception as e:  # noqa: BLE001 - no MPQs / StormLib on this machine
		pytest.skip(f"PD2 MPQs unavailable: {e}")
	inv = _inv(items)
	assert inv["set/Infernal Cranium"] == "invcapu"
	assert inv["set/McAuley's Paragon"] == "invcapu"   # shown in-game as "Sander's Paragon"
	assert inv["unique/Harlequin Crest"] == "invcap"
	assert inv["set/Cow King's Horns"] == "invcap"
	assert inv["unique/Amulet of the Viper"] == "invvip"    # live game: draws invvip, not invamu1..3
	assert inv["unique/Nagelring"] == "invrin1"             # type list (random per instance in-game)
