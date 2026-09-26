"""studio_config must not pin the workspace at first import.

Tests (and scripts) point Studio at a throw-away workspace by setting ASSET_STUDIO_WS BEFORE
importing app.assets. pyd2.mpq imports studio_config for PD2_GAME, so if studio_config computed
WORKSPACE eagerly, merely importing pyd2.mpq first would freeze the REAL workspace and every later
"throw-away workspace" test would silently write into the user's real art. That happened once
(test artifacts + a stray baseline landed in workspace/). WORKSPACE / D2MOO_ROOT are therefore
resolved when first read, not when studio_config is imported.

Each case runs in a fresh interpreter so this session's import state cannot mask the bug.
Usage: python -m pytest tests/test_studio_config_import_order.py
"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _run(code: str, ws: str | None):
	env = {k: v for k, v in os.environ.items() if k != "ASSET_STUDIO_WS"}
	if ws:
		env["ASSET_STUDIO_WS"] = ws
	r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
	assert r.returncode == 0, r.stderr
	return r.stdout.strip().splitlines()[-1]


def test_importing_pyd2_mpq_first_does_not_pin_the_real_workspace():
	tmp = tempfile.mkdtemp(prefix="asset_studio_test_")
	out = _run(
		"import os, sys; sys.path.insert(0, '.')\n"
		"import pyd2.mpq                                  # e.g. a test module that imports the MPQ layer first\n"
		f"os.environ['ASSET_STUDIO_WS'] = r'{tmp}'         # ...and only then chooses its throw-away workspace\n"
		"import app.assets as assets\n"
		"print(assets.WORKSPACE)", ws=None)
	assert os.path.normcase(out) == os.path.normcase(tmp)


def test_env_set_before_any_import_still_wins():
	tmp = tempfile.mkdtemp(prefix="asset_studio_test_")
	out = _run("import sys; sys.path.insert(0, '.')\nimport pyd2.mpq, app.assets as a\nprint(a.WORKSPACE)", ws=tmp)
	assert os.path.normcase(out) == os.path.normcase(tmp)


def test_defaults_without_env_are_unchanged():
	out = _run("import sys; sys.path.insert(0, '.')\nimport studio_config as c\nprint(c.WORKSPACE)", ws=None)
	assert os.path.normcase(out) == os.path.normcase(os.path.join(os.path.abspath(ROOT), "workspace"))


def test_pd2_settings_are_available_without_touching_the_workspace():
	out = _run("import sys; sys.path.insert(0, '.')\nimport studio_config as c\nprint(c.PD2_GAME.lower().endswith('game.exe'))", ws=None)
	assert out == "True"
