# ComfyUI workflows — fidelity-lab methods m0–m7

Importable ComfyUI workflows for the Qwen-Image-Edit generation core of each lab
method (drag the .json into the ComfyUI web UI at http://10.0.10.30:8188).

All eight share the identical production graph (GGUF Qwen-Image-Edit-2509 + 4-step
Lightning LoRA, AuraFlow shift 3.0, CFGNorm, 4x-UltraSharp GAN pre-upscale, euler/simple,
cfg 1.0, denoise 1.0) — the METHOD lives in the prompt/negative text and in Python
pre/post steps that cannot run inside ComfyUI:

| method | prompt | negative | Python pre | Python post |
|---|---|---|---|---|
| m0 baseline   | house style only        | —    | —          | re-cut + fit + quantize |
| m1 anchor     | identity + house        | —    | —          | 〃 |
| m2 catstyle   | identity + gem/glow     | anti | —          | 〃 |
| m3 anchor+CT  | identity + house        | —    | —          | + Oklab color transfer |
| m4 cat+CT     | identity + gem/glow     | anti | —          | + Oklab color transfer |
| m5 pad        | house style only        | —    | margin pad | 〃 (no CT) |
| m6 combo      | identity + gem/glow     | anti | margin pad | 〃 (no CT) |
| m7 combo+CT   | identity + gem/glow     | anti | margin pad | + Oklab color transfer |

Replace `<IDENTITY …>` in the positive prompt with one literal sentence describing the
original sprite (auto-captioned by Florence-2 in the studio pipeline). The LoadImage
node expects the gray-matted sprite the studio uploads (app/comfy.matte_and_size); when
experimenting by hand, any small item image on a plain gray background behaves the same.

The Note node inside each workflow documents the exact Python steps around the graph.
Production entry points: app/comfy.generate_qwen_edit (graph) + scripts/fidelity_lab.py
(pad_sprite, color_transfer, end_to_end scoring).
