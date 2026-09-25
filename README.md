# d2-asset-studio

A local web tool for live-editing Project Diablo 2 item art: browse every item straight from
the game's MPQs, import or AI-generate alternate art (ComfyUI / Meshy.ai + Blender), encode
game-ready DC6, and push it into the running game through a non-destructive `patch.mpq`
overlay. No base MPQ edits are needed.

Split out of [D2MOO](https://github.com/bethington/D2MOO) (`tools/asset-studio/`) on 2026-09-25.
The live-reload, spawn and hover endpoints the studio calls are still part of D2MOO's
**D2Debugger** (`:8790`, `source/D2Debugger/src/D2Debugger.assetreload.cpp`), so running
against the game needs a D2MOO `build-1.13c` build. Several `scripts/*.ps1` expect that
checkout at `C:\Users\benam\source\cpp\D2MOO` (`$root`).

## Layout

| Path | What |
|---|---|
| `app/` | Flask backend + browser UI (`python app/server.py` → http://127.0.0.1:5001). See [app/README.md](app/README.md). |
| `pyd2/` | Pure-Python D2 formats: MPQ (via StormLib), DC6, DCC, COF, palettes, colour transforms |
| `scripts/` | Labs, reports, patch-MPQ build/verify, launch helpers |
| `workflows/` | ComfyUI workflow graphs (m0–m7 enhance recipes) |
| `blender/` | Blender GLB → sprite renderer |
| `tests/` | pytest suite (`python -m pytest tests`) |
| `docs/AssetStudioPlan.md` | The working plan and current-state index (§25) |
| `*_DESIGN.md`, `GHIDRA_FINDINGS.md`, `MESHY_WEB_API.md` | Design notes and RE findings |

## Local, untracked

- `bin/StormLib.dll`: x64 StormLib build (gitignored), required by `pyd2/mpq.py`.
- `_fidelity_out/`: lab and report output (gitignored).
- Workspace (alternates + manifest): `ASSET_STUDIO_WS`, default `C:\Diablo2\AssetStudio`.
