"""Model / prompt shoot-out for the 'From description' problem.

Goal: figure out why the description lane hallucinates a skull/eye-holes into the plain
Skull Cap, and what combination of MODEL + PROMPT + faithfulness-mechanism keeps it true to
the original art. Runs a matrix against ComfyUI and drops every result into scratchpad so the
report builder (build_report.py) can lay them out side by side.

Lanes tested (see UPSCALE_3D_PANEL_DESIGN.md; these are exploratory, not yet wired into the app):
  L1  SDXL txt2img + tile-CN reference   (Juggernaut-X)  -- the CURRENT description lane
  L2  SDXL img2img (starts from sprite)  (Juggernaut-X)  -- the faithful 'Upscale' mechanism
  L3  Flux.1 schnell img2img            (flux1-schnell) -- strong prompt adherence
  L4  Qwen-Image-Edit-2509              (edit model)    -- edits the real sprite, can't invent

Fixed seed across every cell so a side-by-side differs only by the variable under test.

    python scripts/model_shootout.py            # run the whole matrix
    python scripts/model_shootout.py --smoke     # one gen per new lane (Flux, Qwen) to validate
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))
import comfy  # noqa: E402
import box_mem  # noqa: E402

OUT = os.environ.get("SHOOTOUT_OUT") or os.path.join(os.path.dirname(__file__), "..", "_shootout_out")
OUT = os.path.abspath(OUT)
SEED = 12345

# ---- model file names on the box (verified via /object_info) --------------
FLUX_CKPT = "flux1-schnell-fp8.safetensors"
QWEN_UNET = "qwen_image_edit_2509_fp8_e4m3fn.safetensors"
QWEN_GGUF = "Qwen-Image-Edit-2509-Q4_K_M.gguf"  # ~12GB Q4 -> fits the 3090's 24GB (fp8 OOM'd)
QWEN_CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
QWEN_VAE = "qwen_image_vae.safetensors"
QWEN_LORA = "Qwen-Image-Edit-2509-Lightning-4steps-bf16.safetensors"

# ---- prompt variants ------------------------------------------------------
P_DEFAULT = ("Skull Cap (helm) — Diablo II inventory item. The object appears to be made of metal "
             "or a similar material, with a dark brown color and a rough texture. The surface is "
             "covered in small bumps and grooves, giving it a rough and uneven appearance. The edges "
             "of the object are slightly curved, and there are several small holes scattered "
             "throughout the surface, creating a circular pattern. The overall appearance is that of "
             "a helmet or a helmet with a pointed top and a flat base.")

# name + 'skull'/'Diablo' stripped; 'holes/circular pattern' -> rivets/pitting
P_CLEANED = ("A rounded metal helmet with a smooth domed top and a flat lower edge. Made of dark "
             "brown weathered iron with a rough, pitted texture and small raised bumps. A row of "
             "riveted studs runs along the lower rim. Simple and plain, no ornament, no face.")

P_MINIMAL = ("a plain rounded iron skullcap helmet, smooth dome, weathered dark brown metal, "
             "simple, no ornament")

# instruction phrasing for the edit / img2img lanes
INSTRUCT = ("enhance detail and material realism of this helmet, sharpen the metal texture, keep the "
            "exact same shape and silhouette, do not add a face, eyes, or eye holes")
INSTRUCT2 = ("upscale and add fine hand-painted detail, keep the identical shape and outline, "
             "dark weathered iron helmet, no face")

# house-style suffix the app appends to the description lane (kept so L1 matches production)
HOUSE = ", detailed hand-painted dark fantasy, Diablo II inventory item art"

NEG_STD = comfy.DEFAULT_NEGATIVE
NEG_HARD = (NEG_STD + ", skull, face, human face, eyes, eye holes, eye sockets, visor, skeleton, "
            "demonic, teeth, jaw, monster, creature")


# ---- new graph builders (not yet in comfy.py) -----------------------------

def build_flux_img2img_graph(*, image_name: str, width: int, height: int, positive: str,
                             seed: int, denoise: float, steps: int = 4, prefix: str = "d2flux") -> dict:
    """Flux.1 schnell img2img: VAEEncode the sprite, partial-denoise so the result stays anchored
    to the original silhouette. schnell is guidance-distilled -> cfg 1, no FluxGuidance node."""
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


FLUX_CN = "FLUX.1-dev-ControlNet-Union-Pro-2.0.safetensors"


def shape_hint(sprite_png: bytes, long_side: int = 1024) -> bytes:
    """ControlNet hint (white silhouette outline on black) that pins the shape. Built from the
    ORIGINAL sprite's real alpha -- matte_and_size fills the bg opaque, so its alpha is useless.
    Mirrors that function's crop/scale/center geometry so the ring aligns with the matted canvas."""
    from PIL import Image, ImageFilter, ImageChops
    im = Image.open(io.BytesIO(sprite_png)).convert("RGBA")
    bb = im.split()[-1].getbbox()
    if bb:
        im = im.crop(bb)
    w, h = im.size
    scale = long_side / float(max(w, h))
    nw, nh = comfy.round8(int(w * scale)), comfy.round8(int(h * scale))
    a = im.resize((nw, nh), Image.LANCZOS).split()[-1]
    side = comfy.round8(long_side)
    mask = Image.new("L", (side, side), 0)
    mask.paste(a, ((side - nw) // 2, (side - nh) // 2))
    m = mask.point(lambda v: 255 if v > 24 else 0)                  # hard silhouette
    ring = ImageChops.difference(m.filter(ImageFilter.MaxFilter(7)),
                                 m.filter(ImageFilter.MinFilter(7)))  # dilate - erode = thick outline
    b = io.BytesIO(); ring.convert("RGB").save(b, "PNG"); return b.getvalue()


def build_flux_cn_graph(*, hint_name: str, width: int, height: int, positive: str, seed: int,
                        cn_strength: float = 0.6, steps: int = 4, prefix: str = "d2fluxcn") -> dict:
    """Flux txt2img whose composition is pinned to the original's edges by the Union-Pro ControlNet,
    so the prompt can restyle material/colour while the silhouette stays put."""
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


def run_flux_cn(sprite, *, prompt, cn_strength, seed):
    rgb, alpha, (w, h) = _matte(sprite)
    hint = comfy.upload_image(shape_hint(sprite))
    g = build_flux_cn_graph(hint_name=hint, width=w, height=h, positive=prompt,
                            cn_strength=cn_strength, seed=seed)
    return comfy.run(g, timeout=400)


def build_flux_txt2img_graph(*, width: int, height: int, positive: str, seed: int,
                             steps: int = 4, prefix: str = "d2fluxt") -> dict:
    """Flux.1 schnell txt2img (empty latent, full denoise) — the fully prompt-driven end of the
    spectrum, same checkpoint as the img2img graph."""
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


def run_flux_txt2img(sprite, *, prompt, seed):
    rgb, _a, (w, h) = _matte(sprite)
    g = build_flux_txt2img_graph(width=w, height=h, positive=prompt, seed=seed)
    return comfy.run(g, timeout=400)


def build_qwen_edit_graph(*, image_name: str, prompt: str, seed: int, px: int = 768,
                          shift: float = 3.0, steps: int = 4, prefix: str = "d2qwen") -> dict:
    """Qwen-Image-Edit-2509 with the 4-step Lightning LoRA. Translated 1:1 from ComfyUI's bundled
    template (image_qwen_image_edit_2509.json), switches resolved to the LoRA/4-step/cfg1 path.
    The reference image is carried by TextEncodeQwenImageEditPlus, so it edits the real sprite.

    We scale the reference to a fixed `px` square instead of the template's FluxKontextImageScale
    (~1MP): a 56px inventory icon needs nothing near 1024^2, and the smaller latent keeps sampling
    inside the 3090's 24GB (the ~1MP default OOMs after the 16.6GB model is resident)."""
    return {
        "unet": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": QWEN_GGUF}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": QWEN_CLIP, "type": "qwen_image", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN_VAE}},
        "lora": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["unet", 0], "lora_name": QWEN_LORA, "strength_model": 1.0}},
        "ms": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["lora", 0], "shift": shift}},
        "cfgn": {"class_type": "CFGNorm", "inputs": {"model": ["ms", 0], "strength": 1.0}},
        "img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "scale": {"class_type": "ImageScale", "inputs": {
            "image": ["img", 0], "upscale_method": "lanczos", "width": px, "height": px, "crop": "disabled"}},
        "pos": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
            "clip": ["clip", 0], "vae": ["vae", 0], "image1": ["scale", 0], "prompt": prompt}},
        "neg": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
            "clip": ["clip", 0], "vae": ["vae", 0], "image1": ["scale", 0], "prompt": ""}},
        "enc": {"class_type": "VAEEncode", "inputs": {"pixels": ["scale", 0], "vae": ["vae", 0]}},
        "ks": {"class_type": "KSampler", "inputs": {
            "model": ["cfgn", 0], "positive": ["pos", 0], "negative": ["neg", 0],
            "latent_image": ["enc", 0], "seed": seed, "steps": steps, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["ks", 0], "vae": ["vae", 0]}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": prefix}},
    }


