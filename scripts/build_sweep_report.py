"""Wide m0-m7 sweep report: 55 items x 8 Qwen-Image-Edit recipes, per-class fidelity
breakdowns, method explanations, and hover-zoom comparison grids.

Reads _fidelity_out/lab/manifest.json (rows written by fidelity_lab.py) and
_fidelity_out/testset50.json (item names + categories). Reuses the image/hover
helpers from build_fidelity_report. Output: _fidelity_out/sweep_report.html.

    python scripts/build_sweep_report.py
"""

from __future__ import annotations

import html
import io
import json
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))
import assets  # noqa: E402
import build_fidelity_report as brep  # noqa: E402 -- img_tag/hover_uri/nearest_uri/refit4x/_thumb/file_png

OUT = brep.OUT
LAB = brep.LAB
METHODS = ["m0_base", "m1_anchor", "m2_catstyle", "m3_anchor_ct",
           "m4_catstyle_ct", "m5_pad", "m6_combo", "m7_combo_ct"]
SHORT = {
	"m0_base": "m0", "m1_anchor": "m1", "m2_catstyle": "m2", "m3_anchor_ct": "m3",
	"m4_catstyle_ct": "m4", "m5_pad": "m5", "m6_combo": "m6", "m7_combo_ct": "m7",
}
CATS = [("gem", "Gems &amp; jewels"), ("glow", "Essences (glow)"), ("control", "Controls / other")]

METHOD_DOC = [
	("m0", "Baseline — Qwen enhance, no prompt",
	 "The current production behaviour. Qwen-Image-Edit is told only the fixed house style "
	 "(“muted palette, no glow…”) and “keep the item,” but never <i>what the item is</i>. "
	 "From a 29px blob it invents an identity — the source of the treasure-chest gems."),
	("m1", "+ Identity anchor",
	 "Prepend one literal sentence describing the original sprite (“the item is a blood-red ruby "
	 "gemstone, rectangular step-cut…”). This single change stops the hallucination — the model now "
	 "knows the subject and enhances <i>it</i> instead of guessing. Biggest single quality jump."),
	("m2", "+ Category style + anti-hallucination negative",
	 "Swap the armor-oriented house style for a category-appropriate one (gems: vivid, glossy, "
	 "translucent, keep the inner glow; essences: luminous magical glow) and add a negative prompt "
	 "listing the exact failure objects (“treasure chest, box, letter, face…”). Restores gem colour "
	 "and saturation the house style was actively killing."),
	("m3", "= m1 + colour lock",
	 "Take the m1 (identity) master and histogram-match its colours back to the original in Oklab "
	 "perceptual space (per-channel quantile mapping over the object pixels). Deterministic, ~free, "
	 "and it pins the palette exactly to the source so the model can’t drift the hue."),
	("m4", "= m2 + colour lock",
	 "The m2 (identity + category style) master with the same Oklab colour transfer applied."),
	("m5", "+ Margin pad",
	 "Before matting, force a transparent margin around the sprite. Cell-filling sprites (e.g. the "
	 "rectangular Sapphire) otherwise leave the background cutter nothing to segment — producing empty "
	 "sprites. The pad guarantees an object-on-background composition so the re-cut always has an edge."),
	("m6", "Combo — pad + identity + category style",
	 "The generation-side recipe: all three fixes together (margin pad + identity anchor + category "
	 "style + negative). This is the best result before any post-processing — recommended when you want "
	 "the model free to pick its own colours."),
	("m7", "Combo + colour lock  (recommended default)",
	 "m6 plus the Oklab colour transfer. Identity fixes hallucination, the pad fixes cell-filling "
	 "sprites, the category style keeps gems vivid, and the colour lock guarantees the palette matches "
	 "the original. The most faithful of the eight and the recommended production default."),
]


def load():
	rows = []
	p = os.path.join(LAB, "manifest.json")
	if os.path.exists(p):
		rows = [r for r in json.load(open(p, encoding="utf-8"))["rows"] if r["method"] in METHODS]
	by = {(r["inv"], r["method"]): r for r in rows}
	meta = {}
	tp = os.path.join(OUT, "testset50.json")
	if os.path.exists(tp):
		for t in json.load(open(tp, encoding="utf-8")):
			meta[t["inv"]] = t
	return rows, by, meta


