"""Build the comprehensive 3-engine comparison report from engine_compare.py output.

Artifact-ready page (body content only): decision matrix, per-engine cards (with measured speed +
avg shape-IoU), head-to-head grids per task, knob sweeps, seed-consistency, and a failure gallery.
Every render shows the 1024 hero + a true inventory-cell-size thumbnail + its shape-fidelity IoU.

    python scripts/build_engine_report.py --items-dir <dir> --out report.html
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import statistics

from PIL import Image

OUT = os.path.abspath(os.environ.get("ENGINE_OUT")
                      or os.path.join(os.path.dirname(__file__), "..", "_engine_out"))

ITEM_LABEL = {"helm": "Helmet", "gloves": "Gloves", "boots": "Boots",
              "sword": "Sword (thin)", "armor": "Body armor", "ring": "Ring (tiny)"}


def b64(png):
    return "data:image/png;base64," + base64.b64encode(png).decode()

def thumb(png, box=240):
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    im.thumbnail((box, box), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()

def content(im):
    bb = im.split()[-1].getbbox()
    return im.crop(bb) if bb else im

def ingame(png, orig_png):
    """Downscale the render to the ORIGINAL's content pixel size (what actually ships in the DC6),
    then present it magnified with nearest-neighbour so the at-size detail is legible."""
    o = content(Image.open(io.BytesIO(orig_png)).convert("RGBA"))
    g = content(Image.open(io.BytesIO(png)).convert("RGBA"))
    tw, th = o.size
    g = g.resize((max(1, tw), max(1, th)), Image.LANCZOS)          # the real in-game downsample
    disp = g.resize((tw * 3, th * 3), Image.NEAREST)               # magnify to see it
    b = io.BytesIO(); disp.save(b, "PNG"); return b.getvalue()

def orig_hero(orig_png, box=240):
    im = content(Image.open(io.BytesIO(orig_png)).convert("RGBA"))
    scale = max(1, box // max(im.size))
    im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()

def iou_cls(v):
    if v is None: return ""
    return "good" if v >= 0.85 else ("warn" if v >= 0.6 else "bad")

def num(label):
    m = re.search(r"(\d+\.?\d*)", label)
    return float(m.group(1)) if m else 0.0


def card(row, origs):
    it = row["item"]
    if not row.get("ok"):
        return (f'<figure class="err"><div class="x">failed</div>'
                f'<figcaption>{row["engine"]}<br><span class="sub">{row.get("error","")[:80]}</span></figcaption></figure>')
    png = open(os.path.join(OUT, row["file"]), "rb").read()
    hero = b64(thumb(png))
    ig = b64(ingame(png, origs[it]))
    v = row.get("iou")
    badge = f'<span class="iou {iou_cls(v)}">IoU {v}</span>' if v is not None else ""
    return (f'<figure><div class="imgwrap"><img class="hero" src="{hero}" alt="{row["engine"]}"></div>'
            f'<figcaption><div class="cap-top">{row["engine"]} {badge}</div>'
            f'<div class="ig"><img src="{ig}" alt="in-game size"><span class="sub">in-game &middot; {row["secs"]}s</span></div>'
            f'</figcaption></figure>')


CSS = """
<style>
:root{ --ground:#efe9df; --panel:#f6f1e8; --ink:#241d16; --muted:#6d6152; --line:#d8cdba;
  --brass:#9c7513; --rust:#a8391f; --verd:#4f6b3f; --amber:#8a6d1a;
  --checker:#e3dccd; --shadow:0 1px 0 rgba(0,0,0,.04),0 8px 24px rgba(40,30,15,.06); }
@media (prefers-color-scheme:dark){ :root{ --ground:#15120e; --panel:#1f1a15; --ink:#e9e0d1; --muted:#9a8d78;
  --line:#39312a; --brass:#d6a94b; --rust:#c9583b; --verd:#8aa86a; --amber:#d6a94b; --checker:#2a241d;
  --shadow:0 1px 0 rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.35); } }