# ---- lane runners ---------------------------------------------------------

def _matte(sprite_png: bytes, long_side: int = 1024):
    return comfy.matte_and_size(sprite_png, long_side)


def run_l1_sdxl_txt_ref(sprite, *, prompt, negative, ref, seed):
    """Current description lane: SDXL txt2img from noise + tile-CN reference."""
    rgb, _a, (w, h) = _matte(sprite)
    ref_name = comfy.upload_image(rgb) if ref > 0.01 else None
    g = comfy.build_sdxl_text_graph(width=w, height=h, positive=prompt + HOUSE, negative=negative,
                                    seed=seed, ref_image_name=ref_name, ref_strength=ref)
    return comfy.run(g, timeout=300)


def run_l2_sdxl_img2img(sprite, *, prompt, negative, denoise, cn, seed):
    """Faithful SDXL 'Upscale' lane: img2img off the sprite + tile CN."""
    rgb, _a, (w, h) = _matte(sprite)
    name = comfy.upload_image(rgb)
    g = comfy.build_sdxl_tile_graph(image_name=name, width=w, height=h, positive=prompt,
                                    negative=negative, seed=seed, denoise=denoise, cn_strength=cn)
    return comfy.run(g, timeout=300)


def run_l3_flux_img2img(sprite, *, prompt, denoise, seed):
    rgb, _a, (w, h) = _matte(sprite)
    name = comfy.upload_image(rgb)
    g = build_flux_img2img_graph(image_name=name, width=w, height=h, positive=prompt,
                                 seed=seed, denoise=denoise)
    return comfy.run(g, timeout=400)


