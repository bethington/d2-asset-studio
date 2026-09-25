"""Build the visual-fidelity report: audit of existing alternates + method-experiment
results, as one self-contained HTML (base64-embedded images, dark theme).

Inputs:  _fidelity_out/audit.json, _fidelity_out/lab/manifest.json, lab PNGs.
Output:  _fidelity_out/fidelity_report.html

    python scripts/build_fidelity_report.py
"""

from __future__ import annotations

import base64
import html
import io
import json
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import assets  # noqa: E402
sys.path.insert(0, os.path.dirname(__file__))
import upscale4x_lab as u4  # noqa: E402 -- fit_master_to_4x for display fairness

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_fidelity_out"))
LAB = os.path.join(OUT, "lab")

METHOD_LABELS = {
	"m0_base": ("m0 Qwen-Edit baseline", "current production: Qwen-Image-Edit enhance, house style, no prompt"),
	"m1_anchor": ("m1 Qwen-Edit + identity anchor", "“the item is &lt;physical description&gt;” + house style"),
	"m2_catstyle": ("m2 Qwen-Edit + category style", "identity + gem/glow style (vivid, glossy, keep glow) + anti-hallucination negative"),
	"m3_anchor_ct": ("m3 = m1 + color transfer", "Oklab quantile histogram match to the original"),
	"m4_catstyle_ct": ("m4 = m2 + color transfer", ""),
	"m5_pad": ("m5 Qwen-Edit + margin pad", "baseline + forced transparent margin (full-cell sprites)"),
	"m6_combo": ("m6 Qwen-Edit combo", "pad + identity + category style"),
	"m7_combo_ct": ("m7 = m6 + color transfer", "the candidate production recipe (Qwen-Edit route)"),
	"m8_sdxl": ("m8 SDXL tile", "SDXL img2img, tile-ControlNet structure pin (cn 0.85, denoise 0.30), identity + category style"),
	"m9_sdxl_ct": ("m9 = m8 + color transfer", ""),
	"m11_bestof3": ("m11 Qwen-Edit best-of-3 seeds", "m7 recipe over 3 seeds, auto-keep the best score (production auto-QA shape)"),
	"u_lanczos": ("u· lanczos", "classical filter (premultiplied-alpha), the do-nothing baseline of the upscaler family"),
	"u_scale4x": ("u· scale4x", "EPX pixel-art scaler — deliberate retro look"),
	"u_hq4x": ("u· hq4x", "hqx pixel-art scaler"),
	"u_ultrasharp": ("u· 4x-UltraSharp", "painterly GAN — invents facet detail, keeps silhouette+palette"),
	"u_remacri": ("u· Remacri", "painterly GAN"),
	"u_animesharp": ("u· AnimeSharp", "GAN, cleaner flatter planes"),
	"u_siax": ("u· NMKD-Siax", "GAN"),
	"u_rleanime": ("u· RealESRGAN-anime", "GAN, 6B anime variant"),
	"u_hybrid": ("u· scale4x→Remacri", "community hybrid: pixel-art 4x for geometry, GAN re-paint for texture"),
	"u_seedvr2": ("u· SeedVR2 3B", "ByteDance one-step restoration transformer"),
}
METHOD_ORDER = list(METHOD_LABELS)
# compact per-tile/column captions that name the ENGINE (every m0-m7 run is Qwen-Image-Edit)
METHOD_SHORT = {
	"m0_base": "m0 Qwen·no-prompt", "m1_anchor": "m1 Qwen·anchor", "m2_catstyle": "m2 Qwen·catstyle",
	"m3_anchor_ct": "m3 Qwen·anchor+CT", "m4_catstyle_ct": "m4 Qwen·cat+CT", "m5_pad": "m5 Qwen·pad",
	"m6_combo": "m6 Qwen·combo", "m7_combo_ct": "m7 Qwen·combo+CT", "m8_sdxl": "m8 SDXL-tile",
	"m9_sdxl_ct": "m9 SDXL+CT", "m11_bestof3": "m11 Qwen·best-of-3",
}


def short(m: str) -> str:
	return METHOD_SHORT.get(m, METHOD_LABELS[m][0])
# hi-res master detail strip: the enhancement family + the two diffusion recipes
MASTER_STRIP = ["u_lanczos", "u_scale4x", "u_hq4x", "u_ultrasharp", "u_remacri",
                "u_animesharp", "u_siax", "u_rleanime", "u_hybrid", "u_seedvr2",
                "m8_sdxl", "m7_combo_ct"]


def b64(png_bytes: bytes) -> str:
	return base64.b64encode(png_bytes).decode()


