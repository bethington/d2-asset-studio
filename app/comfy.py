"""ComfyUI client + workflow builders for the AI upscale lanes.

Talks to the ComfyUI instance on the home docker box (RTX 3090). The app's job model is
poll-based (no background queue), so this mirrors it: `run()` submits a prompt graph, polls
`/history/{id}` until the outputs appear, and returns the decoded PNG bytes.

Two lanes (see ../UPSCALE_3D_PANEL_DESIGN.md §3):
  A  SDXL img2img + ControlNet-Union-ProMax(tile) + DMD2 4-step LoRA   -> build_sdxl_tile_graph
  B  Qwen-Image-Edit-2509 (GGUF + Lightning)                           -> (added in a later phase)

Geometry (matte, pre-size, alpha re-cut) is done here in Python, so the graphs only carry the
GAN cleanup + diffusion. This keeps ComfyUI custom-node deps at zero for Lane A: every node used
is base ComfyUI (CheckpointLoaderSimple, LoraLoader, UpscaleModelLoader, ImageUpscaleWithModel,
ImageScale, ControlNetLoader, SetUnionControlNetType, ControlNetApplyAdvanced, VAEEncode,
KSampler, VAEDecode, SaveImage).
"""

from __future__ import annotations

import io
import json
import os
import threading
import time
import urllib.request
import uuid

from PIL import Image

COMFY_URL = os.environ.get("COMFY_URL", "http://10.0.10.30:8188").rstrip("/")

# Installed model file names (verified present via /object_info).
CKPT = os.environ.get("COMFY_SDXL_CKPT", "Juggernaut-X-v10-RunDiffusion.safetensors")
CONTROLNET_UNION = "controlnet-union-sdxl-promax.safetensors"
DMD2_LORA = "dmd2_sdxl_4step_lora_fp16.safetensors"
UPSCALE_MODEL = "4x-UltraSharp.pth"
FLUX_CKPT = os.environ.get("COMFY_FLUX_CKPT", "flux1-schnell-fp8.safetensors")  # all-in-one (model+clip+vae)
FLUX_CN = "FLUX.1-dev-ControlNet-Union-Pro-2.0.safetensors"  # holds silhouette while the prompt restyles
# Qwen-Image-Edit: true image-edit model. GGUF via the ComfyUI-GGUF loader, which offloads well:
# Q4 (~13GB), Q6 (~16GB) and even Q8 (~21GB) all fit the 3090's 24GB (the fp8 UNETLoader OOM'd at
# 16.6GB — GGUF is the reason the bigger quants work). Q6 is the quality/speed sweet spot; set
# COMFY_QWEN_GGUF=Qwen-Image-Edit-2509-Q8_0.gguf for max quality (~40% slower).
QWEN_GGUF = os.environ.get("COMFY_QWEN_GGUF", "Qwen-Image-Edit-2509-Q6_K.gguf")

# Fixed house art-style for the Qwen edit lane. Describe the STYLE, never the item name -- Qwen
# already sees the item, and naming it makes it render a photoreal prop instead of a painted icon.
QWEN_STYLE = ("hand-painted dark-fantasy RPG inventory icon, crisp sharp edges, richly detailed "
              "painterly materials, soft even lighting, no glow, no outline halo, gritty realistic "
              "metal and leather, muted palette")


def qwen_enhance_instruction() -> str:
	"""Preset: faithful clean-up -- keep the item exactly, render it in the house style, sharper."""
	return (QWEN_STYLE + ", enhance detail and sharpness, keep the exact shape, silhouette, "
	        "materials and colours of the original")


def qwen_restyle_instruction(user_text: str) -> str:
	"""Preset: change the look -- apply the user's restyle over the house style, keep the shape."""
	ut = (user_text or "").strip().rstrip(".")
	lead = (ut + ", ") if ut else ""
	return (lead + QWEN_STYLE + ", keep the exact shape, silhouette and proportions of the original, "
	        "do not add a face")
QWEN_CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
QWEN_VAE = "qwen_image_vae.safetensors"
QWEN_LORA = "Qwen-Image-Edit-2509-Lightning-4steps-bf16.safetensors"

GRAY = (128, 128, 128)  # neutral matte; transparent PNGs carry garbage RGB under alpha=0

DEFAULT_NEGATIVE = ("blurry, pixelated, jpeg artifacts, low quality, text, watermark, signature, "
                    "extra objects, frame, border")


class ComfyError(RuntimeError):
	pass


# ---- HTTP plumbing ------------------------------------------------------

def _req(path: str, data: bytes | None = None, method: str | None = None,
         headers: dict | None = None, timeout: int = 60):
	url = f"{COMFY_URL}{path}"
	req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
	with urllib.request.urlopen(req, timeout=timeout) as r:
		return r.read()


