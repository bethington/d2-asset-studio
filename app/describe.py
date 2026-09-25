"""Per-item text descriptions via a local vision model (Ollama on the docker box).

Captions an item's art into a structured description saved with the item. Two uses:
  1. displayed/editable in the workflow panel (source-of-truth prose for the item),
  2. a reusable prompt seed for generating wholly-new items later (see UPSCALE_3D_PANEL_DESIGN
     §8 / decision #8).

Storage: <workspace>/descriptions.json keyed by item id -> {text, source, edited, ts}.
The model runs on the shared RTX 3090; keep calls short and one-at-a-time (it competes with
ComfyUI for VRAM).
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.request

WORKSPACE = os.environ.get("ASSET_STUDIO_WS", r"C:\Diablo2\AssetStudio")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://10.0.10.30:11434").rstrip("/")
VISION_MODEL = os.environ.get("DESCRIBE_MODEL", "qwen2.5vl:7b")
STORE = os.path.join(WORKSPACE, "descriptions.json")

# Structured caption: identity + the attributes that make a good img/3D generation prompt.
PROMPT = (
	"You are cataloguing a Diablo II inventory item sprite for an art pipeline. The image is a "
	"single game item on a plain background. Describe ONLY what is visibly depicted. Respond with "
	"these labelled lines and nothing else:\n"
	"Item: <one short noun phrase naming the item type>\n"
	"Materials: <the materials/surfaces you can see>\n"
	"Shape: <silhouette and notable structural features>\n"
	"Colors: <dominant palette>\n"
	"Condition: <e.g. pristine, battle-worn, ornate, crude>\n"
	"Style: <3-6 adjectives for its art style>\n"
	"Prompt: <one vivid sentence usable as a text-to-image prompt to recreate a similar item>"
)


class DescribeError(RuntimeError):
	pass


def _load() -> dict:
	try:
		with open(STORE, encoding="utf-8") as f:
			return json.load(f)
	except (OSError, ValueError):
		return {}


def _save(d: dict) -> None:
	os.makedirs(WORKSPACE, exist_ok=True)
	tmp = STORE + ".tmp"
	with open(tmp, "w", encoding="utf-8") as f:
		json.dump(d, f, indent=2, ensure_ascii=False)
	os.replace(tmp, STORE)


def get(item_id: str) -> dict | None:
	return _load().get(item_id)


def set_text(item_id: str, text: str, *, source: str = "user", ts: float = 0.0) -> dict:
	d = _load()
	rec = {"text": text, "source": source, "edited": source == "user", "ts": ts}
	d[item_id] = rec
	_save(d)
	return rec


_NOISE = re.compile(r"background|blurr|focal point|the image|this image|rendering of|3d render",
                    re.IGNORECASE)


def _clean_caption(text: str) -> str:
	"""Drop photo-descriptive noise sentences (background/blur/'the image is…') that would
	pollute a generation prompt; keep the material/shape/color prose."""
	parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
	kept = [s for s in parts if not _NOISE.search(s)]
	# never return nothing: fall back to the raw text minus a leading "The image is …" clause
	if not kept:
		return re.sub(r"^(the|this) image (is|shows|appears to (be|show))\s*", "", text,
		              flags=re.IGNORECASE).strip()
	return " ".join(kept)


def caption_florence(png_bytes: bytes, *, item_name: str | None = None,
                     item_kind: str | None = None, timeout: int = 180) -> str:
	"""GPU caption via the ComfyUI Florence-2 node (the default path — seconds, local).

	Florence has no context and can misread tiny stylized sprites (a gauntlet as a 'screwdriver'),
	so the catalog identity anchors the description and Florence supplies the visual prose."""
	import app.comfy as comfy
	raw = comfy.caption_florence(png_bytes, timeout=timeout)
	body = _clean_caption(raw)
	anchor = ""
	if item_name:
		kind = f" ({item_kind})" if item_kind and item_kind.lower() not in item_name.lower() else ""
		anchor = f"{item_name}{kind} — Diablo II inventory item. "
	return (anchor + body).strip()


def caption(png_bytes: bytes, *, timeout: int = 120) -> str:
	"""Run the vision model on the art and return the structured description text."""
	b64 = base64.b64encode(png_bytes).decode()
	body = json.dumps({
		"model": VISION_MODEL,
		"prompt": PROMPT,
		"images": [b64],
		"stream": False,
		"options": {"temperature": 0.2},
	}).encode()
	req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=body,
	                             headers={"Content-Type": "application/json"}, method="POST")
	try:
		with urllib.request.urlopen(req, timeout=timeout) as r:
			out = json.loads(r.read())
	except Exception as e:  # noqa: BLE001
		raise DescribeError(f"ollama caption failed: {e}") from e
	text = (out.get("response") or "").strip()
	if not text:
		raise DescribeError("empty caption")
	return text


def caption_and_store(item_id: str, png_bytes: bytes, *, ts: float = 0.0,
                      overwrite_user: bool = False, item_name: str | None = None,
                      item_kind: str | None = None, backend: str = "florence") -> dict:
	"""Caption + persist, unless a user-edited description already exists.
	backend: "florence" (GPU via ComfyUI, seconds — default) or "ollama" (CPU, minutes)."""
	existing = get(item_id)
	if existing and existing.get("edited") and not overwrite_user:
		return existing
	if backend == "florence":
		text = caption_florence(png_bytes, item_name=item_name, item_kind=item_kind)
		source = "florence-2"
	else:
		text = caption(png_bytes)
		source = VISION_MODEL
	d = _load()
	rec = {"text": text, "source": source, "edited": False, "ts": ts}
	d[item_id] = rec
	_save(d)
	return rec
