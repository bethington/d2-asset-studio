"""Machine-specific locations for Asset Studio, all overridable by environment variable.

WORKSPACE  -- alternates, overlay, exports, manifest, prompts, upscales. User data that is
              NOT in git. $ASSET_STUDIO_WS, default <repo>/workspace (gitignored). The game
              side (D2MOO's D2Debugger) reads the same variable to find autoload.txt, so
              the launch scripts pass it through (scripts/_paths.ps1).
D2MOO_ROOT -- D2MOO checkout with the build-1.13c conformance build (D2.Detours launcher,
              patch dir, D2Debugger). $D2MOO_ROOT, default C:\\Users\\benam\\source\\cpp\\D2MOO.
PD2_GAME   -- Project Diablo 2 Game.exe. $PD2_GAME, default C:\\Diablo2\\ProjectD2\\Game.exe. The
              MPQs Studio reads (pyd2/mpq.py) are located from this path: ProjectD2\\ holds the
              pd2*/patch archives and its parent holds the stock Diablo II archives.
PD2_EXTRA_MPQS -- optional archives to read IN ADDITION to the known set, highest priority first,
              separated by os.pathsep (';' on Windows). For a new PD2 release that adds an
              archive Studio doesn't recognise yet (it warns about those instead of guessing).

Keep the defaults in step with scripts/_paths.ps1.
"""
from __future__ import annotations

import os

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

PD2_GAME = os.environ.get("PD2_GAME") or r"C:\Diablo2\ProjectD2\Game.exe"
PD2_EXTRA_MPQS = [p.strip() for p in (os.environ.get("PD2_EXTRA_MPQS") or "").split(os.pathsep)
                  if p.strip()]


# WORKSPACE and D2MOO_ROOT are resolved on FIRST READ, not when this module is imported. pyd2.mpq
# imports this module for PD2_GAME, and computing WORKSPACE at that moment would freeze the
# environment as it was then: a test that imports the MPQ layer before setting ASSET_STUDIO_WS
# would silently write into the real workspace (it happened -- see tests/
# test_studio_config_import_order.py). The first read is cached so every consumer agrees.
_LAZY = {
	"WORKSPACE": lambda: os.environ.get("ASSET_STUDIO_WS") or os.path.join(REPO_ROOT, "workspace"),
	"D2MOO_ROOT": lambda: os.environ.get("D2MOO_ROOT") or r"C:\Users\benam\source\cpp\D2MOO",
}
_RESOLVED: dict = {}


def __getattr__(name):
	if name in _LAZY:
		if name not in _RESOLVED:
			_RESOLVED[name] = _LAZY[name]()
		return _RESOLVED[name]
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