def upload_image(png: bytes, name: str | None = None, subfolder: str = "d2studio") -> str:
	"""POST a PNG to ComfyUI's input store; returns the name LoadImage should reference
	(``subfolder/name`` when a subfolder is used)."""
	name = name or f"{uuid.uuid4().hex}.png"
	boundary = "----d2studio" + uuid.uuid4().hex
	parts = []
	def field(headers, body: bytes):
		parts.append(("--" + boundary + "\r\n" + headers + "\r\n\r\n").encode() + body + b"\r\n")
	field(f'Content-Disposition: form-data; name="image"; filename="{name}"\r\n'
	      "Content-Type: image/png", png)
	field('Content-Disposition: form-data; name="subfolder"', subfolder.encode())
	field('Content-Disposition: form-data; name="overwrite"', b"true")
	body = b"".join(parts) + ("--" + boundary + "--\r\n").encode()
	out = _req("/upload/image", data=body, method="POST",
	           headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
	info = json.loads(out)
	sub = info.get("subfolder") or ""
	return f"{sub}/{info['name']}" if sub else info["name"]


def _submit(graph: dict) -> str:
	cid = uuid.uuid4().hex
	out = _req("/prompt", data=json.dumps({"prompt": graph, "client_id": cid}).encode(),
	           method="POST", headers={"Content-Type": "application/json"})
	res = json.loads(out)
	if "prompt_id" not in res:
		raise ComfyError(f"submit rejected: {res}")
	return res["prompt_id"]


def _await(pid: str, timeout: int = 300, poll: float = 1.0) -> dict:
	deadline = time.time() + timeout
	while time.time() < deadline:
		hist = json.loads(_req(f"/history/{pid}", timeout=15) or b"{}")
		if pid in hist:
			h = hist[pid]
			status = (h.get("status") or {}).get("status_str")
			if status == "error":
				raise ComfyError(f"comfy run error: {json.dumps(h.get('status'))[:500]}")
			if h.get("outputs"):
				return h["outputs"]
		time.sleep(poll)
	raise ComfyError(f"timed out after {timeout}s waiting for {pid}")


def _fetch_first_text(outputs: dict) -> str:
	"""Pull the first text value out of run outputs (PreviewAny publishes under a ui key)."""
	for node in outputs.values():
		for key in ("text", "string", "value"):
			v = node.get(key)
			if isinstance(v, list) and v and isinstance(v[0], str):
				return "\n".join(v).strip()
	raise ComfyError(f"no text in outputs: {json.dumps(outputs)[:300]}")


def _fetch_first_image(outputs: dict) -> bytes:
	for node in outputs.values():
		for img in node.get("images", []):
			import urllib.parse
			q = urllib.parse.urlencode({k: img[k] for k in ("filename", "subfolder", "type") if k in img})
			return _req(f"/view?{q}", timeout=30)
	raise ComfyError("no image in outputs")


def run(graph: dict, timeout: int = 300) -> bytes:
	"""Submit a prompt graph and return the first output image's PNG bytes."""
	return _fetch_first_image(_await(_submit(graph), timeout=timeout))


def free_memory(*, unload_models: bool = True, free_memory: bool = True) -> bool:
	"""Ask ComfyUI to evict resident models / free VRAM (POST /free). Call this before switching
	to a different big model so we never hold two large checkpoints in VRAM at once. Best-effort:
	returns False if the endpoint isn't reachable rather than raising."""
	global _ACTIVE_FAMILY
	_ACTIVE_FAMILY = None  # any explicit evict invalidates our "already loaded" tracking
	try:
		_req("/free", data=json.dumps({"unload_models": unload_models,
		                               "free_memory": free_memory}).encode(),
		     method="POST", headers={"Content-Type": "application/json"}, timeout=30)
		return True
	except Exception:  # noqa: BLE001
		return False


# Tracks which model family last ran a generation, so back-to-back calls in the same family (e.g.
# a batch doing 100+ Qwen edits in a row) skip the eviction -- ComfyUI's own checkpoint cache then
# keeps the model resident instead of a full unload+disk-reload on every single call.
_ACTIVE_FAMILY: str | None = None


def _ensure_family(family: str) -> None:
	global _ACTIVE_FAMILY
	if _ACTIVE_FAMILY != family:
		free_memory()  # evicts whatever's resident (sets _ACTIVE_FAMILY back to None)
	_ACTIVE_FAMILY = family


# ---- geometry (done in Python, not ComfyUI) -----------------------------

def round8(n: int) -> int:
	return max(8, int(round(n / 8.0)) * 8)


def matte_and_size(sprite_png: bytes, long_side: int = 1024):
	"""RGBA sprite -> (matted RGB PNG on gray at a /8 working canvas, alpha PNG at that size).

	Keeps aspect (long side -> ``long_side``), pads with gray, and returns the sprite's own
	alpha resized to the same canvas so the result can be re-cut without a segmentation model."""
	im = Image.open(io.BytesIO(sprite_png)).convert("RGBA")
	bb = im.split()[-1].getbbox()
	if bb:
		im = im.crop(bb)
	w, h = im.size
	scale = long_side / float(max(w, h))
	nw, nh = round8(w * scale), round8(h * scale)
	im = im.resize((nw, nh), Image.LANCZOS)
	# center on a /8 square canvas so every item shares one working size
	side = round8(long_side)
	canvas = Image.new("RGBA", (side, side), (*GRAY, 255))
	ox, oy = (side - nw) // 2, (side - nh) // 2
	canvas.alpha_composite(im, (ox, oy))
	rgb = canvas.convert("RGB")
	# the alpha guide must come from compositing onto a TRANSPARENT canvas -- alpha_composite
	# over the opaque gray `canvas` above always yields 255 (Porter-Duff "over" on an opaque
	# destination is opaque regardless of the source), which silently made every guide alpha a
	# solid square and broke protect_silhouette's floor union (forced full-canvas opacity).
	alpha_canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
	alpha_canvas.alpha_composite(im, (ox, oy))
	alpha = alpha_canvas.split()[-1]
	rb, ab = io.BytesIO(), io.BytesIO()
	rgb.save(rb, "PNG")
	alpha.save(ab, "PNG")
	return rb.getvalue(), ab.getvalue(), (side, side)


_REMBG_SESSIONS: dict[str, object] = {}
_REMBG_SESSION_LOCK = threading.Lock()
# birefnet-general is the cutter the design doc chose (UPSCALE_3D_PANEL_DESIGN.md §"the cutter"):
# isnet-general-use is photo-trained and "tore the silhouette" on thin metallic art (swords), but
# nothing ever set COMFY_REMBG_MODEL so production silently ran the rejected model. BiRefNet is
# ~35s/image on CPU vs seconds for isnet — acceptable behind an already-90s+ generation; set
# COMFY_REMBG_MODEL=isnet-general-use to opt back into the fast cutter.
REMBG_MODEL = os.environ.get("COMFY_REMBG_MODEL", "birefnet-general")


def _rembg_session(model: str | None = None):
	# session.run() is safe for concurrent callers (batch pipelines the local rembg cutout of one
	# item behind the next item's remote GPU wait); only the lazy first-creation needs a lock.
	# Sessions are cached per model name so callers (e.g. gems -> isnet) don't reload on every call.
	model = model or REMBG_MODEL
	if model not in _REMBG_SESSIONS:
		with _REMBG_SESSION_LOCK:
			if model not in _REMBG_SESSIONS:
				from rembg import new_session
				print(f"[comfy] rembg session: {model}", flush=True)
				_REMBG_SESSIONS[model] = new_session(model)
	return _REMBG_SESSIONS[model]


def _defringe(rgba: Image.Image, iters: int = 3) -> Image.Image:
	"""Bleed opaque edge colours outward under the alpha so semi-transparent pixels don't show the
	gray matte. Cheap: repeatedly blur the RGB and paste it only where alpha is low."""
	import numpy as np
	arr = np.asarray(rgba).astype(np.float32)
	rgb, a = arr[..., :3], arr[..., 3:4] / 255.0
	from PIL import ImageFilter
	for _ in range(iters):
		blurred = np.asarray(Image.fromarray(rgb.astype("uint8")).filter(
			ImageFilter.GaussianBlur(2))).astype(np.float32)
		# keep original colour where opaque, use blurred bleed where transparent
		rgb = rgb * a + blurred * (1 - a)
	out = np.concatenate([rgb, arr[..., 3:4]], axis=-1).astype("uint8")
	return Image.fromarray(out, "RGBA")


def recut_alpha_rembg(generated_png: bytes, guide_alpha_png: bytes | None = None, *,
                      protect_silhouette: bool = False, rembg_model: str | None = None) -> bytes:
	"""Segment the generated 1024 image with rembg (native-res silhouette, no 56px blockiness),
	optionally clamped to a dilated version of the original alpha so the model can't invent
	background blobs, then defringe the gray matte. Falls back to `recut_alpha` on any error.

	`protect_silhouette` (enhance methods): after the reject clamp, UNION a slightly-eroded copy
	of the original alpha back in, so the cutter can never DELETE interior silhouette (the thin
	Long Sword blade). The erosion (~1 original-sprite pixel at master scale) still lets the model
	refine the very edge; interior loss is impossible. Only meaningful with a guide alpha.

	`rembg_model` overrides the default cutter (birefnet-general) for this call -- e.g. gems pass
	isnet-general-use, which is 10-70x faster and matched birefnet's quality on chunky convex
	shapes with no thin parts to tear (birefnet's whole reason for existing)."""
	try:
		from rembg import remove
		from PIL import ImageFilter
		import numpy as np
		cut = Image.open(io.BytesIO(remove(generated_png, session=_rembg_session(rembg_model)))).convert("RGBA")
		a = cut.split()[-1]
		if guide_alpha_png:
			# Loose reject-only clamp: heavily dilate + blur the original silhouette so it kills
			# only far-away hallucinated blobs, never chops BiRefNet's clean native edge.
			g = Image.open(io.BytesIO(guide_alpha_png)).convert("L").resize(a.size, Image.LANCZOS)
			g = g.point(lambda v: 255 if v > 8 else 0).filter(ImageFilter.MaxFilter(9))
			g = g.filter(ImageFilter.GaussianBlur(24)).point(lambda v: 255 if v > 24 else 0)
			a = Image.fromarray(np.minimum(np.asarray(a), np.asarray(g)).astype("uint8"), "L")
			if protect_silhouette:
				# floor = the original silhouette eroded by ~1 original pixel; union it back so
				# nothing interior can be removed. The dilated reject-guide strictly contains this
				# floor, so this can't resurrect a rejected far blob.
				floor = Image.open(io.BytesIO(guide_alpha_png)).convert("L").resize(a.size, Image.LANCZOS)
				floor = floor.point(lambda v: 255 if v > 128 else 0)
				k = max(3, (round(min(a.size) / 64) | 1))   # odd kernel, ~just under one orig pixel
				floor = floor.filter(ImageFilter.MinFilter(k))
				a = Image.fromarray(np.maximum(np.asarray(a), np.asarray(floor)).astype("uint8"), "L")
		rgb = Image.open(io.BytesIO(generated_png)).convert("RGB").convert("RGBA")
		rgb.putalpha(a)
		buf = io.BytesIO()
		_defringe(rgb).save(buf, "PNG")
		return buf.getvalue()
	except Exception as e:  # noqa: BLE001
		if guide_alpha_png:
			return recut_alpha(generated_png, guide_alpha_png)
		raise ComfyError(f"rembg recut failed and no guide alpha: {e}")


def recut_alpha(generated_png: bytes, alpha_png: bytes, feather: int = 1) -> bytes:
	"""Apply the (upscaled original) alpha back onto the generated RGB. Fallback for when rembg is
	unavailable; blocky because the guide alpha is only original-resolution."""
	rgb = Image.open(io.BytesIO(generated_png)).convert("RGB")
	a = Image.open(io.BytesIO(alpha_png)).convert("L").resize(rgb.size, Image.LANCZOS)
	if feather:
		from PIL import ImageFilter
		a = a.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(feather))
	out = rgb.convert("RGBA")
	out.putalpha(a)
	buf = io.BytesIO()
	out.save(buf, "PNG")
	return buf.getvalue()


