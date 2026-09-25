"""Turn a qwen_settings_sweep run into a self-contained HTML report for visual evaluation.

    python scripts/build_qwen_sweep_report.py --sweep <DIR> --out <report.html>

Every image is base64-embedded so the file is portable / publishable as an Artifact.
"""
from __future__ import annotations
import argparse, base64, io, json, os
from PIL import Image

FORGE = {}  # (styles are inline in TEMPLATE below)


def b64_png(data: bytes, max_side: int | None = None) -> str:
    if max_side:
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        if max(im.size) > max_side:
            s = max_side / max(im.size)
            im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        b = io.BytesIO(); im.save(b, "PNG"); data = b.getvalue()
    return "data:image/png;base64," + base64.b64encode(data).decode()


def load(sweep: str, fn: str) -> bytes:
    with open(os.path.join(sweep, fn), "rb") as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    m = json.load(open(os.path.join(args.sweep, "manifest.json")))

    variants = m["variants"]
    by = {(r["item"], r["variant"]): r for r in m["renders"]}
    # avg secs per variant
    avg = {}
    for v in variants:
        secs = [by[(it["id"], v["key"])].get("secs") for it in m["items"]
                if by.get((it["id"], v["key"]), {}).get("ok")]
        secs = [s for s in secs if s]
        avg[v["key"]] = round(sum(secs) / len(secs), 0) if secs else None

    # ---- settings legend rows
    legrows = []
    for v in variants:
        base = "yes" if v["key"] == "baseline" else "&mdash;"
        gan = "GAN 4&times;" if v["gan"] else "LANCZOS"
        quant = "Q8" if "Q8" in v["gguf"] else "Q6"
        legrows.append(
            f"<tr><td><b>{v['label']}</b></td><td class=num>{v['px']}</td><td>{gan}</td>"
            f"<td class=num>{quant}</td><td class=num>{v['steps']}</td>"
            f"<td class=num>{avg[v['key']] or '&mdash;'}</td>"
            f"<td>{'current live path' if v['key']=='baseline' else ''}</td></tr>")
    legend = "\n".join(legrows)

    # ---- per-item sections
    sections = []
    for it in m["items"]:
        src_b64 = b64_png(load(args.sweep, it["src"]), 96)
        cards = []
        for v in variants:
            r = by.get((it["id"], v["key"]))
            flag = "base" if v["key"] == "baseline" else ""
            if not r or not r.get("ok"):
                cards.append(
                    f'<figure class="card err"><figcaption>{v["label"]}</figcaption>'
                    f'<div class="x">render failed</div></figure>')
                continue
            master = b64_png(load(args.sweep, r["master"]), 340)
            tile = b64_png(load(args.sweep, r["canon"]))
            secs = r.get("secs", "")
            gan = "GAN 4&times;" if v["gan"] else "LANCZOS"
            quant = "Q8" if "Q8" in v["gguf"] else "Q6"
            cards.append(f"""<figure class="card {flag}">
  <div class="hero checker"><img src="{master}" alt="{v['label']} enhance of {it['name']}"></div>
  <figcaption>
    <div class="cap-top"><span class="vlabel">{v['label']}</span><span class="secs">{secs}s</span></div>
    <div class="params">{v['px']}px &middot; {gan} &middot; {quant} &middot; {v['steps']} steps</div>
    <div class="tilerow"><span class="tlbl">in&ndash;game tile</span><span class="tile checker"><img src="{tile}"></span></div>
  </figcaption>
</figure>""")
        sections.append(f"""<section class="item">
  <div class="ihead">
    <div class="src checker"><img src="{src_b64}"></div>
    <div><h2>{it['name']}</h2><div class="imeta">{it['iw']}&times;{it['ih']} cells &middot; source ~{it['native']}px</div></div>
  </div>
  <div class="cards">{''.join(cards)}</div>
</section>""")

    prompt = m.get("prompt", "")
    html = TEMPLATE.format(legend=legend, sections="\n".join(sections),
                           prompt=prompt, seed=m.get("seed", ""),
                           nitems=len(m["items"]), nvar=len(variants))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", args.out, os.path.getsize(args.out), "bytes")


