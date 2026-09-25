"""Auto-split a two-boot sprite into left/right single boots (UPSCALE_3D_PANEL_DESIGN §4, D).

Boots ship as one artwork containing both boots, usually with clear space between them
(unlike gloves, which overlap into one alpha blob). Two strategies, tried in order:

  components — 8-connected components of the alpha plane, clustered into two groups at the
               largest gap between component centroid x's. High confidence when the groups'
               x-extents barely overlap.
  gap        — fallback when the alpha is one merged component: split at the most transparent
               column "valley" in the middle half of the sprite.

Labeling runs on a ≤256px copy of the alpha (BFS; sprites are small), the resulting group
masks are scaled back up and applied to the full-res image, so the split works identically on
56px originals and 1024px upscale masters.
"""

from __future__ import annotations

import io
from collections import deque

from PIL import Image

MAX_LABEL_SIDE = 256
MIN_COMPONENT_FRAC = 0.02   # ignore specks under 2% of the foreground
ALPHA_THRESHOLD = 16


def _components(mask, w, h):
	"""8-connected components of a flat 0/1 list. Returns list of (pixels:set, cx, xmin, xmax)."""
	seen = [False] * (w * h)
	comps = []
	for start in range(w * h):
		if not mask[start] or seen[start]:
			continue
		q = deque([start])
		seen[start] = True
		pixels = []
		while q:
			i = q.popleft()
			pixels.append(i)
			x, y = i % w, i // w
			for dy in (-1, 0, 1):
				for dx in (-1, 0, 1):
					nx, ny = x + dx, y + dy
					if 0 <= nx < w and 0 <= ny < h:
						j = ny * w + nx
						if mask[j] and not seen[j]:
							seen[j] = True
							q.append(j)
		xs = [i % w for i in pixels]
		comps.append((pixels, sum(xs) / len(xs), min(xs), max(xs)))
	return comps


def split(png_bytes: bytes) -> dict:
	"""Split a two-boot sprite. Returns {ok, method, confidence, left, right} where left/right
	are PNG bytes (full input resolution, cropped to each boot's content + small margin), or
	{ok: False, error} when no sane split exists."""
	im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
	W, H = im.size
	scale = min(1.0, MAX_LABEL_SIDE / max(W, H))
	lw, lh = max(1, int(W * scale)), max(1, int(H * scale))
	small = im.split()[-1].resize((lw, lh), Image.BILINEAR)
	mask = [1 if p > ALPHA_THRESHOLD else 0 for p in small.getdata()]
	fg = sum(mask)
	if not fg:
		return {"ok": False, "error": "empty alpha"}

	comps = [c for c in _components(mask, lw, lh) if len(c[0]) >= fg * MIN_COMPONENT_FRAC]
	method = None
	groups = None  # (left_pixelset, right_pixelset) in label space

	if len(comps) >= 2:
		comps.sort(key=lambda c: c[1])
		# split the centroid-ordered components at the largest x jump
		gaps = [comps[i + 1][1] - comps[i][1] for i in range(len(comps) - 1)]
		cut = gaps.index(max(gaps)) + 1
		g1 = set().union(*[set(c[0]) for c in comps[:cut]])
		g2 = set().union(*[set(c[0]) for c in comps[cut:]])
		l_xmax = max(c[3] for c in comps[:cut])
		r_xmin = min(c[2] for c in comps[cut:])
		overlap = max(0, l_xmax - r_xmin + 1)
		span = max(c[3] for c in comps) - min(c[2] for c in comps) + 1
		# both sides need real mass for this to be a boots split, not boot + buckle-speck
		balance = min(len(g1), len(g2)) / max(len(g1), len(g2))
		if balance >= 0.15:
			method = "components"
			confidence = max(0.0, 1.0 - overlap / max(1, span)) * (0.6 + 0.4 * min(1.0, balance * 2))
			groups = (g1, g2)

	if groups is None:
		# gap fallback: most transparent column valley in the middle half
		colsum = [0] * lw
		for i, v in enumerate(mask):
			if v:
				colsum[i % lw] += 1
		lo, hi = lw // 4, lw * 3 // 4
		valley = min(range(lo, hi), key=lambda x: colsum[x])
		method = "gap"
		peak = max(colsum) or 1
		confidence = 0.5 * (1.0 - colsum[valley] / peak)
		g1 = {i for i, v in enumerate(mask) if v and (i % lw) <= valley}
		g2 = {i for i, v in enumerate(mask) if v and (i % lw) > valley}
		if not g1 or not g2:
			return {"ok": False, "error": "no gap found — mask by hand"}
		groups = (g1, g2)

	def side_png(pixset) -> bytes:
		m = Image.new("L", (lw, lh), 0)
		m.putdata([255 if i in pixset else 0 for i in range(lw * lh)])
		m = m.resize((W, H), Image.LANCZOS).point(lambda v: 255 if v > 96 else 0)
		out = im.copy()
		a = out.split()[-1].point(lambda v: v)  # copy
		from PIL import ImageChops
		out.putalpha(ImageChops.multiply(a, m))
		bb = out.split()[-1].getbbox()
		if bb:
			pad = max(2, int(0.04 * max(bb[2] - bb[0], bb[3] - bb[1])))
			out = out.crop((max(0, bb[0] - pad), max(0, bb[1] - pad),
			                min(W, bb[2] + pad), min(H, bb[3] + pad)))
		buf = io.BytesIO()
		out.save(buf, "PNG")
		return buf.getvalue()

	left, right = side_png(groups[0]), side_png(groups[1])
	return {"ok": True, "method": method, "confidence": round(confidence, 3),
	        "left": left, "right": right}
