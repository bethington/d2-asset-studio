"""Qwen-Image-Edit settings sweep for the asset-studio enhance lane.

Goal: find the Qwen-Edit settings that kill the blocky/pixelated silhouette we're seeing
in the live gallery. The prime suspect is the missing GAN pre-upscale (design doc calls it
"non-negotiable"): the live path LANCZOS-blows a ~58px sprite up ~13x, and Qwen faithfully
preserves the terraced edge. This sweep renders the same items under several settings so a
human can pick the winner.

Runs SERIALLY (Qwen needs the full 24GB). Writes PNGs + an incremental manifest to
<out>/ so build_qwen_sweep_report.py can turn it into an HTML report.

    python scripts/qwen_settings_sweep.py [--out DIR] [--items id,id,...]
"""
from __future__ import annotations
import argparse, io, json, os, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "app")
sys.path.insert(0, APP)
import comfy  # noqa: E402
from PIL import Image  # noqa: E402

STUDIO = os.environ.get("STUDIO_URL", "http://127.0.0.1:5001").rstrip("/")
SEED = 12345
CELL_PX = 29  # matches assets.CELL_PX; final in-game canvas = cells * CELL_PX

# The house enhance prompt actually used by the live ✨ Enhance button.
PROMPT = comfy.qwen_enhance_instruction()

# --- item archetypes (variety: compact / thin / large) -------------------
DEFAULT_ITEMS = [
    ("base/armor/skp", "Skull Cap",   2, 2),  # the actual complaint
    ("base/armor/hgl", "Gauntlets",   2, 2),  # metal glove detail
    ("base/armor/mbt", "Chain Boots", 2, 2),
    ("base/weapons/flc", "Falchion",  1, 3),  # thin silhouette stress test
    ("base/armor/plt", "Plate Mail",  2, 3),  # large body armor
]

# --- settings matrix -----------------------------------------------------
# prep: how the source is fed in.  "matte1024" = current live (LANCZOS->1024, no GAN).
#       "native" = matte near-native so the in-graph 4x-UltraSharp GAN does the upscaling.
VARIANTS = [
    dict(key="baseline",    label="Baseline (live)",   prep="matte1024", gan=False, px=768,  gguf="Qwen-Image-Edit-2509-Q6_K.gguf", steps=4),
    dict(key="gan768",      label="GAN pre-upscale",   prep="native",    gan=True,  px=768,  gguf="Qwen-Image-Edit-2509-Q6_K.gguf", steps=4),
    dict(key="gan1024",     label="GAN + 1024",        prep="native",    gan=True,  px=1024, gguf="Qwen-Image-Edit-2509-Q6_K.gguf", steps=4),
    dict(key="hires1024",   label="1024, no GAN",      prep="matte1024", gan=False, px=1024, gguf="Qwen-Image-Edit-2509-Q6_K.gguf", steps=4),
    dict(key="ganQ8_1024",  label="GAN + 1024 + Q8",   prep="native",    gan=True,  px=1024, gguf="Qwen-Image-Edit-2509-Q8_0.gguf", steps=4),
]


def fetch_source(item_id: str) -> bytes:
    url = f"{STUDIO}/api/item/{urllib.request.quote(item_id)}/original.png"
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


def native_long_side(sprite_png: bytes) -> int:
    """Tight content max-dimension, /8-rounded, floored at 64 — the least-processed size to
    hand the GAN so it (not LANCZOS) does the enlargement."""
    im = Image.open(io.BytesIO(sprite_png)).convert("RGBA")
    bb = im.split()[-1].getbbox()
    if bb:
        im = im.crop(bb)
    return max(64, comfy.round8(max(im.size)))


