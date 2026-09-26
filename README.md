# d2-asset-studio

A local web tool for live-editing Project Diablo 2 item art: browse every item straight from
the game's MPQs, import or AI-generate alternate art (ComfyUI / Meshy.ai + Blender), encode
game-ready DC6, and push it into the running game through a non-destructive `patch.mpq`
overlay. No base MPQ edits are needed.

Split out of [D2MOO](https://github.com/bethington/D2MOO) (`tools/asset-studio/`) on 2026-09-25.
The live-reload, spawn and hover endpoints the studio calls are still part of D2MOO's
**D2Debugger** (`:8790`, `source/D2Debugger/src/D2Debugger.assetreload.cpp`), so running
against the game needs a D2MOO `build-1.13c` build (located via `D2MOO_ROOT`, below).

## Setup

1. Python 3.13 (x64): `pip install -r requirements.txt`. For local background cutting, lab
   scripts and tests: `pip install -r requirements-extras.txt`.
2. **StormLib**: build [StormLib](https://github.com/ladislav-zezula/StormLib) x64 with
   `-DBUILD_SHARED_LIBS=ON -DSTORM_USE_BUNDLED_LIBRARIES=ON` and copy `StormLib.dll` to `bin/`
   (or point `STORMLIB_DLL` at it). This is required for all MPQ reads and `patch.mpq` builds.
3. A D2MOO checkout with the `build-1.13c` conformance build (D2.Detours launcher + D2Debugger)
   and Project Diablo 2 installed. These are only needed to push to and reload the live game.
   Browsing, importing and generating work without them.
4. Optional: Blender (Meshy → sprite renders), and a box running ComfyUI / Ollama / the PNG
   upscaler for the AI lanes.
5. `python app/server.py` → http://127.0.0.1:5001

## Configuration

All settings are environment variables. The defaults match the author's machine.

| Variable | Default | Used for |
|---|---|---|
| `ASSET_STUDIO_WS` | `<repo>/workspace` (gitignored) | Workspace: alternates, overlay, exports, prompts, upscales. The launch scripts pass it to the game so D2Debugger finds `autoload.txt`. |
| `D2MOO_ROOT` | `C:\Users\benam\source\cpp\D2MOO` | `build-1.13c` launcher, patch dir and D2Debugger for the `scripts/*.ps1` launchers |
| `PD2_GAME` | `C:\Diablo2\ProjectD2\Game.exe` | Game the launchers start. Also locates the MPQs Studio reads (`ProjectD2\` and its parent), so a moved install just works |
| `PD2_EXTRA_MPQS` | (none) | Extra archives to read first, highest priority first, `;`-separated. For a PD2 release that adds an archive Studio doesn't recognise (it logs a warning naming it instead of guessing) |
| `ASSET_STUDIO_PORT` | `5001` | Web UI port |
| `STORMLIB_DLL` | `bin/StormLib.dll` | StormLib location |
| `BLENDER_EXE` / `CHROME_EXE` | auto-detected | Blender renders / the Meshy logged-in Chrome |
| `COMFY_URL`, `OLLAMA_URL`, `PNG_UPSCALE_URL` | `http://10.0.10.30:{8188,11434,8084}` | AI backends |
| `COMFY_*_CKPT`, `COMFY_QWEN_GGUF`, `COMFY_REMBG_MODEL`, `DESCRIBE_MODEL` | see `app/comfy.py`, `app/describe.py` | Model choices |

Python reads these through `studio_config.py`. The PowerShell launchers read them through
`scripts/_paths.ps1`.

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

- `workspace/`: the default workspace. This is your generated art, so back it up. `git clean -fdx` deletes it.
- `bin/StormLib.dll`: x64 StormLib build, required by `pyd2/mpq.py`.
- `_fidelity_out/`: lab and report output.