def resilient_run(graph, *, timeout=900, poll=2.0):
    """Like comfy.run but tolerant of the server going briefly unresponsive while a huge model
    loads (the /history poll timing out is NOT a failure — retry until the overall deadline)."""
    import urllib.error
    pid = comfy._submit(graph)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            hist = json.loads(comfy._req(f"/history/{pid}", timeout=20) or b"{}")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            time.sleep(poll)  # server busy loading — keep waiting
            continue
        if pid in hist:
            h = hist[pid]
            if (h.get("status") or {}).get("status_str") == "error":
                raise comfy.ComfyError(f"comfy run error: {json.dumps(h.get('status'))[:400]}")
            if h.get("outputs"):
                return comfy._fetch_first_image(h["outputs"])
        time.sleep(poll)
    raise comfy.ComfyError(f"timed out after {timeout}s waiting for {pid}")


def run_l4_qwen_edit(sprite, *, prompt, seed):
    rgb, _a, (w, h) = _matte(sprite)
    name = comfy.upload_image(rgb)
    g = build_qwen_edit_graph(image_name=name, prompt=prompt, seed=seed)
    return resilient_run(g, timeout=900)


# ---- the matrix -----------------------------------------------------------

def matrix():
    """Each cell: (lane_key, label, callable). Fixed seed everywhere."""
    cells = []

    def add(lane, label, fn):
        cells.append((lane, label, fn))

    # L1 SDXL txt2img + reference (the current mechanism)  -- ref sweep
    for r in (0.3, 0.6, 0.9):
        add("L1 SDXL txt2img+ref", f"default  ref={r}",
            lambda s, r=r: run_l1_sdxl_txt_ref(s, prompt=P_DEFAULT, negative=NEG_STD, ref=r, seed=SEED))
    for r in (0.3, 0.6, 0.9):
        add("L1 SDXL txt2img+ref", f"cleaned  ref={r}",
            lambda s, r=r: run_l1_sdxl_txt_ref(s, prompt=P_CLEANED, negative=NEG_STD, ref=r, seed=SEED))
    for r in (0.3, 0.6, 0.9):
        add("L1 SDXL txt2img+ref", f"minimal  ref={r}",
            lambda s, r=r: run_l1_sdxl_txt_ref(s, prompt=P_MINIMAL, negative=NEG_STD, ref=r, seed=SEED))
    for r in (0.6, 0.9):
        add("L1 SDXL txt2img+ref", f"default+hardneg  ref={r}",
            lambda s, r=r: run_l1_sdxl_txt_ref(s, prompt=P_DEFAULT, negative=NEG_HARD, ref=r, seed=SEED))

    # L2 SDXL img2img (faithful upscale)  -- denoise sweep, cn 0.7
    for d in (0.35, 0.5, 0.65):
        add("L2 SDXL img2img", f"empty prompt  denoise={d}",
            lambda s, d=d: run_l2_sdxl_img2img(s, prompt="", negative=NEG_STD, denoise=d, cn=0.7, seed=SEED))
    for d in (0.35, 0.5, 0.65):
        add("L2 SDXL img2img", f"cleaned  denoise={d}",
            lambda s, d=d: run_l2_sdxl_img2img(s, prompt=P_CLEANED, negative=NEG_STD, denoise=d, cn=0.7, seed=SEED))

    # L3 Flux.1 schnell img2img  -- denoise sweep
    for d in (0.35, 0.5, 0.65):
        add("L3 Flux img2img", f"cleaned  denoise={d}",
            lambda s, d=d: run_l3_flux_img2img(s, prompt=P_CLEANED, denoise=d, seed=SEED))
    add("L3 Flux img2img", "minimal  denoise=0.5",
        lambda s: run_l3_flux_img2img(s, prompt=P_MINIMAL, denoise=0.5, seed=SEED))
    add("L3 Flux img2img", "default(trap)  denoise=0.5",
        lambda s: run_l3_flux_img2img(s, prompt=P_DEFAULT, denoise=0.5, seed=SEED))

    # L4 Qwen-Image-Edit  -- OFF by default: the 20B model's fp8 sampling peak exceeds the 3090's
    # 24GB on Ampere (OOMs even at 768px + --lowvram with VRAM fully free). Needs a GGUF Q4 quant
    # + the ComfyUI-GGUF loader to fit. Set SHOOTOUT_QWEN=1 to include it anyway.
    if os.environ.get("SHOOTOUT_QWEN") != "1":
        return cells
    add("L4 Qwen edit", "instruct: keep shape, no face",
        lambda s: run_l4_qwen_edit(s, prompt=INSTRUCT, seed=SEED))
    add("L4 Qwen edit", "instruct2: upscale+detail",
        lambda s: run_l4_qwen_edit(s, prompt=INSTRUCT2, seed=SEED))
    add("L4 Qwen edit", "cleaned-as-instruction",
        lambda s: run_l4_qwen_edit(s, prompt=P_CLEANED, seed=SEED))
    add("L4 Qwen edit", "minimal",
        lambda s: run_l4_qwen_edit(s, prompt=P_MINIMAL, seed=SEED))

    return cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sprite", default=os.path.join(OUT, "..", "skullcap_original.png"))
    ap.add_argument("--smoke", action="store_true", help="one gen per new lane to validate graphs")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    with open(a.sprite, "rb") as f:
        sprite = f.read()
    print("COMFY_URL =", comfy.COMFY_URL)
    print("sprite    =", os.path.abspath(a.sprite), len(sprite), "bytes")

    if a.smoke:
        cells = [
            ("L3 Flux img2img", "smoke cleaned d=0.5",
             lambda s: run_l3_flux_img2img(s, prompt=P_CLEANED, denoise=0.5, seed=SEED)),
            ("L4 Qwen edit", "smoke instruct",
             lambda s: run_l4_qwen_edit(s, prompt=INSTRUCT, seed=SEED)),
        ]
    else:
        cells = matrix()

    print("box:", box_mem.snapshot())
    manifest = []

    def run_cell(i, lane, label, fn):
        tag = f"{i:02d}_{lane.split()[0]}"
        print(f"[{i+1}/{len(cells)}] {lane} :: {label}", flush=True)
        t = time.time()
        try:
            png = fn(sprite)
            path = os.path.join(OUT, f"{tag}.png")
            with open(path, "wb") as f:
                f.write(png)
            dt = time.time() - t
            print(f"    ok {dt:.1f}s -> {path}", flush=True)
            manifest.append({"i": i, "lane": lane, "label": label, "file": os.path.basename(path),
                             "secs": round(dt, 1), "ok": True})
        except Exception as e:  # noqa: BLE001
            dt = time.time() - t
            print(f"    FAIL {dt:.1f}s :: {e}", flush=True)
            manifest.append({"i": i, "lane": lane, "label": label, "error": str(e),
                             "secs": round(dt, 1), "ok": False})
        with open(os.path.join(OUT, "manifest.json"), "w") as f:
            json.dump(manifest, f, indent=2)

    # Group by lane so we can evict VRAM between model families (graceful memory mgmt) and wrap
    # the heavy Qwen group in a host-RAM reclaim.
    order, groups = [], {}
    for i, (lane, label, fn) in enumerate(cells):
        if lane not in groups:
            groups[lane] = []; order.append(lane)
        groups[lane].append((i, lane, label, fn))

    for lane in order:
        comfy.free_memory()  # evict the previous family's models from VRAM before this lane
        is_qwen = lane.startswith("L4")
        if is_qwen:
            print(f"=== heavy lane {lane}: reclaiming host RAM ===", flush=True)
            with box_mem.reclaim_ram(need_free_gb=24):
                print("box:", box_mem.snapshot(), flush=True)
                for cell in groups[lane]:
                    run_cell(*cell)
            comfy.free_memory()  # drop the 20GB Qwen model right after its group
        else:
            for cell in groups[lane]:
                run_cell(*cell)
    print("done ->", OUT)


if __name__ == "__main__":
    main()