# ---- Lane A graph builder ----------------------------------------------

def to_canonical_2x(master_rgba_png: bytes, original_canvas: tuple[int, int]) -> bytes:
	"""Downsample the ~1024 hi-res master to EXACTLY 2x the original art's canvas (decision #4).
	The DC6 build later downsizes this to the original size."""
	im = Image.open(io.BytesIO(master_rgba_png)).convert("RGBA")
	tw, th = original_canvas[0] * 2, original_canvas[1] * 2
	# the master is a square working canvas; fit the content back into a 2x-original canvas by
	# cropping to content then centering, so the sprite keeps its position relative to the cell grid
	bb = im.split()[-1].getbbox()
	content = im.crop(bb) if bb else im
	scale = min(tw / content.width, th / content.height)
	nw, nh = max(1, int(content.width * scale)), max(1, int(content.height * scale))
	content = content.resize((nw, nh), Image.LANCZOS)
	canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
	canvas.alpha_composite(content, ((tw - nw) // 2, (th - nh) // 2))
	buf = io.BytesIO()
	canvas.save(buf, "PNG")
	return buf.getvalue()


def build_sdxl_tile_graph(*, image_name: str, width: int, height: int,
                          positive: str, negative: str = "",
                          seed: int = 0, denoise: float = 0.45, cn_strength: float = 0.7,
                          steps: int = 8, cfg: float = 1.0,
                          sampler: str = "lcm", scheduler: str = "sgm_uniform",
                          ckpt: str = CKPT, prefix: str = "d2up") -> dict:
	"""SDXL img2img creative-upscale graph (API/prompt format).

	Fidelity slider -> ``cn_strength`` (tile ControlNet pin to original).
	Creativity slider -> ``denoise`` (how much the model may invent).
	DMD2 4-step LoRA lets us stay at cfg~1 / few steps for fast re-rolls.
	"""
	g = {
		"ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
		"lora": {"class_type": "LoraLoader", "inputs": {
			"model": ["ckpt", 0], "clip": ["ckpt", 1], "lora_name": DMD2_LORA,
			"strength_model": 1.0, "strength_clip": 1.0}},
		"pos": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["lora", 1]}},
		"neg": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["lora", 1]}},
		"img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
		"upmodel": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": UPSCALE_MODEL}},
		"gan": {"class_type": "ImageUpscaleWithModel", "inputs": {
			"upscale_model": ["upmodel", 0], "image": ["img", 0]}},
		"scale": {"class_type": "ImageScale", "inputs": {
			"image": ["gan", 0], "upscale_method": "lanczos",
			"width": width, "height": height, "crop": "disabled"}},
		"cn": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": CONTROLNET_UNION}},
		"cnt": {"class_type": "SetUnionControlNetType", "inputs": {"control_net": ["cn", 0], "type": "tile"}},
		"vae_enc": {"class_type": "VAEEncode", "inputs": {"pixels": ["scale", 0], "vae": ["ckpt", 2]}},
		"cna": {"class_type": "ControlNetApplyAdvanced", "inputs": {
			"positive": ["pos", 0], "negative": ["neg", 0], "control_net": ["cnt", 0],
			"image": ["scale", 0], "strength": cn_strength,
			"start_percent": 0.0, "end_percent": 1.0, "vae": ["ckpt", 2]}},
		"ks": {"class_type": "KSampler", "inputs": {
			"model": ["lora", 0], "positive": ["cna", 0], "negative": ["cna", 1],
			"latent_image": ["vae_enc", 0], "seed": seed, "steps": steps, "cfg": cfg,
			"sampler_name": sampler, "scheduler": scheduler, "denoise": denoise}},
		"dec": {"class_type": "VAEDecode", "inputs": {"samples": ["ks", 0], "vae": ["ckpt", 2]}},
		"save": {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": prefix}},
	}
	return g


