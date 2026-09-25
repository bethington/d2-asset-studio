"""Comprehensive 3-engine comparison harness (Flux faithfulness dial / Flux+ControlNet lock /
Qwen-Image-Edit) across 6 item archetypes, several tasks, knob sweeps, seeds.

Produces _engine_out/manifest.json (one row per render, with shape-fidelity IoU + timing) and the
PNGs, which build_engine_report.py turns into the report. Jobs are grouped so Flux loads once, then
Qwen loads once (VRAM freed between families), to avoid model thrash on the 24GB card.

    python scripts/engine_compare.py            # full deep run
    python scripts/engine_compare.py --quick     # 3 items, best settings only (format preview)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))
import comfy  # noqa: E402
import box_mem  # noqa: E402
from PIL import Image  # noqa: E402

OUT = os.path.abspath(os.environ.get("ENGINE_OUT")
                      or os.path.join(os.path.dirname(__file__), "..", "_engine_out"))
ITEMS_DIR = os.environ.get("ENGINE_ITEMS") or ""
SEED = 12345
STY = ", detailed hand-painted dark fantasy game item art, plain background"

# ---- items: physical (name-free) descriptions + a noun for restyle prompts ----
ITEMS = [
    ("helm",   "a rounded iron cap helmet, smooth domed top, weathered dark metal, riveted studs along the rim", "helmet"),
    ("gloves", "a pair of worn leather and steel armored gauntlets, studded knuckles, aged hide", "pair of gauntlets"),
    ("boots",  "a pair of armored boots, riveted steel plates over chain mail, worn leather straps", "pair of armored boots"),
    ("sword",  "a short steel sword, straight double-edged blade, simple crossguard, leather-wrapped hilt", "sword"),
    ("armor",  "a suit of steel plate mail body armor, riveted plates, worn metal chestpiece", "suit of plate body armor"),
    ("ring",   "a metal ring, worn gold band set with a small gemstone", "ring"),
]
ITEM_BASE = {k: b for k, b, _n in ITEMS}
ITEM_NOUN = {k: n for k, _b, n in ITEMS}

# ---- restyle themes (5 looks) ----
THEMES = {
    "gold":   "made of polished royal gold encrusted with glowing blue sapphires, ornate and regal",
    "fire":   "forged from blackened molten iron with glowing orange embers and cracks of fire",
    "ice":    "carved from frostbitten steel sheathed in blue ice and frost, frozen",
    "bone":   "made of bleached bone and corroded black iron, grim and necromantic",
    "arcane": "made of translucent glowing crystal etched with luminous blue arcane runes",
}


def _load(key):
    d = ITEMS_DIR or os.path.join(OUT, "..", "shootout", "items")
    with open(os.path.join(d, f"{key}.png"), "rb") as f:
        return f.read()


# ---- engine adapters (call the real app functions) ----
def E_qwen(sprite, prompt, seed=SEED):
    instr = prompt + ", enhance detail, keep the exact shape and silhouette, do not add a face"
    return comfy.generate_qwen_edit(sprite, instruction=instr, seed=seed)

def E_flux(sprite, prompt, faith, seed=SEED, steps=4):
    return comfy.generate_flux(sprite, description=prompt + STY, seed=seed, faithfulness=faith, steps=steps)

def E_lock(sprite, prompt, shape, seed=SEED, steps=4):
    return comfy.generate_flux_locked(sprite, description=prompt + STY, seed=seed, shape_strength=shape, steps=steps)


# ---- shape-fidelity IoU (output silhouette vs original silhouette) ----
def _sil(png, size=256):
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    a = im.split()[-1]
    bb = a.getbbox()
    if bb:
        a = a.crop(bb)
    a = a.resize((size, size), Image.LANCZOS).point(lambda v: 255 if v > 40 else 0)
    return a

def iou(orig_png, out_png):
    try:
        import numpy as np
        o = np.asarray(_sil(orig_png)) > 0
        g = np.asarray(_sil(out_png)) > 0
        u = (o | g).sum()
        return round(float((o & g).sum()) / float(u), 3) if u else 0.0
    except Exception:  # noqa: BLE001
        return None


# ---- matrix ----
def build_jobs(quick=False):
    """Each job: dict(group, section, item, engine, label, fn). group in {flux,qwen} drives order."""
    jobs = []
    def add(group, section, item, engine, label, fn):
        jobs.append({"group": group, "section": section, "item": item, "engine": engine, "label": label, "fn": fn})

    items = ["helm", "sword", "gloves"] if quick else [k for k, _b, _n in ITEMS]

    # S1 Faithful enhance: qwen + flux@0.85 + flux@0.4
    for it in items:
        base = ITEM_BASE[it]
        add("qwen", "Faithful enhance", it, "Qwen edit", "qwen", lambda s, b=base: E_qwen(s, b))
        add("flux", "Faithful enhance", it, "Flux faith 0.85", "flux@0.85", lambda s, b=base: E_flux(s, b, 0.85))
        add("flux", "Faithful enhance", it, "Flux faith 0.40", "flux@0.40", lambda s, b=base: E_flux(s, b, 0.40))
    if quick:
        return jobs

    # S2 Restyle: all themes on helm + armor, Flux-lock vs Qwen
    for it in ("helm", "armor"):
        noun = ITEM_NOUN[it]
        for tname, frag in THEMES.items():
            p = f"a {noun} {frag}"
            add("flux", "Restyle (themes)", it, f"Flux-lock {tname}", f"lock:{tname}", lambda s, p=p: E_lock(s, p, 0.7))
            add("qwen", "Restyle (themes)", it, f"Qwen {tname}", f"qwen:{tname}", lambda s, p=p: E_qwen(s, p))
    # gold across all items (generality), Flux-lock
    for it in [k for k, _b, _n in ITEMS]:
        p = f"a {ITEM_NOUN[it]} {THEMES['gold']}"
        add("flux", "Restyle (gold, all items)", it, "Flux-lock gold", "lock:gold", lambda s, p=p: E_lock(s, p, 0.7))

    # S3 Reimagine: pure text (faithfulness 0)
    for it in [k for k, _b, _n in ITEMS]:
        add("flux", "Reimagine (text)", it, "Flux text", "flux@0.0", lambda s, b=ITEM_BASE[it]: E_flux(s, b, 0.0))

    # S4 Sweeps
    for it in ("helm", "sword"):
        for f in (0.0, 0.2, 0.4, 0.65, 0.9):
            add("flux", f"Faithfulness sweep ({it})", it, f"faith {f}", f"f{f}", lambda s, b=ITEM_BASE[it], f=f: E_flux(s, b, f))
    for st in (0.3, 0.5, 0.7, 0.9):
        p = f"a {ITEM_NOUN['helm']} {THEMES['gold']}"
        add("flux", "Shape-lock sweep (helm)", "helm", f"lock {st}", f"cn{st}", lambda s, p=p, st=st: E_lock(s, p, st))

    # S5 Seed consistency: 3 seeds x 3 engines on helm
    for sd in (111, 222, 333):
        add("flux", "Seed consistency (helm)", "helm", f"Flux faith 0.4 seed {sd}", f"seedF{sd}",
            lambda s, sd=sd: E_flux(s, ITEM_BASE["helm"], 0.4, seed=sd))
        add("flux", "Seed consistency (helm)", "helm", f"Flux-lock gold seed {sd}", f"seedL{sd}",
            lambda s, sd=sd: E_lock(s, f"a helmet {THEMES['gold']}", 0.7, seed=sd))
        add("qwen", "Seed consistency (helm)", "helm", f"Qwen edit seed {sd}", f"seedQ{sd}",
            lambda s, sd=sd: E_qwen(s, ITEM_BASE["helm"], seed=sd))
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    sprites = {k: _load(k) for k, _b, _n in ITEMS}
    jobs = build_jobs(a.quick)
    # group order: flux first (loads once), then qwen
    jobs.sort(key=lambda j: 0 if j["group"] == "flux" else 1)
    n_flux = sum(1 for j in jobs if j["group"] == "flux")
    print(f"COMFY={comfy.COMFY_URL}  jobs={len(jobs)} (flux {n_flux}, qwen {len(jobs)-n_flux})", flush=True)
    print("box:", box_mem.snapshot(), flush=True)

    manifest = []
    comfy.free_memory()
    last_group = None
    for i, j in enumerate(jobs):
        if j["group"] != last_group:
            comfy.free_memory()  # switch model family cleanly
            last_group = j["group"]
        tag = f"{i:03d}_{j['item']}_{j['engine'].replace(' ','').replace(':','-')}"
        print(f"[{i+1}/{len(jobs)}] {j['section']} | {j['item']} | {j['engine']}", flush=True)
        t = time.time()
        try:
            png, size = j["fn"](sprites[j["item"]])
            path = os.path.join(OUT, f"{tag}.png")
            with open(path, "wb") as f:
                f.write(png)
            row = {"i": i, "section": j["section"], "item": j["item"], "engine": j["engine"],
                   "label": j["label"], "group": j["group"], "file": os.path.basename(path),
                   "secs": round(time.time() - t, 1), "iou": iou(sprites[j["item"]], png), "ok": True}
            print(f"    ok {row['secs']}s  IoU={row['iou']}", flush=True)
        except Exception as e:  # noqa: BLE001
            row = {"i": i, "section": j["section"], "item": j["item"], "engine": j["engine"],
                   "label": j["label"], "group": j["group"], "error": str(e)[:200],
                   "secs": round(time.time() - t, 1), "ok": False}
            print(f"    FAIL {row['secs']}s :: {row['error']}", flush=True)
        manifest.append(row)
        with open(os.path.join(OUT, "manifest.json"), "w") as f:
            json.dump(manifest, f, indent=2)
    comfy.free_memory()
    print("done ->", OUT, flush=True)


if __name__ == "__main__":
    main()
