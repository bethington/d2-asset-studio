"""Publish fidelity-lab m0-m7 results into the studio UI so every method is visible and
immediately pickable.

For each (item, method) with a master on disk it:
  1. adds an upscale history-strip variant (upscale_store.add_variant) -> shows in §2 with
     a method/score badge and is selectable, and
  2. saves a DC6 alternate (assets.save_alternate_dc6 + provenance) -> immediately pickable
     as an alternate in the gallery, plus the derived rune/gem stack DC6.

Idempotent: a method already published for an item (same alt_id) is skipped unless --force.
Reversible: --unpublish removes every lab-* variant and alternate this script created.

    python scripts/publish_lab_to_ui.py                 # publish m0..m7 for the whole set
    python scripts/publish_lab_to_ui.py --methods m6_combo,m7_combo_ct
    python scripts/publish_lab_to_ui.py --unpublish
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # 'app' package root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import assets, upscale_store  # noqa: E402
import catalog  # noqa: E402
import comfy  # noqa: E402
from PIL import Image  # noqa: E402

# assets.py buckets alternates by invfile (server.py: "shared-by-DC6 bucketing") via a resolver
# the live server wires from its catalog (server.py: register_item_resolver(lambda id: catalog
# by_id.get(id))). Without it, assets.py silently falls back to a per-item-id bucket that the
# real server never reads -- alternates would be written but invisible in the studio UI. Mirror
# that wiring here so this script's writes land in the SAME bucket the server reads from.
_CATALOG_ITEMS, _CATALOG_BY_CODE = catalog.build_catalog()
_BY_ID = {it["id"]: it for it in _CATALOG_ITEMS}
assets.register_item_resolver(lambda item_id: _BY_ID.get(item_id))

LAB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_fidelity_out", "lab"))
TESTSET = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_fidelity_out", "testset50.json"))

# methods to surface + the friendly label the UI badge shows
LABELS = {
	"m0_base": "Qwen · no prompt (baseline)",
	"m1_anchor": "Qwen · identity",
	"m2_catstyle": "Qwen · identity + category",
	"m3_anchor_ct": "Qwen · identity + colour-lock",
	"m4_catstyle_ct": "Qwen · category + colour-lock",
	"m5_pad": "Qwen · padded",
	"m6_combo": "Qwen · combo (best, free colour)",
	"m7_combo_ct": "Qwen · combo + colour-lock (default)",
}
ALT_PREFIX = "lab-"   # every alternate this script writes: lab-m7_combo_ct etc.
MANIFEST = os.path.join(LAB, "manifest.json")


def item_index():
	"""inv -> catalog item (real id + dims), from the same catalog build the resolver above
	uses (so alternates land in the exact bucket the live server reads from)."""
	byinv = {}
	for it in _CATALOG_ITEMS:
		byinv.setdefault(it["invfile"], it)
	return byinv, {"by_code": _CATALOG_BY_CODE}


def load_scores() -> dict:
	"""(inv, method) -> the fidelity_lab scoring dict (iou/color/ssim/score/...), so published
	variants carry the SAME score the wide-sweep report shows -- not a re-measurement."""
	if not os.path.exists(MANIFEST):
		return {}
	rows = json.load(open(MANIFEST, encoding="utf-8"))["rows"]
	return {(r["inv"], r["method"]): {k: r[k] for k in ("iou", "dE_mean", "emd", "ssim", "color", "score")}
	        for r in rows if "score" in r}


# must match server._STACK_TYPES (the stackable rune/gem item types)
STACK_TYPES = {"rune", "gema", "gemd", "geme", "gemr", "gems", "gemz", "gemt"}


def _stack_item(it, cat):
	if (it.get("type") or "").strip() not in STACK_TYPES:
		return None
	return cat["by_code"].get((it.get("code") or "") + "s")


def publish(methods, force=False):
	byinv, cat = item_index()
	scores = load_scores()
	rows = json.load(open(TESTSET, encoding="utf-8")) if os.path.exists(TESTSET) else []
	invs = [r["inv"] for r in rows] or sorted(os.listdir(LAB))
	n_var = n_alt = 0
	for inv in invs:
		it = byinv.get(inv)
		if not it:
			print(f"  {inv}: not in catalog, skip")
			continue
		d = os.path.join(LAB, inv)
		for method in methods:
			mp = os.path.join(d, f"{method}.master.png")
			if not os.path.exists(mp):
				continue
			with open(mp, "rb") as f:
				master = f.read()
			alt_id = ALT_PREFIX + method
			if not force and alt_id in {a["id"] if isinstance(a, dict) else a
			                            for a in assets.list_alternates(it["id"])}:
				continue
			sz = Image.open(io.BytesIO(master)).size
			canonical = comfy.to_canonical_2x(master, (it["invwidth"] * assets.CELL_PX,
			                                            it["invheight"] * assets.CELL_PX))
			score = scores.get((inv, method))
			label = LABELS.get(method, method)
			meta = {"mode": "desc", "engine": "qwen", "method": method, "method_label": label,
			        "label": label, "seed": 7, "source": "fidelity-lab", "size": list(sz),
			        "ts": time.time()}
			if score:
				meta["score"] = score
			upscale_store.add_variant(it["id"], master_png=master, canonical_png=canonical, meta=meta)
			n_var += 1
			try:
				dc6 = assets.png_to_item_dc6(master, it["invwidth"], it["invheight"], fill=0.94)
				assets.save_alternate_dc6(it["id"], alt_id, dc6)
				assets.save_alt_provenance(it["id"], alt_id, render_png=master,
				                           meta={"source": "fidelity-lab", "method": method,
				                                 "label": LABELS.get(method, method)})
				n_alt += 1
				st = _stack_item(it, cat) if (it.get("type") or "") in STACK_TYPES else None
				if st:
					sdc6 = assets.png_to_item_dc6(master, st["invwidth"], st["invheight"],
					                              fill=0.94, stack=True)
					assets.save_alternate_dc6(st["id"], alt_id, sdc6)
					assets.save_alt_provenance(st["id"], alt_id, render_png=master,
					                           meta={"source": "fidelity-lab-stack",
					                                 "from": it["id"], "method": method})
			except Exception as e:  # noqa: BLE001
				print(f"  {inv} {method}: DC6 FAILED {e}")
		print(f"  {inv}: published", flush=True)
	print(f"\n{n_var} variants + {n_alt} DC6 alternates published.")


def unpublish():
	byinv, _ = item_index()
	rows = json.load(open(TESTSET, encoding="utf-8")) if os.path.exists(TESTSET) else []
	removed = 0
	for r in rows:
		it = byinv.get(r["inv"])
		if not it:
			continue
		for a in list(assets.list_alternates(it["id"])):
			aid = a["id"] if isinstance(a, dict) else a
			if aid.startswith(ALT_PREFIX):
				assets.delete_alternate(it["id"], aid)
				removed += 1
		idx = upscale_store.load(it["id"])
		for v in list(idx.get("variants", [])):
			if v.get("source") == "fidelity-lab":
				upscale_store.delete_variant(it["id"], v["id"])
	print(f"removed {removed} lab alternates + their variants")


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--methods", default=",".join(LABELS))
	ap.add_argument("--force", action="store_true")
	ap.add_argument("--unpublish", action="store_true")
	args = ap.parse_args()
	if args.unpublish:
		unpublish()
	else:
		publish(list(filter(None, args.methods.split(","))), force=args.force)


if __name__ == "__main__":
	main()