def build_sdxl_text_graph(*, width: int, height: int, positive: str, negative: str = "",
                          seed: int = 0, ref_image_name: str | None = None,
                          ref_strength: float = 0.0, steps: int = 8, cfg: float = 1.0,
                          sampler: str = "lcm", scheduler: str = "sgm_uniform",
                          ckpt: str = CKPT, prefix: str = "d2desc") -> dict:
	"""Text-to-image graph for the 'from description' lane (denoise 1.0 from an empty latent).
	With ref_strength > 0 the matted original is applied as a weak tile ControlNet so the
	reimagined item keeps roughly the original's framing (the panel's reference slider)."""
	g = {
		"ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
		"lora": {"class_type": "LoraLoader", "inputs": {
			"model": ["ckpt", 0], "clip": ["ckpt", 1], "lora_name": DMD2_LORA,
			"strength_model": 1.0, "strength_clip": 1.0}},
		"pos": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["lora", 1]}},
		"neg": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["lora", 1]}},
		"latent": {"class_type": "EmptyLatentImage", "inputs": {
			"width": width, "height": height, "batch_size": 1}},
		"ks": {"class_type": "KSampler", "inputs": {
			"model": ["lora", 0], "positive": ["pos", 0], "negative": ["neg", 0],
			"latent_image": ["latent", 0], "seed": seed, "steps": steps, "cfg": cfg,
			"sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0}},
		"dec": {"class_type": "VAEDecode", "inputs": {"samples": ["ks", 0], "vae": ["ckpt", 2]}},
		"save": {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": prefix}},
	}
	if ref_image_name and ref_strength > 0.01:
		g["img"] = {"class_type": "LoadImage", "inputs": {"image": ref_image_name}}
		g["cn"] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": CONTROLNET_UNION}}
		g["cnt"] = {"class_type": "SetUnionControlNetType", "inputs": {"control_net": ["cn", 0], "type": "tile"}}
		g["cna"] = {"class_type": "ControlNetApplyAdvanced", "inputs": {
			"positive": ["pos", 0], "negative": ["neg", 0], "control_net": ["cnt", 0],
			"image": ["img", 0], "strength": ref_strength,
			"start_percent": 0.0, "end_percent": 1.0, "vae": ["ckpt", 2]}}
		g["ks"]["inputs"]["positive"] = ["cna", 0]
		g["ks"]["inputs"]["negative"] = ["cna", 1]
	return g


FLORENCE_MODEL = os.environ.get("COMFY_FLORENCE_MODEL", "microsoft/Florence-2-large")


def build_florence_graph(image_name: str, task: str = "more_detailed_caption") -> dict:
	"""GPU caption via the container's baked-in ComfyUI-Florence2 node; PreviewAny (an output
	node) publishes the caption string into the run history."""
	return {
		"img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
		"fl2": {"class_type": "DownloadAndLoadFlorence2Model", "inputs": {
			"model": FLORENCE_MODEL, "precision": "fp16"}},
		"run": {"class_type": "Florence2Run", "inputs": {
			"image": ["img", 0], "florence2_model": ["fl2", 0], "text_input": "",
			"task": task, "fill_mask": False, "keep_model_loaded": True,
			"max_new_tokens": 512, "num_beams": 3, "do_sample": False, "seed": 1}},
		"show": {"class_type": "PreviewAny", "inputs": {"source": ["run", 2]}},
	}


def caption_florence(sprite_png: bytes, *, timeout: int = 180) -> str:
	"""Caption the sprite on the GPU. Uses the same gray-matte prep as generation so Florence
	sees a clean opaque subject instead of transparency garbage."""
	rgb_png, _alpha, _size = matte_and_size(sprite_png, 768)
	name = upload_image(rgb_png)
	outputs = _await(_submit(build_florence_graph(name)), timeout=timeout)
	return _fetch_first_text(outputs)


def upscale_faithful(sprite_png: bytes, *, positive: str, negative: str = "",
                     seed: int = 0, denoise: float = 0.45, cn_strength: float = 0.7,
                     long_side: int = 1024, timeout: int = 300,
                     protect_silhouette: bool = False):
	"""End-to-end Lane A: matte -> upload -> GAN+diffusion -> alpha re-cut.
	Returns (rgba_png_1024, size)."""
	rgb_png, alpha_png, (w, h) = matte_and_size(sprite_png, long_side)
	name = upload_image(rgb_png)
	_ensure_family("sdxl")
	graph = build_sdxl_tile_graph(image_name=name, width=w, height=h,
	                              positive=positive, negative=negative, seed=seed,
	                              denoise=denoise, cn_strength=cn_strength)
	gen = run(graph, timeout=timeout)
	return recut_alpha_rembg(gen, guide_alpha_png=alpha_png,
	                         protect_silhouette=protect_silhouette), (w, h)


def generate_from_description(sprite_png: bytes, *, description: str, negative: str = "",
                              seed: int = 0, ref_strength: float = 0.0,
                              long_side: int = 1024, timeout: int = 300):
	"""'From description' lane: txt2img driven by the description text; the original sprite is
	only used (a) as the optional weak tile-CN reference and (b) as the reject-only alpha guard
	when ref_strength is high enough that the layout tracks the original. At low/zero reference
	the composition is free, so BiRefNet runs unguarded."""
	rgb_png, alpha_png, (w, h) = matte_and_size(sprite_png, long_side)
	ref_name = upload_image(rgb_png) if ref_strength > 0.01 else None
	_ensure_family("sdxl")
	graph = build_sdxl_text_graph(width=w, height=h, positive=description, negative=negative,
	                              seed=seed, ref_image_name=ref_name, ref_strength=ref_strength)
	gen = run(graph, timeout=timeout)
	guide = alpha_png if ref_strength >= 0.35 else None
	return recut_alpha_rembg(gen, guide_alpha_png=guide), (w, h)


# ---- Flux lane: one model, "true -> customizable" via a single faithfulness knob ----------

def build_flux_img2img_graph(*, image_name: str, positive: str, seed: int, denoise: float,
                             steps: int = 4, prefix: str = "d2flux") -> dict:
	"""Flux.1 schnell img2img: encode the sprite, partial-denoise so the result stays anchored to
	the original silhouette. schnell is guidance-distilled -> cfg 1, no FluxGuidance node."""
	return {
		"ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": FLUX_CKPT}},
		"pos": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["ckpt", 1]}},
		"neg": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["ckpt", 1]}},
		"img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
		"enc": {"class_type": "VAEEncode", "inputs": {"pixels": ["img", 0], "vae": ["ckpt", 2]}},
		"ks": {"class_type": "KSampler", "inputs": {
			"model": ["ckpt", 0], "positive": ["pos", 0], "negative": ["neg", 0],
			"latent_image": ["enc", 0], "seed": seed, "steps": steps, "cfg": 1.0,
			"sampler_name": "euler", "scheduler": "simple", "denoise": denoise}},
		"dec": {"class_type": "VAEDecode", "inputs": {"samples": ["ks", 0], "vae": ["ckpt", 2]}},
		"save": {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": prefix}},
	}