def img_tag(png_bytes: bytes, zoom: int = 4, title: str = "", hover: str = "") -> str:
	im = Image.open(io.BytesIO(png_bytes))
	w, h = im.size[0] * zoom, im.size[1] * zoom
	hv = f' data-zoom="{hover}"' if hover else ""
	return (f'<img src="data:image/png;base64,{b64(png_bytes)}" width="{w}" height="{h}" '
	        f'title="{html.escape(title)}" style="image-rendering:pixelated"{hv}>')


def hover_uri(png_bytes: bytes, cap: int = 512) -> str:
	"""Hi-res hover payload: LANCZOS-capped to `cap`, WebP when Pillow supports it."""
	im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
	sc = min(1.0, cap / max(im.size))
	if sc < 1.0:
		im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))), Image.LANCZOS)
	buf = io.BytesIO()
	try:
		im.save(buf, "WEBP", quality=88)
		mime = "webp"
	except Exception:  # noqa: BLE001 -- Pillow without libwebp
		buf = io.BytesIO()
		im.save(buf, "PNG")
		mime = "png"
	return f"data:image/{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"


def nearest_uri(png_bytes: bytes, cap: int = 464) -> str:
	"""Pixel-honest hover payload for cell-scale art: integer NEAREST upscale."""
	im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
	sc = max(1, cap // max(im.size))
	im = im.resize((im.width * sc, im.height * sc), Image.NEAREST)
	buf = io.BytesIO()
	im.save(buf, "PNG")
	return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"


def _thumb(png_bytes: bytes, height: int, nearest: bool = True) -> bytes:
	"""Scale an image to the given display height (up via NEAREST for pixel art, down via
	LANCZOS for hi-res masters) so every master-strip tile shares one visual scale."""
	im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
	w = max(1, round(im.size[0] * height / im.size[1]))
	rs = Image.NEAREST if (nearest and height >= im.size[1]) else Image.LANCZOS
	im = im.resize((w, height), rs)
	buf = io.BytesIO()
	im.save(buf, "PNG")
	return buf.getvalue()


def refit4x(master_png: bytes, oim: Image.Image) -> bytes:
	"""Place a ~1024 diffusion master onto the 4x-frame canvas at the original's content
	position — like u4.fit_master_to_4x, but ASPECT-PRESERVING (fit within the original's
	bbox, centered) so hover/master tiles never show a stretched image."""
	m = Image.open(io.BytesIO(master_png)).convert("RGBA")
	bb_m = m.split()[-1].getbbox()
	if bb_m:
		m = m.crop(bb_m)
	tw, th = oim.width * 4, oim.height * 4
	bb_o = oim.split()[-1].getbbox() or (0, 0, oim.width, oim.height)
	cw, ch = (bb_o[2] - bb_o[0]) * 4, (bb_o[3] - bb_o[1]) * 4
	sc = min(cw / m.width, ch / m.height)
	nw, nh = max(1, round(m.width * sc)), max(1, round(m.height * sc))
	m = u4.premult_resize(m, (nw, nh), Image.LANCZOS)
	canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
	canvas.alpha_composite(m, (bb_o[0] * 4 + (cw - nw) // 2, bb_o[1] * 4 + (ch - nh) // 2))
	buf = io.BytesIO()
	canvas.save(buf, "PNG")
	return buf.getvalue()


def file_png(path: str) -> bytes | None:
	if os.path.exists(path):
		with open(path, "rb") as f:
			return f.read()
	return None


def score_cell(r: dict) -> str:
	if not r:
		return "<td class=na>—</td>"
	if "error" in r:
		return f'<td class=bad title="{html.escape(r["error"])}">ERR</td>'
	s = r["score"]
	cls = "good" if s >= 0.72 else "ok" if s >= 0.55 else "bad"
	return (f'<td class={cls}>{s:.3f}<div class=sub>iou {r["iou"]:.2f} · '
	        f'emd {r["emd"]:.3f} · ssim {r["ssim"]:.2f}</div></td>')


def build(verdict_html: str = "") -> str:
	with open(os.path.join(OUT, "audit.json"), encoding="utf-8") as f:
		audit = json.load(f)
	lab_manifest_path = os.path.join(LAB, "manifest.json")
	lab_rows = []
	if os.path.exists(lab_manifest_path):
		with open(lab_manifest_path, encoding="utf-8") as f:
			lab_rows = json.load(f)["rows"]
	by = {(r["inv"], r["method"]): r for r in lab_rows}
	invs = sorted({r["inv"] for r in lab_rows},
	              key=lambda i: (by.get((i, "m0_base"), {}).get("cat", ""), i))

	parts = ["""<!-- built by build_fidelity_report.py -->
<meta charset="utf-8"><title>Alternate-art visual fidelity report</title>
<style>
 body{background:#17171d;color:#d8d4c8;font:14px/1.5 'Segoe UI',sans-serif;margin:24px auto;padding:0 28px}
 h1,h2,h3{color:#e8d9a0;font-weight:600} h1{font-size:22px} h2{border-bottom:1px solid #3a3a44;padding-bottom:4px;margin-top:36px}
 table{border-collapse:collapse;margin:12px 0} td,th{border:1px solid #33333c;padding:5px 9px;text-align:left;vertical-align:top}
 th{background:#22222a;color:#cfc59a} td.good{background:#1d3020;color:#9fe0a0} td.ok{background:#2e2a1a;color:#e0d090}
 td.bad{background:#33191b;color:#e09090} td.na{color:#666} .sub{font-size:10px;color:#999;margin-top:2px}
 .strip{background:#282830;padding:10px;border-radius:6px;margin:8px 0;overflow-x:auto;white-space:nowrap}
 .strip figure{display:inline-block;vertical-align:top;margin:0 10px 4px 0;padding:7px 7px 3px;text-align:center;
   background:#1e1e25;border:1px solid #3a3a44;border-radius:5px}
 .strip figcaption{font-size:11px;color:#aaa;margin-top:4px}
 img[data-zoom]{cursor:zoom-in}
 #zv{display:none;position:fixed;z-index:99;pointer-events:none;background:#0c0c10;border:1px solid #666;
   padding:6px;border-radius:6px;box-shadow:0 4px 24px #000c}
 #zv img{display:block;max-width:520px;max-height:520px}
 .verdict{background:#20241d;border:1px solid #3d4433;border-radius:6px;padding:12px 16px}
 .tablewrap{overflow-x:auto}
 code{background:#26262e;padding:1px 5px;border-radius:3px;font-size:12.5px}
 .small{font-size:12px;color:#9a968c}
</style>
<h1>Alternate artwork vs original — visual fidelity report</h1>"""]

	if verdict_html:
		parts.append(f'<div class=verdict>{verdict_html}</div>')

	# ---- audit section ----
	worst = audit["best_per_bucket"][:20]
	parts.append("<h2>1 · Audit of accepted alternates (all 603 buckets)</h2>")
	parts.append(f"<p>{len(audit['rows'])} accepted alternates scored against their MPQ originals "
	             "(composite = 0.35·shape-IoU + 0.35·color + 0.30·SSIM). "
	             "Worst 20 buckets by their <i>best</i> alternate:</p>")
	parts.append("<table><tr><th>score</th><th>bucket</th><th>item(s)</th><th>iou</th>"
	             "<th>color emd</th><th>ssim</th></tr>")
	for r in worst:
		nm = html.escape(", ".join(r["items"][:2]) or "?")
		cls = "bad" if r["score"] < 0.55 else "ok"
		parts.append(f"<tr><td class={cls}>{r['score']:.3f}</td><td><code>{r['invfile']}</code></td>"
		             f"<td>{nm}</td><td>{r['iou']:.2f}</td><td>{r['emd']:.3f}</td>"
		             f"<td>{r['ssim']:.2f}</td></tr>")
	parts.append("</table>")
	parts.append("<h3>What the worst look like (original | accepted alternates)</h3>")
	for r in worst[:8]:
		png = file_png(os.path.join(OUT, "img", f"{r['invfile']}.png"))
		if png:
			nm = html.escape(", ".join(r["items"][:2]) or r["invfile"])
			parts.append(f'<div class=strip><figure>{img_tag(png, zoom=1)}'
			             f'<figcaption>{nm} ({r["invfile"]})</figcaption></figure></div>')

	# ---- method matrix ----
	if lab_rows:
		parts.append("<h2>2 · Method experiments (12-item test set)</h2>")
		parts.append("<table><tr><th>method</th><th>what it is</th><th>mean all</th>"
		             "<th>mean gems+glow</th><th>mean controls</th></tr>")
		for m in METHOD_ORDER:
			sub = [r for r in lab_rows if r["method"] == m and "score" in r]
			if not sub:
				continue
			gem = [r["score"] for r in sub if r.get("cat") in ("gem", "glow")]
			ctl = [r["score"] for r in sub if r.get("cat") == "control"]
			lbl, desc = METHOD_LABELS[m]
			mean = sum(r["score"] for r in sub) / len(sub)
			gm = sum(gem) / len(gem) if gem else None
			cm = sum(ctl) / len(ctl) if ctl else None
			parts.append(f"<tr><td><b>{lbl}</b></td><td class=small>{desc}</td>"
			             f"<td>{mean:.3f}</td><td>{gm:.3f}</td><td>{cm:.3f}</td></tr>"
			             if gm is not None and cm is not None else
			             f"<tr><td><b>{lbl}</b></td><td class=small>{desc}</td>"
			             f"<td>{mean:.3f}</td><td>—</td><td>—</td></tr>")
		parts.append("</table>")

		parts.append("<h3>Per-item results (end-of-pipe: fitted → palette-quantized → DC6 render)</h3>")
		parts.append("<p class=small>The u· upscaler family <i>enhances</i> the existing art rather than "
		             "re-imagining it, and re-cuts the original silhouette back on — so its end-of-pipe "
		             "scores are a near-round-trip and read as the fidelity ceiling. Judge its detail "
		             "quality in the hi-res master strips below each item.</p>")
		parts.append('<div class=tablewrap><table><tr><th>item</th>' +
		             "".join(f"<th>{short(m)}</th>" for m in METHOD_ORDER) + "</tr>")
		for inv in invs:
			cat = by.get((inv, "m0_base"), {}).get("cat", "")
			parts.append(f"<tr><td><code>{inv}</code><div class=sub>{cat}</div></td>" +
			             "".join(score_cell(by.get((inv, m))) for m in METHOD_ORDER) + "</tr>")
		parts.append("</table></div>")

		for inv in invs:
			try:
				orig = assets.dc6_to_png_bytes(assets.read_original_dc6(inv))
			except Exception:  # noqa: BLE001
				continue
			oim = Image.open(io.BytesIO(orig))

			# masters: raw = native resolution (diffusion ~1024px) for hover zoom;
			# refit = diffusion refit onto the u*-family 4x frame for equal-res strip tiles
			refit: dict[str, bytes] = {}
			raw: dict[str, bytes] = {}
			for m in METHOD_ORDER:
				png = file_png(os.path.join(LAB, inv, f"{m}.master.png"))
				if not png:
					continue
				raw[m] = png
				if not m.startswith("u_"):
					png = refit4x(png, oim.convert("RGBA"))
				refit[m] = png

			def hover_for(m: str) -> str:
				"""Max-resolution hover: raw 1024 master for diffusion; the 4x master IS the
				max for u* methods, so integer-NEAREST it up to viewing size."""
				if m.startswith("u_"):
					return nearest_uri(refit[m])
				return hover_uri(raw[m], cap=520)

			figs = [f"<figure>{img_tag(orig, hover=nearest_uri(orig))}"
			        f"<figcaption>ORIGINAL</figcaption></figure>"]
			for m in METHOD_ORDER:
				png = file_png(os.path.join(LAB, inv, f"{m}.fitted.png"))
				if png:
					r = by.get((inv, m)) or {}
					sc = f" · {r['score']:.3f}" if "score" in r else ""
					hv = hover_for(m) if m in refit else nearest_uri(png)
					figs.append(f"<figure>{img_tag(png, hover=hv)}"
					            f"<figcaption>{short(m)}{sc}</figcaption></figure>")
			parts.append(f"<h3><code>{inv}</code></h3><div class=strip>{''.join(figs)}</div>")

			# hi-res master detail strip: where the u· family's actual quality lives
			th = min(176, oim.size[1] * 4)
			mfigs = [f"<figure>{img_tag(_thumb(orig, th), zoom=1, hover=nearest_uri(orig))}"
			         f"<figcaption>ORIG (nearest)</figcaption></figure>"]
			for m in MASTER_STRIP:
				if m not in refit:
					continue
				mfigs.append(f"<figure>{img_tag(_thumb(refit[m], th, nearest=False), zoom=1, hover=hover_for(m))}"
				             f"<figcaption>{short(m)}</figcaption></figure>")
			if len(mfigs) > 1:
				parts.append(f"<div class=strip>{''.join(mfigs)}</div>")

	parts.append("""<div id=zv><img id=zvi alt=""></div>
<script>
const zv=document.getElementById('zv'), zvi=document.getElementById('zvi');
document.addEventListener('mouseover',e=>{
  const t=e.target.closest('img[data-zoom]'); if(!t) return;
  zvi.src=t.dataset.zoom; zv.style.display='block';
});
document.addEventListener('mouseout',e=>{
  if(e.target.closest('img[data-zoom]')) zv.style.display='none';
});
document.addEventListener('mousemove',e=>{
  if(zv.style.display==='none') return;
  const m=18, r=zv.getBoundingClientRect();
  let x=e.clientX+m, y=e.clientY+m;
  if(x+r.width>innerWidth-8)  x=Math.max(8, e.clientX-r.width-m);
  if(y+r.height>innerHeight-8) y=Math.max(8, innerHeight-r.height-8);
  zv.style.left=x+'px'; zv.style.top=y+'px';
});
</script>""")
	return "\n".join(parts)


def main():
	verdict = ""
	vp = os.path.join(OUT, "verdict.html")
	if os.path.exists(vp):
		with open(vp, encoding="utf-8") as f:
			verdict = f.read()
	html_out = build(verdict)
	p = os.path.join(OUT, "fidelity_report.html")
	with open(p, "w", encoding="utf-8") as f:
		f.write(html_out)
	print(p)


if __name__ == "__main__":
	main()
