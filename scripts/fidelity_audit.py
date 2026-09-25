"""Fidelity audit: score every accepted alternate DC6 against its MPQ original.

For each bucket in <workspace>\alternates\dc6\<invfile>\ this renders the original
(live from the MPQs, ACT1 palette) and each alternate to RGBA, then scores:

- iou      silhouette IoU (bbox-crop, 256px, threshold) -- shape fidelity
- dE_mean  mean Oklab distance between the two object-pixel color distributions'
           means (0 = identical average color; ~0.02 is a JND)
- emd      per-channel Oklab quantile EMD (sliced Wasserstein over L/a/b, averaged)
           -- distribution-level color/tone match, robust to alignment
- ssim     grayscale SSIM on bbox-cropped, aspect-padded 96px renders over black
           -- internal structure/shading
- score    composite in [0,1]: 0.35*iou + 0.35*color + 0.30*ssim, where
           color = max(0, 1 - emd/0.12)

Output: _fidelity_out/audit.json (one row per alternate, plus per-bucket summary).

    python scripts/fidelity_audit.py            # full audit
    python scripts/fidelity_audit.py inv9cru    # just the named bucket(s)
"""

from __future__ import annotations

import io
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import assets  # noqa: E402
import catalog  # noqa: E402

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_fidelity_out"))
ALT_DC6 = os.path.join(assets.WORKSPACE, "alternates", "dc6")


# ---- rendering -------------------------------------------------------------

def render_dc6_rgba(dc6_bytes: bytes) -> Image.Image:
	png = assets.dc6_to_png_bytes(dc6_bytes)
	return Image.open(io.BytesIO(png)).convert("RGBA")


# ---- color space -----------------------------------------------------------

def srgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
	"""rgb float [0,1] shape (N,3) -> Oklab (N,3)."""
	c = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
	l = 0.4122214708 * c[:, 0] + 0.5363325363 * c[:, 1] + 0.0514459929 * c[:, 2]
	m = 0.2119034982 * c[:, 0] + 0.6806995451 * c[:, 1] + 0.1073969566 * c[:, 2]
	s = 0.0883024619 * c[:, 0] + 0.2817188376 * c[:, 1] + 0.6299787005 * c[:, 2]
	l_, m_, s_ = np.cbrt(l), np.cbrt(m), np.cbrt(s)
	return np.stack([
		0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
		1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
		0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
	], axis=1)


def object_oklab(img: Image.Image) -> np.ndarray:
	"""Oklab colors of opaque object pixels, (N,3). Empty array when no object."""
	a = np.asarray(img)
	mask = a[:, :, 3] > 40
	if not mask.any():
		return np.empty((0, 3))
	return srgb_to_oklab(a[mask][:, :3].astype(np.float64) / 255.0)


# ---- metrics ---------------------------------------------------------------

def sil_mask(img: Image.Image, size: int = 256) -> np.ndarray:
	a = img.split()[-1]
	bb = a.getbbox()
	if bb:
		a = a.crop(bb)
	a = a.resize((size, size), Image.LANCZOS).point(lambda v: 255 if v > 40 else 0)
	return np.asarray(a) > 0


def iou(orig: Image.Image, alt: Image.Image) -> float:
	o, g = sil_mask(orig), sil_mask(alt)
	u = (o | g).sum()
	return round(float((o & g).sum()) / float(u), 4) if u else 0.0


def color_metrics(orig: Image.Image, alt: Image.Image) -> tuple[float, float]:
	"""(dE_mean, emd): Oklab mean-color distance + per-channel quantile EMD."""
	po, pa = object_oklab(orig), object_oklab(alt)
	if not len(po) or not len(pa):
		return 1.0, 1.0
	de = float(np.linalg.norm(po.mean(axis=0) - pa.mean(axis=0)))
	qs = np.linspace(0.01, 0.99, 64)
	emd = float(np.mean([
		np.abs(np.quantile(po[:, i], qs) - np.quantile(pa[:, i], qs)).mean()
		for i in range(3)
	]))
	return round(de, 4), round(emd, 4)


