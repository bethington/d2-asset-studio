# PD2 Asset Studio — Implementation Plan

> **Paths:** this plan was written inside D2MOO, where the studio lived at `tools/asset-studio/`
> until 2026-09-25. Paths under `source/`, `conformance/`, `build-1.13c/` refer to the
> [D2MOO repo](https://github.com/bethington/D2MOO) (the D2Debugger AssetReload/showcase
> endpoints still live there); `app/`, `pyd2/`, `scripts/` etc. refer to this repo.

A tool for live-editing Project Diablo 2's game assets via a non-destructive overlay, with
Meshy.ai-powered artwork generation and in-game live reload. This document is the working
plan: decisions are recorded, investigation findings are cited with `file:line`, and phases
are broken into tasks a fresh implementation session can pick up directly.

**Status:** Phase 1 (Item Art Studio) FEATURE-COMPLETE 2026-07-18 — browse → PNG-import /
Meshy 3D → Blender render → DC6 → patch.mpq push → one-click reload → in-game verify
(spawn-to-inventory + item-stats/text/hover) all proven live (§18–§28). §29 adds the txt
sliver (per-unique invfile via uniqueitems.bin cell edit), flippy authoring (Blender
turntable → multi-frame DC6), and the early-registration hook that makes excel-bin edits
land. **Meshy generation now runs entirely through the web-app login (§24, 2026-07-19)** —
the Generation Studio (`/studio`): register → draft → rotatable 3D preview → re-roll → texture →
accept → Blender render → DC6; the billed openapi-key path was removed. Next phases: units (DCC),
data grid editor, maps, distribution.
**Override channel SETTLED (2026-07-18, §13):** loose-`data\` DC6 does NOT render in PD2, and
modifying PD2's own archives corrupts the loader — BUT a **separate `patch.mpq` registered at
runtime via `SFileOpenArchive` at priority > 5000 DOES override and render** (proven live: belt
potions turned green). Shipped the D2Debugger **AssetReload subsystem** (`/asset/register` etc.)
that does this. Critical: archives must be **MPQ v1 + PKWARE** — D2's Storm.dll can't inflate
zlib (hangs the loader). Reuse source: OpenD2 has reference decoders for DCC/DS1/DT1/DC6 +
PKWARE MPQ (§12). Full history in §12 (Phase 0) and §13 (the GO result + what shipped).

---

## 1. Vision

Edit PD2 assets (item art first, then units, data tables, maps) without ever touching the
original MPQs, and see the result in the running game within seconds. The edit surface is a
loose-file overlay + exported `patch.mpq`; the generation engine for alternate artwork is
Meshy.ai (sprite → 3D model → textured → re-rendered → back to game format); the live-reload
mechanism is a new **AssetReload** subsystem inside D2Debugger, which already runs inside the
game process.

Canonical first use case: give the unique Harlequin Crest ("Shako") an alternate inventory
image and ground-drop animation, generated from its original art via Meshy, and see it in the
open inventory without restarting the game.

---

## 2. Decision log (ratified 2026-07-18)

| # | Question | Decision |
|---|----------|----------|
| 1 | Play context | **Single-player first.** Build/test entirely in SP (same rig as the conformance oracle). Online use revisited later — never assume realm safety. |
| 2 | Edit-loop storage | **Hybrid overlay.** Live workspace = loose-file overlay the game reads with top priority; one-click export builds `patch.mpq` for distribution/final verification. |
| 3 | In-game agent | **Extend D2Debugger** with an AssetReload subsystem (new routes on the :8790 server + game-thread marshalled invalidation). |
| 4 | Tool stack | **Python backend + local web UI** (same family as the fun-doc dashboards). Pillow/NumPy for codecs, StormLib via ctypes for MPQ authoring, three.js for 3D preview. |
| 5 | Item-phase scope | **All item surfaces in v1**: inventory DC6 (`invfile`/`uniqueinvfile`/`setinvfile` — covers inventory, stash, cursor-drag, vendor) *and* the animated ground-drop flippy (`flippyfile`). |
| 6 | Meshy pipeline | **Staged with review, driven through the Meshy web-app login — NOT the billed API key.** The Studio uses the user's browser session (a Supabase JWT captured over CDP) so generations get the plan's **free retries**; the openapi `msy_` key path was removed 2026-07-19 (§24). Every stage inspectable/retryable before the next; batch mode later. |
| 7 | Phase order after items | **Characters/NPCs/monsters next**, then .txt data editor, then maps/tiles. (A minimal txt-edit sliver ships inside the item phase anyway — see §7.3.) |
| 8 | Unit sprite rendering | **Blender headless** render rig (scripted dimetric camera, batch directions/frames), plus a lightweight three.js preview in the UI for interactive rotate/snapshot. |
| 9 | Test items in-game | **In-process spawn.** "Test in game" calls D2Game's item-creation functions via the game-thread oracle — spawn dropped at the character's feet (flippy) and/or placed into inventory/cursor (inv art). Legit in SP since D2Game runs in-process. |
| 10 | Showcase verbs for other assets | **Per-phase.** Each phase ships its own "show it to me" verb alongside its editor (see §5.1). |

---

## 3. Ground truth from investigation (2026-07-18)

Three exploration passes over the D2MOO source and the live install produced these
load-bearing facts. Verify against the live game in Phase 0 before building on them.

### 3.1 MPQ loading & override machinery

- **Install layout:** game root `C:\Diablo2\ProjectD2\` (launcher for our rig:
  `conformance/tools/relaunch_pd2.ps1`), base MPQs one level up in `C:\Diablo2\`.
  PD2's own archives: `pd2data.mpq`, `pd2assets.mpq`, `pd2maps.mpq` (registered by PD2's own
  DLLs, not by the stock archive list — precedent that extra-archive injection works).
- **Priorities:** archives register via `SFileOpenArchive` (Storm.#266, [Storm.h:260](../source/Storm/include/Storm.h#L260))
  from `ARCHIVE_LoadArchives` ([D2WinArchive.cpp:84-95](../source/D2Win/src/D2WinArchive.cpp#L84-L95)).
  Higher priority wins: base = 1000, expansion = 3000, `patch_d2.mpq` = **5000** (max used).
  An archive opened at > 5000 out-ranks everything without touching any existing file.
- **Loose-file overlay is already live.** Fog's direct mode (`FOG_MPQSetConfig` fed from
  [Main.cpp:364](../source/Game/src/Main.cpp#L364)) resolves loose files under
  `BASEPATH\data\...` before MPQs. `C:\Diablo2\D2.ini` sets `BASEPATH=C:/Diablo2/ProjectD2`,
  and `C:\Diablo2\ProjectD2\data\` already contains ~819 loose files (PD2's HD UI PNGs).
  **The overlay mechanism we need is operational in this exact install.**
- **MPQ authoring:** the `squall` submodule contains **no MPQ code at all** (structures/
  utilities only). The tool must use **StormLib** to build `patch.mpq`. Reading at runtime is
  the game's own (original, closed) Storm.dll/Fog.dll — we only author, never serve.

### 3.2 D2Debugger extension points (the AssetReload template)

- Load chain: D2.Detours hooks `LoadLibrary*` at process attach; `D2Debugger.patch.rc`
  targets `D2Game.dll;D2Client.dll`, so D2Debugger's `DllMain` fires during early startup and
  spawns the standalone thread ([D2Debugger.imgui.d3d9.cpp:280](../source/D2Debugger/src/D2Debugger.imgui.d3d9.cpp#L280))
  → HTTP server on 127.0.0.1:8790 ([D2Debugger.mcp.cpp](../source/D2Debugger/src/D2Debugger.mcp.cpp)).
  This answers the "load early" requirement — it's already as early as game-DLL code can run.
- Routing is a hand-written if/else chain in `D2Mcp_HandleRequest`
  ([D2Debugger.LiveDispatch.cpp:586](../source/D2Debugger/src/D2Debugger.LiveDispatch.cpp#L586)).
  Adding endpoints = adding a branch. Mutations serialize under `g_mcpMutex`.
- **Game-thread marshalling exists:** `D2Gt_Call` posts work, `D2Gt_Pump` drains it on the
  game thread between frames, SEH-guarded ([D2Debugger.gtqueue.cpp:38-95](../source/D2Debugger/src/D2Debugger.gtqueue.cpp#L38-L95)).
  Two pumps already installed (in-world capture stub, D2Win menu `RenderMainFrame` hook).
- **Safe hot-swap prior art:** `ReloadProvider()`'s quiesce → free → read-fresh → rebind →
  restore dance ([D2Debugger.LiveDispatch.cpp:240-308](../source/D2Debugger/src/D2Debugger.LiveDispatch.cpp#L240-L308)).
- **Soft-reload verbs already exist:** `POST /action/*` can exit to menu and load a character
  (`d2dbg_exit_to_menu`, `d2dbg_load_character`, `d2dbg_launch_character` MCP tools).
- Estimated cost of AssetReload subsystem: one new `.cpp` + a line in
  `source/D2Debugger/CMakeLists.txt` + router branch + optional MCP tool in
  `conformance/d2debugger_mcp/server.py`. Watch-outs: no DirectX/heavy work at DllMain time;
  reach game symbols via the verified-address resolver/bridge, not raw imports; the repo's
  hard-coded absolute paths (spike-grade — keep AssetReload's paths configurable).

### 3.3 Asset cache map → live-reload feasibility

| Asset | Loader | Stale cache lives in | Verdict |
|---|---|---|---|
| Item DC6 (inv + flippy) | `ARCHIVE_LoadCellFile` ([D2WinArchive.cpp:31](../source/D2Win/src/D2WinArchive.cpp#L31), uncached) | `CellFile*` held by **D2Client.dll** (proprietary) + D2CMP.dll sprite cache (`FlushSpriteCache` #10053 = whole-cache flush only) | Hard in-place → **Tier B after Ghidra work; Tier C fallback works now** |
| UI fonts | `D2Win_10127_SetFont` ([D2WinFont.cpp:255](../source/D2Win/src/D2WinFont.cpp#L255)) | `stru_6F8FD8C0[14]` in D2Win | **Easy** (null slot → lazy re-read) |
| UI panels | per-control `CellFile*` | mostly D2Client-owned controls | Hard in-place → Tier C |
| Unit gfx (DCC/COF) | COF cache `dword_6F8FD654` ([D2Comp.cpp:27](../source/D2Win/src/D2Comp.cpp#L27)), refcounted; DCC frames in D2CMP cache | refcounted list + live `CompositeUnit` references | Hard in-place → **Tier C** |
| Data tables (.txt/.bin) | `DATATBLS_CompileTxt` #10578; loads **.bin** unless compile-txt toggled ([DataTbls.cpp:607](../source/D2Common/src/DataTbls/DataTbls.cpp#L607)) | `sgptDataTables` + baked cross-table indices + derived arrays | Hard; full `UnloadAllBins`+`LoadAllTxts` cycle exists — candidate for **menu-time rebuild** (investigate), else Tier D |
| Map DT1/DS1 | loaded once globally at init ([LevelsTbls.cpp:965](../source/D2Common/src/DataTbls/LevelsTbls.cpp#L965)) | `ppTileLibraryHash`/`ppLvlPrestFiles` + D2CMP tile cache | **Medium** — per-slot re-read takes effect on next level generation |
| Palettes/.pl2 (incl. invtransform tint tables) | `D2Win_10177`/`sub_6F8AE5E0` ([D2WinPalette.cpp:59-152](../source/D2Win/src/D2WinPalette.cpp#L59-L152)) | plain globals, tints applied per-draw | **Easy** — re-run loader |
| Strings .tbl | D2Lang.dll (proprietary), indices baked into data tables | inside D2Lang.dll | Hard → Tier C/D |

**Key implication:** live reload is a *tiered* problem, not a single mechanism — see §5.

---

## 4. Architecture

Three components:

```
┌─────────────────────────────┐   HTTP :8790    ┌──────────────────────────────┐
│  Asset Studio (Python)      │◄───────────────►│  Game process (PD2 Game.exe) │
│  - web UI (browser)         │                 │  D2Debugger + NEW AssetReload│
│  - catalog / alternates     │    Meshy API    │  subsystem (cache eviction,  │
│  - DC6/DCC codecs           │◄───────────────►│  soft reload, tier logic)    │
│  - Meshy staged pipeline    │   (https)       └──────────────┬───────────────┘
│  - Blender headless driver  │                                │ reads (Storm/Fog)
│  - StormLib patch.mpq export│    writes                      ▼
└──────────────┬──────────────┘   (atomic)      C:\Diablo2\ProjectD2\data\  (overlay)
               ▼                                C:\Diablo2\ProjectD2\*.mpq  (never touched)
   C:\Diablo2\AssetStudio\  (workspace, git-versioned)
```

- **Asset Studio app** — Python backend + browser UI. Originally lived at
  `tools/asset-studio/` in D2MOO beside the D2Debugger code it depends on; now its own repo
  (bethington/d2-asset-studio). Runs side-by-side with the game like the fun-doc dashboards do.
- **AssetReload subsystem** — `source/D2Debugger/src/D2Debugger.assetreload.cpp`. New routes
  (`GET /asset/status`, `POST /asset/reload` with a file list + tier hints). Executes
  invalidation on the game thread via `D2Gt_Call`.
- **Workspace** — `C:\Diablo2\AssetStudio\` (outside the repo; large binaries + generated
  art don't belong in git history of D2MOO; the workspace gets its *own* git repo for
  undo/versioning of mod content). Layout in §6.

### Overlay write discipline (ownership rules)

`ProjectD2\data\` already contains PD2's own loose files. The tool must:
1. Only ever write files listed in its own manifest; never modify/delete a loose file it did
   not create (PD2's 819 files are off-limits).
2. Write via temp-file + atomic rename (game may read mid-frame).
3. Provide "Restore vanilla" (remove every manifest-owned file) and never touch any `.mpq`.

---

## 5. Live reload: tiered strategy

The tool computes, per changed file, the cheapest tier that makes the change visible, and the
UI reports which tier ran. All tiers require **no process restart** except D.

| Tier | Latency | Mechanism | Covers |
|---|---|---|---|
| **A — in-place** | instant | Re-run reimplemented loader / null a cache slot via game-thread call | palettes, fonts (day 1); DT1/DS1 slot re-read (visible next level gen) |
| **B — targeted eviction** | instant | Evict the specific holder + flush D2CMP sprite cache (#10053) | item inv/flippy DC6, UI panels — **requires Phase 0 Ghidra work** to find D2Client's item `CellFile` holders |
| **C — soft reload** | ~5–15 s | Automated exit-to-menu → re-enter game via existing `/action` verbs; game rebuilds per-game caches | item art (fallback), unit gfx, likely UI panels; possibly data tables via menu-time `UnloadAllBins`+`LoadAllTxts` (investigate) |
| **D — full restart** | ~30–60 s | `conformance/tools/relaunch_pd2.ps1` + auto-rejoin via `d2dbg_launch_character` | data tables (until menu-time rebuild proven), strings |

Design rule: **Tier C makes the tool useful on day 1** (everything visual reloads in seconds
in single-player); Tier B is an *optimization* unlocked by investigation, not a blocker.

### 5.1 Showcase verbs ("show me the thing I just edited")

Every editable asset category gets a matching test-drive verb, built **in the same phase as
its editor** (decision #10). Mechanism: D2Game runs in-process in single-player, so these are
game-thread oracle calls exposed as `/showcase/*` routes on the AssetReload subsystem (same
pattern as the existing `/action/*` verbs).

| Category | Verb(s) | Phase |
|---|---|---|
| Items | Spawn exact item (base/unique/set + quality) dropped at the character's feet (shows flippy) and/or placed into inventory / on cursor (shows inv art) | 1 |
| Units | Force-spawn chosen monster/NPC class adjacent to the player; set its animation mode (neutral/walk/attack/get-hit/death…) for frame-by-frame inspection | 2 |
| Data | (covered by item/unit verbs — spawn something that uses the edited row) | 3 |
| Maps | Teleport-to-level + force level regeneration (tile changes take effect on next gen) | 4 |
| UI / Strings | Auto-open the relevant panel; trigger display of the edited string | 5 |

### Phase 0 investigation checklist (Ghidra + live D2Debugger)

1. **Loose DC6 override, empirically:** hex-tweak a shako inv DC6, drop it at
   `ProjectD2\data\global\items\...`, Tier-C reload, confirm visible. Confirms Fog direct
   mode applies to `items\*.dc6` (the existing loose files are only UI PNGs) *and* that
   PD2's renderer (d2gl / HD layer) doesn't bypass it. **This is the go/no-go experiment.**
2. **d2gl texture cache:** PD2 ships `d2gl.mpq` + an OpenGL renderer that may cache uploaded
   GL textures independently of D2CMP. Determine whether Tier B/C also needs a d2gl-level
   flush (test #1 answers this implicitly if art changes on-screen).
3. **D2Client item-gfx cache (Tier B):** in Ghidra (D2Client.dll), trace who calls
   `ARCHIVE_LoadCellFile` (#10039) for `invfile` art and where the `CellFile*` is stored;
   prototype eviction via `d2dbg_call_oracle` game-thread calls *before* writing any C++.
4. **Flippy path:** same trace for `flippyfile` (drop animation) — likely a different holder.
5. **Menu-time data-table rebuild:** at main menu (no game), call `DATATBLS_UnloadAllBins` +
   `DATATBLS_LoadAllTxts` via the oracle; verify stability and that a txt/bin edit shows up
   in the next game. If stable → Tier C covers txt; else Tier D.
6. **Item-spawn entry points (for decision #9):** in Ghidra (D2Game.dll), pin the
   item-creation path (spawn-with-quality → drop at position / place in inventory or cursor;
   D2MOO's D2Game ITEMS/UNITS sources are the reference map). Prototype via
   `d2dbg_call_oracle` before writing the `/showcase/item` route. Uniques need the specific
   unique index forced, not just rolled.
7. **Census:** enumerate every file (listfile) in all MPQs (base + pd2*) by extension →
   feeds the catalog and confirms no unknown PD2-specific formats are missed.
8. **`.bin` vs `.txt`:** PD2 ships compiled `.bin` in `pd2data.mpq`; the engine loads `.bin`
   by default. Decide the overlay story for data edits: ship edited `.txt` + toggle
   compile-txt (#11242), or compile to `.bin` in-tool (schema from `DataTbls.cpp`). Prefer
   in-tool `.bin` compile — no engine-flag dependency.

---

## 6. Workspace layout & data model

```
C:\Diablo2\AssetStudio\            (own git repo)
├── originals\                     read-only extracted mirror (per-archive provenance)
│   └── items\cap\...              organized by catalog, not raw MPQ path
├── alternates\
│   └── <asset_id>\                e.g. items/unique/harlequin_crest/inv
│       └── <alt_id>\              e.g. meshy-2026-07-18-a, png-import-1
│           ├── meta.json          provenance: source, meshy task ids, settings, credits
│           ├── source.png         stage S1 output (prepped/upscaled)
│           ├── model.glb          stage S2/S3 output
│           ├── renders\           stage S4 output (per-angle / per-frame PNGs)
│           └── final.dc6          stage S5 output
├── overlay\                       exact data\-tree of ACTIVE files, mirrored to
│                                  C:\Diablo2\ProjectD2\data\ on save
├── meshy_cache\                   raw API downloads keyed by task id (never re-bill)
├── export\patch.mpq               built from overlay\ via StormLib
└── manifest.json                  per-asset: active choice (original | alt_id),
                                   owned overlay files, required reload tier
```

- **Asset identity** is catalog-level (`items/unique/harlequin_crest/inv`), resolved to game
  file paths through the data tables (`invfile`/`uniqueinvfile`/`flippyfile` columns) — never
  raw paths in the UI. Community-canonical display names come from the string tables.
- **Alternates model:** original + N alternates per asset; picking one copies its output file
  into `overlay\` and syncs; picking "original" removes the overlay file. PNG imports enter
  the same alternates list as Meshy generations (they just skip stages S2–S4).

---

## 7. Catalog organization (all MPQ file types)

Decision on the user's open question: **characters, NPCs, and monsters share one pipeline
module** (identical token/COF/DCC machinery — confirmed by the cache map) **but are separate
catalog categories** (browsing context and completion tracking differ). Items are fully
separate. Maps group DT1 tilesets + DS1 presets together.

| Category | Formats | Editor | Reload tier | Phase |
|---|---|---|---|---|
| **Items** (base / unique / set) | DC6 (inv), DC6 (flippy) | Meshy pipeline + PNG import | B (goal) / C | **1** |
| **Units — Monsters, NPCs, Characters** | DCC, COF, animdata.d2 | Meshy multi-view → Blender rig | C | **2** (monsters/NPCs before player classes — single-component tokens vs. the player component/armor-level system) |
| **Data** | .txt / .bin (excel) | schema-aware grid editor, diff vs original | C (if menu rebuild proves out) / D | 3 (sliver in 1) |
| **Maps** | DT1, DS1 | tileset browser, later editing | A (next level gen) | 4 |
| **UI** | DC6 panels, fonts, cursors; PD2 loose PNGs | image swap | A (fonts) / C | 5 |
| **Strings** | .tbl | key/value editor | D | 5 |
| **Palettes** | pal.dat, .pl2 | preview + regenerate | A | 5 |
| **Audio / Video** | WAV, BIK | out of scope initially | — | later |

### 7.1 Item art specifics
- Inventory art must fit the item's `invwidth × invheight` cell grid (cells are 29 px) —
  the tool enforces canvas size per item from the data tables.
- **invtransform gotcha:** unique/set art can be tinted at draw time via `invtransform`
  (a palette-shift index applied per-blit — [D2WinPalette.cpp:155](../source/D2Win/src/D2WinPalette.cpp#L155)).
  The tool must preview alternates *with* the tint applied, and offer "clear invtransform"
  as a txt edit when custom art should render untinted.
- Flippy = multi-frame animated DC6; v1 generates it by tumbling the 3D model in the Blender
  rig (frame count matched to the original flippy).

### 7.2 DC6 codec
Pure-Python DC6 encoder/decoder (header v6, frame directory, RLE scanlines, palette-indexed)
with round-trip tests against extracted originals. Palette quantization to the correct act
palette with optional dithering; transparency index 0.

### 7.3 The txt sliver in Phase 1
Giving a unique its own art when it shares a base image requires editing one cell
(`invfile` in uniqueitems.txt). Phase 1 ships a minimal targeted-edit facility: patch one
column value → recompile that table's `.bin` → write to overlay → Tier C/D reload. The full
grid editor waits for Phase 3.

---

## 8. Meshy.ai integration

**Auth model — web-app session, not the API key (settled 2026-07-19, §24).** Meshy runs two
separate auth systems: the documented **openapi** key (`Bearer msy_…`) is billed per call and has
**no free retry**; the **web app** uses a browser **login session** (a Supabase JWT, ~1h expiry)
against the internal `api.meshy.ai/web/*` API, which is where the plan's **free ×8 retries** live.
The Studio drives the web API: it launches a dedicated logged-in Chrome, captures the JWT over the
DevTools Protocol (Network header sniff — the app uses axios/XHR, so a `fetch` hook misses it), and
calls `/web` on the user's behalf. Full reverse-engineering in `MESHY_WEB_API.md`.
The openapi client (`app/meshy.py`) and all `/api/meshy/*` routes were **removed** — there is one
generation path now, the Studio.

- **Task model:** async web tasks — register image → create **draft** (geometry) → poll → 3D
  preview → re-roll → **texture** the draft → poll → download GLB. GLBs cached in `meshy_cache\`
  by task id; local steps (Blender re-render, re-quantize) are free and repeatable.
- **Staged pipeline (per decision #6)** — each stage persists artifacts + settings, shows a
  review card in the Studio UI, and is individually retryable:
  - **S1 Source prep** (local, free): DC6 → PNG, **aspect-preserved** pad-to-square (a plain
    `resize((512,512))` distorts non-square sprites and Meshy bakes in the distortion), optional
    upscale — small inv sprites (a 2×2-cell helm is ~58×58 px) are otherwise too small.
  - **S2 Draft (geometry)** (web session): create a draft, poll (~40s), preview the GLB in
    three.js; **re-roll** the shape until it's right (free ×8 on the plan once the in-place PATCH
    is wired — until then a fresh parent-linked draft, ~20 credits).
  - **S3 Texture pass** (web session, optional): texture the approved draft from the source image
    ± a prompt; preview the textured GLB.
  - **S4 Sprite render** (local, free): Blender headless renders — for items a static beauty
    shot at the classic D2 item angle + a tumbling sequence for the flippy; interactive
    angle/lighting tweak in the UI, then re-render.
  - **S5 Game-format encode** (local, free): downscale to cell grid, color-grade + quantize to
    act palette (dither options), DC6 encode, side-by-side A/B against the original, push button.
- **Retry guard:** the session pill shows tier + free-retry allotment; re-rolls flag whether they
  were free.
- **Licensing check (open item):** confirm the Meshy plan's terms permit redistribution of
  generated assets in a shared patch.mpq.

### Unit pipeline (Phase 2 extension)
- **Input:** decode the unit's DCC frames; assemble per-direction contact sheets; feed
  Meshy's multi-image image-to-3D with front/side/back views extracted from the 8 original
  directions.
- **Render rig:** Blender camera locked to D2's 2:1 dimetric projection; yaw steps of 45°
  (8-direction units) or 22.5° (16-direction); frame counts and speeds matched from
  `animdata.d2`; game modes (neutral, walk, run, attack, cast, get-hit, death …) mapped to
  animation clips (Meshy rigged output or Mixamo-style retarget).
- **Output:** per-direction sprite sheets → **DCC encode**. A Python DCC encoder is the
  phase's main technical risk (format is much harder than DC6); evaluate existing community
  encoders before writing one. COF files are reused unchanged when frame counts match the
  original (keep them matching in v1).

---

## 9. Phases

### Phase 0 — Investigation & rails
Deliverables: the §5 checklist answered (each experiment logged in this doc), MPQ census,
DC6 codec with round-trip tests, workspace scaffold + manifest schema.
**Definition of done:** a hand-modified shako inv DC6 is visible in the running game without
process restart (any tier), and we know which tier item art needs.

### Phase 1 — Item Art Studio (priority deliverable)
1. Backend skeleton (the repo root): FastAPI/Flask + static web UI; config for game
   path, workspace path, Meshy key.
2. Catalog: parse item tables (base/unique/set) → browsable list with original art previews
   (name, cell size, current invfile/flippyfile, invtransform badge).
3. Alternates: PNG import path end-to-end (import → fit-to-cells → quantize → DC6 → overlay
   → live reload). This is the full loop minus Meshy — ship it first.
4. AssetReload subsystem in D2Debugger: `/asset/reload` (tier logic: B if proven in Phase 0,
   else C via existing action verbs), `/asset/status`, and `/showcase/item` (spawn at feet /
   into inventory / on cursor, per §5.1); MCP tool wrappers.
5. Meshy staged pipeline S1–S5 with review cards; flippy generation via Blender tumble.
6. Alternates gallery + active-pick; manifest-driven overlay sync; "restore vanilla".
7. txt sliver: `invfile` column edit + single-table bin recompile (§7.3).
8. `patch.mpq` export via StormLib + a verification mode that loads the game with the
   export instead of the overlay.
**Definition of done (amended 2026-07-18):** Harlequin Crest has ≥2 alternates (one Meshy,
one PNG import), switchable from the UI with in-game update via one-click reload. ~~Export
loads in a clean PD2 without the tool running~~ — struck: archive registration requires the
D2Debugger AssetReload hook at runtime, so a standalone-consumer load mechanism is a
*distribution* concern, deferred to Phase 5 (loader options listed there). The editing-loop
DoD is met (§18–§25).

### Phase 2 — Units (monsters/NPCs first, player classes later)
DCC decode + sheet extraction; Meshy multi-view generation; Blender direction/animation rig;
DCC encode (or community encoder integration); Tier C reload; catalog categories for
Monsters / NPCs / Characters; showcase verbs: force-spawn unit + animation-mode control
(§5.1).

### Phase 3 — Data table editor
Schema-aware grid (schemas already exist in `DataTbls.cpp` field tables), diff-vs-original,
validation, bin compile, menu-time rebuild if Phase 0 proved it (else automated Tier D).

### Phase 4 — Maps/tiles
DT1/DS1 browser with tile rendering; per-slot re-read (Tier A, visible on next level gen);
showcase verbs: teleport-to-level + force regeneration (§5.1).

### Phase 5 — UI, fonts, strings, palettes, polish & distribution
Showcase verbs: auto-open panels, trigger edited strings (§5.1).
Distribution story: consumers of a shared `patch.mpq` need a load mechanism (PD2 does not
auto-load arbitrary MPQs) — options: a tiny loader that registers the archive at priority
> 5000, or PD2-launcher integration. (The "ship as loose `data\` files" option is struck —
§12 proved PD2 does not serve loose DC6.)

---

## 10. Risks & open questions

1. **~~PD2 renderer bypass (top risk)~~ — PARTIALLY RESOLVED 2026-07-18 (§12):** the loose
   `data\` DC6 overlay does NOT render in PD2 (proven with a solid-green potin, full relaunch,
   both HOMEPATH+BASEPATH). PD2 serves loose *PNGs* (UI only) through d2gl's HD pipeline, a
   different system from Fog's DC6 path. Consequence: the overlay must be a high-priority
   `patch.mpq`, OR item art must be replaced as d2gl HD PNGs. Next Phase 0 experiment:
   author a `patch.mpq` at priority > 5000 (StormLib path already built) and confirm a DC6
   override renders. Whether d2gl also disk-caches upscaled DC6→PNG is still open.
2. **Tier B may stay out of reach** for some holders (D2Client internals) — acceptable;
   Tier C is the guaranteed floor in single-player.
3. **Meshy quality on pixel art:** tiny sprites may produce poor 3D; mitigations: upscale
   stage, multi-candidate prep, staged review (decision #6). Expect a tuning period.
4. **DCC encoding (Phase 2):** hardest format in the plan; scout community encoders early.
5. **Data-table edits vs. saved characters:** txt changes can invalidate SP saves/items —
   auto-backup `Save\` before any data-affecting reload.
6. **Online play:** out of scope by decision #1. Revisit only with PD2-team guidance.
7. **~~Meshy licensing/limits~~ — RESOLVED 2026-07-18:** on a paid plan the subscriber owns
   generated assets outright with full distribution/sale rights (free plan = CC BY 4.0,
   credit required) — per Meshy's Terms of Use + Help Center ownership articles. The real
   constraint on sharing a patch.mpq is upstream: the art derives from Blizzard sprites, so
   distribution is governed by D2 modding norms (same status as every other PD2 mod asset),
   not by Meshy.
8. **`.bin` schema drift:** PD2 may extend txt columns beyond vanilla 1.13c schemas —
   compare census `.bin` sizes against `DataTbls.cpp` schemas during Phase 0.

---

## 11. Operating notes for implementation sessions

- The conformance rig and this tool share D2Debugger. Don't destabilize the proving loop:
  AssetReload changes rebuild D2Common/D2Debugger patches → coordinate with the shadow/
  provider-reload mutual-exclusion rules already documented in memory.
- The game for testing launches via `conformance/tools/relaunch_pd2.ps1` (oracle on :8790).
- Every Phase 0 finding gets written back into §3/§5 of this doc (source-of-truth
  principle), and reusable engine facts also go to Ghidra.

---

## 12. Phase 0 results log (2026-07-18)

Work landed under the repo root (Python package `pyd2` + `scripts/` + `tests/`).
Workspace scaffold created at `C:\Diablo2\AssetStudio\` (own git repo).

### Built & verified
- **StormLib read wrapper** (`pyd2/mpq.py`) — StormLib.dll built x64 from source
  (`-DSTORM_USE_BUNDLED_LIBRARIES=ON`), dropped in `bin/` (gitignored).
  ctypes prototypes are pinned (64-bit handle-truncation bug fixed). `read_effective()`
  walks the PD2 priority order and skips phantom hash entries (protected archives return
  `SFileHasFile==true` + `size==0xFFFFFFFF` for files they don't really hold — handled).
- **DC6 codec** (`pyd2/dc6.py`) — decode/encode, byte-exact round-trip verified on 13 real
  files (`tests/test_dc6_roundtrip.py`): `invcap` (Cap/Shako, 56×56), `flpcap` (17-frame
  flippy), all belt potions. Re-encoded bytes match original lengths exactly.
- **Palette** (`pyd2/palette.py`) — `pal.dat` channel order is **BGR** (verified: healing
  potions decode red, mana blue only under BGR). DC6→RGBA/PNG render works.
- **Extraction** — item txt tables (misc/armor/uniqueitems), ACT1/ACT2 palettes, and 14 item
  DC6s pulled into `originals\`. Confirmed data model facts: Shako (`uap`) shares base Cap's
  `invfile=invcap`; unique `Harlequin Crest` has empty `invfile` (inherits base) +
  `invtransform=cgrn` (the green tint). This validates §7.3 — giving the Shako its own art
  needs a `uniqueitems.txt` edit.
- **Census** (`scripts/census.py`) — targeted reads solid; full enumeration needs a
  `(listfile)` (PD2 archives lack one, so StormLib over-enumerates phantom entries — counts
  inflated, extension *presence* reliable). Extensions confirmed: **pd2data.mpq ships 94
  editable `.txt`** + `.bin` + `.tbl` (retires the "only compiled .bin" worry, checklist #8);
  **pd2assets.mpq** = `.dcc/.dc6/.cof/.dt1/.dat` (the PD2 art set); **pd2maps.mpq** = `.ds1`;
  **d2gl.mpq** = `.slang` shaders + `.png` + `.dc6` (the HD renderer's own assets).

### GO/NO-GO experiment — loose DC6 override: **NO-GO**
Hypothesis (checklist #1): drop an edited DC6 at `ProjectD2\data\global\items\…` and, with
Fog direct-mode, the game serves it over the MPQ. **Result: it does not.**

Method: solid bright-green fill of all 12 belt-potion inv DC6s → deployed to **both**
`C:\Diablo2\ProjectD2\data\` (BASEPATH) and `C:\Diablo2\data\` (HOMEPATH) → **full clean
relaunch** of PD2 with `-w -direct` (`scripts/relaunch_pd2_direct.ps1`) → fresh character
load → belt still shows the **original purple/blue potions**, zero green. Evidence:
`census/belt_after_full_relaunch_solidgreen_NOGO.png`.

Why (best current understanding):
- PD2 *does* serve loose files — but the 771 live loose files are **PNGs under
  `global\ui\FrontEnd\`** (title/char-select art), loaded by **d2gl's HD-PNG pipeline**,
  which is a *separate system* from Fog's classic DC6 loader. There are **no loose item-art
  PNGs** — item art still originates from DC6 in the MPQs.
- PD2's Fog/Storm evidently does not honor the classic `-direct` loose-DC6 path (or it is
  disabled), so an edited DC6 on disk is never consulted.

Consequences for the plan:
- **Decision #2 (hybrid loose overlay) is invalidated for DC6/binary assets.** The live
  workspace can still be loose files for *authoring/versioning*, but the game must be fed via
  a **`patch.mpq` registered at priority > 5000** (StormLib authoring path already built) —
  OR item art must be provided as **d2gl HD PNGs** in d2gl's expected location/format (worth
  investigating; may be the *native* PD2 way to reskin items and could sidestep DC6 entirely).
- The Tier-C "soft reload" floor still stands for whatever override mechanism we pick; the
  open question moved from "which reload tier" to "which *override channel* PD2 actually
  reads" (patch.mpq vs d2gl-HD-PNG).

### Not finished
- **Ghidra investigation** (checklist #3/#4/#6: D2Client item-art cache eviction targets +
  D2Game item-spawn entry-point addresses) — the sub-agent gathered all D2MOO-side semantics
  but hit the Anthropic session limit before pinning PD2 D2Game.dll addresses. Resume there.
- **Next Phase 0 experiment (now the go/no-go):** build a `patch.mpq` containing the
  green potions, get it registered at priority > 5000 in the live game, and confirm the
  override renders. Also test the d2gl-HD-PNG channel for one item. One of these two is the
  real override mechanism; the plan's Phase 1 overlay design should follow whichever wins.

### Environment gotchas logged
- Multi-monitor + display scaling: `GetWindowRect` returns *logical* coords but monitor
  captures are *physical* pixels (monitor is 2194×1234 physical). The reliable capture path
  is `screenshot all` (virtual screen) then crop by eye from a downscaled overview; per-window
  and per-monitor crops misalign. cnc-ddraw's game window also resists `MoveWindow`.
- `d2dbg_main_menu_singleplayer` FAULTS if called before the title screen is fully up after a
  relaunch; wait until `charListLoaded`/`charSelectReady` settle (~30 s) before driving menus.

### GO/NO-GO #2 — high-priority MPQ override: **INCONCLUSIVE (do not modify PD2 archives)**
Follow-up to experiment #1. Built the write side of the StormLib wrapper
(`pyd2/mpq.build_archive` + `add_files`) and authored a verified `patch.mpq` from the
overlay (`scripts/build_patch_mpq.py` — 12 green potions, reads back byte-exact).

To test whether a *higher-priority archive* overrides item DC6 where loose files didn't, I
injected the 12 green potions into `patch_d2.mpq` (priority 5000, the highest stock archive,
which already overrides `d2data.mpq`) — backed up first, via `scripts/patch_d2_experiment.ps1`
(elevated: kill → inject → relaunch `-direct`). The injection verified byte-exact in the
archive. **Result: the game hangs on the LOADING screen** (captureCount frozen at 33; evidence
= game window stuck on "LOADING…"). Restoring the backup (`scripts/restore_and_relaunch.ps1`)
returned the game to a healthy in-world state (captureCount climbs normally), confirming the
injection was the cause.

Conclusion: **StormLib rewriting PD2's own `patch_d2.mpq` corrupts it for the game's loader.**
(PD2's archives lack a normal `(listfile)` and use `.xxx` phantom entries; StormLib's
add-and-rewrite doesn't preserve whatever layout the loader depends on.) This does not answer
the priority-override question — it proves the *method* was wrong. **Firm rule: never modify
PD2's existing archives.** The correct test is a **separate** archive registered at runtime
via `SFileOpenArchive(path, >5000, …)`, which is exactly the AssetReload subsystem's job
(Phase 1) — not a Phase 0 script. The MCP oracle exposed today only calls the coord-family
dispatchers; a general Storm-export call needs the `POST /oracle` absolute-address path (Storm
isn't in the D2Common resolve table) or a small addition to D2Debugger. That is the first
build task of Phase 1, and it doubles as the live-reload archive-registration primitive.

### Reuse source found — OpenD2 (`C:\Users\benam\source\cpp\OpenD2`)
The user flagged OpenD2 as prior code worth reusing. It contains reference C/C++ decoders for
**every format in this plan**: `Engine/DC6.cpp`, **`Engine/DCC.cpp`** (the hardest format —
Phase 2 unit graphics), `Engine/DS1.cpp`, `Engine/DT1.cpp`, `Engine/COF.cpp`, plus
`Engine/EditorCompat/mpq/` (PKWARE `Explode.c`, `MpqView`) and a `dc6_to_png` tool. Plan:
treat OpenD2 as the authoritative reference when porting DCC/DS1/DT1 decoders to `pyd2` (or
compile its decoders into a helper DLL called via ctypes, the same pattern as StormLib). Our
Python DC6 codec is already done and round-trip-verified; OpenD2's DC6 is a cross-check.
**DCC is the big win** — a working reference decoder de-risks Phase 2's hardest task.

### Phase 0 net status
- **Confirmed:** loose-`data\` DC6 override does NOT render in PD2 (#1). d2gl has no disk
  texture cache and only HD-cursor/HD-text (not HD items), so items are DC6-sourced and a
  *working* override channel should show — the open question is purely *which channel*.
- **Open (now Phase 1's first task):** register a separate high-priority `patch.mpq` at
  runtime and confirm the override renders. Build the `SFileOpenArchive` primitive into
  D2Debugger; it is also the archive-registration half of live reload.
- **Tooling ready:** MPQ read+write, DC6 codec, palette, workspace, patch.mpq authoring,
  and reversible experiment scripts all built under the repo root.
- **Environment:** patch_d2.mpq restored byte-exact; game healthy. All test DC6s removed
  from loose dirs (kept in workspace overlay). No PD2 archive left modified.

### Ghidra investigation — RESUMED & LARGELY COMPLETE (2026-07-18)
Full detail in `GHIDRA_FINDINGS.md`. Ghidra had both PD2 binaries open
(D2Client base 6fab0000, D2Game base 6fc20000, D2Common 6fd50000). Read-only.

**Q3 — item spawn for `/showcase/item`: DONE (verified against the game's own quest-drop code
`ITEMS_FindItemByDataCode @ 6fc8b430`).** The recipe:
```
idx = DATATBLS_GetItemDataByCode('uap ')                  // 6fd50000-based 6fd9e1d0, __stdcall
ITEMS_CreateAndDropItem(pGame, pPlayerUnit, idx, TRUE)    // 6fc8b070, int __stdcall(Game*,UnitAny*,int,BOOL)
```
- `ITEMS_CreateAndDropItem @ 6fc8b070` — clean `int __stdcall(Game*, UnitAny*, int nItemCode, BOOL bDrop)`;
  bDrop=TRUE drops at the player's feet (flippy shows; pick-up shows inv art). Directly
  oracle-callable on the game thread.
- `DATATBLS_GetItemDataByCode @ 6fd9e1d0` (D2Common, `int __stdcall`) resolves a 4-char code
  DWORD → tbl index (or precompute the index from the extracted tables and skip this).
- Force a specific unique/set: `CreateItemWithParams @ 6fc31880`
  (`int __stdcall(Game*, uint code, int quality, ItemCreationDesc*, uint dropFlags)`); the
  unique-index field in the desc + quality enum are the one TBD for uniques.
- Runtime inputs: server `Game*` + server player `UnitAny*` — the D2Debugger capture hook
  already holds a live server `UnitAny*`; the oracle supports live-handle args; `Game*` derives
  from the player unit. Wire in the AssetReload `/showcase/item` route (Phase 1).

**Q1/Q2 — item-art cache (Tier-B eviction target): characterized.** Item inv sprites render
through D2Client's Gfx vtable interface; the inv `CellFile*` is cached in the item unit's
per-unit client GfxInfo (loaded via D2Win `ARCHIVE_LoadCellFile` #10039), decompressed cells in
D2CMP's cache. Targeted eviction = null the unit's cached cell pointer + D2CMP whole-cache flush
(`#10053`, the only exposed lever). Exact GfxInfo field offset is the lone detail left for
Phase-1 Tier-B work. Tier B remains an optimization gated on the override channel; Tier C (soft
reload) is the day-1 floor and needs none of this.

**Net:** the showcase-spawn verb is fully specified and ready to build. The Ghidra investigation
item is closed except the two small offsets (unique-index desc field, GfxInfo inv-CellFile
field) that are naturally pinned while implementing their respective Phase-1 routes.

---

## 13. GO — override channel PROVEN + AssetReload subsystem shipped (2026-07-18)

**The override channel works.** A separate `patch.mpq`, authored by the Asset Studio tool and
registered into the live game at priority 9000 via a new D2Debugger primitive, **overrides the
base-game item DC6s and renders live** (belt potions turned solid green). No existing MPQ was
touched; no crash. Evidence: `census/belt_GO_highprio_archive_override_RENDERS.png`. This
resolves the #1 architectural blocker and validates decision #2's replacement (separate
high-priority archive, not loose files).

### What shipped
- **`source/D2Debugger/src/D2Debugger.assetreload.cpp`** — the AssetReload subsystem. Resolves
  Storm.dll's `SFileOpenArchive` (#266) / `SFileCloseArchive` (#252) by ordinal and marshals
  the open onto the game thread (via the gtqueue + menu pump). Wired into the router
  (`D2Debugger.LiveDispatch.cpp`) and build (`D2Debugger/CMakeLists.txt`). New HTTP routes on
  :8790:
  - `GET  /asset/status` — is an overlay archive registered (handle, path, priority).
  - `POST /asset/register {path, priority?=9000, confirm:true}` — `SFileOpenArchive` on the game
    thread; priority > patch_d2.mpq's 5000 so our archive wins every shared file.
  - `POST /asset/close {confirm:true}` — `SFileCloseArchive` (revert to stock resolution).
- **Tool side:** `pyd2.mpq.build_archive` now forces **MPQ v1 + PKWARE** compression;
  `scripts/build_patch_mpq.py` authors the archive, `scripts/verify_patch_mpq.py` round-trips it.
- **Deploy:** `scripts/deploy_debugger_and_relaunch.ps1` (elevated) copies the rebuilt
  D2Debugger.dll into `build-1.13c\patch` and relaunches with `-direct` + debugger.

### Critical gotcha discovered (and fixed)
**Diablo II's Storm.dll cannot inflate zlib.** The first patch.mpq used StormLib's default zlib
compression; registering it hung the game on the LOADING screen the instant the game read a file
from it (same signature as the earlier patch_d2 corruption — both were StormLib output the
ancient Storm couldn't parse). Fix: author with **PKWARE DCL (`MPQ_COMPRESSION_PKWARE=0x08`) and
force format v1 (`MPQ_CREATE_ARCHIVE_V1`)** — the format D2 archives natively use. With that, the
game loaded cleanly and the override rendered. **Rule: all Asset Studio MPQs must be v1 + PKWARE
(or stored), never zlib/v2+.**

### Reload semantics learned
The green art appeared on **first world entry** because the archive was registered at the menu,
*before* the game loaded item art. So the working reload flow today is: **register archive →
(re)enter game**. For an in-world live swap without re-entering, re-register + evict the item
cache (Tier B, GfxInfo cell ptr + D2CMP #10053 per §Ghidra) or soft-reload (Tier C). The channel
itself is settled; the remaining reload work is latency optimization, not feasibility.

### Immediate next steps (Phase 1 proper)
1. Confirm d2gl renders the override at its true resolution (green showed through the HD
   upscaler — looks fine; verify with real art, not a flat fill).
2. Build the `/showcase/item` spawn verb (`ITEMS_CreateAndDropItem @ 6fc8b070`, recipe in
   `GHIDRA_FINDINGS.md`) so any item can be summoned to test its art, not just belt potions.
3. In-world live reload (Tier B/C) so edits show without re-entering the game.
4. Start the Asset Studio Python app (catalog + PNG-import → DC6 → patch.mpq → `/asset/register`).

---

## 14. Spawn verb + Ghidra correction (2026-07-18)

### `/showcase/item` spawn verb — wired, ABI-verified, safety-guarded, pending server handle
Added `POST /showcase/item {code, drop, confirm}` to the AssetReload subsystem
(`D2Debugger.assetreload.cpp` SpawnItemImpl; capture accessor `D2Capture_UnitOfType` added to
`D2Debugger.capture.cpp`). It resolves `ITEMS_GetDataByCode @ 6fdc1940` (code→classId) and
`ITEMS_CreateAndDropItem @ 6fc8b070` by GetModuleHandle+RVA and calls them on the game thread —
the exact sequence the game's own quest-drop code uses. ABI verified by disassembly (both clean
__stdcall; GetDataByCode is 1-arg RET 4).

**Not yet functional:** it needs the SERVER `Game*` + SERVER player unit, but the capture hook
grabs the **CLIENT** player in single-player. `UnitAny.pGame` (+0x80) is a union field — `Game*`
for server units, a tick count for client units — so the route's pointer-sanity guard correctly
rejects the client unit (clear error, no crash) rather than passing garbage to the allocator.
Finishing it is a scoped next increment: source the server handle via
`GAME_GetGameByClientId` + `SUNIT_GetServerUnit(pGame, UNIT_PLAYER, clientPlayerGUID)` (full path
in GHIDRA_FINDINGS.md). Until then, art is tested via the belt-potion override (§13), which works.

### Ghidra correction applied
`DATATBLS_GetItemDataByCode @ 6fd9e1d0` (D2Common) was mislabeled — its body is a pure
`return **(short**)arg` double-deref, NOT an item-code lookup, and its "#10601" annotation is a
stale vanilla-ordinal guess that doesn't match PD2. Wrote a correcting plate comment pointing to
the real resolver (`ITEMS_GetDataByCode @ 6fdc1940`) and saved D2Common. Left un-renamed (its true
semantic identity is undetermined — export entry only, no internal callers — so not guessed).

---

## 15. Spawn verb — server handle solved; faults inside the call (2026-07-18)

Big progress on `/showcase/item`, stopping at a well-instrumented point.

**Solved:** the server `Game*`. Hooked `GAME_ProcessGameFrameTick @ 6fc4e050` (the per-frame
server tick; game arrives in EAX) with a naked stub that caches it every frame. Walk to the
player works: `Game.pClientList (+0x88) -> GameClient.pPlayer (+0x174)`. `ITEMS_GetDataByCode`
returns the right classId (435 for 'uap'). ABI verified by disassembly (RET 0x10, 4 stdcall args).

**Blocked:** `ITEMS_CreateAndDropItem` FAULTS internally (SEH-caught, game unharmed). Live
diagnostics via `/asset/status` (`spawnDbg`: stage/game/client/player/classId) show every input
resolves to a valid-looking value and the fault is at stage 7 (inside the call, at
`GetUnitLevelPosition(pUnit)`). Leading cause: **execution context** — the spawn runs from the
CLIENT-side capture pump, but server item-creation needs the **server game-lock/frame context**
(the game calls this function from inside its own server tick). Fix: run the spawn from INSIDE
the `GAME_ProcessGameFrameTick` hook (a one-shot request the stub consumes) so it executes in the
server context. This is the next increment; the diagnostics endpoint is in place to verify it.

**Dead-ends ruled out (save the next session the time):** piggybacking on
`GAME_UpdateProgress_WithDebugger` fails — D2Debugger's patch hardcodes D2Game 0x54400, which is
`PLAYER_HandleDisconnect` in PD2 (misaligned/dead). `GAME_UpdateGameTick @ 6fd02600` is a
unit-event handler, not per-frame. The D2Common capture hook only sees the CLIENT player (union
at +0x80 is a tick count, not pGame). `GAME_ProcessGameFrameTick` (game in EAX) is the right
per-frame server anchor.

**Shipped this round:** the server-game frame-tick hook + struct walk + spawn diagnostics in
`D2Debugger.assetreload.cpp`; `/asset/status` now reports `frameHook`, `serverGame`, and
`spawnDbg`. Built, deployed, and exercised live. `patch_d2.mpq` restored stock; game healthy.

---

## 16. Spawn verb — server-side item CREATION works; drop is the last mile (2026-07-18)

Moved the spawn to execute **inside the server frame-tick handler** (hook on
`GAME_ProcessGameFrameTick @ 6fc4e050`, game in EAX; spawn runs there under SEH). That fixed
creation: **`POST /showcase/item {code:"uap", drop:false}` SUCCEEDS** — the item is created
server-side, no fault (diag stage 8). Live diagnostics proved every input correct: the player
walk `Game.pClientList(+0x88) -> GameClient.pPlayer(+0x174)` yields the REAL player
(`dwType=0, dwClassId=2=Necromancer matching the launched char, dwUnitId=1, valid pPath`), and
`ITEMS_GetDataByCode('uap')=435`. The hard part — obtaining a valid server game+player and
running item code in the right context — is **solved**.

**Only `drop:true` faults** (SEH-caught, game fine), isolated to the drop/placement path
(`ITEMS_DropItemAtUnitPosition @ 6fcf2d90` + the `ValidateAndWriteCommand @ 6fcac810` sequence).
Likely the client-notify/command context isn't valid at frame-tick entry.

**Next increment (last mile), two options — (b) preferred:**
(a) fix the drop context (run the drop at a different point in the tick / set up its command
buffer); (b) BETTER for the item-art goal — create the item and place it directly in the
player's **inventory** (shows the inventory DC6, the priority surface) via
`CreateItemWithParams @ 6fc31880` (returns the item) + an inventory-add call, sidestepping the
drop/flippy path. Diagnostics (`/asset/status` `spawnDbg`) are in place to guide it.

**Shipped this round:** server-frame-tick spawn execution + full player-walk validation + rich
spawn diagnostics in `D2Debugger.assetreload.cpp`; `/asset/status` reports frameHook/serverGame/
spawnDbg(stage,game,client,player,classId,playerCls,playerId,playerPath). Built, deployed,
exercised live (create verified working). Note: `drop:false` items are orphaned (cleared on game
restart) — harmless in the SP test rig. `patch_d2.mpq` restored stock; game healthy.

---

## 17. Drop path RULED OUT (timing-independent); inventory placement is the route (2026-07-18)

Tested Path A (drop-at-feet): restructured the frame-tick stub to run the spawn at frame-tick
EXIT (call the trampoline to run the tick body first, then the pending spawn) so the per-frame
command context would be set up. **Still faults at stage 7** (the drop tail). So the drop fault is
NOT a frame-tick entry-vs-exit timing issue -- it's intrinsic to the drop's per-command/
client-notify context (`ValidateAndWriteCommand @ 6fcac810` + `ITEMS_DropItemAtUnitPosition @
6fcf2d90`), which the game only sets up inside its own loot-drop command processing. Creation
still works reliably with the post-tick stub (`drop:false` -> spawned, game healthy).

**Remaining route = Path B (inventory placement)** -- also the better fit for inv-art testing.
Next session (functions pinned in GHIDRA_FINDINGS.md): `CreateItemWithParams @ 6fc31880` (clean
stdcall, returns the item `UnitAny*`; call from a naked wrapper that zeroes EBX -- it derefs EBX
in a magic-0x187 check) -> `PLAYER_PlaceItemInInventory(item, player, game, 1)` (find its PD2
address; D2Game `__fastcall`). That shows the inventory DC6 directly.

**Net state of the spawn verb:** server-side item CREATION works end-to-end (server game+player
resolved and validated, classId correct, ABI verified, runs in server context). Making the item
VISIBLE is the last mile: drop-at-feet is blocked (command context), inventory placement is the
documented path. All diagnostics + the AssetReload subsystem are shipped and deployed.

---

## 18. Asset Studio APP SHIPPED — full item-art loop proven live (2026-07-18)

The actual editing tool is built and **proven end-to-end**: a Flask + browser app
(`app/`) that browses all 1400+ PD2 items (read live from the MPQs, each
rendered to PNG), imports a PNG as an alternate (auto-fit to the item's cell grid + palette
quantize → DC6), and **pushes it live** — builds a v1+PKWARE `patch.mpq` from the active choices
and registers it at priority 9000 via the AssetReload endpoint. Verified: importing a magenta PNG
for the belt potions renders them magenta in-game (`census/APP_LOOP_WORKS_magenta_potions.png`).

**Components:** `app/catalog.py` (item-table parsing, unique/set invfile inheritance),
`app/assets.py` (DC6↔PNG, palette quantize, overlay + manifest, rotating-filename patch.mpq
build), `app/server.py` (Flask API + `/api/push` close-rebuild-register + `/api/reload`),
`app/static/*` (item gallery + detail + PNG import + push/reload). README in `app/README.md`.

**Two real bugs found & fixed during the live test:**
1. **int16 overflow in palette quantization** — squared color distances (231²=53361) overflowed
   int16 and wrapped negative, so nearest-color picked garbage (magenta rendered green). Fixed to
   int32; magenta now maps to (252,0,128) pink correctly.
2. **patch.mpq file lock** — the running game keeps the archive's OS handle even after
   SFileCloseArchive, so rebuilding a fixed filename fails (WinError 32 / Errno 13). Fixed by
   writing a fresh rotating `patch_<n>.mpq` each push (the game closes the old on re-register) and
   pruning stale ones.

**Reload-tier learning (confirms §3.3 / the plan's cache map):** a SOFT reload (exit-to-menu +
re-enter) does NOT refresh already-cached item art, because D2CMP's decompressed-sprite cache is
process-global with only a whole-cache flush. Reliable path today: register at the MENU on a FRESH
process, then enter (the belt loaded the override cleanly this way). Tier-B in-world reload
(D2CMP `FlushSpriteCache #10053` from an AssetReload route) remains the future optimization to
avoid the relaunch.

**Status of the whole tool:** the core live item-art editing loop is DONE and PROVEN. The tool
turns the override channel into a real product. Remaining roadmap (unchanged): Meshy.ai staged
pipeline, flippy + unit-graphics phases, Tier-B in-world reload, and the item-spawn convenience
(inventory-placement recipe in GHIDRA_FINDINGS.md).

---

## 19. Reload — automated one-click "Full reload" SHIPPED + reload behavior mapped (2026-07-18)

Chose the reliable, safe reload over the risky in-world flush. The Asset Studio app now has a
**"Full reload"** button (`/api/full-reload`) that, in one click: Popens the elevated `-direct`
relaunch (1 UAC) → waits for the fresh process to boot → registers the overlay at the menu →
drives single-player → char-select → launch → in-world. **Tested end-to-end:** the belt loaded
the app's imported art cleanly (`census/` belt shots). A fresh process guarantees clean art
because both the D2Client CellFile cache and the D2CMP cell cache start empty. Also kept a
faster **"Soft reload"** (`/api/reload`, exit→re-enter, no UAC) with a caveat.

### Reload behavior — empirically mapped (important for future Tier-B)
- **Fresh process + register at menu + enter = clean art** (proven repeatedly: magenta, then
  cyan/blue belt potions).
- **Soft reload (exit-to-menu + re-enter, same process) does NOT give clean item art.** It DOES
  free + re-read the item CellFile (the belt art *changed*), but leaves **stale D2CMP decompressed
  cells**, so the potions rendered broken/empty. So true in-world reload needs re-read CellFile
  **AND** a D2CMP cache flush.
- **The D2CMP flush lever is `FlushCelCache @ D2CMP 6fe1bc60` (#10078, `void __stdcall`)** — it
  loads the LRU cache global (0x6fe872b0) and calls `CMP_DestroyLRUCache`, which **deallocates the
  cache pool** (not just evicts). So a blind call risks crashing the next sprite draw unless the
  cache is re-initialized (`CMP_InitializeCelCache`) — the flush+reinit sequence lives cross-DLL
  and is unverified. **Future true-instant Tier-B:** evict the client's item CellFile (still
  unmapped in D2Client) + `FlushCelCache` + re-init, verified carefully. Until then, "Full reload"
  is the reliable path and it's one click.

The Asset Studio tool is now feature-complete for the item-art loop: browse → import PNG → DC6 →
push → **one-click reliable reload** → see it in-game. Remaining roadmap unchanged (Meshy pipeline
[needs MESHY_API_KEY at C:\Diablo2\AssetStudio\meshy.key + Blender], flippy/unit phases,
true-instant Tier-B, item-spawn inventory placement).

---

## 20. Meshy.ai pipeline SHIPPED + proven (2026-07-18)

> **SUPERSEDED (§24, 2026-07-19):** this describes the original **openapi key** pipeline, since
> removed. The generation path is now the web-app Studio (free retries). Kept as a factual record;
> `app/meshy.py` and the `/api/meshy/*` routes below no longer exist.

The headline feature works end-to-end. With the API key in `C:\Diablo2\AssetStudio\meshy.key`
(gitignored; balance 5353 credits at start), the app now generates item art via Meshy 3D:

**Flow (staged, decision #6):** S1 extract the item's original inv DC6 → PNG + upscale to 512²
(local) → S2 `image-to-3D` submit + poll (Meshy, ~110s, ~15 credits) → download the rendered
preview → S5 fit-to-cell-grid + palette-quantize → DC6 alternate → (activate + Push to game like
any alternate). Proven live on the Harlequin Crest: the 56² Shako sprite → a 3D cap model
(`census/meshy_shako_3d_preview.png`) → a game-ready DC6 alternate (`census/meshy_shako_as_dc6.png`).

**Components:** `app/meshy.py` (key resolution, balance, submit_image_to_3d, get_task, download —
Meshy openapi/v1). `app/server.py` routes: `GET /api/meshy/status` (key + credit balance),
`POST /api/item/<id>/meshy/generate` (S1+S2 submit), `GET /api/meshy/task/<tid>` (poll),
`POST /api/item/<id>/meshy/use/<tid>` (preview → DC6 alternate). UI: "✦ Generate 3D from Meshy"
button on the item detail with live progress + auto-import; header shows the credit balance.

**API facts (verified):** base `https://api.meshy.ai/openapi/v1`; auth `Bearer <key>`;
image-to-3D body `{image_url: data-URI, ai_model: meshy-5, topology, target_polycount, should_texture}`
→ `{result: task_id}`; poll `/image-to-3d/{id}` → `{status, progress, thumbnail_url (rendered
preview), model_urls{glb,fbx,obj,usdz,mtl,stl}, texture_urls}`. Signed URLs expire (~days).

**Blender gap (S4):** the plan's exact-dimetric-angle re-render + animation rig needs Blender
(not installed). For now the app uses Meshy's default **preview render** as the sprite source —
fine for items (fixed 3/4 inventory view). Blender S4 is the future add for angle-exact item art
and (Phase 2) the unit direction/animation rig; the GLB from model_urls feeds it directly.

**Security:** the key was pasted in chat this session — recommend rotating it in the Meshy
dashboard; in future it lives only in the .key file.

**The Asset Studio tool is now feature-complete for items:** browse → (import PNG OR generate via
Meshy 3D) → DC6 → push → one-click reload → in-game. Remaining roadmap: Blender S4 (angle-exact +
animation), flippy + unit phases, true-instant Tier-B reload, item-spawn inventory placement.

---

## 21. Blender S4 render stage SHIPPED — Meshy pipeline now angle-exact (2026-07-18)

> **PARTLY SUPERSEDED (§24):** the Blender S4 render rig described here is unchanged and still in
> use, but the Meshy *generation* half (the `/api/item/<id>/meshy/render/<tid>` route wiring it to
> an openapi task) moved to the Studio's `/api/studio/accept`. The `blender/render_glb.py` rig and
> `app/blender.py` are current.

Blender installed (5.2.0 LTS). Built the S4 render rig, so the Meshy pipeline now renders the 3D
model at a controlled angle instead of using Meshy's flat preview — decision #8 delivered for items.

**Flow (complete):** S1 extract+upscale → S2 Meshy image-to-3D (GLB) → **S4 Blender headless
render at (azim, elev), orthographic, transparent, bright studio lighting → PNG** → S5 fit-to-cells
+ palette-quantize → DC6 alternate → activate + push. Proven on the Harlequin Crest: GLB → Blender
3/4 render (azim 25, elev 20) → game DC6 (`census/blender_S4_shako_a25e20.png`).

**Components:** `blender/render_glb.py` (headless: factory-startup, GLTF import, auto-frame,
ORTHO camera positioned by azimuth/elevation, Cycles CPU, transparent film, key+fill sun +
ambient; supports `--frames N --azim-step D` for turntables / unit directions). `app/blender.py`
(locates blender.exe — found `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe` — runs
it, parses RENDER_OK). `app/server.py`: `POST /api/item/<id>/meshy/render/<tid> {azim,elev,size}`
(caches the GLB in meshy_cache, renders, → DC6 alternate; re-render at new angles for FREE, no new
Meshy credits). UI: after "Generate 3D", an angle row (azim/elev) + "Render (Blender)" vs "Use
preview"; header shows `blender ✓`. `/api/meshy/status` reports Blender availability.

**Full item-art tool is now complete per the plan:** browse → (import PNG | Meshy 3D → Blender
angle render) → DC6 → push → one-click reload → in-game. The multi-frame render support
(`--frames/--azim-step`) is the foundation for Phase 2 (unit direction/animation sprites from a
rigged Meshy model). Remaining roadmap: flippy (animated ground-drop) authoring, the unit phase
(DCC encode + direction/animation rig), true-instant Tier-B reload, and the item-spawn convenience.

---

## 22. Item-spawn investigation — injected-call spawn is architecturally BLOCKED (2026-07-18)

Chose to finish item-spawn→inventory (test any item's art). After tracing every entry point, the
conclusion is that injecting an item into a live single-player game is **architecturally blocked**,
not a missing-address problem:

- **Create works** (`CreateItemWithParams @ 6fc31880` returns the item UnitAny*; EBX=source unit),
  but every PLACEMENT path fails:
- **Drop** (`ITEMS_CreateAndDropItem` drop tail) faults in `ValidateAndWriteCommand @ 6fcac810` →
  `NET_WriteServerCommandPacket`: it validates an unwind-index against a live `GameContext` and
  writes a server→client command packet. Injected from the frame-tick (or capture pump), that
  command-processing context isn't set up → fault/abort. Timing-independent (tested entry & exit).
- **Pickup** (`ITEMS_PickupGroundItem @ 6fcf76a0`) requires the item to already be in the
  player-game-data ground-item hash (`+0x1720`, keyed by GUID) with `pNext==0x4` (on-ground) and
  quality==NORMAL — i.e. it needs a successful drop first (blocked above), and takes a
  player-game-data context, not the Game*.
- **Grid place** (`INV_PlaceItemIntoInventoryPage @ 6fd718a0`, D2Common) updates only the SERVER
  inventory data; in SP the client renders its OWN player copy, so without a server→client sync
  packet (same command context that faults) it won't show.
- **Equip** (`ITEMS_CreateItemAndEquip @ 6fcc1420`) needs an equip-context struct.

**Root cause:** SP runs client + server in-process with SEPARATE unit copies; getting an item to
appear requires the server→client command/sync path, which only works inside the game's own
command-processing context — unavailable to an injected call.

**Reliable path = SAVE-FILE INJECTION (decision #9 fallback):** write the item into the character's
`.d2s` at the menu (character not loaded); on load, both client and server build the item fresh
from the save — no runtime sync/command context needed, no crash risk. Cost: a `.d2s` items-section
parser + simple-item bitstream writer + checksum — a focused, offline (low-risk) module, separate
from the injected-call approach. Until then, test any item's art by activating it in the app and
viewing it on a character that already has that item (the override + full-reload already work).

**Net:** the item-spawn convenience is deferred (architectural wall for injected calls; save
injection is the real path). It does NOT block the tool — art editing + live override + reload are
complete and proven.

---

## 23. CORRECTION — drop WORKS; full Meshy→Blender→in-game loop PROVEN VISIBLE (2026-07-18)

§22's "injected-call spawn is architecturally blocked" was WRONG for the practical goal. The user
observed shakos dropping in-game, and verification confirmed it: **`ITEMS_CreateAndDropItem(drop=TRUE)`
lands the item on the ground — the SEH-caught fault is a HARMLESS POST-drop notify step
(`NET_WriteServerCommandPacket`), which fires AFTER the item is already placed.** The item is
visible, pickup-able, and equippable; the game stays stable. So the drop IS the spawn capability.

**Full loop proven VISIBLE end-to-end:** Meshy image-to-3D (Shako sprite → 3D cap) → Blender render
at inventory angle (azim 25, elev 20) → DC6 (invcap override) → patch.mpq registered → dropped in
game via `/showcase/item` → picked up → **the inventory shows the generated shako artwork**
(`census/GENERATED_shako_in_inventory.png`, `generated_shako_scene.png`). This is the whole tool
working, from AI generation to the item in the player's hands.

**Shipped:** app "⤓ Drop in game" button + `POST /api/item/<id>/drop` (proxies `/showcase/item`,
reports the harmless post-drop fault as a successful drop with a "pick it up to see the inv art"
note). The ground drop shows the flippy (flpcap, un-overridden); the INVENTORY sprite (invcap) shows
the override on pickup/equip.

**Corrected takeaway:** item spawning for testing IS available now (drop at feet → pick up).
Save-file injection (§22) remains a nice-to-have for placing items directly in inventory without a
manual pickup, but is no longer needed for the core "test any item's art" workflow.

## §24. Meshy staged flow: texture-from-image (2026-07-18, DONE + validated live)

**Problem:** the first Meshy 3D models came out flat/untextured. Root cause was using the older
`meshy-5` model without the texture-from-image options — NOT a fundamental limit.

**Fix (Meshy's normal documented pipeline):**
- **Image-to-3D** (`meshy.submit_image_to_3d`): now `ai_model="latest"` (Meshy 6),
  `should_texture=True`, `texture_image_url` = the source sprite, plus `image_enhancement=True`
  and `remove_lighting=True` (Meshy 6 pixel-art input helpers). Textures FROM the source image at
  generation time — no text prompt needed.
- **Retexture-from-image** (`meshy.submit_retexture`): accepts `image_bytes` → sends
  `image_style_url` (the item's own sprite). `image_style_url` takes priority over any text prompt;
  a text prompt is optional refinement. This is the separate staged step the user asked for.

**Staged UI (app.js):** generate → **review the 3D model** (`#meshyPreview` proxied from Meshy) →
**texture (from image, optional prompt)** → **render (Blender)**. `CUR_TID` tracks the current
task so texturing chains into render on the textured model. Server routes:
`/api/meshy/preview/<tid>.png`, `/api/item/<id>/meshy/texture/<tid>` (`use_image` default true).

**Validated live (shako, 2026-07-18):** improved image-to-3D → a properly textured fur cap
(`C:\tmp\shako_model_v2.png`); retexture-from-image → coherent guided retexture
(`C:\tmp\shako_retex.png`); Blender render → DC6 alternate `blender-019f750b-a25e20`, 58×58 RGBA
sprite, activated. Full chain (generate → texture-from-image → render → DC6 → activate) green.

## §25. Live in-game proof of textured shako + screenshot method (2026-07-18, DONE)

**Goal:** see the §24 textured shako art rendered live in the game.

**Two findings that made it trivial (no rebuild needed):**
1. **The D2Debugger is a SEPARATE top-most 900x720 window** (its own D3D9 device, clears
   blue-gray), NOT an overlay on the game's D3D surface (D2Debugger.imgui.d3d9.cpp:263). So it
   does NOT cover the game render. **Screenshot method that works:** capture the FULL virtual
   screen (`/screenshot all`), then crop to the Diablo II window. Targeting the game *process*
   window grabs the debugger window instead — capture full-screen and crop.
2. `invcap.dc6` is the **inventory** sprite; a loaded character already carrying Shakos shows the
   overridden art the moment the inventory panel is open — no spawn needed.

**Result:** with the textured `invcap.dc6` live (§24 patch registered, full-reload done), the open
inventory shows Shakos rendered with the generated bone/fur textured art, pixel-identical to the
Blender sprite. Tooltip confirms "SHAKO / Defense 125 / Req Level 43". Proof saved:
`C:\Diablo2\AssetStudio\census\shako_textured_ingame_inventory.png` and
`...\shako_textured_vs_generated.png`.

**Inventory-placement showcase verb (create -> ITEMS_PickupGroundItem by GUID):** RE'd but NOT
built. The game's own path is create item (CreateItemByCodeAlt @6fc8afa0 returns pItem) then
`ITEMS_PickupGroundItem(pGame, pPlayer, pItem->dwUnitId)` (@6fcf76a0) -- confirmed by CreateQuestItem
(@6fc56470) which does exactly this (no manual drop). SpawnItemCore's SEH net makes it low-risk to
attempt. Deferred: it's now a convenience (one-click place-in-inventory), not needed to view art.

## §26. Inventory-placement showcase verb -- IMPLEMENTED, deploy/test pending one UAC (2026-07-18)

Added `dest:"inventory"` to `POST /showcase/item` (default `"feet"` unchanged). Wiring lives in
D2Debugger.assetreload.cpp (`D2Asset_SpawnItem(code, drop, dest, timeoutMs)`) + LiveDispatch route.

**Approach (server frame-tick context, all SEH-guarded):**
1. `ITEMS_CreateAndDropItem(pGame, pPlayer, classId, bDrop=1)` under an inner __try/__except --
   the SP drop lands+registers the item then faults on a harmless post-drop client-notify step;
   we swallow that fault (`CreateAndDropSwallow`) so control continues. (This is why the first
   attempts returned SEH -1 at stage 7: the fault unwound the whole handler before pickup.)
2. `ScanForDroppedItemNode` walks the server item hash (Game+0x1720, 128 buckets, chain +0xE4 --
   offsets read from ITEMS_PickupGroundItem's disasm) for the newest item (max dwUnitId) with
   dwType==4 and dwTxtFileNo==classId. Read-only + pointer-guarded. RELIABLY LOCATES the item.
3. `PickupGroundItemShim` replicates CreateQuestItem's verified call to ITEMS_PickupGroundItem
   (@6fcf76a0): EDI=pGame, EBX=pPlayer, EAX=guid, ECX=(D2Game+0xFCC74), EDX=0x9CF, 8 stack args
   (0,1,1,0,0,guid,pPlayer,pGame); callee RET 0x20 keeps ESP balanced.

**Status:** create+drop+locate all work live (stage reaches 12, guid found, e.g. 0x208/0x265).
The pickup was REJECTED (returns 0) on BOTH a maxed char and a fresh empty-inventory Amazon --
so it is NOT inventory-fullness and NOT the drop mode. Prime suspect: ITEMS_PickupGroundItem gates
on item field +0x10 == 4 (loose-on-ground marker); our create path leaves it != 4. Latest build
records that field (status spawnDbg.mode) and sets it to 4 before pickup. **Built (D2Debugger.dll
07:42) but NOT deployed** -- the deploy_debugger_and_relaunch UAC was declined twice. To finish:
accept the UAC on redeploy, then title->SinglePlayer->load a char with a free 2x2 slot->
`POST /showcase/item {"code":"uap","dest":"inventory","confirm":true}` and read spawnDbg.mode.

**Note:** the user's original goal (see textured shako in-game) is already met (§25); this verb is a
convenience. The feet path (`dest:"feet"`) is unaffected and still works.

### §26 update (2026-07-18): verb succeeds server-side; client-render gap found

The `+0x10==4` hypothesis was CORRECT: the located item's field +0x10 was 3 (on-ground) and setting
it to 4 made ITEMS_PickupGroundItem accept it. `POST /showcase/item {"dest":"inventory"}` now returns
`{"ok":true,"spawned":true}` and the diagnostic reports stage 12, guid found, mode 3.

BUT: on opening the inventory (Amazon, empty inv), the shako is NOT visible -- not in the grid, not
equipped, not on the cursor. So the pickup succeeded on the SERVER but the item did not render in the
CLIENT inventory. This is the single-player client/server split: the drop path syncs a client ground
copy (dropped items DO render), but the create(bDrop=1)+forced-mode+pickup path does not leave the
client with a proper inventory item. Likely need the real client-side inventory-add sync (the packet
path a genuine ground pickup triggers) rather than forcing the server item's mode and calling pickup.

STATUS: verb works server-side; client render of the placed item is unconfirmed/absent. Deferred --
the user's primary goal (see textured shako in-game) is already met (§25). Next options if pursued:
(a) create WITHOUT drop via the real client-synced create (ITEMS_CreateItemWithLevel path used by
CreateQuestItem, incl. its coord setup), or (b) drive a genuine client-side pickup. Feet drop
(dest:"feet") remains fully working and client-synced.

Note: keyboard to D2 requires REAL focus -- SetForegroundWindow from a background proc raises but
doesn't focus; a mouse click in the client area grabs focus, then SendKeys('i') opens the inventory.

### §26 update 2 (2026-07-18): force-mode pickup CRASHES the client -- do not use

Retested on a fresh Amazon: `dest:"inventory"` returned `{"ok":true,"spawned":true}` (server pickup
succeeded) but the GAME CRASHED moments later (Game process gone, :8790 down). The server-side spawn
is SEH-guarded, but the CLIENT-side handling of the force-mode'd/picked-up item runs on a later
frame OUTSIDE that guard and faults -> hard crash. So forcing item field +0x10=4 and calling
ITEMS_PickupGroundItem is UNSAFE: it leaves the client/server item state inconsistent.

DECISION: the create+drop+force-mode+pickup approach is a dead end. Do NOT ship `dest:"inventory"`
as-is. Safe options:
  (a) Proper client-synced give-item: replicate CreateQuestItem end-to-end (ITEMS_CreateItemWithLevel
      @6fc31980 with its coord setup + the real pickup args) so BOTH server and client get consistent
      item state. Substantial RE; the create call has 8 stack args + ECX/EDX + stack-local coords.
  (b) Revert dest:"inventory" to the safe feet-drop (dest:"feet"), which is client-synced and works.
Recommend (b) now (make the verb safe), and treat (a) as a separate, carefully-scoped task.

The feet-drop path and everything in §24/§25 remain correct and safe. The textured shako is already
proven in-game (§25).

### §26 FINAL (2026-07-18): option (a) implemented -- create-in-cursor + place, NO crash

Root cause fully resolved. UnitAny +0x10 = dwMode (authoritative struct). ITEMS_PickupGroundItem
(@6fcf76a0) is really "place the CURSOR item into the inventory" -- it gates on dwMode==4 (ONCURSOR),
not on-ground. The earlier crash was from create+DROP (dwMode=3 ONGROUND, client has a ground copy)
then FORCING dwMode=4 -> client/server desync -> hard crash.

FIX (shipped): dest:"inventory" now faithfully replicates CreateQuestItem:
  1. CreateItemCursorShim -> ITEMS_CreateItemWithLevel(@6fc31980), __fastcall ECX=1,EDX=0,EAX=1(level),
     8 stack args (pUnit=pPlayer, pData=classId, pGame, nQuality=4, 0,0,0,0). Creates the item in
     CURSOR mode (dwMode=4) with proper client setup, NO drop -> no ground copy to desync.
  2. PickupGroundItemShim -> ITEMS_PickupGroundItem places cursor->inventory with client sync.

LIVE RESULT (fresh Amazon): {"ok":true,"spawned":true}; diagnostic stage 12, guid 0x209, **mode 4**
(natural, no forcing); the GAME STAYED ALIVE (captureCount kept climbing, world still rendering) --
the crash is GONE. Server pickup returned success.

Remaining: a live screenshot of the item sitting in the inventory grid is unconfirmed ONLY because
synthetic keyboard input to D2's DirectInput is unreliable from a background process (the inventory
'I' toggle landed once in ~10 tries). Not a verb problem -- pressing I in the game shows it. The
no-crash + dwMode==4 + create-in-cursor (the game's own client-synced path) give high confidence the
item is in the client inventory.

Verb is now SAFE (no crash) and correct-by-construction. dest:"feet" unchanged.

### §26 update 3 (2026-07-18): the full pickup pipeline decoded -- 3 interlocking stages

Chased the real ground-pickup. Findings (all in D2Game, disassembled):
- UnitAny +0x10 = dwMode (authoritative). D2 item "pickup" = place a CURSOR item (dwMode==4) into
  the inventory grid. ALL the place functions gate on dwMode==4:
    * ITEMS_PickupGroundItem @6fcf76a0  -> requires cursor item; RET 0x20 (8 stack args).
    * ITEMS_ProcessItemPickupFromGround @6fcf4e80 (the one ITEMS_PickupGameItem @6fc57ec0 calls)
      -> also `if (item->dwMode == 4)`; __fastcall ECX=pPlayer, EDX=game(hash base +0x1720),
      EAX=itemGUID, +stack (nPosition, flags, pnResult).
    * @6fcf6760 -> a socket/equip variant that bails unless item->pInventory (+0x60) != 0, so it
      rejects plain items. (This is the one the current build calls -> returns -11 cleanly, no crash.)
- Client rendering requires a client-side copy, created only by specific S->C commands:
    * DROP -> "item on ground" command (client gets a ground copy; dropped items render).
    * SendItemPickupCommand @6fc97d90 -> command 0x42 + SetOrClearCursorItem (client gets a CURSOR
      copy; item shows on the cursor).
  A "move to grid" command only applies if the client already holds the item.

So a one-click, client-synced INVENTORY give is a 3-stage pipeline: create/drop -> transition to
cursor WITH the 0x42 sync -> place cursor->grid. Each stage is a distinct internal fn with a custom
register convention; wiring all three safely is effectively re-implementing D2's pickup pipeline.

STATE: dest:"inventory" is SAFE (no crash) but does not place (current fn rejects plain items).
dest:"feet" works and is client-synced (drop, then click in-game to pick up -> inventory shows the
textured art). The §25 in-game proof stands. Given the depth vs. the convenience payoff, pausing
the one-click inventory give here; the full mechanism is documented above for a future pass. A
lighter partial win if desired: create-in-cursor + SendItemPickupCommand(0x42) to render the item
ON THE CURSOR (shows the inv art) without solving grid placement.

### §26 FINAL DECISION (2026-07-18): consolidated on drop-then-click

After fully mapping D2's pickup pipeline (§26 updates 1-3), the one-click inventory placement was
judged not worth the multi-stage re-implementation for a convenience feature. Consolidated on the
drop-then-click workflow and cleaned up:

- `POST /showcase/item {"code":"uap","confirm":true}` now always drops the item at the player's feet
  (the only runtime path that syncs a full item copy to the SP client) and returns
  `{"ok":true,"spawned":true,"note":"dropped at your feet -- click it in-game to pick it up ..."}`.
  The legacy `dest` field is accepted but ignored.
- Removed the dead inventory-pipeline code (ScanForDroppedItemNode, ProcessPickupFromGroundShim,
  PickupGroundItemShim, CreateItemCursorShim). Kept CreateAndDropSwallow (swallows the harmless
  post-drop notify fault -> clean success). The full pickup mechanism stays documented above for a
  future, properly-scoped pass (or the save-file-injection alternative).
- Rebuilt + deployed. Verified live: clean drop returns ok:true, game stays alive (no crash).

WORKFLOW: push overlay -> `/showcase/item {code}` -> click the dropped item in-game -> it enters the
inventory showing the overridden art. Combined with §25 (textured shako proven in-inventory), the
Asset Studio in-game art-verification loop is complete and safe.

## §27. Item pickup-to-inventory: the NATIVE packet path (2026-07-18 investigation)

Investigated across the internet (Phrozen Keep / MephisTools diablo2-protocol), Ghidra (D2Game +
D2Client), to find how to place an item into inventory WITH full SP client sync -- the piece the
§26 direct-function attempts couldn't do. Answer: don't call internal placement functions; replay
the CLIENT->SERVER packet a real mouse-click sends. In single-player it loops back through D2Net to
the local server, which runs the ENTIRE native, synced ground->inventory sequence.

**C->S pickup packet 0x16 (13 bytes):**
  byte 0    = 0x16
  bytes 1-4 = 04 00 00 00   (fixed)
  bytes 5-8 = item GUID (dwUnitId, DWORD LE)
  bytes 9-12= destination DWORD: 0 = auto-to-inventory, 1 = to cursor
So dest=0 = "pick this ground item straight into the inventory grid" -- one packet does it all.
(Other item packets: 0x17 drop, 0x18 item->buffer at x,y {17 bytes}, 0x1A equip, 0x23 to belt.)

**Send path (D2Client, verified in Ghidra):**
  every C->S game command funnels through SendChatMessageThrottled @6fac43e0 (misnamed; it's the
  generic sender: ECX = packet buffer, EBX = length; 200ms dedup window) -> Ordinal_10015 (D2Net) ->
  SP loopback -> server queue NET_ProcessClientPacketQueue @6fc4d500 -> per-packet dispatch (IAT
  @6fd18b64) -> the 0x16 handler runs the full synced pickup. CLIENT_SendShortChatMessage @6fac4910
  is the thin __fastcall wrapper (ECX=buf, caller sets EBX=len).

**Plan:** new D2Debugger verb -- drop the item (existing, client-synced ground copy) -> read its
GUID -> build the 13-byte 0x16 packet (dest=0) -> call SendChatMessageThrottled on the GAME THREAD
(ECX=&buf, EBX=13) via the gtqueue. The server picks it up natively -> item in inventory, fully
rendered on the client. No mode-forcing, no desync.

**Stat/tooltip verification (already native):** once the item is in inventory, HOVERING renders its
full properties via CLIENT_BuildItemTooltipDisplay @6fb43450 / CLIENT_BuildItemDescriptionTooltip
@6fb414f0 / BuildItemDescriptionText @6fb05b20. So a screenshot of the hovered item shows all
stats -- the verification path for both ART and STATS (the .txt-edit -> in-game-verify loop).

### §27 update (2026-07-18): packet-replay pickup implemented + convention fixed; deploy pending UAC

Implemented `dest:"inventory"` = drop -> read GUID from item hash -> replay 0x16 packet on the game
thread via D2Client SendChatMessageThrottled (@6fac43e0).

FIRST live test (buggy shim) result: the DROP worked and the GUID was captured (0x20a), the GAME
SURVIVED (no crash), but the send FAULTED -- I'd assumed ECX=buffer. DISASSEMBLY showed the real
convention: the packet BUFFER is a single STACK arg ([EBP+8]), LENGTH in EBX, callee RET 4. Fixed
the shim accordingly (push buf; EBX=len; call). Rebuilt OK (D2Debugger.dll 11:29).

BLOCKED: deploying the fix needs the elevated kill+relaunch (the running elevated game holds the DLL
locked, so a plain copy fails "device or resource busy"). The relaunch UAC was declined ~4x, so the
fixed DLL is built but not yet deployed. To finish: accept the UAC on
scripts/deploy_debugger_and_relaunch.ps1, then in-world POST /showcase/item
{"code":"uap","dest":"inventory","confirm":true} and hover the item for the stat tooltip.

### §27 SUCCESS (2026-07-18): packet-replay pickup VERIFIED in-game

Fixed shim deployed. Live test: in-world, POST /showcase/item {"code":"uap","dest":"inventory",
"confirm":true} -> {"ok":true,"spawned":true,"note":"picked up into inventory (0x16 replay)"}, game
alive (no crash), item no longer on the ground. Opened inventory + hovered: the textured SHAKO sits
in the grid with its full tooltip -- "SHAKO / Defense 129 / Durability 10 of 12 / Required Strength
50 / Required Level 43 / Socketed (2)". BOTH art and stats verified in-game.

Proof: C:\Diablo2\AssetStudio\census\shako_pickup_inventory_tooltip.png

This completes the item-verification tooling:
- ART: edit DC6 -> push overlay -> spawn to inventory -> the sprite renders in the grid.
- STATS: hover the item -> the tooltip shows Defense/Durability/Requirements/Sockets/etc.
Together with .txt editing this is the full edit->verify loop for both item images and item stats.

Verb (final): POST /showcase/item {"code":"<code>","dest":"inventory","confirm":true}
  = drop (client-synced) -> capture GUID from item hash -> replay native 0x16 pickup packet on the
    game thread (D2Client SendChatMessageThrottled @6fac43e0; buffer on stack, EBX=len, RET 4) ->
    SP D2Net loopback -> server runs the full synced ground->inventory pickup.
  dest:"feet" (or omitted) = drop only.

## §28. Item-management suite: inventory UI + tooltip investigation (2026-07-18)

Investigated (internet: Phrozen Keep / D2Ptrs; Ghidra: D2Client; D2Debugger: live) the functions and
globals needed to programmatically open the inventory and display/extract an item's hover text, so
item art AND stats can be verified autonomously (the .txt-edit -> in-game-verify loop).

FUNCTIONS (D2Client base 0x6fab0000):
- D2CLIENT_GetItemName  @6fb414f0  (RVA 0x914f0)  __stdcall(UnitAny* pItem, wchar_t* wBuf, int len)
    Builds the item NAME string (quality/runeword/personalized) into wBuf. Clean, callable.
- Item tooltip DISPLAY builder @6fb43450 (RVA 0x93450) -- draws the full hover tooltip (name+stats)
    for an item at the hover position; called from the inventory render when an item is hovered.
- Full DESC string builder: complex, builds on the stack (needs global flags / draw-hook); §27 note.
- CLIENT_ProcessInventoryStateToggle @6fb0c3d0 (RVA 0x5c3d0) void(void) -- what pressing 'I' runs.

GLOBALS (D2Client):
- g_dwInventoryPanelState @6fbcc284 (RVA 0x11c284): 0 closed / 1 open in-game / 2 open in menu.
    (Set by the toggle; NOT what the render gates on -- see below.)
- g_dwObjectType (panel render mode) @6fbcbc34 (RVA 0x11bc34): the inventory RENDER
    (CLIENT_RenderInventoryPanelUI @6fb49440) returns early if this == 0xa (10 = no panel). 1-9 =
    NPC trade, 0xb char, 0xc stash, 0xe cube; the player bag grid draws for any value != 0xa.
- g_dwHoverItemGuid @6fbc971d + hover flag @6fbc9721: when set, the render walks the unit hash by
    (guid & 0x7f) at [0x6fbba808 + bucket*4], chains via UnitAny+0xE4, matches +0x0C, and calls the
    tooltip builder @6fb43450 at the item's slot. => set these two globals to DISPLAY an item's
    hover tooltip with NO real mouse (provided the panel is rendering).
- client unit hash base @0x6fbba808 (128 buckets by guid&0x7f, chain +0xE4) -- for client item lookup.

MECHANISMS / PLAN:
- OPEN inventory: toggle sets panelState, but the render gate is g_dwObjectType. A queue-call of the
  toggle set panelState yet the panel did not render (objectType/UI-state not driven in that
  context). Correct approach: run the toggle in the CLIENT UI context, or set g_dwObjectType to a
  non-0xa inventory mode + panelState=1 together. (Open item; the render-gate global is identified.)
- HOVER text: open panel, then set g_dwHoverItemGuid + flag = the spawned item's GUID -> the tooltip
  renders itself -> screenshot. (Machine-readable alt: call D2CLIENT_GetItemName for the name; read
  the item StatList for values.)
- STATS (machine-readable): enumerate the item's StatList / read known stat ids to compare vs .txt.

STATUS: investigation complete (all functions+globals mapped). Implemented so far: D2Asset_OpenInventory
(sets panelState via the toggle on the game thread) -- returns ok but the panel does not yet render
because g_dwObjectType is not driven; the fix is to also set the render-mode global / run in UI ctx.
Item spawn-to-inventory (§27) works; combined with a real 'I' press the item + tooltip are visible.

### §28 update (2026-07-18): live probe results + the thread-context wall for auto-open

Added peek/poke diagnostics (POST /asset/peek, /asset/poke -- read/write any module+rva dword,
SEH-guarded; broadly useful). Probed the inventory UI live (Amazon, in-world):
- baseline (closed): objectType@0x11bc34=0, panelState@0x11c284=0, hoverGuid=0, hoverFlag=0.
- D2Asset_OpenInventory (toggle via gtqueue) set panelState=2 (MENU branch) -- because g_dwGameMode
  @0x11c394 reads 0 in that context even though we're in-world. Panel did NOT render.
- poke panelState=1 directly: panel still did NOT render.
CONCLUSION: the inventory render is dispatched by the full, coordinated client UI state (panel-active
array g_adwUIPanelStates + viewport via CLIENT_ProcessUIStateChange @6fb72790), not by any single
global, and that state machine must run on the CLIENT input/render thread. The D2Debugger gtqueue
runs these calls in a context where g_dwGameMode=0, so the toggle mis-branches and the coordinated
setup doesn't take effect. => auto-open-inventory by poking globals / queue-calling the toggle is
NOT sufficient; it needs execution in the client UI thread (e.g. a hook in D2Win's input/frame path).

BETTER PATH for autonomous STAT/ART verification (sidesteps the panel entirely):
- NAME/STATS as TEXT: call D2CLIENT_GetItemName @6fb414f0 (item name) and/or the item description
  builder on the spawned item to extract its properties as a string over HTTP -- no panel/hover
  needed. (Needs the CLIENT item pointer: walk the client player's inventory, or client unit hash
  @0x6fbba808 by GUID.)
- STATS (ground truth): read the item's StatList server-side (D2Debugger already has the server item)
  and compare stat ids/values vs the .txt.
- ART: already verified -- spawn-to-inventory renders the DC6; a real 'I' + hover shows the tooltip.

STATUS: investigation + diagnostics complete. Auto-open panel deferred (client-thread hook needed).
Recommended next build: item-text extraction (GetItemName + StatList) for hands-off stat verification.

### §28 SUCCESS (2026-07-18): autonomous item-stat extraction VERIFIED

Implemented + verified `POST /showcase/item-stats {"guid":"0x.."}` (or omit -> last-spawned item):
dumps the server item's StatList as JSON, fully hands-off (no panel/hover/mouse, server-side,
pointer-guarded + SEH). Live result for a spawned Shako (classId 435):
  {"ok":true,"guid":"0x020d","classId":435,"stats":[
     {"id":31,"sub":0,"val":119},   // armorclass (defense), within the Shako armor.txt range
     {"id":70,"sub":0,"val":1},
     {"id":72,"sub":0,"val":10},    // durability
     {"id":73,"sub":0,"val":12}]}   // max durability
Matches the earlier hover tooltip; stable across repeated calls; game stayed alive.

Also shipped this session: /asset/peek + /asset/poke (read/write any module dword, SEH-guarded).

THE AUTONOMOUS .txt VERIFY LOOP is now closed for STATS:
  edit armor/weapons/etc .txt -> rebuild MPQ/overlay -> spawn item (/showcase/item dest=inventory,
  §27 native 0x16) -> /showcase/item-stats -> compare {id:val} vs expected. stat ids map to
  ItemStatCost.txt (31=armorclass, 72/73=durability, 39/41/43/45=resists, ...).
ART verify loop (already working): edit DC6 -> overlay -> spawn -> renders in inventory (+ manual 'I'
+ hover for the visual tooltip; auto-open panel deferred -- client-UI-thread hook, see §28 update).

### §28 SESSION 2 (2026-07-18): auto-open SOLVED + item-text + visual hover -- corrections to §28

Fresh Ghidra pass corrected two misidentifications in §28; the full suite is now live-verified.

**CORRECTIONS (write-backs applied to Ghidra PD2-S12):**
- `@6fb0c3d0` is NOT the inventory 'I' handler -- it toggles UI panel **0x23 = the party-NAME-overlay**
  (its active flag gates the party-member name display @6fb71340, never the inventory render). Renamed
  `CLIENT_TogglePartyNameOverlayPanel`. Its state global `@6fbcc284` renamed `g_dwPartyNameOverlayState`.
  That is why the old D2Asset_OpenInventory "worked" but nothing rendered -- wrong panel entirely,
  no thread-context wall involved (`g_dwGameMode`@0x11c394 peeks 0 from ANY thread while in-world).
- The §28 "hover globals" `@6fbc971d/@6fbc9721` are NOT an item-hover mechanism: their consumer
  `@6faf7b20` is player-leave cleanup with a type==1 (PLAYER) validation that CleanupAndAborts on
  mismatch. **Poking an item GUID there would crash the game.** Do not use.

**THE REAL MECHANISM (live-verified):**
- UI panel active array `g_dwUIPanelActiveArray @6fbaad80` (dword[0x26]; count canary 0x26 @6fb8d5d0).
  Panel 1 = inventory; render pipeline @6fb739e0 calls RenderInventoryPanelUI @6fb49440 when
  flags[1]|[0xC]|[0xE]|[0x19]|[0x1A]|[0x1C]|[0x1D] (inventory | npc-shop | cube | trade family).
- Open/close lever: `CLIENT_ProcessUIStateChange @6fb72790` __fastcall(ECX=panelId, EDX=action
  0=open/1=close/2=toggle, [esp]=bUpdateCursor). Callable from the gtqueue pump -- no UI-thread hook
  needed. `/showcase/open-inventory` now drives PUSC(1, action, 0) and verifies flags[1]; supports
  `{"close":true}`.
- Item NAME text: `CLIENT_BuildItemDescriptionTooltip @6fb414f0` __stdcall(UnitAny*, wchar*, len),
  called on the CLIENT item (walk player @6fbcbbfc -> pInventory+0x60 -> pFirstItem+0x0C -> chain
  via pItemData+0x14 -> +0x64). NEW route `POST /showcase/item-text {"guid":"0x.."}` (guid 0 = first
  item) returns the localized hover-name string over HTTP.
- Visual hover tooltip: the hover scan polls the REAL cursor -- poking g_nMouseX/Y `@6fbcb828/@6fbcb824`
  or posting WM_MOUSEMOVE does NOT trigger it. `SetCursorPos` over the grid cell DOES (window is
  DPI-scaled: physical = winRect + gameXY * 859/1068 at the current layout). Verified: Shako tooltip
  (name/Defense/Durability/Required...) rendered on screen, screenshot-capturable.

**LIVE RESULTS (Amazon, in-world, fresh deploy):**
  /showcase/open-inventory -> {"ok":true,"inventoryOpen":true} + panel visible in screenshot.
  /showcase/item {"code":"uap","dest":"inventory"} -> picked up, guid 0x20e.
  /showcase/item-stats -> {"classId":435,"stats":[{31:122},{70:1},{72:9},{73:12}]}.
  /showcase/item-text {"guid":"0x20e"} -> {"text":"Shako"}; no-guid -> "Bill\nHwanin's Justice" (set item).
  SetCursorPos over cell -> hover tooltip rendered (screenshot proof).

THE FULL AUTONOMOUS .txt VERIFY LOOP is closed for ART + STATS + TEXT: edit .txt/DC6 -> overlay ->
spawn to inventory -> screenshot (art) + item-text (localized name) + item-stats (numbers), all
hands-off; visual hover tooltip additionally available via SetCursorPos when pixel-proof is wanted.

### §28 addendum: full-tooltip capture recipe (2026-07-18)

The visual hover is now fully capturable end-to-end. Recipe (all unattended-safe):
1. Reposition windows once per game launch via an ELEVATED helper (unelevated SetWindowPos is
   UIPI-blocked silently against the elevated game): move the 'Diablo II' render window to (20,20)
   and the 'D2Debugger' ImGui overlay out of the way (pattern: scratchpad move_d2_window.ps1).
2. Aim by CALIBRATION, not DPI math: the game draws its own cursor sprite which is visible in
   `window Diablo` captures. SetCursorPos(estimate) -> screenshot -> read the drawn-cursor position
   -> apply the delta (scale ~1 in capture space) -> re-set. Two iterations land on a grid cell.
   (g_pSelectedItem does NOT latch on bag-grid hover -- it is click/equip-slot state; use
   screenshots as hover ground truth.)
3. Screenshot: full tooltip proof achieved -- "SHAKO / DEFENSE: 119 / DURABILITY: 10 OF 12 /
   REQUIRED STRENGTH: 50 (red) / REQUIRED LEVEL: 43 / CTRL+RIGHT: DROP" rendered unclipped.

### §28 next steps IMPLEMENTED (2026-07-18, session 3): hover-xy + MCP surface

1. `POST /showcase/hover-xy {"x":<gameX>,"y":<gameY>}` -- deterministic hover in GAME coordinates.
   In-process feedback loop: SetCursorPos -> wait a frame -> read the game's own mouse view
   (g_nMouseX/Y @6fbcb828/@6fbcb824) -> correct; re-estimates the screen/game scale from observed
   movement, so it needs NO window-position/DPI constants. Seeds from (and recovers to) the game
   window center via same-process FindWindowA("Diablo II") -- the game only refreshes its mouse
   globals while the physical cursor is inside its window (learned the hard way: an outside cursor
   freezes the readback and the naive loop diverges). Converged exactly (643,266) across a window
   move AND a game restart. With the inventory open, aiming inside an occupied cell renders the
   full tooltip: verified "SHAKO / DEFENSE: 119 / DURABILITY: 10 OF 12 / REQUIRED STRENGTH: 50 /
   REQUIRED LEVEL: 43 / CTRL+RIGHT: DROP" unclipped in a `window Diablo` screenshot.
2. MCP tools (conformance/d2debugger_mcp/server.py; visible after a session restart):
   d2dbg_open_inventory(close, confirm) / d2dbg_spawn_item(code, dest, confirm) /
   d2dbg_item_stats(guid) / d2dbg_item_text(guid) / d2dbg_hover_xy(x, y).
   The whole art+stats+text+tooltip loop is now first-class agent tooling -- no curl needed.
3. Known-good hover anchors (current PD2 layout, 1068x600): Shako anchor cell -> game (643,266).
   Grid-cell -> game-coord mapping is deliberately NOT hardcoded in C++; agents derive cells
   cheaply per-session with hover-xy + a screenshot (grid geometry drifted between hand
   measurements; the feedback primitive is the stable part).
Spawned items persist in the character save across restarts (guid 0x20e Shako re-resolved
identically post-relaunch) -- spawn once, verify across sessions.

## §29. txt sliver + flippy authoring + early-registration hook (2026-07-18)

Closed the remaining Phase-1 gaps: the §7.3 txt sliver (per-unique invfile), flippy
authoring (decision #5's second half), and the delivery mechanism that makes excel-bin
edits actually reach the game. Also committed the whole tool to git (was untracked).

**txt sliver — direct uniqueitems.bin cell edit (no compiler needed).**
`DATATBLS_CompileTxt` (DataTbls.cpp:607) shows the .bin format is just
`[int32 recordCount][recordCount x UniqueItemsTxt]`, and `DATATBLS_LoadFromBin = TRUE` is
hardcoded (DataTbls.cpp:24) — the game always reads the .bin. UniqueItemsTxt is 0x14C bytes
(ItemsTbls.h:121) with `szInvFile` @0x5A and `szFlippyFile` @0x3A as plain char[32] cells, so
giving a unique its own art file is a byte-patch of one cell — verified against live PD2 data
(473 records × 332 B; Harlequin Crest @ row 248 blank invfile; The Grandfather's own
`invgsdu` proves per-unique invfile works in PD2). Shipped `app/excel.py` (edit/revert/stack,
atomic overlay write, manifest `txt_edits`, integrity guards), routes
`GET/POST /api/item/<id>/txt`, and an "Own art file" UI section on unique detail. Setting an
invfile auto-seeds the new filename with the current art (or relocates the active alternate)
so the game never dangles on a missing DC6; full revert leaves zero residue. 8/8 offline
checks green (`tests/test_excel_and_flippy.py`).

**Flippy authoring — Blender turntable → multi-frame DC6.** `pngs_to_flippy_dc6()` encodes a
tumble sequence matched to the ORIGINAL flippy's geometry: same frame count, same per-frame
offset trajectory (the offsets trace the fall arc, oy -140→0), center-corrected for a uniform
box sized just above the original's largest frame (so drops keep authentic scale). Routes:
`POST /api/item/<id>/meshy/render-flippy/<tid>` (one Blender render per flippy frame via the
existing `--frames/--azim-step` rig), `activate-flippy`, animated-GIF previews; flippy
variant strip + "Render flippy (Blender)" in the UI. Showcase = the existing feet-drop verb.

**Early-registration hook — excel-bin edits now land.** Data tables load at PROCESS START
(before the menu), so the menu-time `/asset/register` is too late for `data\global\excel\*`.
Fix shipped in `D2Debugger.assetreload.cpp`: a Detours hook on `DATATBLS_LoadAllTxts`
(resolved by EXPORT ORDINAL #10576 — no RVA guessing), installed at DllMain time
(`D2Debugger_StartStandalone`), whose body fires once exactly before table load ON the
game's own data-load thread and registers the archive named in `<workspace>\autoload.txt`
(written by the app on every push; env `ASSET_STUDIO_WS` overrides the workspace root).
SEH-guarded so a fault can never stop the boot; `/asset/status` now reports
`earlyReg:{hooked,fired,result}`. Result codes: 0 ok / 1 no config / 2 archive missing /
3 Storm unresolved / 4 open failed. Reload story for bin edits = plain Full reload (the
hook beats the table load by construction).

**Also:** Phase-1 DoD amended (standalone-consumer export load deferred to Phase 5
distribution); Meshy licensing resolved (risk #7); fixed a UI bug where "Use preview"
passed no task id (`/api/meshy/use/undefined`).

**Live verify staged, deploy PENDING one UAC.** The canonical Shako test is fully staged in
the real workspace: Harlequin Crest row 248 `invfile=invuapu` in the overlay
`uniqueitems.bin`, the textured Blender alternate relocated `invcap.dc6 → invuapu.dc6`
(base caps revert to stock art), `patch_0.mpq` built + byte-verified (bin row 248 reads
`invuapu` from the archive), `autoload.txt` → `export\patch_0.mpq` @ 9000. D2Debugger with
the early-reg hook is built in BOTH trees (out/build/VS2022 + build-1.13c). The deploy UAC
was declined (unattended session). To finish:
1. Run `scripts/deploy_debugger_and_relaunch.ps1` (accept the UAC).
2. After boot check `GET :8790/asset/status` → `earlyReg:{hooked:true,fired:true,result:0}`
   and `registered:true` (the hook registered patch_0.mpq before table load).
3. Enter game → `d2dbg_spawn_item code=uap dest=inventory` → open inventory: the Shako
   shows the textured art while a plain Cap (`d2dbg_spawn_item code=cap`) shows STOCK art —
   that split is the proof the per-unique invfile landed.

### §29 live test #1 (2026-07-18): hook fires correctly; game crashes at world entry — isolating

Deploy accepted; new build up. **Two mechanism findings, one crash:**
- **`DATATBLS_LoadAllTxts` fires at GAME ENTRY in PD2, not process start.** `earlyReg` stayed
  `fired:false` through title → char-select, then flipped `fired:true result:0 registered:true`
  the instant the character launched. So the hook's ordering guarantee (register-before-table-load)
  holds, and it holds on EVERY fresh process regardless of when the menu-time register would run.
- **The registration itself succeeded** (patch_0.mpq @ 9000, result 0).
- **CRASH:** ~26 captured frames into world entry → UNHANDLED EXCEPTION ACCESS_VIOLATION
  (c0000005) dialog. Suspects, in order: (a) the Detours trampoline on LoadAllTxts (bad prologue
  relocation — crash timing matches the tick right after tables load); (b) `SFileOpenArchive`
  racing other threads' Storm reads at game-entry time (boot-time registration was single-threaded
  in the §13 proof, game-entry is not); (c) the modified bin content (least likely — byte-identical
  to pd2data's copy except one char[32] cell).
- Also learned (read-only archive scan): THREE different `uniqueitems.bin` versions ship in the
  chain — pd2data 473 records (the game's effective copy, our edit base), patch_d2 402, d2exp 263.
  Record size 332 in all = vanilla struct, no PD2 schema drift.

**Isolation plan (staged, needs one elevated relaunch per test):** Test A = `autoload.txt` moved
aside (currently `autoload.txt.testA`) → hook+trampoline exercised, NO registration. Crash ⇒
detour bug (fix: hook a different site or restore-original-bytes-after-first-fire). Clean ⇒
Test B = autoload → archive WITHOUT the bin (DC6s only). Crash ⇒ registration race (fix: register
from the frame-tick handler's first server frame instead, or pre-open before Storm goes
multi-threaded). Clean ⇒ the bin itself; diff/inspect. The crashed process is harmless (exception
dialog up, :8790 alive) but must be killed by the elevated relaunch before any test.

### §29 live test #2 (2026-07-18): ROOT CAUSE FIXED — full chain PROVEN IN-GAME ✔

The isolation plan was short-circuited by finding the root cause statically: **PD2 renumbered
D2Common's export ordinals.** Vanilla #10576 (`DATATBLS_LoadAllTxts`) points at
`MISSILES_TestTblMaskField_154` (1-arg, RET 4) in PD2 — detouring it with the 3-arg RET-0xC
signature smashed the caller's stack. (The §14 stale-ordinal lesson, relearned the hard way:
NEVER trust vanilla ordinals against PD2 — resolve and VERIFY.) The real PD2 loader is
`DATATBLS_LoadAllDataTables @ 6fdb6160` = **export #10943**, verified RET 0xC + Detours-safe
prologue (`SUB ESP,0x108`). Fix (commit 859a0a1): hook #10943 with a prologue byte-guard that
refuses to attach on mismatch (`earlyResult:-3`) so a future PD2 build fails safe, not wrong.

**Live results with the fix (evidence in census/OWN_INVFILE_*):**
- Boot → `earlyReg {hooked:true}`, guard passed; **fires at GAME ENTRY** (PD2 loads tables per
  first game entry, not process start) with `result:0, registered:true`; game enters world and
  stays stable — the crash is gone.
- **Bin override PROVEN at the memory level:** peeking the live `sgptDataTables
  (g_pDataTables @ 6fde9e1c) → pUniqueItemsTxt (+0xC24)` shows row 248 `szName='Harlequin
  Crest', szInvFile='invuapu'` — the game's loaded table IS our edited bin. (Also learned:
  `ProjectDiablo.dll` opens pd2data/pd2assets/pd2maps at **priority 6000**, so 9000 wins; and
  THREE uniqueitems.bin versions ship in the chain — pd2data 473 records @ 332 B, patch_d2 402,
  d2exp 263 — pd2data's is the effective one and record size matches vanilla, no schema drift.)
- **Art override PROVEN on-screen:** an identified (ethereal) Harlequin Crest in the inventory
  renders the Meshy-generated white-fur `invuapu.dc6` (with the row's cgrn tint) while FOUR
  adjacent unidentified/normal Shakos + a plain Cap correctly keep stock `invcap` art — the
  per-unique split working exactly as designed. Tooltip "HARLEQUIN CREST / SHAKO" over the
  custom-art cell captured.
- **D2 semantics note for demos:** unidentified uniques (and the spawn verb's items, whose
  client copies are quality=NORMAL) always render BASE art — only an IDENTIFIED unique shows
  its own invfile. The spawn verb does not currently produce identified uniques; testing
  per-unique art needs an identified specimen (this character's ethereal HC served).
- Diagnostics that cracked it, now standing tools: `/asset/peek`-based live table/item
  inspection with wrapped module-relative RVAs (any heap address readable), and the
  client-inventory chain walk (player `@6fbcbbfc → +0x60 → +0x0C`, chain `+0x14/+0x64`;
  ItemData: quality +0x00, fileIndex +0x28, flags +0x18, invPage +0x45).

**Net: the §1 canonical use case is CLOSED end-to-end** — uniqueitems.bin cell edit → auto-seeded
own-file art → patch.mpq → pre-table-load auto-registration → identified Harlequin Crest wearing
Meshy-generated art in the live game, base caps untouched.

## §31. Art squish FIXED + framing tooling + provenance + Open-in-Blender (2026-07-18)

User flagged generated item art looked "squished vertically / doesn't fill the cell." Diagnosed
(NOT a Meshy problem — the 3D models are fine) as two compounding bugs in our render→DC6 path,
measured concretely (our items filled 30–67% of the cell vs originals 80–100%h):
1. **Blender framed the object by its 3D DIAGONAL into a SQUARE** (`ortho_scale = bbox-diagonal
   × margin`, `resolution = size×size`). The diagonal over-frames, so the object filled only
   32–62%w / 46–80%h of the render (a thin sword at an angle worst).
2. **`png_to_item_dc6` then thumbnailed that mostly-empty square into the TALL cell centered** —
   a square shrank to width×width with empty vertical bands. Compounded → tiny floating object.

**Fixes shipped (commit 853e71c):**
- **DC6 fit = crop-to-fill.** `fit_png_to_cell` crops to the object's alpha bbox then scales it to
  FILL the cell (aspect preserved — no distortion) with `fill`/`dx`/`dy` controls. Items now fill
  91–95% like the originals (Crystal Sword 31%→93%). Instant; works on any render.
- **Blender aspect + tight framing.** `render_glb.py` renders at the item's cell aspect ratio
  (`--res_x/--res_y`, ~64px/cell) and sets `ortho_scale` from the PROJECTED silhouette (camera-
  space bbox of the 8 corners), not the 3D diagonal, with `sensor_fit` on the limiting axis +
  smaller default margin (1.06).
- **Provenance retention.** Each alt keeps its `.glb`, `.source.png`, `.render.png`, `.meta.json`
  (angle/fill/dx/dy/task) next to the `.dc6` — nothing thrown away; the saved render drives instant
  re-fitting.
- **Open in Blender.** `blender.open_gui(glb)` launches the GUI with the model; route
  `POST /api/item/<id>/alt/<alt>/open-blender`.
- **Meshy quality.** `hd_texture` (4K) + `save_pre_remeshed_model` + `alpha_thumbnail` enabled;
  "Use preview" now prefers the transparent alpha thumbnail.
- **UI framing panel.** Live in-cell preview (`/alt/<alt>/cell.png?fill&dx&dy`) + fill/x/y sliders +
  Apply (`/refit`, instant, no re-render) + Open-in-Blender + a margin input on render. PNG-import
  and Use-preview also save render provenance so the sliders work there too.

**Proven live:** re-ran the 5 items (Full Helm, Crystal Sword, Kite Shield, Plate Mail, Military
Pick) through the improved pipeline (cached GLBs = free), pushed patch_1.mpq, fresh-booted, spawned
to inventory — all five now fill their cells (equipped on the paper doll + in the grid), game stable.
Offline before/after montage `C:\tmp\fill_fix_compare.png`; in-game `C:\tmp\inv_panel_zoom.png`.
**Note on limits:** fill preserves aspect, so if a 3D model's silhouette from the chosen angle has
a different aspect than the original (e.g. the Meshy plate mail came out a wide dome → 47%h vs
original 70%h), fix it with the angle/elev/fill sliders — that is exactly what the framing panel is
for. Shape fidelity is driven by the input sprite + camera angle, not a Meshy tunable.

## §30. Forced set-item spawn — closes the CreateItemWithParams quality-data TBD (2026-07-18)

The spawn verb could only make NORMAL-quality items (§14/§22 TBD). Built the "proper verb" to
spawn a SPECIFIC set/unique piece, so e.g. the full Tal Rasha's Wrappings can be summoned.

**Mechanism (RE'd + confirmed in Ghidra, closes the TBD):** a set item's exact row is forced by
the creation desc — `sub_6FC542C0` (ItemsMagic.cpp:842) selects setitems row `i` when
`pItemDrop->nItemIndex-1 == i`, provided the item's base code matches the row's `item` base,
item format ≥ 1 (expansion), and item level ≥ the set's lvl. The single create+assign entry is
`ITEMS_CreateItemUnit @ 6fc31490` — `__stdcall(Game*, ItemDrop*, int)` RET 0xC — which BOTH
`ITEMS_CreateAndDropItem` and `CreateItemWithParams` call. Its disassembly confirmed the desc
layout is **byte-identical to D2MOO's `ItemDrop`** (nItemLvl@0x0C, nId@0x14, nSpawnType@0x18,
wUnitInitFlags@0x28, wItemFormat@0x2A, **nQuality@0x30, nItemIndex@0x40**, dwFlags2@0x80).

**Design — hook, don't hand-build.** Rather than construct the 132-byte desc (crash-risky) or
replicate the register-coupled drop tail (`ITEMS_DropItemAtUnitPosition @ 6fcf2d90` takes its
item/carrier via registers — not standalone-callable, the cause of earlier faults), we DETOUR
`ITEMS_CreateItemUnit` and flip `nQuality=SET(5)` + `nItemIndex=setRow+1` (plus ilvl≥30,
dwFlags2|=1) just-in-time on the one create call, reusing the entire proven
create→drop→0x16-pickup path unchanged. One-shot armed flag, consumed inside the create. Set
pieces drop UNIDENTIFIED (sub_6FC542C0 clears IFLAG_IDENTIFIED), so we identify the server copy
post-create so it renders its set name/props (the drop/pickup syncs identified to the client).
Prologue byte-guard on the detour = fail-safe if a future PD2 build moves the export.

**Shipped (commit 873f2bc, built clean both trees):**
- `D2Asset_SpawnItem` gains a `setRow` param; `/showcase/item` accepts `{"setRow":N}`;
  `/asset/status` reports `createItemHook`.
- Python: `catalog.set_pieces(name)` resolves a set → [{row, base, index}];
  `POST /api/set/spawn {"set":"tal rasha"}` spawns every piece (correct base + forced row),
  `GET /api/set/list?q=`. Verified offline: 5 Tal Rasha's Wrappings rows resolve
  (Fire-Spun Cloth zmb·77, Adjudication amu·78, Lidless Eye oba·79, Howling Wind uth·80,
  Horadric Crest xsk·81).

### §30 live test (2026-07-18): mechanism PROVEN; two fixes; safe verb shipped

Deployed and tested live. **The core mechanism WORKS:** `POST /showcase/item {"code":"zmb",
"setRow":76}` produced a real green-named **"Mesh Belt / Tal Rasha's Fine-Spun Cloth"** set item,
created→dropped→picked-up→identified with no crash. So forcing a specific set row via the
CreateItemUnit detour is correct. Two issues found and fixed:

1. **Off-by-one set index (fixed, app-side, no redeploy).** The game's `setitems` array (compiled
   `.bin`) is 0-based over the rows the compiler KEEPS — it drops the `Expansion` section
   separator (raw CSV row 62), so raw CSV row ≠ game index. Confirmed by peeking the live table
   (`g_pDataTables @6fde9e1c → +0xC18 pSetItemsTxt`, `SetItemsTxt` = 0x1B8 bytes, szName@0x02,
   szItemCode@0x28): Tal Rasha's Wrappings are **game indices 76–80** (Fine-Spun Cloth zmb·76,
   Adjudication amu·77, Lidless Eye oba·78, Howling Wind uth·79, Horadric Crest xsk·80).
   `catalog.set_pieces` now counts only kept rows (skips blank/`Expansion`) → indices match the
   game exactly. **Rule: a set item's forced index is its position in the KEPT rows, not the raw
   .txt line.**
2. **Auto-identify + force-pickup of a set AMULET CRASHED the client (fixed).** The belt spawned
   fine, but the next piece (amulet, auto-identified then 0x16-picked-up) hard-crashed the game
   (exception dialog, pump frozen at captureCount 1122). Root cause = the §26 pattern: poking
   IFLAG_IDENTIFIED on a set piece + force-pickup drives the client's set/partial-bonus recompute
   on a later frame OUTSIDE the server SEH → fault. **Fix:** removed the auto-identify entirely
   (set items drop UNIDENTIFIED — vanilla + safe), and `/api/set/spawn` now drops each piece at
   the player's **feet** (dest "feet", the proven client-synced §23 path, paced 0.6s apart) rather
   than force-pickup. The player IDs + grabs them in-game. Rebuilt both trees.

### §30 DONE (2026-07-18): full Tal Rasha's Wrappings spawned + verified, game stable ✔

Redeployed the safe verb and spawned all 5 pieces at the player's feet (paced). **All five landed
correctly and the game stayed alive** (captureCount kept climbing 140→193 through and after the
drops). Verified server-side by walking the game item hash and reading each item's quality
(ItemData+0x00) + fileIndex (ItemData+0x28) against the live setitems table — the 5 fresh drops
(guids 0x211–0x215) are exactly:
```
0x211 row76 SET -> Tal Rasha's Fire-Spun Cloth   (base zmb, belt)
0x212 row77 SET -> Tal Rasha's Adjudication       (base amu, amulet)
0x213 row78 SET -> Tal Rasha's Lidless Eye        (base oba, orb)
0x214 row79 SET -> Tal Rasha's Howling Wind       (base uth, armor)
0x215 row80 SET -> Tal Rasha's Horadric Crest     (base xsk, helm)
```
Each forced to its correct set row on its correct base, SET quality, no crash. They sit on the
ground unidentified (vanilla) — pick up + ID in-game to wear the green set. **The forced set-item
spawn verb is complete and proven.** The `/api/set/spawn {"set":"<name>"}` route spawns any set by
name the same way (resolves rows via `catalog.set_pieces`).

**Reusable facts:** a set item's forced index = its position among the KEPT setitems rows (skip
blank/`Expansion` separators), NOT the raw .txt line. Auto-identifying + force-picking-up a set
piece crashes the client (set-bonus recompute outside the server SEH) — drop at feet + let the
player ID/grab. Live-table peek recipe: `g_pDataTables @6fde9e1c → +0xC18 pSetItemsTxt`,
SetItemsTxt = 0x1B8 bytes (szName@0x02, szItemCode@0x28); item quality @ ItemData+0x00, set row @
ItemData+0x28.

---

## 24. Meshy moved to the web-app login; billed openapi key path REMOVED (2026-07-19)

**Decision:** there is now exactly **one** Meshy generation path — the **Generation Studio**
(`/studio`), which drives Meshy through the user's **web-app browser login**, not the billed
`msy_` API key. Reason: the openapi key is charged per call and has **no free retry**, while the
plan's **free ×8 retries** live only on the web app's internal `api.meshy.ai/web/*` API (auth =
a Supabase JWT from the browser session). Generating through the key also created tasks that never
appeared in the user's Meshy workspace, which is what surfaced the split (an "Aegis Shield"
generated from the gallery button was invisible in the web app).

**Removed** (commit `ebe59b9`):
- `app/meshy.py` — the entire openapi client (key-file resolution, `balance`, `submit_image_to_3d`,
  `submit_retexture`, `get_task`, `download`).
- server routes: `/api/meshy/status`, `/api/meshy/generate`, `/api/meshy/task`, `/api/meshy/preview`,
  `/api/meshy/texture`, `/api/meshy/use`, `/api/meshy/render`, `/api/meshy/render-flippy`,
  `/api/meshy/tasks`, and `/api/item/<id>/import-glb` (+ its GLB-render helper).
- gallery UI: the "Import a GLB" section (file drop + task-id fetch) and the legacy "Generate 3D
  via API" block with its poll/texture/render/use helpers and framing panel.

**Kept / current path** (the Studio — §earlier Studio work, commits `a98c3f4`, `f3c0826`):
`meshy_web.py` (dedicated-Chrome session + CDP JWT capture + register/draft/texture/reroll/poll/glb)
→ `/api/studio/*` routes → `studio.html`/`studio.js` (three.js viewer, two-phase flow, tone
controls). The gallery's item detail now shows a single **"⚒ Open in Studio →"** button that
deep-links `/studio?item=<id>`. `/api/studio/session` reports login tier + Blender availability.
Blender S4 (`blender/render_glb.py`, `app/blender.py`) is unchanged and feeds off the Studio's GLB
via `/api/studio/accept`. Full web-API reverse-engineering: `MESHY_WEB_API.md`.

**Open thread — CLOSED 2026-07-19:** the free ×8 re-roll was captured live by CDP-driving the
workspace viewer's ⟳ button: **`POST /web/v2/tasks/{id}/retry` (empty body)** — not a PATCH.
Verified semantics: in-place replace with a **NEW task id** (old id 404s, `retryCount`+1,
remaining = 8 − retryCount), credit balance untouched. Wired end-to-end:
`meshy_web.retry_task()` → `/api/studio/reroll` now returns `free: true` + the new id (the
~20-credit parent-linked-draft fallback removed; studio.js already adopts the returned id).
One free retry of the older duplicate "Chainmail hauberk" draft was consumed by the capture.
Full details: MESHY_WEB_API.md §"Free ×8 RE-ROLL". NOTE: restart the Asset Studio server
(:5001) to pick up the new route.

---

## 25. Direction roll-up: 2026-07-21 → 2026-07-29 (housekeeping entry)

Everything between §24 and this entry happened in this repo's design docs and code
rather than this file; this entry pins the direction changes so this doc stays the master index.

### 25.1 Generate panel (Upscale → 3D workflow) — SHIPPED, all four phases
Planned + ratified 2026-07-21, built 2026-07-22. The item pipeline's front end is now a single
top-to-bottom workflow: AI detail-filling upscale (ComfyUI on the home 3090 box, two lanes —
SDXL+tile-ControlNet+DMD2 fast/default, Qwen-Image-Edit structural) → Meshy image-to-3D with free
×8 re-rolls → texture → GLB → Blender or in-browser render framed like the original art → DC6
alternate → activate. Boots auto-split, gloves reuse the mask→mirror→pair pipeline. A long
DC6-quality investigation closed the in-game "firefly"/speck artifacts (root cause:
act-variable palette indexes; plus source-side fixes). **Full plan + per-phase results log:
`UPSCALE_3D_PANEL_DESIGN.md`** (§6a–6k).

### 25.2 UI direction change: 3-column gallery; `/studio` DELETED (2026-07-27)
Supersedes §24's Studio-page architecture and the panel doc's original slide-over decision. The
gallery is now **1 Gallery / 2 Details / 3 Generate** — the Generate column is persistent and
auto-populates for the selected item. `/studio` (`studio.html`/`studio.js`) was deleted; its one
unique capability, the glove single-hand mask brush, was ported inline into the Generate column's
3D step. The `/api/studio/*` backend is unchanged and still serves the Generate column.

### 25.3 Enhance lane: curated recipes + per-item prompts + QA gate (2026-07-24/25)
The gem fidelity lab (2026-07-24) established category-routed enhance recipes (identity anchor =
biggest single fidelity win; solid-item vs glow-item lanes) and a scoring harness
(`app/fidelity_score.py`). The old Enhance/Restyle tabs were replaced (2026-07-25) by a curated
recipe picker with per-item prompt persistence (`app/gen_prompts.py` — fixes cross-item prompt
bleed) and an automatic fidelity gate on generate (`app/enhance_recipes.py`).

### 25.4 Spawn verb generalized (2026-07-21) — supersedes §30's set-only path
`D2Asset_SpawnItem(code, drop, dest, quality, qualRow, identify, timeoutMs)`: any ITEMQUAL_*
forced at create via the one-shot CreateItemUnit hook (nQuality@0x30, nItemIndex@0x40 row+1,
ilvl stamped 99 for set/unique eligibility), optional IFLAG_IDENTIFIED on the dropped ground item
(server-side only — needed for a unique's own invfile art; safe because there is no force-pickup).
Gallery "Drop in game" now offers a quality menu; uniques/sets drop as themselves. Item
colour-transform (`pyd2/colortransform.py`, D2CMP MixPalette port) is wired into gallery
thumbnails (inv recolour) and the equipped preview (worn recolour).

### 25.5 NEW SUB-PROJECT: Equipped ("paper doll") pipeline — Phase 2 begins
Design ratified + grounded against live PD2 data and D2MOO source:
**`EQUIPPED_PIPELINE_DESIGN.md`**. Status:
- **Stage 0 (foundations) SHIPPED**: `pyd2/cof.py` (COF parser), `pyd2/dcc.py` (DCC decoder),
  `pyd2/chars.py` (item→components→tokens→paths resolver + compositor), `pyd2/DCC_FORMAT.md`.
- **Stage 1 (paper-doll preview) SHIPPED**: **Equipped tab** in the gallery — animated on-character
  GIF composite (class / mode / direction pickers, item-only toggle, worn recolour) via
  `/api/item/<id>/equipped/info` + `/equipped.gif`. Supported kinds: body-armor, helm, weapon,
  shield (gloves/boots/belts have no worn component art in D2).
- **Spike 0 result**: a DC6 renamed `.dcc` does NOT render (decode follows the requested-format
  flag, not the header) — so the Stage-3 in-game write path is still an open decision:
  **Route A** (build a DCC encoder, asset-only) vs **Route B** (CelFileNormalize header-dispatch
  hook — needs PD2 D2CMP/D2Client RE + stock-DLL detour plumbing; elegant but an engine-hook
  sub-project).
- **Next frontier**: Stage 2a (weapon frame generation — 2D re-projection vs per-frame 3D render
  of the Meshy model, undecided) + Stage 3 write path go/no-go.

### 25.6 Phase status vs §9
Phase 1 (Item Art Studio) is **complete beyond its original scope** (DoD met §18–§25 + the whole
Generate/Enhance front end). Phase 2 (units/characters) has **started early via the equipped
pipeline** — driven by item-on-character needs rather than monster sheets; monster/NPC art remains
future. Phases 3–5 unchanged.