def build_flux_txt2img_graph(*, width: int, height: int, positive: str, seed: int,
                             steps: int = 4, prefix: str = "d2fluxt") -> dict:
	"""Flux.1 schnell txt2img (empty latent, full denoise) -- the fully prompt-driven end."""
	return {
		"ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": FLUX_CKPT}},
		"pos": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["ckpt", 1]}},
		"neg": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["ckpt", 1]}},
		"latent": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
		"ks": {"class_type": "KSampler", "inputs": {
			"model": ["ckpt", 0], "positive": ["pos", 0], "negative": ["neg", 0],
			"latent_image": ["latent", 0], "seed": seed, "steps": steps, "cfg": 1.0,
			"sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
		"dec": {"class_type": "VAEDecode", "inputs": {"samples": ["ks", 0], "vae": ["ckpt", 2]}},
		"save": {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": prefix}},
	}


def faithfulness_to_denoise(faithfulness: float) -> float:
	"""Map a 0..1 'faithfulness to original' slider to a Flux img2img denoise.
	1.0 -> 0.20 (hug the silhouette, soft)   ~0.4 -> 0.65 (crisp + faithful sweet spot)
	0.0 -> 0.95 (fully reimagined). Below ~0.06 the caller should switch to pure txt2img."""
	return max(0.20, min(0.95, 0.95 - 0.75 * max(0.0, min(1.0, faithfulness))))


def generate_flux(sprite_png: bytes, *, description: str, seed: int = 0, faithfulness: float = 0.4,
                  steps: int = 4, long_side: int = 1024, timeout: int = 400):
	"""'From description' lane on Flux.1 schnell. One faithfulness knob spans the range: high keeps
	the original silhouette (img2img, low denoise); low reimagines it; ~0 is pure text-to-image.
	Flux follows prose precisely and resists the SDXL 'skull' trap, so it stays on-subject.
	`steps` (4-8) trades speed for refinement; schnell is happy at 4."""
	steps = max(1, min(12, int(steps)))
	rgb_png, alpha_png, (w, h) = matte_and_size(sprite_png, long_side)
	if faithfulness <= 0.06:  # fully prompt-driven -> text-to-image, unguarded silhouette
		graph = build_flux_txt2img_graph(width=w, height=h, positive=description, seed=seed, steps=steps)
		guide = None
	else:
		denoise = faithfulness_to_denoise(faithfulness)
		graph = build_flux_img2img_graph(image_name=upload_image(rgb_png), positive=description,
		                                 seed=seed, denoise=denoise, steps=steps)
		# the layout only tracks the original while denoise is low enough; guard the cut then
		guide = alpha_png if denoise <= 0.55 else None
	gen = run(graph, timeout=timeout)
	return recut_alpha_rembg(gen, guide_alpha_png=guide), (w, h)


def shape_hint(sprite_png: bytes, long_side: int = 1024) -> bytes:
	"""ControlNet hint (white silhouette outline on black) that pins the shape. Built from the
	ORIGINAL sprite's real alpha -- matte_and_size fills the bg opaque, so its alpha is useless.
	Mirrors that function's crop/scale/center geometry so the ring aligns with the matted canvas."""
	from PIL import ImageFilter, ImageChops
	im = Image.open(io.BytesIO(sprite_png)).convert("RGBA")
	bb = im.split()[-1].getbbox()
	if bb:
		im = im.crop(bb)
	w, h = im.size
	scale = long_side / float(max(w, h))
	nw, nh = round8(int(w * scale)), round8(int(h * scale))
	a = im.resize((nw, nh), Image.LANCZOS).split()[-1]
	side = round8(long_side)
	mask = Image.new("L", (side, side), 0)
	mask.paste(a, ((side - nw) // 2, (side - nh) // 2))
	m = mask.point(lambda v: 255 if v > 24 else 0)
	ring = ImageChops.difference(m.filter(ImageFilter.MaxFilter(7)), m.filter(ImageFilter.MinFilter(7)))
	b = io.BytesIO(); ring.convert("RGB").save(b, "PNG"); return b.getvalue()


def build_flux_cn_graph(*, hint_name: str, width: int, height: int, positive: str, seed: int,
                        cn_strength: float = 0.65, steps: int = 4, prefix: str = "d2fluxcn") -> dict:
	"""Flux txt2img whose composition is pinned to the original's outline by the Union-Pro
	ControlNet, so the prompt can freely restyle material/colour while the silhouette stays put."""
	return {
		"ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": FLUX_CKPT}},
		"pos": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["ckpt", 1]}},
		"neg": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["ckpt", 1]}},
		"cn": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": FLUX_CN}},
		"hint": {"class_type": "LoadImage", "inputs": {"image": hint_name}},
		"latent": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
		"cna": {"class_type": "ControlNetApplyAdvanced", "inputs": {
			"positive": ["pos", 0], "negative": ["neg", 0], "control_net": ["cn", 0],
			"image": ["hint", 0], "strength": cn_strength, "start_percent": 0.0, "end_percent": 0.9,
			"vae": ["ckpt", 2]}},
		"ks": {"class_type": "KSampler", "inputs": {
			"model": ["ckpt", 0], "positive": ["cna", 0], "negative": ["cna", 1],
			"latent_image": ["latent", 0], "seed": seed, "steps": steps, "cfg": 1.0,
			"sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
		"dec": {"class_type": "VAEDecode", "inputs": {"samples": ["ks", 0], "vae": ["ckpt", 2]}},
		"save": {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": prefix}},
	}


def build_qwen_edit_graph(*, image_name: str, prompt: str, seed: int, px: int = 1024,
                          shift: float = 3.0, steps: int = 4, gan: bool = True,
                          model: str | None = None, negative: str = "",
                          denoise: float = 1.0, prefix: str = "d2qwen") -> dict:
	"""Qwen-Image-Edit-2509 (Q4 GGUF + 4-step Lightning LoRA). Translated from ComfyUI's bundled
	template, switches resolved to the LoRA/4-step/cfg1 path. The reference image is carried by
	TextEncodeQwenImageEditPlus, so it edits the real sprite. Q4 fits the 24GB card; fp8 OOM'd.

	``denoise``: 1.0 (the template default) starts the sampler from PURE NOISE and discards the
	encoded source latent entirely -- the reference reaches the model only through the text
	encoder, which is why output is a re-imagining rather than a refinement of the actual pixels
	(a rune came back with a different glyph). Below 1.0 this becomes true img2img: the source
	latent is partially noised, so the art is genuinely re-rendered by Qwen while staying anchored
	to the original's composition. The 4-step Lightning LoRA means EFFECTIVE steps ~ steps x
	denoise, so raise `steps` when lowering denoise or the result is undercooked.

	``gan``: pre-upscale the source with 4x-UltraSharp before the sampler (design-doc
	"non-negotiable"). A ~58px sprite LANCZOS-blown-up leaves a terraced silhouette that Qwen
	faithfully preserves -> blocky edges; the GAN hands it a crisp edge instead. The caller must
	feed a near-native matte in this mode so the GAN (not LANCZOS) does the enlargement.
	"""
	g = {
		"unet": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": model or QWEN_GGUF}},
		"clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": QWEN_CLIP, "type": "qwen_image", "device": "default"}},
		"vae": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN_VAE}},
		"lora": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["unet", 0], "lora_name": QWEN_LORA, "strength_model": 1.0}},
		"ms": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["lora", 0], "shift": shift}},
		"cfgn": {"class_type": "CFGNorm", "inputs": {"model": ["ms", 0], "strength": 1.0}},
		"img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
		"scale": {"class_type": "ImageScale", "inputs": {
			"image": ["img", 0], "upscale_method": "lanczos", "width": px, "height": px, "crop": "disabled"}},
		"pos": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
			"clip": ["clip", 0], "vae": ["vae", 0], "image1": ["scale", 0], "prompt": prompt}},
		"neg": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
			"clip": ["clip", 0], "vae": ["vae", 0], "image1": ["scale", 0], "prompt": negative}},
		"enc": {"class_type": "VAEEncode", "inputs": {"pixels": ["scale", 0], "vae": ["vae", 0]}},
		"ks": {"class_type": "KSampler", "inputs": {
			"model": ["cfgn", 0], "positive": ["pos", 0], "negative": ["neg", 0],
			"latent_image": ["enc", 0], "seed": seed, "steps": steps, "cfg": 1.0,
			"sampler_name": "euler", "scheduler": "simple", "denoise": float(denoise)}},
		"dec": {"class_type": "VAEDecode", "inputs": {"samples": ["ks", 0], "vae": ["vae", 0]}},
		"save": {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": prefix}},
	}
	if gan:
		g["upmodel"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": UPSCALE_MODEL}}
		g["gan"] = {"class_type": "ImageUpscaleWithModel", "inputs": {
			"upscale_model": ["upmodel", 0], "image": ["img", 0]}}
		g["scale"]["inputs"]["image"] = ["gan", 0]
	return g


def generate_flux_locked(sprite_png: bytes, *, description: str, seed: int = 0,
                         shape_strength: float = 0.65, steps: int = 4,
                         long_side: int = 1024, timeout: int = 400,
                         protect_silhouette: bool = False):
	"""Restyle freely from the prompt while the ControlNet locks the original outline. Use when you
	want a totally different look (material/colour) but the exact same silhouette."""
	steps = max(1, min(12, int(steps)))
	_rgb, alpha_png, (w, h) = matte_and_size(sprite_png, long_side)
	hint = upload_image(shape_hint(sprite_png, long_side))
	_ensure_family("flux")
	graph = build_flux_cn_graph(hint_name=hint, width=w, height=h, positive=description, seed=seed,
	                            cn_strength=max(0.1, min(1.0, shape_strength)), steps=steps)
	gen = run(graph, timeout=timeout)
	return recut_alpha_rembg(gen, guide_alpha_png=alpha_png,
	                         protect_silhouette=protect_silhouette), (w, h)


def _native_long_side(sprite_png: bytes, floor: int = 64) -> int:
	"""Tight content max-dimension, /8-rounded, floored -- the least-processed size to hand the
	GAN so it (not LANCZOS) does the enlargement in the ``gan`` path."""
	im = Image.open(io.BytesIO(sprite_png)).convert("RGBA")
	bb = im.split()[-1].getbbox()
	if bb:
		im = im.crop(bb)
	return max(floor, round8(max(im.size)))


def generate_qwen_edit(sprite_png: bytes, *, instruction: str, seed: int = 0, px: int = 1024,
                       gan: bool = True, steps: int = 4, model: str | None = None,
                       negative: str = "", long_side: int = 1024, timeout: int = 600,
                       protect_silhouette: bool = False, rembg_model: str | None = None,
                       denoise: float = 1.0):
	"""True image-edit lane: Qwen-Image-Edit-2509 (Q4 GGUF) edits the actual sprite per instruction.
	Structurally faithful -- it can't wander off the subject the way txt2img does.

	``gan`` (default): 4x-UltraSharp pre-upscale for a crisp, non-terraced silhouette. The source
	is matted near-native so the GAN does the enlargement; without it we'd feed the GAN an already
	LANCZOS-blown-up image and lose the point. ``gan=False`` is the plain-LANCZOS backup.

	``protect_silhouette`` (off by default): unions the original sprite's silhouette back onto the
	cutout so rembg can never delete interior detail -- needed for thin weapon blades, where the
	cutter can chop a blade in half. Off by default because for most items (esp. full-cell shapes
	like gems) the original silhouette doesn't line up tightly with the generated art, and the
	floor union then re-introduces a halo/shadow around the object instead of protecting it. Turn
	it on explicitly per-call for items that demonstrably need it.
	"""
	# alpha guide always comes from a decent-res matte (loose reject clamp only, size-agnostic)
	_rgb1k, alpha_png, (w, h) = matte_and_size(sprite_png, long_side)
	if gan:
		rgb_png, _a, _sz = matte_and_size(sprite_png, _native_long_side(sprite_png))
	else:
		rgb_png = _rgb1k
	name = upload_image(rgb_png)
	_ensure_family("qwen")  # Qwen needs the full 24GB; evict only if something else is resident
	graph = build_qwen_edit_graph(image_name=name, prompt=instruction, seed=seed, px=px, gan=gan,
	                              steps=max(1, min(16, int(steps))), model=model,
	                              negative=negative or "", denoise=denoise)
	gen = run(graph, timeout=timeout)
	return recut_alpha_rembg(gen, guide_alpha_png=alpha_png, protect_silhouette=protect_silhouette,
	                         rembg_model=rembg_model), (w, h)