def _norm_crop(img: Image.Image, size: int = 96) -> np.ndarray:
	"""bbox-crop, aspect-preserving resize into size x size over black, grayscale."""
	a = img.split()[-1]
	bb = a.getbbox()
	im = img.crop(bb) if bb else img
	w, h = im.size
	sc = size / max(w, h, 1)
	im = im.resize((max(1, int(w * sc)), max(1, int(h * sc))), Image.LANCZOS)
	canvas = Image.new("RGBA", (size, size), (0, 0, 0, 255))
	canvas.paste(im, ((size - im.size[0]) // 2, (size - im.size[1]) // 2), im)
	return np.asarray(canvas.convert("L")).astype(np.float64)


def ssim_score(orig: Image.Image, alt: Image.Image) -> float:
	from skimage.metrics import structural_similarity
	o, g = _norm_crop(orig), _norm_crop(alt)
	return round(float(structural_similarity(o, g, data_range=255.0)), 4)


def score_pair(orig: Image.Image, alt: Image.Image) -> dict:
	i = iou(orig, alt)
	de, emd = color_metrics(orig, alt)
	ss = ssim_score(orig, alt)
	color = max(0.0, 1.0 - emd / 0.12)
	comp = 0.35 * i + 0.35 * color + 0.30 * ss
	return {"iou": i, "dE_mean": de, "emd": emd, "ssim": ss,
	        "color": round(color, 4), "score": round(comp, 4)}


# ---- audit -----------------------------------------------------------------

def invfile_names() -> dict:
	"""invfile -> up to 3 item names that draw it (for readable reports)."""
	names: dict[str, list] = {}
	try:
		items, _ = catalog.build_catalog()
	except Exception as e:  # noqa: BLE001 -- MPQ layer down: names are optional
		print(f"  (catalog unavailable: {e})")
		return {}
	for it in items:
		names.setdefault(it["invfile"], [])
		if len(names[it["invfile"]]) < 3 and it["name"] not in names[it["invfile"]]:
			names[it["invfile"]].append(it["name"])
	return names


def audit(only: list[str] | None = None) -> dict:
	names = invfile_names()
	rows, missing = [], []
	buckets = sorted(os.listdir(ALT_DC6))
	if only:
		buckets = [b for b in buckets if b in only]
	for bi, invfile in enumerate(buckets):
		bdir = os.path.join(ALT_DC6, invfile)
		if not os.path.isdir(bdir):
			continue
		orig_bytes = assets.try_read_effective(assets.item_dc6_path(invfile))
		if orig_bytes is None:
			missing.append(invfile)
			continue
		try:
			orig = render_dc6_rgba(orig_bytes)
		except Exception as e:  # noqa: BLE001
			missing.append(f"{invfile} (decode: {e})")
			continue
		for fn in sorted(os.listdir(bdir)):
			if not fn.endswith(".dc6"):
				continue
			alt_id = fn[:-4]
			try:
				with open(os.path.join(bdir, fn), "rb") as f:
					alt = render_dc6_rgba(f.read())
				m = score_pair(orig, alt)
			except Exception as e:  # noqa: BLE001
				m = {"error": str(e)}
			rows.append({"invfile": invfile, "alt": alt_id,
			             "items": names.get(invfile, []), **m})
		if bi % 50 == 0:
			print(f"  {bi}/{len(buckets)} buckets...")

	scored = [r for r in rows if "score" in r]
	best = {}
	for r in scored:
		if r["invfile"] not in best or r["score"] > best[r["invfile"]]["score"]:
			best[r["invfile"]] = r
	return {"rows": rows,
	        "best_per_bucket": sorted(best.values(), key=lambda r: r["score"]),
	        "no_original": missing}


def main():
	only = [a for a in sys.argv[1:] if not a.startswith("-")] or None
	os.makedirs(OUT, exist_ok=True)
	result = audit(only)
	out = os.path.join(OUT, "audit.json")
	with open(out, "w", encoding="utf-8") as f:
		json.dump(result, f, indent=1)
	scored = result["best_per_bucket"]
	print(f"\n{len(result['rows'])} alternates scored across {len(scored)} buckets; "
	      f"{len(result['no_original'])} buckets have no MPQ original.")
	print(f"-> {out}\n\nWorst 25 buckets (best alternate each):")
	for r in scored[:25]:
		nm = ", ".join(r["items"][:2]) or "?"
		print(f"  {r['score']:.3f}  iou={r['iou']:.2f} emd={r['emd']:.3f} "
		      f"ssim={r['ssim']:.2f}  {r['invfile']:<12} [{nm}] {r['alt']}")


if __name__ == "__main__":
	main()
