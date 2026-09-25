"""Turn the model_shootout results into a single self-contained page (Artifact-ready).

Reads _shootout_out/manifest.json + the PNGs, base64-embeds everything (no external refs -> works
as a Claude Artifact and as a standalone file), and lays out a verdict panel + per-lane grids on a
dark-fantasy palette grounded in the subject (a Diablo II inventory item).

Output is BODY CONTENT ONLY (a <style> block + divs, no <!doctype>/<html>/<head>/<body>) so the
Artifact publisher can wrap it; it still renders fine opened directly in a browser.

    python scripts/build_shootout_report.py --sprite <original.png> --out report.html
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os

from PIL import Image

OUT = os.environ.get("SHOOTOUT_OUT") or os.path.join(os.path.dirname(__file__), "..", "_shootout_out")
OUT = os.path.abspath(OUT)

# ---- analysis (my read of the results; tie notes to specific cell indices) ----
VERDICT = [
    ("Why the skull appears",
     "The <b>From&nbsp;description</b> lane is text-to-image from pure noise, so it paints whatever the "
     "words say. The caption literally contains <i>&ldquo;Skull&nbsp;Cap&rdquo;</i>, <i>&ldquo;Diablo&rdquo;</i> "
     "and <i>&ldquo;several small holes&hellip; circular pattern&rdquo;</i> &mdash; which the model renders as a "
     "literal skull with eye-sockets. See <b>L1 &middot; default &middot; ref&nbsp;0.3</b>: a full skull."),
    ("Reference is the strongest lever",
     "With the <i>same</i> default prompt, pushing reference to <b>0.9</b> pins the silhouette and the skull "
     "disappears (<b>L1 &middot; default &middot; ref&nbsp;0.9</b>). The hard-negative alone at ref&nbsp;0.6 "
     "does <i>not</i> fully remove it &mdash; so raise reference before you reach for negatives."),
    ("The winner: Flux img2img",
     "Flux edits from the actual sprite and follows prose precisely, so it stays faithful to the rounded cap "
     "<i>and</i> is immune to the trap &mdash; even fed the raw &ldquo;Skull&nbsp;Cap&rdquo; caption it renders a "
     "clean studded helm, no face (<b>L3 &middot; default(trap)</b>). Crisp detail, ~6&nbsp;s warm."),
    ("Recommendation",
     "For &ldquo;true to the original&rdquo;, switch the faithful path to <b>Flux&nbsp;img2img&nbsp;@&nbsp;denoise&nbsp;0.5</b>. "
     "If you keep the SDXL description lane, default its reference to <b>~0.8&ndash;0.9</b> and strip "
     "<i>Skull&nbsp;Cap</i>/<i>Diablo</i> from the auto-caption before it&rsquo;s sent."),
]

LANE_NOTES = {
    "L1 SDXL txt2img+ref": ("Your current lane &mdash; text-to-image from noise, the original applied as a tile-ControlNet "
                            "&ldquo;reference&rdquo;. Watch the skull vanish as reference climbs 0.3&rarr;0.9, and note the cleaned "
                            "prompt is safe at any strength."),
    "L2 SDXL img2img": ("The faithful &ldquo;Upscale&rdquo; mechanism &mdash; starts from the real sprite, so it can&rsquo;t invent a "
                        "skull. But a 56&nbsp;px source has no detail to enhance, so results are shape-true yet soft."),
    "L3 Flux img2img": ("Flux, editing from the sprite. Faithful rounded shape, crisp studs, and no skull &mdash; including "
                        "the <i>default(trap)</i> cell fed the original skull-laden caption."),
}


def b64(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def thumb(png: bytes, box: int = 300) -> bytes:
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    im.thumbnail((box, box), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


def orig_upscaled(png: bytes, box: int = 300) -> bytes:
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    bb = im.split()[-1].getbbox()
    if bb:
        im = im.crop(bb)
    scale = max(1, box // max(im.size))
    im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


CSS = """
<style>
:root{
  --ground:#efe9df; --panel:#f6f1e8; --ink:#241d16; --muted:#6d6152; --line:#d8cdba;
  --brass:#9c7513; --rust:#a8391f; --verd:#4f6b3f; --shadow:0 1px 0 rgba(0,0,0,.04),0 8px 24px rgba(40,30,15,.06);
  --checker:#e3dccd;
}
@media (prefers-color-scheme:dark){
  :root{ --ground:#15120e; --panel:#1f1a15; --ink:#e9e0d1; --muted:#9a8d78; --line:#39312a;
    --brass:#d6a94b; --rust:#c9583b; --verd:#8aa86a; --shadow:0 1px 0 rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.35);
    --checker:#2a241d; }
}
:root[data-theme="light"]{ --ground:#efe9df; --panel:#f6f1e8; --ink:#241d16; --muted:#6d6152; --line:#d8cdba;
  --brass:#9c7513; --rust:#a8391f; --verd:#4f6b3f; --checker:#e3dccd; }
:root[data-theme="dark"]{ --ground:#15120e; --panel:#1f1a15; --ink:#e9e0d1; --muted:#9a8d78; --line:#39312a;
  --brass:#d6a94b; --rust:#c9583b; --verd:#8aa86a; --checker:#2a241d; }

*{box-sizing:border-box}
.rpt{background:var(--ground);color:var(--ink);
  font:16px/1.6 "Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  padding:48px 22px 96px;min-height:100vh}
.rpt .wrap{max-width:1180px;margin:0 auto}
.eyebrow{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:12px;letter-spacing:.22em;
  text-transform:uppercase;color:var(--brass);margin:0 0 10px}
h1{font-size:clamp(30px,5vw,46px);line-height:1.05;margin:0 0 12px;text-wrap:balance;font-weight:700}
.lede{font-size:18px;color:var(--muted);max-width:64ch;margin:0 0 36px}

.hero{display:flex;gap:26px;align-items:center;background:var(--panel);border:1px solid var(--line);
  border-radius:14px;padding:22px;margin:0 0 40px;box-shadow:var(--shadow);flex-wrap:wrap}
.hero .art{background-image:
  linear-gradient(45deg,var(--checker) 25%,transparent 25%),linear-gradient(-45deg,var(--checker) 25%,transparent 25%),
  linear-gradient(45deg,transparent 75%,var(--checker) 75%),linear-gradient(-45deg,transparent 75%,var(--checker) 75%);
  background-size:18px 18px;background-position:0 0,0 9px,9px -9px,-9px 0;border-radius:10px;
  image-rendering:pixelated;width:150px;height:150px;object-fit:contain;flex:none;border:1px solid var(--line)}
.hero h2{margin:0 0 6px;font-size:22px}
.hero p{margin:0;color:var(--muted);max-width:52ch}
.meta{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;color:var(--brass);margin-top:8px}

.verdict{display:grid;grid-template-columns:repeat(auto-fit,minmax(255px,1fr));gap:16px;margin:0 0 48px}
.vcard{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--brass);
  border-radius:10px;padding:16px 18px;box-shadow:var(--shadow)}
.vcard.win{border-left-color:var(--verd)} .vcard.warn{border-left-color:var(--rust)}
.vcard h3{margin:0 0 7px;font-size:16.5px}
.vcard p{margin:0;font-size:14.5px;color:var(--ink);line-height:1.5}
.vcard i{color:var(--muted)}

section{margin:0 0 44px}
.lane-head{display:flex;align-items:baseline;gap:14px;border-bottom:1px solid var(--line);padding-bottom:8px;margin:0 0 6px}
.lane-head h2{font-size:20px;margin:0}
.lane-note{color:var(--muted);font-size:14.5px;max-width:74ch;margin:0 0 18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:16px}
.grid.three{grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}
.shipped{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--verd);
  border-radius:12px;padding:20px 20px 24px;margin-bottom:44px}
.shipped .lane-head{border-bottom-color:var(--line)}
figure{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;box-shadow:var(--shadow)}
figure .imgwrap{background-image:
  linear-gradient(45deg,var(--checker) 25%,transparent 25%),linear-gradient(-45deg,var(--checker) 25%,transparent 25%),
  linear-gradient(45deg,transparent 75%,var(--checker) 75%),linear-gradient(-45deg,transparent 75%,var(--checker) 75%);
  background-size:16px 16px;background-position:0 0,0 8px,8px -8px,-8px 0}
figure img{display:block;width:100%;height:auto}
figcaption{padding:8px 11px 10px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;
  color:var(--ink);border-top:1px solid var(--line)}
figcaption .secs{color:var(--muted);font-variant-numeric:tabular-nums}
figure.err{border-color:var(--rust)} figure.err .x{padding:30px 12px;text-align:center;color:var(--rust);font-weight:700}
.tag{display:inline-block;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;letter-spacing:.04em;
  padding:2px 7px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.foot{color:var(--muted);font-size:13px;border-top:1px solid var(--line);padding-top:18px;margin-top:12px}
</style>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sprite", required=True)
    ap.add_argument("--out", default=os.path.join(OUT, "report.html"))
    a = ap.parse_args()

    manifest = json.load(open(os.path.join(OUT, "manifest.json")))
    orig = open(a.sprite, "rb").read()

    lanes: dict[str, list] = {}
    for c in manifest:
        lanes.setdefault(c["lane"], []).append(c)

    vcards = []
    for i, (title, body) in enumerate(VERDICT):
        cls = "win" if title.startswith("The winner") else ("warn" if "skull appears" in title else "")
        vcards.append(f'<div class="vcard {cls}"><h3>{title}</h3><p>{body}</p></div>')

    sections = []
    for lane, cells in lanes.items():
        note = LANE_NOTES.get(lane, "")
        items = []
        for c in cells:
            if c.get("ok"):
                img = b64(thumb(open(os.path.join(OUT, c["file"]), "rb").read()))
                items.append(f'<figure><div class="imgwrap"><img src="{img}" loading="lazy" alt="{c["label"]}">'
                             f'</div><figcaption>{c["label"]}<br><span class="secs">{c["secs"]}s</span></figcaption></figure>')
            else:
                items.append(f'<figure class="err"><div class="x">OOM / failed</div>'
                             f'<figcaption>{c["label"]}</figcaption></figure>')
        n = len([c for c in cells if c.get("ok")])
        sections.append(f'<section><div class="lane-head"><h2>{lane}</h2><span class="tag">{n} renders</span></div>'
                        f'<p class="lane-note">{note}</p><div class="grid">{"".join(items)}</div></section>')

    # Shipped-feature band: the single Faithfulness dial (Flux), rendered through the real app path.
    spectrum_dir = os.path.join(OUT, "..", "spectrum")
    SPECTRUM = [
        ("app_faith0.85_true.png", "Faithfulness 0.85", "hugs the original silhouette"),
        ("app_faith0.4_sweet.png", "Faithfulness 0.40 &middot; default", "faithful shape + crisp detail"),
        ("app_faith0.0_puretext.png", "Faithfulness 0.00", "pure text-to-image, reimagined"),
    ]
    spec_cards = []
    for fn, label, note in SPECTRUM:
        p = os.path.join(spectrum_dir, fn)
        if not os.path.exists(p):
            continue
        img = b64(thumb(open(p, "rb").read(), 340))
        spec_cards.append(f'<figure><div class="imgwrap"><img src="{img}" alt="{label}"></div>'
                          f'<figcaption>{label}<br><span class="secs">{note}</span></figcaption></figure>')
    spectrum_html = ("" if not spec_cards else
        '<section class="shipped"><div class="lane-head"><h2>Shipped &middot; the Faithfulness dial</h2>'
        '<span class="tag">Flux &middot; one slider</span></div>'
        '<p class="lane-note">The description lane now runs on Flux with a single <b>Faithfulness</b> knob: '
        'high hugs the original (img2img, low denoise), low reimagines it, bottom is pure text-to-image &mdash; '
        'and the item name is stripped from the caption so it can never draw a skull.</p>'
        f'<div class="grid three">{"".join(spec_cards)}</div></section>')

    # Second shipped band: the three description-lane engines.
    final_dir = os.path.join(OUT, "..", "final")
    ENGINES = [
        ("skull_flux_faithful.png", "Flux &middot; faithfulness dial", "one knob: true &rarr; reimagine"),
        ("skull_flux_lock_gold.png", "Flux &middot; lock outline", "restyle freely, silhouette locked"),
        ("skull_qwen_edit.png", "Qwen edit &middot; most faithful", "edits the real sprite"),
    ]
    eng_cards = []
    for fn, label, note in ENGINES:
        p = os.path.join(final_dir, fn)
        if not os.path.exists(p):
            continue
        img = b64(thumb(open(p, "rb").read(), 340))
        eng_cards.append(f'<figure><div class="imgwrap"><img src="{img}" alt="{label}"></div>'
                         f'<figcaption>{label}<br><span class="secs">{note}</span></figcaption></figure>')
    engines_html = ("" if not eng_cards else
        '<section class="shipped"><div class="lane-head"><h2>Shipped &middot; three engines, one lane</h2>'
        '<span class="tag">Flux &middot; Flux+ControlNet &middot; Qwen-Edit</span></div>'
        '<p class="lane-note">The description lane now offers three engines: <b>Flux</b> with the faithfulness dial; '
        '<b>Flux + ControlNet</b> to restyle the material/colour while a silhouette hint locks the exact outline; and '
        '<b>Qwen-Image-Edit</b> (Q4 GGUF, fits the 24&nbsp;GB card) which edits the actual sprite for the most faithful '
        'result. Each also exposes a Steps control.</p>'
        f'<div class="grid three">{"".join(eng_cards)}</div></section>')

    total = sum(1 for c in manifest if c.get("ok"))
    body = f"""{CSS}
<div class="rpt"><div class="wrap">
  <p class="eyebrow">Asset Studio &middot; model &amp; prompt shoot-out</p>
  <h1>Keeping the Skull Cap true to its art</h1>
  <p class="lede">Which model + prompt + faithfulness mechanism stops the &ldquo;From description&rdquo; lane from
  hallucinating a skull into a plain metal cap. One fixed seed across every cell, so each image differs only by the
  variable named beneath it.</p>

  <div class="hero">
    <img class="art" src="{b64(orig_upscaled(orig))}" alt="original Skull Cap sprite">
    <div>
      <h2>The target</h2>
      <p>The original in-game art &mdash; a plain rounded metal cap. Every render below is judged against this.</p>
      <div class="meta">base/armor/skp &middot; invskp &middot; 56&times;56 (2&times;2 cells) &middot; {total} renders &middot; seed 12345</div>
    </div>
  </div>

  <div class="verdict">{''.join(vcards)}</div>
  {engines_html}
  {spectrum_html}
  {''.join(sections)}
  <p class="foot">Renders on ComfyUI (RTX&nbsp;3090). SDXL&nbsp;=&nbsp;Juggernaut-X&nbsp;+&nbsp;DMD2&nbsp;4-step;
  Flux&nbsp;=&nbsp;flux.1&nbsp;schnell. Qwen-Image-Edit was installed but omitted &mdash; its 20B fp8 sampling peak
  exceeds 24&nbsp;GB VRAM on this card (needs a GGUF&nbsp;Q4 quant).</p>
</div></div>"""

    with open(a.out, "w", encoding="utf-8") as f:
        f.write(body)
    print("report ->", a.out, f"({len(body)//1024} KB)")


if __name__ == "__main__":
    main()
