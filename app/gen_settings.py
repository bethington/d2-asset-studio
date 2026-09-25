"""User-editable image-generation settings, persisted so what goes into every generation is
transparent and tunable from the UI instead of hard-coded here.

Stored at <workspace>/gen_settings.json. `get()` returns DEFAULTS merged with any saved overrides;
`update()` writes a partial patch; `reset()` clears overrides back to DEFAULTS. comfy.py and the
/generate endpoint read the live values through here, so an edit takes effect on the next image
with no restart.
"""
from __future__ import annotations

import json
import os

import app.assets as assets

_PATH = os.path.join(assets.WORKSPACE, "gen_settings.json")

# Default house style. Deliberately NOT dark-biased: the previous default carried
# "dark-fantasy / gritty / muted palette / no glow", which pulled output ~25% darker than the
# source. This keeps the painterly look but preserves the original's exposure and colour.
DEFAULTS = {
	"model": "Qwen-Image-Edit-2509-Q6_K.gguf",       # UNet GGUF (Q4/Q6/Q8 tradeoff)
	"steps": 4,                                        # sampler steps (see edit_denoise)
	# Qwen-Edit img2img strength. The graph encodes the source sprite into the sampler's latent,
	# but denoise 1.0 (the ComfyUI template default this was built from) starts from pure noise
	# and DISCARDS it -- the model then only sees a text description of the art, so it redesigns:
	# Nef Rune came back as a different glyph, and small invented marks appeared as "text".
	# 0.45 keeps the source latent and refines the real pixels. Measured 2026-07-30 across a
	# rune/potion/key sweep: fidelity roughly doubled (potion ssim 0.32 -> 0.73). Effective steps
	# ~ steps x denoise, which is why `steps` rose from 4 to 10 alongside it.
	# CHOSEN BY EYE 1.0 (2026-07-30, ComfyUI job 2070ced6): on a ~29px sprite the source art is
	# so low-res that "faithful" means preserving mush -- 0.45 kept the original's smeary texture
	# and looked muddy, while 1.0 re-renders it as a clean icon that still reads as the same item.
	# Metrics disagree (0.45 scored ssim 0.83 vs 1.0's 0.61) because SSIM rewards reproducing the
	# original's noise. Drop to ~0.45 with steps ~10 when EXACT design must survive (a rune's
	# glyph changed at 1.0); effective steps ~ steps x denoise, so raise steps as denoise falls.
	"edit_denoise": 1.0,
	"gan": True,                                       # GAN 4x pre-upscale (vs plain LANCZOS)
	"px": 1024,                                         # generation resolution
	"style": ("hand-painted RPG inventory icon, crisp sharp edges, richly detailed painterly "
	          "materials, clear even lighting, true to the original's brightness and colours, "
	          "realistic metal and leather, no glow halo, no outline halo"),
	"enhance_tail": "enhance detail and sharpness, keep the exact shape, silhouette, materials and colours of the original",
	"restyle_tail": "keep the exact shape, silhouette and proportions of the original, do not add a face",
	"negative": ("blurry, pixelated, jpeg artifacts, low quality, text, watermark, signature, "
	             "extra objects, frame, border"),
	# accept-stage tone: a mild brightness lift applied to the master before it becomes a DC6, to
	# recover exposure lost in the model + outline steps. 1.0 = off. Applied at accept, so it fixes
	# the EXISTING library on re-accept without re-generating.
	"accept_brightness": 1.0,
}

# keys that must stay their declared type / range when patched
_NUMERIC = {"steps": int, "px": int, "accept_brightness": float}


def get() -> dict:
	out = dict(DEFAULTS)
	try:
		with open(_PATH, encoding="utf-8") as f:
			saved = json.load(f)
		if isinstance(saved, dict):
			out.update({k: v for k, v in saved.items() if k in DEFAULTS})
	except (OSError, ValueError):
		pass
	return out


def update(patch: dict) -> dict:
	cur = get()
	for k, v in (patch or {}).items():
		if k not in DEFAULTS:
			continue
		if k in _NUMERIC:
			try:
				v = _NUMERIC[k](v)
			except (TypeError, ValueError):
				continue
		if isinstance(DEFAULTS[k], bool):
			v = bool(v)
		cur[k] = v
	# persist only the diff from DEFAULTS to keep the file honest
	diff = {k: cur[k] for k in DEFAULTS if cur[k] != DEFAULTS[k]}
	os.makedirs(os.path.dirname(_PATH), exist_ok=True)
	tmp = _PATH + ".tmp"
	with open(tmp, "w", encoding="utf-8") as f:
		json.dump(diff, f, indent=2)
	os.replace(tmp, _PATH)
	return cur


def reset() -> dict:
	try:
		os.remove(_PATH)
	except OSError:
		pass
	return get()


def enhance_instruction() -> str:
	s = get()
	return f"{s['style']}, {s['enhance_tail']}"


def restyle_instruction(user_text: str) -> str:
	s = get()
	ut = (user_text or "").strip().rstrip(".")
	lead = (ut + ", ") if ut else ""
	return f"{lead}{s['style']}, {s['restyle_tail']}"