:root[data-theme="light"]{ --ground:#efe9df; --panel:#f6f1e8; --ink:#241d16; --muted:#6d6152; --line:#d8cdba;
  --brass:#9c7513; --rust:#a8391f; --verd:#4f6b3f; --amber:#8a6d1a; --checker:#e3dccd; }
:root[data-theme="dark"]{ --ground:#15120e; --panel:#1f1a15; --ink:#e9e0d1; --muted:#9a8d78; --line:#39312a;
  --brass:#d6a94b; --rust:#c9583b; --verd:#8aa86a; --amber:#d6a94b; --checker:#2a241d; }
*{box-sizing:border-box}
.rpt{background:var(--ground);color:var(--ink);font:16px/1.6 "Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;padding:48px 22px 96px}
.wrap{max-width:1240px;margin:0 auto}
.eyebrow{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;letter-spacing:.22em;text-transform:uppercase;color:var(--brass);margin:0 0 10px}
h1{font-size:clamp(30px,5vw,46px);line-height:1.04;margin:0 0 12px;text-wrap:balance}
.lede{font-size:18px;color:var(--muted);max-width:66ch;margin:0 0 34px}
h2{font-size:21px;margin:0}
.sec{margin:0 0 46px}
.sec-head{display:flex;align-items:baseline;gap:12px;border-bottom:1px solid var(--line);padding-bottom:8px;margin:0 0 8px}
.note{color:var(--muted);font-size:14.5px;max-width:80ch;margin:0 0 18px}
table.mtx{width:100%;border-collapse:collapse;margin:0 0 10px;font-size:14px}
table.mtx th,table.mtx td{border:1px solid var(--line);padding:9px 11px;text-align:left;vertical-align:top}
table.mtx th{background:var(--panel);font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;letter-spacing:.03em;text-transform:uppercase;color:var(--muted)}
table.mtx td b{color:var(--brass)}
.cards3{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin:0 0 10px}
.ecard{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--brass);border-radius:12px;padding:16px 18px;box-shadow:var(--shadow)}
.ecard h3{margin:0 0 4px;font-size:17px} .ecard .k{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;color:var(--muted);margin:0 0 10px}
.ecard p{margin:0 0 8px;font-size:14px} .ecard .win{color:var(--verd)} .ecard .lose{color:var(--rust)}
.ecard .stat{display:flex;gap:16px;margin-top:8px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px}
.ecard .stat b{color:var(--ink);font-size:15px;display:block}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(178px,1fr));gap:14px}
.strip{display:flex;gap:12px;overflow-x:auto;padding-bottom:8px}
.strip figure{flex:0 0 172px}
.itemrow{margin:0 0 8px} .itemrow h4{margin:14px 0 8px;font-size:14px;color:var(--brass);font-family:ui-monospace,Menlo,Consolas,monospace;letter-spacing:.04em}
figure{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;box-shadow:var(--shadow)}
.imgwrap{background-image:linear-gradient(45deg,var(--checker) 25%,transparent 25%),linear-gradient(-45deg,var(--checker) 25%,transparent 25%),linear-gradient(45deg,transparent 75%,var(--checker) 75%),linear-gradient(-45deg,transparent 75%,var(--checker) 75%);background-size:14px 14px;background-position:0 0,0 7px,7px -7px,-7px 0}
figure img.hero{display:block;width:100%;height:auto}
figcaption{padding:7px 9px 9px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px}
.cap-top{display:flex;justify-content:space-between;align-items:center;gap:6px}
.ig{display:flex;align-items:center;gap:8px;margin-top:7px;padding-top:7px;border-top:1px solid var(--line)}
.ig img{image-rendering:pixelated;border:1px solid var(--line);border-radius:3px;background:#0003}
.ig .sub{color:var(--muted)} .sub{color:var(--muted)}
.iou{font-size:10.5px;padding:1px 6px;border-radius:999px;border:1px solid var(--line);white-space:nowrap}
.iou.good{color:var(--verd);border-color:var(--verd)} .iou.warn{color:var(--amber);border-color:var(--amber)} .iou.bad{color:var(--rust);border-color:var(--rust)}
figure.err{border-color:var(--rust)} figure.err .x{padding:26px;text-align:center;color:var(--rust);font-weight:700}
.tag{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:2px 8px}
.hero-strip{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 30px}
.hero-strip figure{flex:0 0 120px} .hero-strip img{image-rendering:pixelated}
.foot{color:var(--muted);font-size:13px;border-top:1px solid var(--line);padding-top:18px;margin-top:14px}
</style>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--items-dir", required=True)
    ap.add_argument("--out", default=os.path.join(OUT, "report.html"))
    a = ap.parse_args()
    manifest = json.load(open(os.path.join(OUT, "manifest.json")))
    origs = {}
    for k in ITEM_LABEL:
        p = os.path.join(a.items_dir, f"{k}.png")
        if os.path.exists(p):
            origs[k] = open(p, "rb").read()

    ok = [r for r in manifest if r.get("ok")]
    def avg_secs(pred):
        xs = [r["secs"] for r in ok if pred(r)]
        return round(statistics.mean(xs), 1) if xs else 0
    def avg_iou(pred):
        xs = [r["iou"] for r in ok if pred(r) and r.get("iou") is not None]
        return round(statistics.mean(xs), 2) if xs else None

    # ---- engine cards (measured) ----
    flux_faith = lambda r: r["engine"].startswith("Flux faith")
    flux_lock = lambda r: "lock" in r["engine"].lower() or r["engine"].startswith("Flux-lock")
    qwen = lambda r: r["engine"].lower().startswith("qwen")
    ecards = f"""
    <div class="ecard"><h3>Flux — faithfulness dial</h3><div class="k">img2img &rarr; txt2img, one knob</div>
      <p>One slider spans very-true (low denoise) to fully reimagined (pure text). Fast, follows prose,
      immune to the SDXL 'skull' trap.</p>
      <p class="win">+ fastest, flexible range</p><p class="lose">&ndash; soft on tiny sources; drifts at high creativity</p>
      <div class="stat"><span><b>{avg_secs(flux_faith)}s</b>avg (warm)</span><span><b>{avg_iou(flux_faith)}</b>avg IoU</span><span><b>~4GB</b>VRAM</span></div></div>
    <div class="ecard" style="border-left-color:var(--verd)"><h3>Flux + ControlNet — lock outline</h3><div class="k">txt2img pinned to the silhouette</div>
      <p>Restyle material/colour freely from the prompt while a silhouette hint locks the exact
      outline. The 'reinvent the look, keep the shape' engine.</p>
      <p class="win">+ dramatic restyle, shape held</p><p class="lose">&ndash; only as good as the silhouette hint</p>
      <div class="stat"><span><b>{avg_secs(flux_lock)}s</b>avg (warm)</span><span><b>{avg_iou(flux_lock)}</b>avg IoU</span><span><b>~5GB</b>VRAM</span></div></div>
    <div class="ecard" style="border-left-color:var(--brass)"><h3>Qwen-Image-Edit — true edit</h3><div class="k">Q4 GGUF, edits the real sprite</div>
      <p>Edits the actual pixels per instruction, so it stays on-subject and structurally faithful.
      The most faithful engine — at ~10&times; the cost.</p>
      <p class="win">+ most faithful, richest detail</p><p class="lose">&ndash; slow; elaborates ambiguous sprites</p>
      <div class="stat"><span><b>{avg_secs(qwen)}s</b>avg</span><span><b>{avg_iou(qwen)}</b>avg IoU</span><span><b>~13GB</b>VRAM</span></div></div>
    """

    # ---- sections (in manifest order of first appearance) ----
    order, groups = [], {}
    for r in manifest:
        s = r["section"]
        if s not in groups: groups[s] = []; order.append(s)
        groups[s].append(r)

    sec_html = []
    for s in order:
        rows = groups[s]
        is_sweep = "sweep" in s.lower()
        note = SECTION_NOTES.get(s, "")
        if is_sweep:
            rows = sorted(rows, key=lambda r: num(r["label"]))
            body = f'<div class="strip">{"".join(card(r, origs) for r in rows)}</div>'
        elif s.startswith("Restyle (themes)") or s.startswith("Seed") or s == "Faithful enhance":
            # group by item, engines side by side
            byitem = {}
            for r in rows: byitem.setdefault(r["item"], []).append(r)
            blocks = []
            for it, rs in byitem.items():
                blocks.append(f'<div class="itemrow"><h4>{ITEM_LABEL.get(it,it)}</h4>'
                              f'<div class="grid">{"".join(card(r,origs) for r in rs)}</div></div>')
            body = "".join(blocks)
        else:
            body = f'<div class="grid">{"".join(card(r,origs) for r in rows)}</div>'
        sec_html.append(f'<section class="sec"><div class="sec-head"><h2>{s}</h2>'
                        f'<span class="tag">{len(rows)} renders</span></div>'
                        f'<p class="note">{note}</p>{body}</section>')

    # ---- original items hero strip ----
    hero_items = "".join(
        f'<figure><div class="imgwrap"><img src="{b64(orig_hero(origs[k],120))}"></div>'
        f'<figcaption>{ITEM_LABEL[k]}</figcaption></figure>' for k in ITEM_LABEL if k in origs)

    total = len(ok)
    body = f"""{CSS}
<div class="rpt"><div class="wrap">
  <p class="eyebrow">Asset Studio &middot; engine comparison</p>
  <h1>Three engines, six shapes, four tasks</h1>
  <p class="lede">A decision guide for the description lane: which engine + settings to reach for, by what
  you're doing and what item you're doing it to. {total} renders, fixed seed unless noted. Each shows the
  1024 render, its true <b>in-game cell size</b>, and a <b>shape-fidelity IoU</b> (silhouette match to the original).</p>
  <div class="hero-strip">{hero_items}</div>

  {DECISION_MATRIX}

  <section class="sec"><div class="sec-head"><h2>The three engines</h2><span class="tag">measured</span></div>
  <p class="note">Speed and average shape-IoU are measured from this run on the RTX&nbsp;3090.</p>
  <div class="cards3">{ecards}</div></section>

  {''.join(sec_html)}

  {RECOMMENDATIONS}
  <p class="foot">ComfyUI on RTX&nbsp;3090. Flux&nbsp;=&nbsp;flux.1&nbsp;schnell (+Union-Pro ControlNet for lock);
  Qwen&nbsp;=&nbsp;Qwen-Image-Edit-2509 Q4 GGUF. IoU = intersection-over-union of the output silhouette vs the
  original at 256&sup2;. In-game thumbnails are the render downsampled to the sprite's real pixel size, shown 3&times;.</p>
</div></div>"""
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(body)
    print("report ->", a.out, f"({len(body)//1024} KB)")


SECTION_NOTES = {
    "Faithful enhance": "Keep the item exactly, just cleaner and higher-detail. Compare Qwen (edits the real "
        "sprite) against Flux at high and mid faithfulness. Watch the IoU: high on compact shapes, low on the thin sword.",
    "Restyle (themes)": "Reinvent the material/look while keeping the item. Flux-lock pins the outline via ControlNet; "
        "Qwen restyles the real sprite. Two silhouettes (helmet, body armor) &times; five themes.",
    "Restyle (gold, all items)": "The gold/gem 'legendary upgrade' restyle across every archetype (Flux-lock) — a "
        "generality check on how well the outline holds per shape.",
    "Reimagine (text)": "Fully prompt-driven (pure text-to-image). No anchor to the original — a fresh take on the item type.",
    "Faithfulness sweep (helm)": "The Flux dial on an easy shape: faithfulness 0 (text) &rarr; 0.9 (hug the original).",
    "Faithfulness sweep (sword)": "The same dial on the hard shape — note how faithfulness struggles to hold a thin blade.",
    "Shape-lock sweep (helm)": "Flux-lock ControlNet strength 0.3 &rarr; 0.9: higher pins the outline harder while the prompt restyles.",
    "Seed consistency (helm)": "Same settings, three seeds, per engine — how stable/predictable each is.",
}

DECISION_MATRIX = """
<section class="sec"><div class="sec-head"><h2>Which engine, when</h2><span class="tag">start here</span></div>
<p class="note">The short answer, by task. Details and evidence below.</p>
<table class="mtx"><tr><th>You want to&hellip;</th><th>Best engine</th><th>Setting</th><th>Why</th></tr>
<tr><td>Faithfully enhance the exact item</td><td><b>Qwen edit</b></td><td>default</td><td>Edits the real sprite — highest shape fidelity &amp; detail. Use Flux&nbsp;faith&nbsp;0.85 if you need it fast.</td></tr>
<tr><td>Keep the shape, change the look (reskin)</td><td><b>Flux + ControlNet (lock)</b></td><td>shape 0.6&ndash;0.8</td><td>Restyles material/colour freely while the silhouette stays put.</td></tr>
<tr><td>A faithful-ish clean-up, fast &amp; cheap</td><td><b>Flux — faithfulness</b></td><td>~0.4&ndash;0.65</td><td>~10&times; faster than Qwen; great on compact shapes.</td></tr>
<tr><td>A fresh reinterpretation of the item</td><td><b>Flux — faithfulness</b></td><td>0.0&ndash;0.15 (text)</td><td>Pure prompt; invent a new take on the item type.</td></tr>
<tr><td>Work a thin/complex silhouette (sword, staff)</td><td><b>Flux + ControlNet</b></td><td>shape 0.8&ndash;0.9</td><td>Faithful/edit engines lose thin shapes (low IoU); the outline hint is the only reliable hold.</td></tr>
</table></section>
"""

RECOMMENDATIONS = """
<section class="sec"><div class="sec-head"><h2>Recommended defaults &amp; gotchas</h2></div>
<table class="mtx"><tr><th>Item type</th><th>Default engine</th><th>Note</th></tr>
<tr><td>Helmets, boots, compact armor</td><td><b>Qwen</b> (quality) / <b>Flux 0.4</b> (speed)</td><td>Both hold the shape well (IoU high).</td></tr>
<tr><td>Gloves &amp; ambiguous icons</td><td><b>Flux 0.4</b> or <b>Flux-lock</b></td><td>Qwen can elaborate them (extra fingers/hands) — lock the outline if that happens.</td></tr>
<tr><td>Swords &amp; thin weapons</td><td><b>Flux-lock 0.8&ndash;0.9</b></td><td>Faithful engines can't hold a thin blade; the ControlNet outline is the fix.</td></tr>
<tr><td>Tiny items (rings, gems)</td><td><b>Qwen</b></td><td>So little source detail that a true edit reads best.</td></tr>
<tr><td>Reskins / set variants</td><td><b>Flux-lock</b></td><td>One outline, many looks (gold/fire/ice/bone/arcane) at fixed shape.</td></tr>
</table>
<p class="note" style="margin-top:12px"><b>Cost:</b> Flux ~6&ndash;15s warm, Qwen ~60&ndash;75s — budget accordingly for batch work.
<b>Always check the in-game thumbnail:</b> a rich 1024 render can muddy at 40&nbsp;px, which is what actually ships.</p>
</section>
"""

if __name__ == "__main__":
    main()
