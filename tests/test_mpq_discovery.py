"""MPQ discovery + change detection: Studio must follow the installed game, not a fixed path list.

Covers: the archive order is derived from PD2_GAME's location; an unrecognised pd2*/patch* archive
is reported (never silently skipped) but not read unless the user lists it in PD2_EXTRA_MPQS;
and a size/mtime change to any archive closes the cached handles, bumps the generation and runs
the registered cache-clear callbacks -- so a game update is picked up without restarting Studio.

Hermetic: fake (empty) archive files under a temp dir; no StormLib read is attempted.
Usage: python -m pytest tests/test_mpq_discovery.py
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pyd2 import mpq  # noqa: E402

KNOWN_P = ["pd2data.mpq", "pd2assets.mpq", "pd2maps.mpq", "patch_d2.mpq"]   # in ProjectD2\
KNOWN_R = ["patch_d2.mpq", "d2exp.mpq", "d2data.mpq", "d2char.mpq"]         # in the Diablo II root


@pytest.fixture
def game(tmp_path):
	root = tmp_path / "Diablo2"
	pd2 = root / "ProjectD2"
	pd2.mkdir(parents=True)
	for n in KNOWN_P:
		(pd2 / n).write_bytes(b"x" * 10)
	for n in KNOWN_R:
		(root / n).write_bytes(b"x" * 10)
	exe = str(pd2 / "Game.exe")
	saved = dict(mpq._CONFIG)
	saved_cbs = list(mpq._CHANGE_CALLBACKS)
	mpq.configure(exe)
	yield root, pd2, exe
	mpq._CHANGE_CALLBACKS[:] = saved_cbs
	mpq.configure(saved["game"], extra=saved["extra"])


def test_order_is_derived_from_the_game_location(game):
	root, pd2, _ = game
	expect = [str(pd2 / n) for n in KNOWN_P] + [str(root / n) for n in KNOWN_R]
	assert mpq.PD2_SEARCH_ORDER == expect


def test_unknown_pd2_and_patch_archives_are_reported_not_read(game):
	root, pd2, exe = game
	(pd2 / "pd2extra.mpq").write_bytes(b"x")
	(pd2 / "Patch_PD2_2.MPQ").write_bytes(b"x")
	(root / "d2music.mpq").write_bytes(b"x")          # audio/video archives are irrelevant noise
	mpq.configure(exe)
	unknown = sorted(os.path.basename(p).lower() for p in mpq.unknown_archives())
	assert unknown == ["patch_pd2_2.mpq", "pd2extra.mpq"]
	assert not any("pd2extra" in p.lower() for p in mpq.PD2_SEARCH_ORDER)


def test_extra_mpqs_are_read_first_and_no_longer_unknown(game):
	root, pd2, exe = game
	extra = pd2 / "pd2data2.mpq"
	extra.write_bytes(b"x")
	mpq.configure(exe, extra=[str(extra)])
	assert mpq.PD2_SEARCH_ORDER[0] == str(extra)
	assert str(extra) not in mpq.unknown_archives()


def test_status_lists_missing_known_archives(game):
	root, pd2, _ = game
	os.remove(pd2 / "pd2maps.mpq")
	st = mpq.status()
	assert str(pd2 / "pd2maps.mpq") in st["missing"]
	assert st["generation"] == mpq.generation()


def test_unchanged_files_do_not_refresh(game):
	assert mpq.refresh_if_changed(min_interval=0) is False
	assert mpq.refresh_if_changed(min_interval=0) is False


@pytest.mark.parametrize("mutate", ["grow", "touch", "add_unknown", "remove"])
def test_any_archive_change_refreshes_and_clears(game, mutate):
	root, pd2, _ = game
	closed = []

	class _Handle:
		def close(self):
			closed.append(True)

	mpq._OPEN_ARCHIVES[str(pd2 / "pd2data.mpq")] = _Handle()
	fired = []
	mpq.on_change(lambda: fired.append(1))
	mpq.refresh_if_changed(min_interval=0)                  # baseline
	gen = mpq.generation()

	target = pd2 / "pd2data.mpq"
	if mutate == "grow":
		target.write_bytes(b"x" * 25)
	elif mutate == "touch":
		st = target.stat()
		os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
	elif mutate == "add_unknown":
		(pd2 / "pd2new.mpq").write_bytes(b"x")
	else:
		os.remove(pd2 / "pd2maps.mpq")

	assert mpq.refresh_if_changed(min_interval=0) is True
	assert mpq.generation() == gen + 1
	assert closed == [True] and not mpq._OPEN_ARCHIVES
	assert fired == [1]
	assert mpq.refresh_if_changed(min_interval=0) is False  # settled: no repeat refresh
	assert fired == [1]


def test_check_is_throttled(game):
	root, pd2, _ = game
	mpq.refresh_if_changed(min_interval=0)                  # baseline + stamps the check time
	(pd2 / "pd2data.mpq").write_bytes(b"x" * 99)
	assert mpq.refresh_if_changed(min_interval=3600) is False   # too soon: not even looked at
	assert mpq.refresh_if_changed(min_interval=0) is True


def test_unknown_archives_are_logged(game, caplog):
	root, pd2, exe = game
	(pd2 / "pd2extra.mpq").write_bytes(b"x")
	with caplog.at_level(logging.WARNING, logger="pyd2.mpq"):
		mpq.configure(exe)
	assert any("pd2extra.mpq" in r.getMessage() and "PD2_EXTRA_MPQS" in r.getMessage()
	           for r in caplog.records)