def mean(vals):
	vals = [v for v in vals if v is not None]
	return sum(vals) / len(vals) if vals else None


def cell(v, bold=False):
	if v is None:
		return "<td class=na>—</td>"
	cls = "good" if v >= 0.72 else "ok" if v >= 0.55 else "bad"
	b = "font-weight:700" if bold else ""
	return f'<td class={cls} style="{b}">{v:.3f}</td>'


def build():
	rows, by, meta = load()
	invs = sorted({r["inv"] for r in rows},
	              key=lambda i: ({"gem": 0, "glow": 1, "control": 2}.get(meta.get(i, {}).get("cat", "control"), 3),
	                             meta.get(i, {}).get("name", i)))
	cat_of = {i: meta.get(i, {}).get("cat", "control") for i in invs}

	P = [f"""<!-- build_sweep_report.py -->
<meta charset="utf-8"><title>m0–m7 wide sweep — {len(invs)} items</title>
<style>
 body{{background:#17171d;color:#d8d4c8;font:14px/1.55 'Segoe UI',sans-serif;margin:24px auto;padding:0 28px}}
 h1,h2,h3{{color:#e8d9a0;font-weight:600}} h1{{font-size:23px}}
 h2{{border-bottom:1px solid #3a3a44;padding-bottom:4px;margin-top:38px}}
 table{{border-collapse:collapse;margin:12px 0}} td,th{{border:1px solid #33333c;padding:5px 10px;text-align:center}}
 th{{background:#22222a;color:#cfc59a}} td.name{{text-align:left}}
 td.good{{background:#1d3020;color:#9fe0a0}} td.ok{{background:#2e2a1a;color:#e0d090}} td.bad{{background:#33191b;color:#e09090}} td.na{{color:#666}}
 .strip{{background:#282830;padding:10px;border-radius:6px;margin:8px 0;overflow-x:auto;white-space:nowrap}}
 .strip figure{{display:inline-block;vertical-align:top;margin:0 10px 4px 0;padding:7px 7px 3px;text-align:center;
   background:#1e1e25;border:1px solid #3a3a44;border-radius:5px}} .strip figcaption{{font-size:11px;color:#aaa;margin-top:4px}}
 img[data-zoom]{{cursor:zoom-in}}
 #zv{{display:none;position:fixed;z-index:99;pointer-events:none;background:#0c0c10;border:1px solid #666;padding:6px;border-radius:6px;box-shadow:0 4px 24px #000c}}
 #zv img{{display:block;max-width:520px;max-height:520px}}
 .doc{{background:#1c1e24;border:1px solid #333;border-radius:6px;padding:4px 18px;margin:10px 0}}
 .doc h3{{margin:14px 0 2px}} code{{background:#26262e;padding:1px 5px;border-radius:3px}} .small{{font-size:12px;color:#9a968c}}
 .rec{{background:#20241d;border:1px solid #3d4433;border-radius:6px;padding:2px 18px;margin:12px 0}}
</style>
<h1>Enhance methods m0–m7 — wide sweep across {len(invs)} items</h1>
<p class=small>Every recipe is Qwen-Image-Edit-2509; the method is the prompt + the Python pad/colour-transfer
steps around it. Composite fidelity = 0.35·shape-IoU + 0.35·colour + 0.30·SSIM, scored end-of-pipe
(fit → palette-quantize → DC6 → render vs the MPQ original). Hover any tile for its hi-res master.</p>"""]

	# ---- method docs ----
	P.append("<h2>1 · What each method is</h2><div class=doc>")
	for tag, title, body in METHOD_DOC:
		P.append(f"<h3>{tag} — {title}</h3><p>{body}</p>")
	P.append("</div>")

	# ---- per-class summary ----
	P.append("<h2>2 · Fidelity by item class</h2>")
	P.append("<p class=small>Mean composite score per method (higher = closer to the original). "
	         "Bold = best in row.</p>")
	P.append("<table><tr><th>class</th><th>n</th>" +
	         "".join(f"<th>{SHORT[m]}</th>" for m in METHODS) + "</tr>")
	for cat, label in CATS:
		cinvs = [i for i in invs if cat_of[i] == cat]
		if not cinvs:
			continue
		means = {m: mean([by.get((i, m), {}).get("score") for i in cinvs]) for m in METHODS}
		best = max((v for v in means.values() if v is not None), default=None)
		P.append(f"<tr><td class=name>{label}</td><td>{len(cinvs)}</td>" +
		         "".join(cell(means[m], bold=(means[m] == best)) for m in METHODS) + "</tr>")
	allmeans = {m: mean([by.get((i, m), {}).get("score") for i in invs]) for m in METHODS}
	allbest = max((v for v in allmeans.values() if v is not None), default=None)
	P.append(f"<tr><td class=name><b>ALL</b></td><td>{len(invs)}</td>" +
	         "".join(cell(allmeans[m], bold=(allmeans[m] == allbest)) for m in METHODS) + "</tr>")
	P.append("</table>")

	# ---- per-item score table ----
	P.append("<h2>3 · Per-item scores</h2><table><tr><th class=name>item</th><th>class</th>" +
	         "".join(f"<th>{SHORT[m]}</th>" for m in METHODS) + "</tr>")
	for i in invs:
		nm = html.escape(meta.get(i, {}).get("name", i))
		scores = {m: by.get((i, m), {}).get("score") for m in METHODS}
		best = max((v for v in scores.values() if v is not None), default=None)
		P.append(f'<tr><td class=name>{nm}<div class=small>{i}</div></td>'
		         f'<td>{cat_of[i]}</td>' +
		         "".join(cell(scores[m], bold=(scores[m] == best)) for m in METHODS) + "</tr>")
	P.append("</table>")

	# ---- per-item visual grids ----
	P.append("<h2>4 · Side-by-side (hover a tile for its hi-res master)</h2>")
	for i in invs:
		try:
			orig = assets.dc6_to_png_bytes(assets.read_original_dc6(i))
		except Exception:  # noqa: BLE001
			continue
		oim = Image.open(io.BytesIO(orig))
		nm = html.escape(meta.get(i, {}).get("name", i))
		figs = [f"<figure>{brep.img_tag(orig, hover=brep.nearest_uri(orig))}<figcaption>ORIGINAL</figcaption></figure>"]
		for m in METHODS:
			png = brep.file_png(os.path.join(LAB, i, f"{m}.fitted.png"))
			if not png:
				continue
			raw = brep.file_png(os.path.join(LAB, i, f"{m}.master.png"))
			hv = brep.hover_uri(brep.refit4x(raw, oim.convert("RGBA")), cap=520) if raw else brep.nearest_uri(png)
			sc = by.get((i, m), {}).get("score")
			cap = f"{SHORT[m]}" + (f" · {sc:.3f}" if sc is not None else "")
			figs.append(f"<figure>{brep.img_tag(png, hover=hv)}<figcaption>{cap}</figcaption></figure>")
		P.append(f"<h3>{nm} <span class=small>({i} · {cat_of[i]})</span></h3>"
		         f"<div class=strip>{''.join(figs)}</div>")

	P.append("""<div id=zv><img id=zvi alt=""></div>
<script>
const zv=document.getElementById('zv'),zvi=document.getElementById('zvi');
document.addEventListener('mouseover',e=>{const t=e.target.closest('img[data-zoom]');if(!t)return;zvi.src=t.dataset.zoom;zv.style.display='block';});
document.addEventListener('mouseout',e=>{if(e.target.closest('img[data-zoom]'))zv.style.display='none';});
document.addEventListener('mousemove',e=>{if(zv.style.display==='none')return;const m=18,r=zv.getBoundingClientRect();
let x=e.clientX+m,y=e.clientY+m;if(x+r.width>innerWidth-8)x=Math.max(8,e.clientX-r.width-m);if(y+r.height>innerHeight-8)y=Math.max(8,innerHeight-r.height-8);
zv.style.left=x+'px';zv.style.top=y+'px';});
</script>""")
	return "\n".join(P)


def main():
	out = os.path.join(OUT, "sweep_report.html")
	with open(out, "w", encoding="utf-8") as f:
		f.write(build())
	print(out)


if __name__ == "__main__":
	main()