TEMPLATE = r"""<title>Qwen-Edit settings sweep</title>
<style>
:root{{
  --ground:#e9e0cf; --panel:#f4eee0; --ink:#241d16; --muted:#6f6350; --line:#d3c6ae;
  --brass:#8a6712; --rust:#a8391f; --verd:#4f6b3f; --checker:#d9d0bd;
  --shadow:0 1px 0 rgba(0,0,0,.04),0 10px 30px rgba(50,38,18,.08);
}}
@media (prefers-color-scheme:dark){{
  :root{{ --ground:#141009; --panel:#1e1710; --ink:#ece2cf; --muted:#9a8c72; --line:#382d1e;
    --brass:#d6a94b; --rust:#d1583b; --verd:#8fae6a; --checker:#241d12;
    --shadow:0 1px 0 rgba(0,0,0,.3),0 12px 34px rgba(0,0,0,.4); }}
}}
:root[data-theme="light"]{{ --ground:#e9e0cf; --panel:#f4eee0; --ink:#241d16; --muted:#6f6350;
  --line:#d3c6ae; --brass:#8a6712; --rust:#a8391f; --verd:#4f6b3f; --checker:#d9d0bd; }}
:root[data-theme="dark"]{{ --ground:#141009; --panel:#1e1710; --ink:#ece2cf; --muted:#9a8c72;
  --line:#382d1e; --brass:#d6a94b; --rust:#d1583b; --verd:#8fae6a; --checker:#241d12; }}
*{{box-sizing:border-box}}
body{{margin:0}}
.rpt{{background:var(--ground);color:var(--ink);
  font:16px/1.65 "Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  padding:52px 22px 110px;}}
.wrap{{max-width:1280px;margin:0 auto}}
.eyebrow{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;letter-spacing:.24em;
  text-transform:uppercase;color:var(--brass);margin:0 0 12px}}
h1{{font-size:clamp(30px,4.6vw,44px);line-height:1.05;margin:0 0 14px;text-wrap:balance;font-weight:600}}
.lede{{font-size:18px;color:var(--muted);max-width:70ch;margin:0 0 20px}}
.readme{{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--brass);
  border-radius:10px;padding:14px 18px;margin:0 0 34px;font-size:14.5px;max-width:82ch;box-shadow:var(--shadow)}}
.readme b{{color:var(--brass)}}
h2{{font-size:22px;margin:0;font-weight:600}}
table.leg{{width:100%;border-collapse:collapse;margin:0 0 40px;font-size:13.5px}}
table.leg th,table.leg td{{border:1px solid var(--line);padding:8px 11px;text-align:left}}
table.leg th{{background:var(--panel);font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;
  letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}}
table.leg td.num,table.leg th.num{{text-align:right;font-variant-numeric:tabular-nums;
  font-family:ui-monospace,Menlo,Consolas,monospace}}
.item{{margin:0 0 50px}}
.ihead{{display:flex;align-items:center;gap:16px;border-bottom:1px solid var(--line);
  padding-bottom:12px;margin:0 0 20px}}
.imeta{{color:var(--muted);font-size:13px;font-family:ui-monospace,Menlo,Consolas,monospace;margin-top:3px}}
.src{{width:60px;height:60px;flex:0 0 60px;border:1px solid var(--line);border-radius:8px;
  display:flex;align-items:center;justify-content:center;overflow:hidden}}
.src img{{max-width:100%;max-height:100%;image-rendering:pixelated}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(216px,1fr));gap:18px}}
.card{{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:12px;
  overflow:hidden;box-shadow:var(--shadow)}}
.card.base{{border-color:var(--rust);border-width:1.5px}}
.checker{{background-image:
  linear-gradient(45deg,var(--checker) 25%,transparent 25%),
  linear-gradient(-45deg,var(--checker) 25%,transparent 25%),
  linear-gradient(45deg,transparent 75%,var(--checker) 75%),
  linear-gradient(-45deg,transparent 75%,var(--checker) 75%);
  background-size:16px 16px;background-position:0 0,0 8px,8px -8px,-8px 0}}
.hero{{display:flex;align-items:center;justify-content:center;padding:10px;min-height:210px}}
.hero img{{max-width:100%;max-height:300px;image-rendering:auto}}
figcaption{{padding:11px 13px 13px;border-top:1px solid var(--line)}}
.cap-top{{display:flex;justify-content:space-between;align-items:baseline;gap:8px}}
.vlabel{{font-weight:600;font-size:15px}}
.card.base .vlabel::after{{content:" · live";color:var(--rust);font-size:11px;
  font-family:ui-monospace,monospace;letter-spacing:.04em}}
.secs{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:var(--muted);
  font-variant-numeric:tabular-nums}}
.params{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;color:var(--muted);
  margin-top:5px;letter-spacing:.01em}}
.tilerow{{display:flex;align-items:center;gap:10px;margin-top:11px;padding-top:10px;border-top:1px dashed var(--line)}}
.tlbl{{font-family:ui-monospace,monospace;font-size:10.5px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--muted)}}
.tile{{width:52px;height:52px;flex:0 0 52px;border:1px solid var(--line);border-radius:5px;
  display:flex;align-items:center;justify-content:center;overflow:hidden}}
.tile img{{max-width:100%;max-height:100%;image-rendering:pixelated}}
.card.err{{border-color:var(--rust)}}
.card.err .x{{padding:60px 10px;text-align:center;color:var(--rust);font-weight:600}}
.foot{{color:var(--muted);font-size:13px;border-top:1px solid var(--line);padding-top:20px;margin-top:20px;
  max-width:90ch}}
.foot code{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:var(--ink);
  background:var(--panel);padding:2px 6px;border-radius:4px}}
</style>
<div class="rpt"><div class="wrap">
  <p class="eyebrow">asset-studio &middot; qwen-edit sweep</p>
  <h1>Which Qwen-Edit settings kill the blocky edge</h1>
  <p class="lede">{nitems} items &times; {nvar} settings, same seed and same enhance prompt. The blocky
  silhouette in the live output is the thing to beat &mdash; scan each row and pick the look you want.</p>
  <div class="readme">
    <b>How to read this.</b> Each large image is the full enhance render &mdash; judge the
    <b>silhouette edges</b> there (that's where the stair-stepping shows). The small square is the
    same result at true in&ndash;game cell size. The <b>Baseline (live)</b> card, outlined in rust, is
    exactly what the ✨ Enhance button does today. The hypothesis under test: feeding the sampler a
    <b>4&times;-UltraSharp GAN</b> pre-upscale (instead of a plain LANCZOS blow-up of the ~58px sprite)
    gives it crisp edges to preserve instead of terraced ones.
  </div>
  <table class="leg">
    <tr><th>Setting</th><th class=num>Res</th><th>Pre-upscale</th><th class=num>Quant</th>
        <th class=num>Steps</th><th class=num>~sec</th><th>Note</th></tr>
    {legend}
  </table>
  {sections}
  <div class="foot">
    <p>Fixed seed <code>{seed}</code>. Enhance prompt (house style, no item name):<br>
    <code>{prompt}</code></p>
    <p>Qwen-Image-Edit-2509 GGUF + 4-step Lightning LoRA, cfg 1.0, euler/simple, ModelSamplingAuraFlow
    shift 3.0, on the RTX&nbsp;3090. Alpha re-cut with rembg/BiRefNet.</p>
  </div>
</div></div>
"""


if __name__ == "__main__":
    main()
