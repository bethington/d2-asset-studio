"""Batch-generate ALL enhance methods for every gem that isn't already fully done, then promote
the best-scoring variant of each to a shipped DC6 alternate (matching the studio's accept-2d path).

User decisions (2026-07-25):
  1. Skip the 12 gems that already have a full 10-variant set; process the other 23.
  2. Run all 10 methods; feed the two lock lanes a generic per-colour crystal restyle.
  3. Hand-written, shape-neutral per-colour identities (colour only, no shape words).
  4. Auto-select the highest-fidelity variant, write it as the DC6 alternate, activate it.

Reuses app.server's module-level wiring (assets.register_item_resolver at import -> alternates land
in the REAL bucket the live UI reads, per the gem-fidelity-lab publish-bug lesson) and its exact
accept-2d/activate helpers, so results are identical to clicking through the UI, just batched.

Run from this repo with ASSET_STUDIO_WS pointed at the workspace:
    ASSET_STUDIO_WS="C:\\Diablo2\\AssetStudio" python scripts/gen_all_gem_methods.py
Resumable: skips any (item, method) this batch already produced (meta.batch == BATCH_TAG).
"""
from __future__ import annotations

import concurrent.futures as cf
import sys
import time
import traceback

sys.path.insert(0, ".")

import app.server as server          # noqa: E402  (module import wires the resolver + catalog)
import app.assets as assets          # noqa: E402
import app.comfy as comfy            # noqa: E402
import app.describe as describe      # noqa: E402
import app.enhance_recipes as er     # noqa: E402
import app.upscale_store as us       # noqa: E402

BATCH_TAG = "gems-allmethods-2026-07-26-alphafix"

# Family-grouped so each checkpoint stays resident across all gems (Qwen -> GAN -> SDXL -> Flux).
QWEN = ["m0", "m1", "m2", "m3", "m5", "m6", "m7"]
METHODS = QWEN  # 2026-07-25: user decided to skip the upscale-lock lanes, Qwen methods only

# The 12 already-full gems to skip (10 variants each).
SKIP_NAMES = {
    "Amethyst", "Chipped Topaz", "Topaz", "Saphire", "Perfect Saphire", "Emerald",
    "Chipped Ruby", "Ruby", "Perfect Ruby", "Diamond", "Skull", "Perfect Skull",
}

# Shape-neutral, colour-only identities (reused across a colour's 5 grades). Skulls get a
# skull identity, not a crystal one, to counter the gem-category crystal house style.
IDENTITY = {
    "Amethyst": "a vivid glossy translucent faceted purple amethyst gemstone",
    "Topaz":    "a vivid glossy translucent faceted golden-yellow topaz gemstone",
    "Saphire":  "a vivid glossy translucent faceted deep-blue sapphire gemstone",
    "Emerald":  "a vivid glossy translucent faceted rich-green emerald gemstone",
    "Ruby":     "a vivid glossy translucent faceted brilliant-red ruby gemstone",
    "Diamond":  "a brilliant clear sparkling white faceted diamond gemstone",
    "Skull":    "a polished glossy off-white bone skull with dark eye sockets",
}
# Restyle text for the two lock lanes (they require a described new look).
RESTYLE = {k: v.replace("a ", "", 1) for k, v in IDENTITY.items()}


def _color_of(name: str) -> str:
    for c in IDENTITY:
        if c.split()[-1].lower() in name.lower() or c.lower() in name.lower():
            return c
    if "skull" in name.lower():
        return "Skull"
    raise KeyError(name)


def _targets():
    items, by_id = server.catalog()["items"], server.catalog()["by_id"]  # noqa: F841
    gem_types = server._GEM_TYPES
    gems = [it for it in items
            if (it.get("type") or "").lower() in gem_types and "#g" not in it["id"]]
    return [it for it in gems if it["name"] not in SKIP_NAMES]


def _already(item_id: str, method: str) -> bool:
    # add_variant flattens meta onto the top level of the record (no nested "meta" key).
    for v in us.load(item_id).get("variants", []):
        if v.get("method") == method and v.get("batch") == BATCH_TAG:
            return True
    return False


def _cat(it) -> str:
    t = (it.get("type") or "").lower()
    return "gem" if t in server._GEM_TYPES else "glow" if t in server._GLOW_TYPES else "control"


def _generate_one(it, method, color):
    orig = server._original_png_for(it)
    restyle = RESTYLE[color] if method in ("sdxl_lock", "flux_lock") else ""
    master, meta = er.run(method, sprite_png=orig, identity=IDENTITY[color], cat=_cat(it),
                          restyle=restyle)
    scores = er.score(orig, master)
    if scores.get("iou", 0) == 0.0:
        return None, "empty (iou=0)"
    if scores["score"] < 0.35:
        return None, f"low fidelity {scores['score']:.2f}"
    w, h = meta.pop("size")
    canonical = comfy.to_canonical_2x(master, (it["invwidth"] * assets.CELL_PX,
                                               it["invheight"] * assets.CELL_PX))
    rec = us.add_variant(it["id"], master_png=master, canonical_png=canonical,
                         meta={**meta, "method": method, "score": scores, "size": [w, h],
                               "batch": BATCH_TAG, "ts": time.time()})
    return rec, scores["score"]


