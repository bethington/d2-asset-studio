# Equipped-Appearance ("paper doll") Pipeline — Design

Goal: let the Asset Studio show, and eventually replace, how an item looks **worn on the
character** — the "Equipped" tab. This is D2's most complex art system: not one sprite, but a
layered, 8/16-directional, multi-mode animation composite. This doc grounds the design in what
the game actually does, what we can reuse, and a staged plan that ships value early.

Everything below was verified against the live PD2 game data (MPQs) and the D2MOO source.

---

## 1. How Diablo II draws an equipped item

The on-screen character is a **layered composite of up to 16 components**:

    HD  TR  LG  RA  LA  RH  LH  SH  S1..S8
    head torso legs r-arm l-arm r-hand l-hand shield  special/shoulder/etc.

(`enum Composits`, `source\D2Common\include\D2Composit.h:8-27`.)

Three data layers drive it:

1. **COF file** — `DATA\GLOBAL\CHARS\<class>\COF\<class><mode><wclass>.cof`
   Defines, for a given (char class, animation mode, weapon class): **which components are
   drawn, in what draw order, with which flags** (shadow / transparency / draw-effect), and
   **which wclass token each component uses**. Verified layout of `AMNUHTH.cof`:
   - header: `layers, framesPerDir, dirs` (8, 8, 16) + timing/pad
   - then `layers` records of 9 bytes: `component, shadow, selectable, transparent,
     drawEffect, wclass[3]`. Layer order = paint order.
   - D2MOO builds this path (`D2Composit.cpp:170-177`) but does **not** parse the binary — the
     stock client does.

