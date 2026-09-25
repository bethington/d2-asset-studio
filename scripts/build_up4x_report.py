"""Build the 4x gem-upscale comparison report (HTML with embedded images).

Reads _fidelity_out/up4x/manifest.json + per-gem PNGs and writes up4x_report.html
next to them. The HTML body is artifact-ready (no doctype/head wrapper).

    python scripts/build_up4x_report.py
"""

from __future__ import annotations

import base64
import json
import os
from collections import defaultdict

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_fidelity_out", "up4x"))

# display order + human labels; anything not listed lands at the end unlabelled
METHOD_INFO = [
	("nearest", "Nearest", "baseline", "Pure pixel duplication - the 'no upscaler' reference."),
	("bicubic", "Bicubic", "baseline", "Classic smooth interpolation."),
	("lanczos", "Lanczos", "baseline", "Sharpest classical filter."),
	("scale4x", "Scale4x (EPX)", "pixel-art", "Classic console-emulator edge-smoothing scaler."),
	("hq4x", "hq4x", "pixel-art", "Lookup-table pixel-art scaler, stronger edge blending."),
	("gan_4x-UltraSharp", "4x-UltraSharp", "GAN", "General-purpose community ESRGAN; already used in the studio pipeline."),
	("gan_4x_foolhardy_Remacri", "Remacri", "GAN", "Community favourite for painterly art; interpolation of several IRL models."),
	("gan_4x-AnimeSharp", "AnimeSharp", "GAN", "UltraSharp x TextSharp interpolation; strong on flat colour + linework."),
	("gan_4x_NMKD-Siax_200k", "NMKD Siax", "GAN", "Detail-oriented ESRGAN trained on photos+art."),
	("gan_RealESRGAN_x4plus_anime_6B", "RealESRGAN anime6B", "GAN", "Compact official Real-ESRGAN tuned for anime/cel art."),
	("hybrid_scale4x_remacri", "Scale4x -> Remacri", "hybrid", "Pixel-art pre-scale for geometry, GAN re-paint for texture."),
	("qwen_enhance", "Qwen-Image-Edit", "diffusion", "20B edit model re-paints the sprite at 1024px (identity-anchored prompt), fitted back to 4x."),
	("sdxl_tile", "SDXL tile", "diffusion", "Studio Lane A: GAN pre-scale + SDXL img2img pinned by tile ControlNet, fitted back to 4x."),
	("seedvr2", "SeedVR2 3B", "diffusion", "ByteDance one-step restoration transformer (2025 SOTA lane), native target-res output."),
]
FAMILY_COLORS = {"baseline": "#8a7c66", "pixel-art": "#7fa06a", "GAN": "#6a8fa0",
                 "hybrid": "#a08f6a", "diffusion": "#a06a8f"}

GEM_ORDER = ["invgsre", "invgsbe", "invgsge", "invgsve", "invgsye", "invgswe", "invskz"]


