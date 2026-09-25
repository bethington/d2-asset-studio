"""Visual-fidelity scoring: how close a generated/alternate item render is to the original.

Ported from scripts/fidelity_audit.py so production code (server.py's generate endpoint,
enhance_recipes.py) can score results without importing from scripts/. Used for two things:
the fidelity badge shown on every AI-upscale variant, and the auto-QA reject gate that blocks
empty/garbage generations before they're added to an item's history strip.

- iou      silhouette IoU (bbox-crop, 256px, threshold) -- shape fidelity
- dE_mean  mean Oklab distance between the two object-pixel color distributions' means
           (0 = identical average color; ~0.02 is a JND)
- emd      per-channel Oklab quantile EMD (sliced Wasserstein over L/a/b, averaged) --
           distribution-level color/tone match, robust to alignment
- ssim     grayscale SSIM on bbox-cropped, aspect-padded 96px renders over black --
           internal structure/shading
- score    composite in [0,1]: 0.35*iou + 0.35*color + 0.30*ssim, where
           color = max(0, 1 - emd/0.12)
"""

from __future__ import annotations

import numpy as np
from PIL import Image


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


def oklab_to_srgb(lab: np.ndarray) -> np.ndarray:
	"""Oklab (N,3) -> sRGB float [0,1] (N,3), clipped."""
	l_ = lab[:, 0] + 0.3963377774 * lab[:, 1] + 0.2158037573 * lab[:, 2]
	m_ = lab[:, 0] - 0.1055613458 * lab[:, 1] - 0.0638541728 * lab[:, 2]
	s_ = lab[:, 0] - 0.0894841775 * lab[:, 1] - 1.2914855480 * lab[:, 2]
	l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
	rgb = np.stack([
		+4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
		-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
		-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
	], axis=1)
	rgb = np.clip(rgb, 0.0, 1.0)
	return np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055)


def object_oklab(img: Image.Image) -> np.ndarray:
	"""Oklab colors of opaque object pixels, (N,3). Empty array when no object."""
	a = np.asarray(img)
	mask = a[:, :, 3] > 40
	if not mask.any():
		return np.empty((0, 3))
	return srgb_to_oklab(a[mask][:, :3].astype(np.float64) / 255.0)


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


def detail(img: Image.Image) -> float:
	"""Mean gradient magnitude inside the object -- how much internal detail the art carries.

	The one metric a colour-transfer recipe cannot fake. `iou` is inherited from the original's
	alpha guide and `color` is pinned by the transfer itself, so on m3/m7 both stay ~1.0 even when
	the model returned a bare silhouette; gradient energy collapses to ~0 in exactly that case.
	"""
	a = _norm_crop(img)
	gy, gx = np.gradient(a)
	mag = np.hypot(gx, gy)
	obj = _norm_crop_alpha(img) > 0.5
	return round(float(mag[obj].mean()) if obj.any() else 0.0, 4)


def _norm_crop_alpha(img: Image.Image, size: int = 96) -> np.ndarray:
	"""The object mask under the same bbox-crop/resize/pad as _norm_crop, in [0,1]."""
	a = img.split()[-1]
	bb = a.getbbox()
	im = img.crop(bb) if bb else img
	w, h = im.size
	sc = size / max(w, h, 1)
	im = im.resize((max(1, int(w * sc)), max(1, int(h * sc))), Image.LANCZOS)
	canvas = Image.new("L", (size, size), 0)
	canvas.paste(im.split()[-1], ((size - im.size[0]) // 2, (size - im.size[1]) // 2))
	return np.asarray(canvas).astype(np.float64) / 255.0


def score_pair(orig: Image.Image, alt: Image.Image) -> dict:
	i = iou(orig, alt)
	de, emd = color_metrics(orig, alt)
	ss = ssim_score(orig, alt)
	color = max(0.0, 1.0 - emd / 0.12)
	comp = 0.35 * i + 0.35 * color + 0.30 * ss
	d_o, d_a = detail(orig), detail(alt)
	return {"iou": i, "dE_mean": de, "emd": emd, "ssim": ss,
	        "color": round(color, 4), "score": round(comp, 4),
	        "detail": d_a, "detail_ratio": round(d_a / d_o, 4) if d_o > 1e-6 else 0.0}