2. **DCC file** — `DATA\GLOBAL\CHARS\<class>\<comp>\<class><comp><graphic><mode><wclass>.dcc`
   The actual animation frames for one component with one equipment graphic. Verified header of
   `BATRLITNUHTH.dcc`: signature `0x74`, version `6`, `directions=16`, `framesPerDir=8`, then a
   bit-stream-compressed, palette-indexed pixel payload. **DCC files exist only per valid combo
   the COF references** (e.g. `AMTRLITNU1HS.dcc` is absent because that combo isn't used).

3. **Item → graphic mapping** (from the txt tables; fully modeled in D2MOO):
   - **Armor** (`armor.txt` / merged `ItemsTxt`): `component` = body group (0 = helm/head-only,
     1 = body armor). For body armor, six columns `rArm,lArm,Torso,Legs,rSPad,lSPad` hold graphic
     codes `0/1/2` → **lit / med / hvy** (`ItemsTxt.nArmorComp[6]`, `ItemsTbls.h:352`;
     `ArmType.txt` tokens, `TokenTbls.h:7-11`). Example: Chain Mail = Torso 1, Legs 1, arms 1,
     shoulders 2. A body-armor item therefore repaints **six** components at once
     (`INVENTORY_GetItemSaveGfxInfo`, `D2Inventory.cpp:2712-2758`).
   - **Weapon** (`weapons.txt`): `wclass` / `2handedwclass` ∈ {1hs,1ht,2hs,2ht,bow,xbw,stf,ht1,…}
     select the arm animation + COF; the weapon's own held graphic is the RH (or LH) component
     with the weapon's `component` graphic code. Dual-wield remaps to LH (`Units.cpp:3171-3224`).
   - **Helm**: `component = 0`, repaints the **HD** component only.
   - Color/tint per component is also carried (`pColor[]`) — supports palette-shifted variants.

4. **Animation modes** (`enum PlayerModes`, `Player.h:11-33`): NU TN WL TW RN A1 A2 BL SC TH KK
   S1..S4 GH DT DD … Each mode × weapon-class combination is a distinct COF, and each pulls its
   own set of per-component DCCs.

### Why this is hard (read before scoping)

A single body-armor item, fully covered, is:

    7 classes × 6 components × ~20 modes × several wclasses × 16 dirs × N frames
      ≈ tens of thousands of rendered frames.

Equipped art is **animated with the body skeleton** — Blizzard produced it by rigging 3D models
to the character rig and rendering every mode/direction/frame. "Generating" new equipped art is,
in the general case, re-creating that pipeline. So we **stage** it: deliver a viewer first, then
generate only the tractable item types, and treat full body-armor generation as a later, opt-in
phase.

---

## 2. Reuse vs build-new

**Reuse (already in the repo):**
- **Item → component/graphic data model** — mirror it in Python from the txt tables. D2MOO gives
  the exact field order to copy: `ItemsTxt.nComponent` / `nArmorComp[6]` (`ItemsTbls.h:351-352`),
  `ArmTypeTxt` lit/med/hvy (`TokenTbls.h:7-11`), `dwWeapClass`/`dwWeapClass2Hand`
  (`ItemsTbls.h:313-314`), weaponclass token table (`D2Composit.cpp:16-31`), component tokens
  (`composit.txt`), class/mode tokens (`PlrType.txt`/`PlrMode.txt`, `TokenTbls.h:13-25`), and the
  path format (`D2Composit.cpp:170-177`). We only need the tables + these orderings — no C++ port.
- **`INVENTORY_GetItemSaveGfxInfo` logic** (`D2Inventory.cpp:2651-2761`) — the reference for
  "which item paints which component with which token/color." Port this one function's logic.
- **Overlay + patch.mpq injection** — `assets._write_overlay(rel, bytes)` writes **any** relative
  path into the overlay, and `patch.mpq` is built from it. Injecting DCC at
  `data\global\chars\...\*.dcc` needs **no new plumbing** (verified). Stage 3 rides this.
- **Our 3D + render tooling** — Meshy models, Blender / three.js renderers, palette quantizer,
  DC6 encoder. These feed the frame-generation stages.
- **AnimData.d2** (`AnimTbls.h`) — frame counts / speeds / action-frame flags per token, for
  correct playback timing in the preview.

**Build new (not present anywhere in D2MOO — it lives in the stock client):**
- **COF binary reader** (Python) — easy: header + 9-byte layer records → composition model.
- **DCC decoder** (Python) — medium: the bit-stream format is public (Paul Siramy's spec). Needed
  for the preview and for reading stock frames as generation references.
- **Composite renderer** (Python/PIL or canvas) — order the COF layers, blit each component's DCC
  frame at its offset, apply palette/tint, per direction/mode. Medium.
- **In-game write path — DCC encoder *or* the DC6 shortcut** — **hard, and the gate for in-game.**
  Two routes (see the spike below), because the retail cel loader is **format-agnostic**:
  - The engine's `ARCHIVE_LoadCellFile` builds the name with `GetGfxFileExtension(bAllowCompressed)`
    → `.dcc` when compressed, `.dc6` when not (`D2CMP.h:164`), and **both DC6 and DCC normalize to
    the same `CellFile`/`GfxData`** (`CelFileNormalize`, `D2CMP.h:156`). So the engine *can* render
    a component from **DC6**, which we already encode.
  - The catch: the *client* decides `bAllowCompressed` per load (that code is in `D2Client.dll`,
    not in this repo; char layers default to `.dcc`). A pure patch.mpq DC6 at a char path won't be
    requested unless the client asks for `.dc6`.
  - **Route A — DCC encoder** (pure asset mod, no engine change): build from the public spec or
    port an open-source DCC writer. Highest risk, most portable.
  - **Route B — DC6 + a compressed-mode hook** (fits this project): reuse our DC6 encoder and add a
    tiny **D2.Detours** hook so char-layer loads request `.dc6` (force `bAllowCompressed=FALSE` /
    `SetCompressedDataMode`, ordinal `0xB9C0`). Far cheaper *if* it renders correctly — but it
    couples the art mod to an engine patch. **Unproven; validate with the spike first.**

---

## 3. Staged pipeline

Each stage is independently useful. Effort is rough dev-time; risk is technical uncertainty.

### Spike 0 — Can the client render a character component from DC6?  ✅ RAN (2026-07-21)
Test: built valid green **DC6** stand-ins for the Amazon torso (`AMTR{lit,med,hvy}{TN,NU}1HT.dcc`,
structure-matched 16×16 / 16×8), placed them at the `.dcc` paths via the overlay, pushed, full
reloaded, observed the worn character.

**Result: the torso rendered EMPTY (see-through).** The file was loaded (the original torso was
gone) but produced no pixels — and our DC6 is provably valid (round-trips to 256 opaque frames,
header `06000000`). So the client **decoded our DC6 as DCC**, the signature check failed
(`0x06` ≠ DCC `0x74`), and it bailed to an empty layer. Confirmed: **`CelFileNormalize` dispatches
on the requested-format flag, NOT the file header** — so a DC6 named `.dcc` is rejected.

**Consequences:**
- ❌ "Reuse our DC6 encoder, name the file `.dcc`" — **dead** as a pure asset swap.
- **Route A — DCC encoder** (asset-only, portable): still the safe path; build/port a DCC writer.
- **Route B — DC6 + engine hook** (fits this project): a D2.Detours hook that forces char-layer
  loads to **uncompressed** (`SetCompressedDataMode` / `bAllowCompressed=FALSE`, ordinal `0xB9C0`)
  so the client requests **and decodes** `.dc6`. The spike proved the decode follows the flag, so
  flipping that one flag for char loads *should* make our DC6 render — **still unproven; it's the
  next experiment** (needs the hook, ~1 day). If it works, we keep the DC6 encoder and skip DCC.
- Either way, **Stages 0–2 (decode + preview + generation) are unaffected** — only the Stage-3
  writer differs. DCC *decode* is still needed for the preview regardless.

Optional 100%-confirmation of the null result: repeat with a giant (256×256) green frame to fully
rule out mispositioning — but a fully-opaque 80px box rendering *nothing* already makes that
near-certain.

### Route B feasibility RE (2026-07-21) — bigger than "~1 day"
The cel-load mechanism (from `D2WinArchive.cpp:37-47`): `szPath = szFile + GetGfxFileExtension(nType)`
(`.dcc`/`.dc6`) → load → `CelFileNormalize()` decodes. **Cleanest hook: detour `CelFileNormalize`
to auto-detect format by the file header** (`0x74`=DCC, `0x06`=DC6) instead of the flag — that one
hook makes our DC6 render everywhere and keeps the DC6 encoder. But:
- The live game is **PD2** (modified `D2CMP.dll`/`D2Client.dll`); the stock D2MOO addresses
  (`GetGfxFileExtension` `0xB930`, `SetCompressedDataMode` `0xB9C0`, `CelFileNormalize` `#10024`)
  **do not map** to the PD2 binaries — `0xB930` decompiled to a palette function. The hook targets
  must be **re-located in the PD2 D2CMP/D2Client** (Ghidra; both are loaded).
- The char component load runs in **stock D2Client** (not reimplemented, no patch file), and the
  D2.Detours framework currently only swaps the **reimplemented** D2Common/D2Game — there is **no
  detour path for a stock DLL** yet. Route B needs new plumbing to inline-hook a stock-DLL function
  (the trampoline machinery exists; the wiring for a non-reimplemented DLL does not).
- **Net:** Route B is a real engine-hook build (PD2-binary RE + stock-DLL detour infra + build +
  deploy + in-game test, with crash risk from hooking a core loader), not a quick asset test.
  It's still the most elegant end state (small runtime hook, reuse DC6 encoder) — but it's a
  focused sub-project, and it modifies the live engine, so it warrants a go/no-go before building.

### Stage 0 — Foundations: `pyd2/cof.py` + `pyd2/dcc.py` + char data model
- COF parser → `{layers:[{component,order,shadow,transp,effect,wclass}], frames, dirs}`.
- DCC decoder → per (direction, frame) RGBA + offset(x,y). Palette-indexed against the char/act
  palette.
- `pyd2/chars.py` — the item→components→tokens→file-paths resolver (mirrors
  `INVENTORY_GetItemSaveGfxInfo` + the token tables). Given an item + class + mode + wclass, list
  the COF and the per-component DCC paths.
- **Effort:** M–L. **Risk:** M (DCC decode). **Unlocks:** everything below.

### Stage 1 — Paper-doll PREVIEW (read-only)  ← recommended first deliverable
- In the **Equipped** tab: pick class (default the item's most-common wearer) + mode (default NU)
  + direction; composite the **base body + this item's components** from the *existing game* DCC
  via the COF order; play it animated.
- No new art, no DCC encode. Pure win: modders finally **see** the worn appearance, and it becomes
  the on-screen reference the generation stages target.
- **Effort:** M (on top of Stage 0). **Risk:** L–M. **Value:** high.

### Stage 2 — Frame generation (by item type, tractable first)
- **2a — Weapons** (most tractable; weapons read most prominently in play). A weapon is a near-
  rigid object on the hand. Use the **stock weapon-class DCC** (correct wclass) as a per-frame
  **motion/orientation/scale reference**: for each direction & frame it tells you where the hand
  is, the 2D angle, and the size. Then either (i) **2D re-project** the item's existing inventory
  render onto each reference frame (fast, approximate), or (ii) **3D-render** the Meshy model at
  the per-frame orientation derived from the reference (better). Output: RH/LH frame set for the
  item's wclass modes.
- **2b — Helms** (tractable): head attachment, HD component; same reference approach on far fewer
  frames.
- **2c — Body armor** (hard; defer full generation). v1 fallback options instead of true
  generation: **(i) reskin** — transfer the Meshy model's palette/material onto the *existing*
  lit/med/hvy component frames (recolor, not reshape); **(ii) class-swap** — let the user pick a
  different existing armor graphic class. True per-frame body-armor generation is a later phase
  (needs a rigged character render).
- **Effort:** L (2a/2b), XL (2c-true). **Risk:** M (2a/2b), H (2c).

### Stage 3 — DCC encode + activate
- Encode generated frames → valid DCC; write to the overlay at the correct
  `data\global\chars\<class>\<comp>\...dcc` paths; build patch.mpq; **Full reload** to see it worn
  in game. Activation UI mirrors the Item/Flippy "variants + activate" pattern.
- **Effort:** L (wiring) + the DCC-encoder cost from Stage 0. **Risk:** H (DCC encoder validity).

---

## 4. Recommended first slice

**Stage 0 (COF reader + DCC *decoder* + char data model) → Stage 1 (paper-doll preview).**

Rationale: it delivers the thing you actually asked the tab to be — *seeing* the equipped look —
without the hardest piece (the DCC *encoder*). It also produces the reference frames every
generation stage needs, and it de-risks the format work incrementally (decode before encode).
Then tackle **2a weapons + 3 DCC-encode** as the first "replace it in-game" milestone, since
weapons are the most tractable and most visible.

Deliberately deferred: full body-armor generation (2c-true) — biggest effort, roughest results.

---

## 5. Open decisions (need your call before building)

1. **First slice:** confirm Stage 0+1 (preview) first, vs jumping straight at weapon
   replacement in-game (Stage 0 + 2a + 3, which front-loads the DCC-encoder risk).
2. **Coverage for v1 preview:** all modes/dirs, or start with idle (NU) + walk (WL) × 8 dirs to
   keep it small?
3. **Weapon generation fidelity (2a):** fast 2D re-projection of the inventory sprite, or 3D
   per-frame render of the Meshy model?
4. **In-game write path:** run **Spike 0** first. If DC6 renders for char layers → Route B (reuse
   our DC6 encoder + a compressed-mode hook). If not → Route A (build/port a DCC encoder). This one
   choice sets most of the project's risk.
5. **Body armor (2c) for v1:** reskin/recolor existing frames + class-swap only, deferring true
   generation? (recommended)

---

## 6. Status (2026-07-29) — Stage 0 + Stage 1 SHIPPED

The recommended first slice (§4) was built:

- **Stage 0 foundations** — `pyd2/cof.py` (COF parser: layers/order/flags/wclass),
  `pyd2/dcc.py` (DCC decoder: bit-stream frames → RGBA + offsets), `pyd2/chars.py`
  (item→components→tokens→file-paths resolver mirroring `INVENTORY_GetItemSaveGfxInfo`, plus
  `resolve()`/`animate()` compositor and `worn_colormap()` for unique/set worn recolour).
  Format notes captured in `pyd2/DCC_FORMAT.md`.
- **Stage 1 paper-doll preview** — the gallery detail rail has an **Equipped tab**: animated
  on-character composite (COF paint order, act-palette-quantized GIF) with class / mode /
  direction pickers and an item-only toggle; selections persist in localStorage; renders are
  composited on demand and cached server-side. Endpoints:
  `GET /api/item/<id>/equipped/info` (class-spanning probe → worn kind + which classes actually
  render it; barbarian preferred default) and
  `GET /api/item/<id>/equipped.gif?cls=&mode=&dir=&body=`.
- **Coverage** — modes offered: NU TN WL RN A1 A2 SC TH S1 × 8 directions (open decision #2
  resolved as the subset; more modes are a param away). Supported kinds: **body-armor, helm,
  weapon, shield**; gloves/boots/belts have no worn component art in D2 and report unsupported.

**Open-decision status:** #1 resolved (preview shipped first), #2 resolved (mode subset,
on-demand), **#3 open** (weapon generation: 2D re-projection vs per-frame 3D render),
**#4 open** (Stage-3 write path: Route A DCC encoder vs Route B CelFileNormalize header-dispatch
hook — Spike 0 killed the rename-only shortcut; Route B needs PD2 RE + stock-DLL detour
plumbing, see §3), **#5 open** (recommend reskin/class-swap for v1 body armor).

**Next:** Stage 2a (weapons) + the Stage-3 route go/no-go.

---

## Appendix — verified reference points

- Components enum: `D2Composit.h:8-27`; armor-component test `D2Composit.cpp:427-430`.
- Item graphic data: `ItemsTbls.h:351-352` (`nComponent`,`nArmorComp[6]`), `TokenTbls.h:7-11`
  (`ArmType`), `ItemsTbls.h:313-314` (`dwWeapClass`,`dwWeapClass2Hand`).
- Composite assembly: `D2Inventory.cpp:2651-2761`; weapon-class resolve `D2Composit.cpp:217-387`.
- Modes/tokens: `Player.h:11-33`; token tables `TokenTbls.h:13-25`; path format
  `D2Composit.cpp:170-177`.
- Formats (verified from MPQ): COF `AMNUHTH.cof` (8 layers/8 frames/16 dirs, 9-byte layer recs);
  DCC `BATRLITNUHTH.dcc` (sig 0x74, ver 6, 16 dirs × 8 frames).
- Overlay injection: `assets._write_overlay` (arbitrary rel paths → patch.mpq).