def build_graph(*, image_name: str, px: int, gguf: str, gan: bool, steps: int) -> dict:
    """Clone of comfy.build_qwen_edit_graph with the GGUF quant swapped and an optional
    in-graph 4x-UltraSharp GAN pre-upscale inserted before the LANCZOS resize."""
    g = {
        "unet": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": gguf}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": comfy.QWEN_CLIP, "type": "qwen_image", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": comfy.QWEN_VAE}},
        "lora": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["unet", 0], "lora_name": comfy.QWEN_LORA, "strength_model": 1.0}},
        "ms": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["lora", 0], "shift": 3.0}},
        "cfgn": {"class_type": "CFGNorm", "inputs": {"model": ["ms", 0], "strength": 1.0}},
        "img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "scale": {"class_type": "ImageScale", "inputs": {
            "image": ["img", 0], "upscale_method": "lanczos", "width": px, "height": px, "crop": "disabled"}},
        "pos": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
            "clip": ["clip", 0], "vae": ["vae", 0], "image1": ["scale", 0], "prompt": PROMPT}},
        "neg": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
            "clip": ["clip", 0], "vae": ["vae", 0], "image1": ["scale", 0], "prompt": ""}},
        "enc": {"class_type": "VAEEncode", "inputs": {"pixels": ["scale", 0], "vae": ["vae", 0]}},
        "ks": {"class_type": "KSampler", "inputs": {
            "model": ["cfgn", 0], "positive": ["pos", 0], "negative": ["neg", 0],
            "latent_image": ["enc", 0], "seed": SEED, "steps": steps, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["ks", 0], "vae": ["vae", 0]}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": "d2qsweep"}},
    }
    if gan:
        g["upmodel"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": comfy.UPSCALE_MODEL}}
        g["gan"] = {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["upmodel", 0], "image": ["img", 0]}}
        g["scale"]["inputs"]["image"] = ["gan", 0]
    return g


def canonical_and_native(master_png: bytes, iw: int, ih: int) -> tuple[bytes, bytes]:
    """(canonical 2x-cell PNG = what the gallery tile shows, native 1x-cell PNG = the DC6)."""
    canon = comfy.to_canonical_2x(master_png, (iw * CELL_PX, ih * CELL_PX))
    im = Image.open(io.BytesIO(canon)).convert("RGBA").resize((iw * CELL_PX, ih * CELL_PX), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, "PNG")
    return canon, b.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.environ.get("SCRATCH", HERE), "qwen_sweep"))
    ap.add_argument("--items", default="")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    items = DEFAULT_ITEMS
    if args.items:
        want = set(args.items.split(","))
        items = [it for it in DEFAULT_ITEMS if it[0] in want]

    manifest = {"prompt": PROMPT, "seed": SEED, "variants": VARIANTS, "items": [], "renders": []}
    print(f"[sweep] {len(items)} items x {len(VARIANTS)} variants -> {args.out}", flush=True)

    for item_id, name, iw, ih in items:
        src = fetch_source(item_id)
        sp = os.path.join(args.out, f"src_{item_id.replace('/', '_')}.png")
        with open(sp, "wb") as f:
            f.write(src)
        nl = native_long_side(src)
        manifest["items"].append(dict(id=item_id, name=name, iw=iw, ih=ih, native=nl,
                                      src=os.path.basename(sp)))
        # alpha guide (decent-res, used only as a loose reject clamp in recut)
        _rgb1k, alpha_guide, _ = comfy.matte_and_size(src, 1024)

        for v in VARIANTS:
            tag = f"{item_id.split('/')[-1]}_{v['key']}"
            rec = dict(item=item_id, variant=v["key"])
            t0 = time.time()
            try:
                comfy.free_memory()
                if v["prep"] == "native":
                    rgb, _a, _sz = comfy.matte_and_size(src, nl)
                else:
                    rgb, _a, _sz = comfy.matte_and_size(src, 1024)
                name_up = comfy.upload_image(rgb)
                graph = build_graph(image_name=name_up, px=v["px"], gguf=v["gguf"],
                                    gan=v["gan"], steps=v["steps"])
                gen = comfy.run(graph, timeout=900)
                master = comfy.recut_alpha_rembg(gen, guide_alpha_png=alpha_guide)
                canon, native = canonical_and_native(master, iw, ih)
                for suffix, data in (("master", master), ("canon", canon), ("native", native)):
                    fn = f"{tag}_{suffix}.png"
                    with open(os.path.join(args.out, fn), "wb") as f:
                        f.write(data)
                    rec[suffix] = fn
                rec["ok"] = True
                rec["secs"] = round(time.time() - t0, 1)
                print(f"[ok]   {tag}  {rec['secs']}s", flush=True)
            except Exception as e:  # noqa: BLE001
                rec["ok"] = False
                rec["error"] = str(e)
                print(f"[FAIL] {tag}  {e}", flush=True)
            manifest["renders"].append(rec)
            with open(os.path.join(args.out, "manifest.json"), "w") as f:
                json.dump(manifest, f, indent=2)

    print(f"[sweep] done. manifest -> {os.path.join(args.out, 'manifest.json')}", flush=True)


if __name__ == "__main__":
    main()