def b64(path: str) -> str:
	with open(path, "rb") as f:
		return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def main():
	with open(os.path.join(OUT, "manifest.json"), encoding="utf-8") as f:
		manifest = json.load(f)
	rows = [r for r in manifest["rows"] if "score" in r]
	by_gem = defaultdict(dict)
	names = {}
	for r in rows:
		key = r["file"].replace(".png", "")
		by_gem[r["inv"]][key] = r
		names[r["inv"]] = r.get("name", r["inv"])

	known = [m for m, *_ in METHOD_INFO]
	info = {m: (label, fam, desc) for m, label, fam, desc in METHOD_INFO}

	# method averages
	avg = {}
	for m in known:
		sc = [by_gem[g][m]["score"] for g in by_gem if m in by_gem[g]]
		ts = [by_gem[g][m]["secs"] for g in by_gem if m in by_gem[g]]
		if sc:
			avg[m] = (sum(sc) / len(sc), sum(ts) / len(ts), len(sc))

	css = """
<style>
:root{
  --bg:#171310; --panel:#211b16; --slot:#14100d; --line:#3a2f24;
  --ink:#e8ddc8; --muted:#9a8c74; --gold:#c8a15c; --gold-dim:#8a6a2f;
  --good:#7fa06a; --bad:#a06a5a;
}
:root[data-theme="light"]{
  --bg:#f2ead9; --panel:#faf5ea; --slot:#e6dcc6; --line:#cbbea3;
  --ink:#2b241a; --muted:#7a6c54; --gold:#8a6a2f; --gold-dim:#c8a15c;
}
@media (prefers-color-scheme: light){
  :root:not([data-theme="dark"]){
    --bg:#f2ead9; --panel:#faf5ea; --slot:#e6dcc6; --line:#cbbea3;
    --ink:#2b241a; --muted:#7a6c54; --gold:#8a6a2f; --gold-dim:#c8a15c;
  }
}
body{background:var(--bg);color:var(--ink);font:15px/1.55 "Segoe UI",system-ui,sans-serif;
     margin:0;padding:2.2rem 1.4rem 4rem;}
.wrap{max-width:1080px;margin:0 auto;}
h1,h2,h3{font-family:Palatino,"Palatino Linotype",Georgia,serif;font-weight:600;
  letter-spacing:.06em;text-wrap:balance;}
h1{font-size:1.9rem;margin:0 0 .2rem;color:var(--gold);}
h1 small{display:block;font:400 .58em/1.4 "Segoe UI",sans-serif;letter-spacing:.02em;color:var(--muted);}
h2{font-size:1.25rem;margin:2.6rem 0 .6rem;border-bottom:1px solid var(--line);padding-bottom:.35rem;}
h3{font-size:1.02rem;margin:1.6rem 0 .4rem;color:var(--gold);}
p{max-width:68ch;}
.eyebrow{font-size:.72rem;text-transform:uppercase;letter-spacing:.18em;color:var(--muted);margin:0 0 1rem;}
table{border-collapse:collapse;font-variant-numeric:tabular-nums;margin:.8rem 0;}
th,td{padding:.32rem .7rem;text-align:left;border-bottom:1px solid var(--line);font-size:.88rem;}
th{color:var(--muted);font-weight:600;text-transform:uppercase;font-size:.7rem;letter-spacing:.1em;}
td.num{text-align:right;font-family:Consolas,monospace;}
.fam{display:inline-block;font-size:.66rem;text-transform:uppercase;letter-spacing:.1em;
  padding:.1rem .45rem;border-radius:2px;color:#141008;font-weight:700;}
.gemrow{margin:1.2rem 0 2rem;}
.cells{display:flex;gap:.7rem;overflow-x:auto;padding:.6rem 2px .8rem;}
.cell{flex:0 0 auto;text-align:center;}
.cell .imgbox{background:
  repeating-conic-gradient(var(--slot) 0% 25%, var(--panel) 0% 50%) 0 0/16px 16px;
  border:1px solid var(--line);padding:6px;border-radius:3px;}
.cell img{display:block;width:224px;height:auto;image-rendering:pixelated;}
.cell.orig .imgbox{border-color:var(--gold-dim);}
.cell .lbl{font-size:.74rem;margin-top:.3rem;color:var(--ink);}
.cell .sub{font-size:.68rem;color:var(--muted);font-family:Consolas,monospace;}
.bar{height:3px;background:var(--line);border-radius:2px;margin-top:.25rem;overflow:hidden;}
.bar i{display:block;height:100%;background:var(--gold);}
.note{border-left:3px solid var(--gold-dim);background:var(--panel);padding:.7rem 1rem;
  margin:1rem 0;max-width:66ch;font-size:.92rem;}
.verdict{background:var(--panel);border:1px solid var(--gold-dim);border-radius:4px;
  padding:1rem 1.2rem;margin:1.2rem 0;}
code{font-family:Consolas,monospace;font-size:.86em;color:var(--gold);}
a{color:var(--gold);}
</style>"""

	html = [f"<title>Gem 4x Upscaler Shootout</title>{css}<div class='wrap'>"]
	html.append("<p class='eyebrow'>D2MOO asset studio - fidelity lab</p>")
	html.append("<h1>Gem 4x Upscaler Shootout<small>7 Perfect-tier gem sprites - every result at exactly 4x the "
	            "original DC6 frame, shown at 2x zoom (pixel-honest)</small></h1>")
	html.append("<div id='summary-slot'></div>")

	# per-gem grids
	html.append("<h2>Side-by-side results</h2>")
	html.append("<p>Every tile is the actual 4x output (112px for a 28px gem) rendered at 2x with no smoothing. "
	            "The bar under each tile is round-trip fidelity: the 4x image downscaled back to 1x and compared "
	            "against the MPQ original (IoU / Oklab EMD / SSIM composite). It measures faithfulness, not detail "
	            "quality - judge detail with your eyes, drift with the bar.</p>")
	for g in GEM_ORDER:
		if g not in by_gem:
			continue
		html.append(f"<div class='gemrow'><h3>{names[g]} <span style='color:var(--muted);font-size:.8em'>({g})</span></h3>")
		html.append("<div class='cells'>")
		orig = os.path.join(OUT, g, "original.png")
		if os.path.exists(orig):
			html.append(f"<div class='cell orig'><div class='imgbox'><img src='{b64(orig)}' alt='original'></div>"
			            "<div class='lbl'>Original</div><div class='sub'>MPQ, 1x</div></div>")
		for m in known:
			r = by_gem[g].get(m)
			if not r:
				continue
			p = os.path.join(OUT, g, r["file"])
			if not os.path.exists(p):
				continue
			label, fam, _ = info[m]
			pct = max(0.0, min(1.0, r["score"]))
			html.append(f"<div class='cell'><div class='imgbox'><img src='{b64(p)}' alt='{label}'></div>"
			            f"<div class='lbl'>{label}</div><div class='sub'>rt {r['score']:.3f} - {r['secs']:.1f}s</div>"
			            f"<div class='bar'><i style='width:{pct * 100:.0f}%'></i></div></div>")
		html.append("</div></div>")

	# method table
	html.append("<h2>Method roster & scores</h2><table><tr><th>Method</th><th>Family</th>"
	            "<th>Round-trip</th><th>Avg secs</th><th>n</th><th>What it is</th></tr>")
	for m in known:
		if m not in avg:
			continue
		label, fam, desc = info[m]
		a, t, n = avg[m]
		color = FAMILY_COLORS.get(fam, "#888")
		html.append(f"<tr><td>{label}</td><td><span class='fam' style='background:{color}'>{fam}</span></td>"
		            f"<td class='num'>{a:.3f}</td><td class='num'>{t:.1f}</td><td class='num'>{n}</td>"
		            f"<td style='color:var(--muted)'>{desc}</td></tr>")
	html.append("</table>")
	html.append("<div id='notes-slot'></div></div>")

	out = os.path.join(OUT, "up4x_report.html")
	with open(out, "w", encoding="utf-8") as f:
		f.write("\n".join(html))
	print("wrote", out, f"({os.path.getsize(out) // 1024}KB, {len(rows)} rows)")


if __name__ == "__main__":
	main()
