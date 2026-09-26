"""Machine-specific locations for Asset Studio, all overridable by environment variable.

WORKSPACE  -- alternates, overlay, exports, manifest, prompts, upscales. User data that is
              NOT in git. $ASSET_STUDIO_WS, default <repo>/workspace (gitignored). The game
              side (D2MOO's D2Debugger) reads the same variable to find autoload.txt, so
              the launch scripts pass it through (scripts/_paths.ps1).
D2MOO_ROOT -- D2MOO checkout with the build-1.13c conformance build (D2.Detours launcher,
              patch dir, D2Debugger). $D2MOO_ROOT, default C:\\Users\\benam\\source\\cpp\\D2MOO.

Keep the defaults in step with scripts/_paths.ps1.
"""
from __future__ import annotations

import os

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

WORKSPACE = os.environ.get("ASSET_STUDIO_WS") or os.path.join(REPO_ROOT, "workspace")
D2MOO_ROOT = os.environ.get("D2MOO_ROOT") or r"C:\Users\benam\source\cpp\D2MOO"