def _promote_best(it):
    """Pick this batch's highest-scoring variant, write it as a DC6 alternate + stack, activate it.
    Mirrors server.api_upscale_accept2d exactly, then activates (accept-2d itself doesn't)."""
    batch_vars = [v for v in us.load(it["id"]).get("variants", [])
                  if v.get("batch") == BATCH_TAG]
    scored = [(v, (v.get("score") or {}).get("score", 0.0)) for v in batch_vars]
    scored = [(v, s) for v, s in scored if s]
    if not scored:
        return None, None
    best, best_score = max(scored, key=lambda t: t[1])
    vid = best["id"]
    png = us.variant_png(it["id"], vid, "master") or us.variant_png(it["id"], vid, "canonical")
    fill, dx, dy, was_auto = server._resolve_fit(it, "auto", None, None)
    grade = server._accept_grade() or None
    rec = best  # variant fields (method/score/engine/…) live at the top level
    dc6 = assets.png_to_item_dc6(png, it["invwidth"], it["invheight"], fill=fill, dx=dx, dy=dy,
                                 grade=grade, thin=server._is_thin(it), even_border=False)
    alt_id = f"img-{vid}"
    assets.save_alternate_dc6(it["id"], alt_id, dc6)
    meta = {"source": "upscale-2d", "vid": vid, "fill": fill, "dx": dx, "dy": dy,
            "fit_auto": was_auto, "grade": grade, "even_border": False, "ts": time.time()}
    for k in ("engine", "seed", "method", "method_label", "instruction", "positive",
              "negative", "restyle", "nudge", "score", "model", "protect_silhouette"):
        if rec.get(k) is not None:
            meta[k] = rec.get(k)
    assets.save_alt_provenance(it["id"], alt_id, render_png=png, meta=meta)
    server.derive_stack_alternate(it, png, alt_id, fill)
    assets.activate(it["id"], it["invfile"], alt_id)
    return rec.get("method"), best_score


def main():
    targets = _targets()
    print(f"[{time.strftime('%H:%M:%S')}] {len(targets)} target gems x {len(METHODS)} methods "
          f"= {len(targets) * len(METHODS)} runs", flush=True)

    # 0) persist identities so the live server / UI path stays consistent.
    for it in targets:
        describe.set_text(it["id"], IDENTITY[_color_of(it["name"])], source="batch")

    # 1) generate, family-grouped (method-major) to keep each checkpoint resident. Pipelined 2-deep:
    # the remote GPU job (~50s) for one item overlaps the local CPU rembg cutout (~20-25s) of the
    # previous one -- they're different machines/resources, so this is free concurrency, not a race.
    # A 3rd concurrent slot wouldn't help: ComfyUI only executes one GPU job at a time regardless.
    done = fail = 0
    for mi, method in enumerate(METHODS):
        if method in ("faithful_upscale", "sdxl_lock", "flux_lock"):
            comfy.free_memory()  # cleanly switch checkpoints between families
        work = [it for it in targets if not _already(it["id"], method)]
        with cf.ThreadPoolExecutor(max_workers=2) as ex:
            futures = {ex.submit(_generate_one, it, method, _color_of(it["name"])): (it, time.time())
                       for it in work}
            for fut in cf.as_completed(futures):
                it, t0 = futures[fut]
                try:
                    rec, info = fut.result()
                    if rec is None:
                        fail += 1
                        print(f"[{time.strftime('%H:%M:%S')}] SKIP {method:16} {it['name']:18} "
                              f"QA-reject: {info}", flush=True)
                    else:
                        done += 1
                        print(f"[{time.strftime('%H:%M:%S')}] ok   {method:16} {it['name']:18} "
                              f"score={info:.3f} ({time.time()-t0:.0f}s)", flush=True)
                except Exception as e:  # noqa: BLE001
                    fail += 1
                    print(f"[{time.strftime('%H:%M:%S')}] ERR  {method:16} {it['name']:18} {e}",
                          flush=True)
                    traceback.print_exc()

    # 2) promote best-of per gem.
    print(f"[{time.strftime('%H:%M:%S')}] generation done: {done} ok, {fail} skipped/failed. "
          "Promoting best-of...", flush=True)
    for it in targets:
        try:
            method, score = _promote_best(it)
            if method:
                print(f"[{time.strftime('%H:%M:%S')}] PROMOTE {it['name']:18} -> {method} "
                      f"({score:.3f})", flush=True)
            else:
                print(f"[{time.strftime('%H:%M:%S')}] PROMOTE {it['name']:18} -> none passed QA",
                      flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[{time.strftime('%H:%M:%S')}] PROMOTE-ERR {it['name']:18} {e}", flush=True)
            traceback.print_exc()
    print(f"[{time.strftime('%H:%M:%S')}] DONE.", flush=True)


if __name__ == "__main__":
    main()
