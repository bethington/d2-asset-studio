# PD2 Asset Studio — app

A local web tool to reskin Project Diablo 2 **item inventory art** and push it live into
the running game — no MPQ edits, no restart of your edits (the game re-reads on reload).

## What it does (proven end-to-end)

1. **Browse** every PD2 item (base / unique / set) — 1400+ — read live from the game's MPQs,
   with each item's current inventory sprite rendered as a PNG.
2. **Import a PNG** as an alternate for any item. The app auto-fits it to the item's
   `invwidth × invheight` cell grid and quantizes it to the Diablo II act palette, then encodes
   a game-ready **DC6**.
2b. **Generate with Meshy.ai + Blender** — select any item and use **Generate** (column 3,
   always open for the selected item) to turn its art into a 3D model, then render it to a
   sprite. Generate drives Meshy through your **web-app login** (a dedicated logged-in Chrome —
   the plan's free retries, not the billed API key): upscale/enhance the source art → draft
   geometry → rotatable 3D preview → re-roll the shape → texture → accept → Blender render at the
   item's cell aspect → DC6. Needs Blender installed (auto-detected; set BLENDER_EXE to
   override). See ../MESHY_WEB_API.md.
3. **Pick** original or any alternate per item (multiple alternates supported).
4. **Push to game**: builds a `patch.mpq` (MPQ v1 + PKWARE — the only format D2's Storm reads)
   from your active choices and **registers it live** at priority 9000 via the D2Debugger
   AssetReload endpoint (`:8790`), overriding the base game files without touching any MPQ.
5. **Reload game**: soft-reloads so the art re-loads.

Verified live 2026-07-18: importing a magenta PNG for the belt potions renders them magenta
in-game (`census/APP_LOOP_WORKS_magenta_potions.png`).

## Run

```
pip install -r requirements.txt      # from the repo root; StormLib.dll must be in bin/ (see ../README.md)
python app/server.py                  # -> http://127.0.0.1:5001 (ASSET_STUDIO_PORT overrides)
```
Start PD2 with the debugger first (D2MOO's conformance/tools/relaunch_pd2.ps1 or
scripts/relaunch_pd2_direct.ps1) so the :8790 AssetReload endpoint is up.

## Reload buttons

- **Push to game** — build patch.mpq from active choices + register it live (priority 9000).
- **Full reload** (recommended, guaranteed-clean) — relaunches a FRESH game process via the
  elevated `-direct` launcher (1 UAC), registers the overlay at the menu, and drives back
  into the game. ~60-90s, one click. Use this to see art changes reliably.
- **Soft reload** (fast, no UAC) — exit-to-menu + re-enter. Re-reads the item CellFile but does
  NOT flush D2CMP's decompressed-cell cache, so already-loaded item art can render broken/stale.
  Prefer Full reload for item art.

Why: item inv art is cached in two process-global layers (D2Client CellFile + D2CMP cell cache).
Only a fresh process starts both empty. A true in-world instant reload needs a D2CMP flush
(`FlushCelCache` #10078) + client CellFile eviction + cache re-init — see docs/AssetStudioPlan.md §19.

## Next (per docs/AssetStudioPlan.md)

- Meshy.ai staged pipeline (sprite → 3D → re-render → DC6) as an alternate source.
- Flippy (ground-drop) + unit graphics phases.
- Tier-B in-world live reload (D2CMP sprite-cache flush) so no relaunch is needed.
