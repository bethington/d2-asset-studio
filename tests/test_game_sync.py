"""Staying in step with a new game version (app/game_sync.py).

An alternate is generated against the game's ORIGINAL art at one point in time (its pixels and the
item's cell size) and pushed as an overlay that outranks the game. If a game update changes that
original or the cell size, the stale overlay would silently override the new official art. These
tests pin: alternates are fingerprinted against what they were made for; a changed original / cell
size flags the alternate and keeps it OUT of the pushed patch (never deleted); the user can accept
the new state; and unrelated buckets are untouched.

Everything is injected fakes -- no MPQ, workspace or Flask needed.
Usage: python -m pytest tests/test_game_sync.py
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["ASSET_STUDIO_WS"] = tempfile.mkdtemp(prefix="asset_studio_test_")  # before app.assets

from app import game_sync  # noqa: E402


class World:
	"""A fake installed game + Studio state."""

	def __init__(self):
		self.art = {"invcap": b"cap-art-v1", "invhlm": b"hlm-art-v1", "flpcap": b"flip-v1"}
		self.items = [
			{"id": "base/armor/cap", "invfile": "invcap", "invwidth": 2, "invheight": 2},
			{"id": "unique/Biggin", "invfile": "invcap", "invwidth": 2, "invheight": 2},
			{"id": "base/armor/hlm", "invfile": "invhlm", "invwidth": 2, "invheight": 2},
		]
		self.manifest = {"assets": {
			"dc6/invcap": {"active": "img-v1", "invfile": "invcap"},
			"dc6/invhlm": {"active": "img-v1", "invfile": "invhlm"},
			"flip/flpcap": {"flippy_active": "img-v1", "flippyfile": "flpcap"},
		}}
		self.stores = {"base__armor__cap", "base__armor__hlm"}
		self.prompts = {"base/armor/cap"}

	def original(self, name):
		if name not in self.art:
			raise FileNotFoundError(name)
		return self.art[name]

	def checker(self, path):
		return game_sync.SyncChecker(
			manifest_fn=lambda: self.manifest, items_fn=lambda: self.items,
			original_fn=self.original, baseline_path=str(path),
			store_names_fn=lambda: self.stores, expected_store_fn=lambda i: i["id"].replace("/", "__"),
			prompt_ids_fn=lambda: self.prompts)


@pytest.fixture
def w(tmp_path):
	world = World()
	world.chk = world.checker(tmp_path / "baseline.json")
	return world


def _drift(res, key):
	return next((d for d in res["drifted"] if d["key"] == key), None)


def test_first_check_baselines_and_reports_no_drift(w):
	res = w.chk.check()
	assert res["drifted"] == []
	assert sorted(res["baselined"]) == ["dc6/invcap", "dc6/invhlm", "flip/flpcap"]
	assert w.chk.excluded_rels() == set()


def test_baseline_survives_a_restart(w, tmp_path):
	w.chk.check()
	w.art["invcap"] = b"cap-art-v2"                   # game updated while Studio was closed
	res = w.checker(tmp_path / "baseline.json").check()
	assert _drift(res, "dc6/invcap") is not None
	assert res["baselined"] == []                      # nothing re-baselined over the change


def test_changed_original_art_flags_and_excludes(w):
	w.chk.check()
	w.art["invcap"] = b"cap-art-v2"
	res = w.chk.check()
	d = _drift(res, "dc6/invcap")
	assert d and any("original art changed" in r for r in d["reasons"])
	assert w.chk.excluded_rels() == {"data\\global\\items\\invcap.dc6"}
	assert _drift(res, "dc6/invhlm") is None           # unrelated bucket untouched


def test_changed_cell_size_flags(w):
	w.chk.check()
	for it in w.items:
		if it["invfile"] == "invcap":
			it["invheight"] = 3
	d = _drift(w.chk.check(), "dc6/invcap")
	assert d and any("2x2" in r and "2x3" in r for r in d["reasons"])


def test_original_removed_from_game_flags(w):
	w.chk.check()
	del w.art["invhlm"]
	d = _drift(w.chk.check(), "dc6/invhlm")
	assert d and any("no longer exists" in r for r in d["reasons"])


def test_flippy_original_change_flags_without_a_cell_check(w):
	w.chk.check()
	w.art["flpcap"] = b"flip-v2"
	d = _drift(w.chk.check(), "flip/flpcap")
	assert d and w.chk.excluded_rels() == {"data\\global\\items\\flpcap.dc6"}


def test_accept_rebaselines_and_clears_the_drift(w):
	w.chk.check()
	w.art["invcap"] = b"cap-art-v2"
	assert w.chk.excluded_rels()
	w.chk.accept("dc6/invcap")
	assert _drift(w.chk.check(), "dc6/invcap") is None
	assert w.chk.excluded_rels() == set()
	w.art["invcap"] = b"cap-art-v3"                    # ...and it keeps watching after that
	assert _drift(w.chk.check(), "dc6/invcap") is not None


def test_unused_invfile_is_an_orphan_not_drift(w):
	w.chk.check()
	w.items = [i for i in w.items if i["invfile"] != "invhlm"]
	res = w.chk.check()
	assert _drift(res, "dc6/invhlm") is None
	assert "dc6/invhlm" in res["orphans"]["buckets"]
	assert w.chk.excluded_rels() == set()


def test_only_active_alternates_are_tracked(w):
	w.manifest["assets"]["dc6/invhlm"] = {}            # reverted to the game's original
	res = w.chk.check()
	assert "dc6/invhlm" not in res["baselined"]
	w.art["invhlm"] = b"hlm-art-v2"
	assert _drift(w.chk.check(), "dc6/invhlm") is None


def test_orphaned_item_stores_and_prompts_are_reported(w):
	w.stores.add("unique__Renamed_Item")
	w.prompts.add("unique/Renamed Item")
	res = w.chk.check()
	assert res["orphans"]["item_stores"] == ["unique__Renamed_Item"]
	assert res["orphans"]["prompts"] == ["unique/Renamed Item"]


def test_note_activation_baselines_only_once(w):
	w.manifest["assets"]["dc6/invhlm"] = {}
	w.chk.note_activation("dc6/invhlm", "invhlm")      # user activates an alternate for it
	w.art["invhlm"] = b"hlm-art-v2"                    # then the game updates
	w.manifest["assets"]["dc6/invhlm"] = {"active": "img-v1", "invfile": "invhlm"}
	assert _drift(w.chk.check(), "dc6/invhlm") is not None   # baseline predates the update
	w.chk.note_activation("dc6/invhlm", "invhlm")      # re-activating must NOT hide the drift
	assert _drift(w.chk.check(), "dc6/invhlm") is not None


def test_filter_overlay_drops_drifted_files_and_remembers_them(w):
	w.chk.check()
	w.art["invcap"] = b"cap-art-v2"
	files = {"data\\global\\items\\invcap.dc6": "a", "DATA\\global\\items\\invhlm.dc6": "b",
	         "data\\global\\excel\\uniqueitems.bin": "c"}
	skipped = w.chk.filter_overlay(files)
	assert skipped == ["data\\global\\items\\invcap.dc6"]
	assert sorted(files) == ["DATA\\global\\items\\invhlm.dc6", "data\\global\\excel\\uniqueitems.bin"]
	assert w.chk.last_skipped == ["data\\global\\items\\invcap.dc6"]


def test_unreadable_original_is_unverified_not_drift(w):
	"""A game update replacing an archive mid-check must not flag (or un-push) anything."""
	w.chk.check()

	def flaky(name):
		raise OSError("archive is being replaced")

	chk = game_sync.SyncChecker(
		manifest_fn=lambda: w.manifest, items_fn=lambda: w.items, original_fn=flaky,
		baseline_path=w.chk._baseline_path)
	res = chk.check()
	assert res["drifted"] == []
	assert sorted(res["unverified"]) == ["dc6/invcap", "dc6/invhlm", "flip/flpcap"]
	assert chk.excluded_rels() == set()


def test_baseline_file_is_plain_json(w, tmp_path):
	w.chk.check()
	data = json.loads((tmp_path / "baseline.json").read_text(encoding="utf-8"))
	assert set(data["assets"]) == {"dc6/invcap", "dc6/invhlm", "flip/flpcap"}
	assert data["assets"]["dc6/invcap"]["cell"] == [[2, 2]]
	assert len(data["assets"]["dc6/invcap"]["sha1"]) == 40


def test_not_installed_filter_is_a_noop():
	game_sync.uninstall()
	files = {"data\\global\\items\\invcap.dc6": "a"}
	assert game_sync.filter_overlay(files) == []
	assert list(files) == ["data\\global\\items\\invcap.dc6"]


def test_game_change_clears_caches_then_rechecks(w):
	from pyd2 import mpq
	cleared = []
	saved = list(mpq._CHANGE_CALLBACKS)
	try:
		game_sync.install(items_fn=lambda: w.items, extra_clears=[lambda: cleared.append("x")],
		                  checker=w.chk)
		w.chk.check()
		w.art["invcap"] = b"cap-art-v2"
		game_sync.on_game_changed()
		assert cleared == ["x"]
		assert _drift(w.chk.check(), "dc6/invcap") is not None
	finally:
		mpq._CHANGE_CALLBACKS[:] = saved
		game_sync.uninstall()


def test_game_change_rebuilds_bin_edits_and_status_reports_problems(w):
	from pyd2 import mpq
	rebuilt = []
	problems = {"uniqueitems": {"missing": ["Gone"], "invalid": [], "schema_drift": False}}
	saved = list(mpq._CHANGE_CALLBACKS)
	try:
		game_sync.install(items_fn=lambda: w.items, checker=w.chk,
		                  rebuild_fn=lambda: rebuilt.append(1) or {}, bin_report_fn=lambda: problems)
		assert rebuilt == []                               # not at install unless asked
		game_sync.on_game_changed()
		assert rebuilt == [1]
		assert game_sync.status()["bin_edits"] == problems
	finally:
		mpq._CHANGE_CALLBACKS[:] = saved
		game_sync.uninstall()


def test_rebuild_now_runs_at_install_and_a_failure_does_not_break_startup(w):
	from pyd2 import mpq
	saved = list(mpq._CHANGE_CALLBACKS)

	def boom():
		raise RuntimeError("stock table unreadable")

	try:
		game_sync.install(items_fn=lambda: w.items, checker=w.chk, rebuild_fn=boom, rebuild_now=True)
		st = game_sync.status()                             # still serves
		assert st["drift"] is not None
	finally:
		mpq._CHANGE_CALLBACKS[:] = saved
		game_sync.uninstall()


def test_status_survives_a_failing_bin_report(w):
	from pyd2 import mpq
	saved = list(mpq._CHANGE_CALLBACKS)

	def boom():
		raise OSError("archive being replaced")

	try:
		game_sync.install(items_fn=lambda: w.items, checker=w.chk, bin_report_fn=boom)
		assert "archive being replaced" in game_sync.status()["bin_edits"]["error"]
	finally:
		mpq._CHANGE_CALLBACKS[:] = saved
		game_sync.uninstall()
