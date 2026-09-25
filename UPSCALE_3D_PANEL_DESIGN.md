# AI Upscale → 3D Workflow Panel — Design

Goal: a single top-to-bottom workflow, opened as a **slide-over panel** from the item gallery,
that takes an item's original art through: AI detail-filling upscale (with re-rolls and prompted
variation) → Meshy image-to-3D (up to 8 re-rolls) → texture generation → GLB download → final
Blender render framed like the original game art → DC6 alternate (downsized to the original art
size) → activate.

All decisions below were ratified in the planning session (2026-07-21).

---

## 1. Ratified decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Upscaler backend | **New ComfyUI backend** (replaces png-upscale-service for this flow) |
| 2 | ComfyUI host | **Home docker box** `10.0.10.30:8188` (RTX 3090 24GB; container already running, ComfyUI 0.28.0 at `/home/ben/services/comfyui`) |
| 3 | Exposed controls | **Core set**: Fidelity slider (tile-ControlNet weight), Creativity slider (denoise), variation prompt + negative prompt, seed lock/dice, style-preset dropdown |
| 4 | Resolution policy | **Hi-res master + 2× canonical**: generate at ~1024px working res, keep as internal master; LANCZOS-downsample to **exactly 2× original art pixels** as the canonical stored/displayed art. Meshy is fed from the hi-res master. DC6 build downsizes the 2× art to the original size. |
| 5 | Model stack | **Two lanes** (researched, see §3): SDXL + ControlNet-Union-ProMax(tile) + DMD2 as the fast default; Qwen-Image-Edit-2509 + Lightning as the structural-variation lane |
| 6 | Panel architecture | ~~New purpose-built wizard panel, shared backend (`/api/studio/*` reused/extended). Detail rail widened to ~480–520px; `/studio` page stays as the power tool (mask brush) for now~~ **SUPERSEDED 2026-07-27**: the panel is now a persistent **column 3 ("Generate")** in the gallery's 3-column layout (1 Gallery / 2 Details / 3 Generate), not a slide-over — it auto-populates for whatever item is selected. `/studio` was deleted entirely; its one unique capability, the glove single-hand mask brush, was ported inline into column 3's §3 (3D model) step for glove items. `/api/studio/*` backend is unchanged/still shared. |
| 7 | Item scope | **All items**; special-case gloves (existing mask→mirror→pair pipeline) and boots (new auto-split pipeline) |
| 8 | Item descriptions | **Local vision model via Ollama** (pull Qwen2.5-VL 7B onto the existing Ollama at `10.0.10.30:11434`); auto-caption into an editable field, saved per item — future input for generating wholly unique items |
| 9 | Re-roll UX | **History strip**: every upscale generation kept as a thumbnail with seed+settings; click to select which flows to Meshy; per-thumbnail delete |
| 10 | Boots | **Auto-split** (alpha gap/connected-component) → A/B: 2-image `imageIds` Meshy request first, **mirror fallback** (glove recipe) if fusion disappoints. *(2026-07-27: the planned manual mask-brush fallback for a failed auto-split was never built — only gloves got the ported brush tool; a failed split today just says so and suggests the single-boot+mirror option.)* |
| 11 | Finalize | **Save as named alternate + manual Activate** (existing variants+activate pattern); no auto-push |

Meshy constraints honored: re-rolls limited by Meshy's plan allowance (8; already surfaced as
`8 − retryCount` via `retry_task`); texture generation is a separate user-triggered step; GLB
download happens **after texture completes** (`find_textured_children` so we never bake grey clay).

---

## 2. Panel flow (top → bottom = first step → last step)

Opens sliding over the item-detail rail when the user clicks "Upscale → 3D" in the detail panel
(`app.js` add-alternate row). Rail width increases to ~500px for both panels.

```
┌─ ① SOURCE ──────────────────────────────┐
│ original art (zoomed), name, cells/px   │
│ description (auto-captioned, editable,  │
│   [Save] [Re-caption])                  │
│ type-specific prep status:              │
│   gloves: mask state (→ open brush)     │
│   boots:  auto-split preview L/R        │
│   other:  none needed                   │
├─ ② UPSCALE (AI detail fill) ────────────┤
│ style preset ▾  engine: auto (SDXL/Qwen)│
│ Fidelity ────────●──   (tile CN weight) │
│ Creativity ──●───────  (denoise)        │
│ variation prompt  [.............]       │
│ negative prompt   [.............]       │
│ seed [123456] 🔒/🎲       [Generate]    │
│ ▸ history strip: [▣][▣][▣][▣]  (select │
│   one → flows down; ✕ delete; hover =   │
│   seed+settings)                        │
│ selected art @ exactly 2× original px   │
├─ ③ 3D MODEL (Meshy) ────────────────────┤
│ feed: single image / 2-image (boots)    │
│ [Generate 3D]   re-rolls left: 8        │
│ live three.js draft viewer              │
│ [Re-roll] (until satisfied)             │
├─ ④ TEXTURE ─────────────────────────────┤
│ texture prompt (prefilled from ② prompt │
│   + description)   [Generate texture]   │
│ → on complete: auto-download GLB(s)     │
├─ ⑤ FINAL RENDER & SHIP ─────────────────┤
│ pose tuner (shared pair3d controls);    │
│   gloves/boots: pair composed, one side │
│   mirrored                              │
│ live browser preview framed like the    │
│   original game art                     │
│ [Blender final render] → DC6 built at   │
│   original art size → saved as alternate│
│ [Activate]  (existing pattern)          │
└─────────────────────────────────────────┘
```

Each section header shows a state chip (empty / in-progress / done) so the panel reads as a
checklist; a section is enabled once the one above has a selection.

---

## 3. AI stack (researched 2026-07-21)

### Lane A — default: SDXL creative upscale (~5–10s/gen on the 3090)
GAN pre-upscale → SDXL img2img + xinsir **ControlNet-Union-ProMax (tile mode)** + **DMD2 4-step
LoRA**. Tile CN strength = Fidelity slider; denoise = Creativity slider.
- Faithful enhance: denoise 0.35–0.5, tile strength 0.6–0.8, short auto-prompt
  ("detailed painted fantasy leather glove, dark fantasy, Diablo style").
- Stylistic variation: denoise 0.6–0.8, tile ~0.35 (or swap to canny ~0.5 for silhouette-lock),
  user prompt dominant. Optional IP-Adapter-plus @ ~0.4 to anchor palette at high denoise.

### Lane B — structural/semantic variation: Qwen-Image-Edit-2509 (~20–35s/gen)
GGUF Q4 + Lightning 4-step LoRA. For prompts that change *what the item is*
("fire-themed clawed gauntlet, keep silhouette"). 3090 has no FP8 cores → GGUF, not fp8.

**Routing:** engine defaults to auto — Lane A always; Lane B when the variation prompt requests
structural/semantic change (heuristic + a manual engine override in the preset row).

### Pixel-art handling (both lanes, non-negotiable)
1. Never feed the raw 60–200px sprite to the sampler — **GAN pre-upscale first**
   (4x-UltraSharp / RealESRGAN-x4plus-anime → ≥800px, lanczos to 1024).
2. **Matte onto neutral gray** before diffusion (transparent PNGs carry garbage RGB under
   alpha=0); white-bg copy for Meshy.
3. **Re-cut alpha after generation with BiRefNet** (ComfyUI-RMBG node) — better edges than rembg.

### Shopping list (one-time, ~35GB into `/home/ben/services/comfyui/data/models/`)
- DreamShaperXL v2.1 Turbo **or** reuse installed Juggernaut X (~6.9GB, Civitai)
- xinsir/controlnet-union-sdxl-1.0 ProMax (~2.5GB, HF)
- DMD2 SDXL 4-step LoRA (~0.8GB, HF tianweiy/DMD2)
- 4x-UltraSharp + RealESRGAN-x4plus-anime (~85MB)
- Qwen-Image-Edit-2509 GGUF Q4_K_M (~12.1GB) + Qwen2.5-VL-7B TE fp8-scaled (~9.4GB) + Qwen VAE
- Qwen-Image-Edit-2509-Lightning-4steps LoRA (~0.85GB)
- Optional: IP-Adapter-plus_sdxl_vit-h + CLIP-ViT-H (~3.4GB)

**Custom nodes:** ComfyUI_UltimateSDUpscale, comfyui_controlnet_aux, ComfyUI-RMBG, ComfyUI-GGUF,
(optional) ComfyUI_IPAdapter_plus.
**VRAM:** Lane A ~11–13GB, Lane B ~14–18GB — both fit 24GB; never co-load.

### Descriptions (Ollama)
`ollama pull qwen2.5-vl:7b` on the box. Caption prompt asks for: item identity, materials,
shape/silhouette, palette, condition, style adjectives — a template usable later as a
generation prompt for wholly-new items.

---

## 4. Backend work

### New module: `app/comfy.py`
Thin ComfyUI API client (`COMFY_URL`, default `http://10.0.10.30:8188`):
- `submit(workflow_json, inputs) → prompt_id` (POST `/prompt`)
- progress via `/history/{prompt_id}` polling (matches the app's existing poll-based job model —
  no background queue needed; gens are 5–35s)
- image upload (POST `/upload/image`) and output fetch (`/view`)
- workflow templates stored as API-format JSON in `app/comfy_workflows/`:
  `sdxl_tile_upscale.json`, `sdxl_variation.json`, `qwen_edit.json` — parameters (denoise,
  cn_strength, prompt, negative, seed, image) patched into the JSON by node id.

### New module: `app/describe.py`
Ollama vision client (`OLLAMA_URL`, default `http://10.0.10.30:11434`): caption(image, template)
→ text. Storage: `<workspace>/descriptions.json` keyed by item id:
`{text, source ("qwen2.5-vl"|"user"), edited: bool, ts}`.

### New module: `app/boot_split.py`
Alpha-plane connected-component / vertical-gap split → `(left_png, right_png, confidence)`.
Low confidence → panel routes user to the mask brush (existing masks infra, reused per-side).

### New/extended endpoints (`server.py`)
- `POST /api/upscale/generate` — item id + {engine, preset, fidelity, creativity, prompt,
  negative, seed, source (full art | masked hand | boot L/R)} → runs prep (GAN pre-upscale,
  matte) → ComfyUI → BiRefNet re-cut → saves **master** (1024) + **canonical 2×** → variant id
- `GET /api/upscale/variants/<item>` / `DELETE /api/upscale/variant/<vid>` /
  `POST /api/upscale/select` — history strip
- `POST /api/describe/<item>` / `PUT /api/describe/<item>` — caption / save edit
- `POST /api/boots/split/<item>` — auto-split preview
- `POST /api/studio/generate` — extend to accept explicit image payload(s) and
  **`imageIds` list** (boots 2-image mode); rest of the Meshy chain (`reroll`, `texture`,
  `task`, `accept`, glb proxy) is reused as-is
- Finalize: reuse `/api/pair/build3d/blender` (gloves/boots) and the single-item accept path;
  DC6 build stays `png_to_item_dc6` (downsizes to original cell size per decision #4)

### Storage layout
```
<workspace>/upscales/<item_id>/
  v<N>-<seed>-master.png   (1024 hi-res, white-bg copy for Meshy kept alongside)
  v<N>-<seed>.png          (canonical, exactly 2× original px, alpha re-cut)
  meta.json                (per-variant: engine, preset, sliders, prompts, seed, ts, selected)
```

---

## 5. Frontend work

- `style.css`: `.detail` width 340 → ~500px; new `.workflow-panel` that slides over the detail
  rail (absolute overlay inside the rail area, translateX transition, ESC / ← back to detail).
- New `app/static/workflow.js` (+ template in `index.html`): the 5-section wizard; section
  state machine; reuses `pair3d.js` for the draft viewer + pose tuner + framed preview
  (already built to match Blender output).
- Entry point: "Upscale → 3D" button in the detail panel's add-alternate row.
- Meshy pieces (re-roll count, texture button, task polling) ported from `studio.js` patterns.

---

## 6. Build phases

**Phase A — server prep + engines proven (no UI).**
Download models + custom nodes into the ComfyUI container; `ollama pull qwen2.5-vl:7b`;
build the 3 workflow JSONs; `app/comfy.py` + a CLI harness; prove faithful + variation lanes
on sample sprites (a glove, a boot pair, a helm) end-to-end incl. BiRefNet re-cut and 2×
canonical output. *Exit: side-by-side sheet of originals vs upscales that we're happy with.*

**Phase B — panel skeleton + upscale stage.**
Rail widening + slide-over; sections ① + ②; describe endpoints + editable caption; history
strip with select/delete; storage layout. *Exit: pick item → caption → generate/re-roll →
select variant, all in the panel.*

**Phase C — 3D + texture + finalize (single-object items + gloves).**
Sections ③–⑤ wired to existing Meshy endpoints (draft, re-roll ×8, texture, GLB after texture,
pose tuner, Blender render, DC6 alternate + Activate). Gloves use the existing masked-hand →
mirror pipeline fed by the new upscaler. *Exit: helm and glove taken start-to-finish inside
the panel.*

**Phase D — boots.**
`boot_split.py` + split preview UI; 2-image `imageIds` Meshy A/B vs mirror fallback; pair
composition for boots in the finalize stage. *Exit: a boot item shipped both ways, winner noted.*

---

## 6b. Phase A results (2026-07-22) — Lane A PROVEN

Lane A (SDXL tile creative-upscale) runs end-to-end and the exit criterion is met: clean
side-by-side sheets for all three archetypes (helm / glove / boot). Artifacts in the session
scratchpad `phaseA/` + `phaseA/birefnet/`.

**What shipped this phase**
- `app/comfy.py` — ComfyUI client (upload/submit/poll/fetch) + Python-side geometry (matte on
  gray, /8 working canvas, alpha re-cut) + `build_sdxl_tile_graph()` + `upscale_faithful()`.
  Lane A uses **only base ComfyUI nodes** (no custom nodes): CheckpointLoaderSimple, LoraLoader,
  UpscaleModelLoader, ImageUpscaleWithModel, ImageScale, ControlNetLoader,
  SetUnionControlNetType(type=tile), ControlNetApplyAdvanced, VAEEncode, KSampler, VAEDecode.
- `scripts/comfy_prove.py` — proof harness (`--trio`) that renders originals-vs-upscale sheets.
- `app/describe.py` — Ollama vision captioner + `descriptions.json` store (built; see constraint).
- Models installed on the box (`/home/ben/services/comfyui/data/models/`): reused **Juggernaut-X**
  checkpoint; downloaded `controlnet-union-sdxl-promax` (2.4G), `dmd2_sdxl_4step_lora_fp16` (376M),
  `4x-UltraSharp.pth` (64M). Lane B (Qwen) downloaded too: `Qwen-Image-Edit-2509-Q4_K_M.gguf`
  (13G) + qwen TE/VAE/Lightning-LoRA — **Lane B not yet wired** (needs ComfyUI-GGUF custom node).

**Tuning that worked:** Juggernaut-X, DMD2 LoRA @1.0, `lcm`/`sgm_uniform`, 8 steps, cfg 1.0,
denoise 0.45 (Fidelity), tile ControlNet strength 0.7. Short "detailed hand-painted fantasy …,
Diablo II inventory item art" prompt + a fixed junk negative.

**Speed:** cold run 84s (first VRAM load); **warm generations 10–13s** — in the re-roll target.

**Alpha cutter — settled on BiRefNet.** Reusing the 56px original alpha is instant but blocky;
`rembg isnet-general-use` tore the silhouette (photo-trained). **`rembg birefnet-general`
(973MB) gives clean native-res edges** — it's the cutter. A loose reject-only guard (heavily
dilated+blurred original alpha, `min`-clamped) stops far-away hallucinated blobs without
chopping the clean edge; `_defringe()` bleeds edge colour under the matte so no gray halo.
Boots came out cleanly **separated by a gap → confirms the auto-split plan (decision #10).**

**Two open constraints found (fold into later phases):**
1. **BiRefNet on CPU is ~35s/image.** Too slow per re-roll. Plan: during re-roll show the instant
   alpha-reuse preview; run the BiRefNet cut once on the **selected** image. Better: run BiRefNet
   as a **ComfyUI GPU node (ComfyUI-RMBG)** folded into the gen graph (~1–2s) — do this when the
   custom_nodes bind mount goes in for Lane B. (`app/comfy.py:recut_alpha_rembg` is the CPU path.)
2. **Ollama has no GPU access** (`ollama ps` → 100% CPU; deliberate, so it doesn't fight ComfyUI
   for the 3090 per `gpu-mode.sh`). Captions are minutes each on CPU — too slow for interactive
   use. **DECISION (2026-07-22): GPU-time the caption model.** Phase B swaps `describe.py`'s
   backend off Ollama-CPU to a GPU path — either a gpu-mode-style toggle that gives Ollama the
   3090, or run **Qwen2.5-VL through ComfyUI** (GPU-backed, no separate service). Keeps captioning
   local/free and fast for the structured descriptions that seed future unique-item generation.
   `describe.py` (Ollama client) stays as the CPU/batch fallback.

**Infra notes for later phases:**
- ComfyUI: `comfyui-comfyui` image, `/opt/ComfyUI`, cmd `main.py --listen 0.0.0.0 --port 8188`.
  Host mounts only `data/{models,input,output,user}`. **`custom_nodes` is NOT mounted** — for
  Lane B, add `./data/custom_nodes:/opt/ComfyUI/custom_nodes` to `docker-compose.yml` (copy the
  baked-in ComfyUI-Florence2 out to the host dir first) and bake node pip-deps into the Dockerfile.
- **GPU is shared 1-at-a-time** (`gpu-mode.sh`: ComfyUI vs whisper/png-upscale/f5tts). Don't
  recreate the ComfyUI container while a generation is running.
- Host CPU lacks AVX2 (kornia removed) → avoid kornia-backed preprocessors (canny). Tile CN needs
  none, so Lane A is unaffected; if canny silhouette-lock is wanted later, generate it with cv2.
- `rembg[cpu]` + `onnxruntime` installed into the studio Python; numpy pinned `<2.3` for opencv.

---

## 6c. Phase B results (2026-07-22) — panel + upscale stage DONE

Exit criterion met, verified in the real browser (Playwright): **pick item → caption → generate
→ re-roll → select variant, all in the slide-over panel.**

**Shipped**
- Backend (`app/server.py`, new section): `GET /api/upscale/<id>/state`,
  `POST /api/upscale/<id>/generate`, `GET …/variant/<vid>.png` (`?master=1` for the hi-res),
  `POST …/select`, `DELETE …/variant/<vid>`, `POST /api/describe/<id>` (async caption job with
  polled status), `PUT /api/describe/<id>` (save edit). Generation is synchronous (~11–45s incl.
  BiRefNet); captions run in a background thread (`_CAPTION_JOBS`).
- `app/upscale_store.py` — variant storage `<ws>/upscales/<item_key>/{<vid>-master.png,<vid>.png,
  index.json}`, newest auto-selected.
- `app/static/workflow.js` + CSS in `style.css` (`.workflow-panel` 500px slide-over) — the
  5-section top-to-bottom wizard. §1 Source+editable description, §2 Upscale (Fidelity/Creativity
  sliders = CN-strength/denoise, prompt, seed+dice, Generate, history strip with select/delete,
  big selected preview). §3–5 stubbed disabled ("Phase C").
- Entry point: **✨ Upscale → 3D…** button in the detail panel's add-alternate row (`app.js`).

**Verified:** state endpoint (raw + %2F-encoded item ids both route), generate stores master
(1024) + canonical (2× cell-grid, e.g. 116×116 for a 2×2 item) + full meta; UI re-roll adds a
2nd variant; no JS console errors.

**Notes / small follow-ups:**
- **Caption GPU path still pending** (decision was "GPU-time the caption model"). The endpoint
  works but runs on CPU Ollama (minutes). Implement before this is pleasant to use — cleanest
  non-disruptive route is Qwen2.5-VL as a ComfyUI GPU node (lands with the Lane-B custom_nodes
  work); giving the shared `ollama` container GPU touches open-webui etc., so prefer the ComfyUI
  route. `describe.py` stays the pluggable backend.
- **Canonical size uses cell-grid (`invwidth*29`), not the DC6 art frame.** "2× original" is
  currently 2× the 58px cell canvas (→116), vs the 56px art frame. DC6 build downsizes anyway;
  revisit if exact-frame doubling matters.
- Old `/studio` page + `png-upscale-service` untouched.

**Next: Phase C** — wire §3–5 to the existing Meshy endpoints (draft → ×8 re-roll → texture →
GLB-after-texture → pose tuner → Blender render → DC6 alternate + Activate), single-object items
+ gloves first.

### 6c-2. Restructure (2026-07-22): description = separate tab, Florence-2 GPU captions

User-ratified changes (5 decisions), all built + Playwright-verified:
1. **§1 Source** is art-only; **§2 Generate** holds two tabs: **Upscale** (main flow, unchanged
   img2img controls) and **From description** (caption → editable text → txt2img).
2. **From-description mode** = txt2img on the same SDXL stack; **Reference slider 0–0.5** = tile
   ControlNet strength (0 = pure text; higher = original frames the composition). Server: same
   `/generate` endpoint with `{mode:"desc", prompt:<description>, ref_strength}`; house-style
   suffix (", detailed hand-painted dark fantasy, Diablo II inventory item art") appended at
   generation time, never stored. BiRefNet runs guided only when ref ≥ 0.35 (else composition is
   free). `comfy.build_sdxl_text_graph` / `generate_from_description`.
3. **One shared history strip**, badges: ↑ = upscale, ✎ = from-description (`meta.mode`).
4. Same SDXL+DMD2 engine for both tabs (~10s and ~8s warm respectively).
5. **Captions: Florence-2 via the container's baked-in ComfyUI-Florence2 node** (GPU). Warm ~6s,
   cold ~49s (one-time HF model download, `keep_model_loaded=True`). `comfy.caption_florence`
   (graph: LoadImage → DownloadAndLoadFlorence2Model[Florence-2-large fp16] → Florence2Run
   [more_detailed_caption] → **PreviewAny** as the text sink read from history outputs).
   Florence misreads tiny sprites (gauntlets → "screwdriver"), so `describe.caption_florence`
   **anchors with the catalog identity** ("<Name> (<type>) — Diablo II inventory item.") and
   strips photo-noise sentences (background/blur/"the image is…") via `_clean_caption`.
   CPU Ollama path retained as `backend="ollama"` fallback; the GPU-caption open item is CLOSED.

---

## 6d. Phase C results (2026-07-22) — 3D + texture + finalize DONE (single-object items)

Exit criterion met: the **Armet helm went start-to-finish inside the panel** — upscale variant
(v3, from-description skull art) → Meshy draft (~50s, live three.js viewer, 8 re-rolls shown) →
image-guided texture (~110s) → GLB (cached server-side) → Blender render at the panel's
angle sliders → DC6 alternate `studio-<tid>` (downsized to original art size) → **manual
Activate** — final state verified in the browser (original vs new art side-by-side, ACTIVE chip).

**Shipped**
- `POST /api/studio/generate-upscale` — Meshy draft fed from the selected variant's hi-res
  master; persists the chain (`draft_tid`/`phase`/`source_vid`) into the item's upscale index.
- `PUT /api/upscale/<id>/meshy` — chain state persistence (panel survives reloads/restarts;
  verified by resuming mid-chain).
- `GET /api/studio/task/<tid>` now returns `rerollsLeft` (8 − retryCount).
- `/api/studio/accept` + `/api/pair/build3d/blender` gained an `activate` flag (default true =
  old studio behaviour; the panel passes false → variants+manual-Activate pattern).
- **GLB disk cache** in the `/api/studio/glb/<tid>.glb` proxy (28MB textured GLBs re-downloaded
  from Meshy per request before; now 37ms from `meshy_cache/`).
- `workflow3d.js` — module GLB viewer (importmap + three 0.160 CDN, same lights as studio.js),
  exposed as `window.WF3D` for the plain-script panel; **load-generation guard** (two loads in
  flight = last-arrival-wins race; draft was clobbering the textured model).
- `workflow.js` §3–5 real: draft/re-roll with progress polling, texture step, angle sliders,
  Render → DC6, side-by-side compare, Activate. Chain resumes from persisted state on open.

**Meshy API discovery:** texture tasks reject `prompt`+`imageId` together
(*"prompt and image are mutually exclusive"*). `meshy_web.create_texture` now sends imageId only
when the prompt is empty; the §4 UI defaults to empty-prompt = image-guided (recommended) and
explains that typing a prompt switches to text-guided.

**Timings observed:** draft ~50s; texture ~110s; finalize ~3.5min (28MB GLB download + Cycles).
The finalize button says "1-4 min" accordingly.

**Honest status per item type:** single-object items fully proven E2E. **Gloves: wired but not
E2E-run** (§3 routes to `generate-masked` off the saved single-hand mask, §5 to the mirrored-pair
Blender build with `activate:false`; needs a masked glove + Meshy credits to prove). **Boots:
Phase D** (§3 shows the standard path until `boot_split.py` exists).

---

## 6e. Phase D results (2026-07-22) — boots DONE + glove path E2E-proven

**Boots (decision #10) — proven end-to-end, and the A/B has a winner.** Battle Boots (xtb):
§2 faithful upscale → `boot_split.py` auto-split (gap method, conf 0.46 — the boots touch at the
strap so components fell through to the column-valley split; clean output) → **2-image
`imageIds` draft**: Meshy read the two boots as **two views of ONE object** and produced a
single better-informed boot (side detail from the left view, front proportions from the right)
→ image-guided texture → **mirrored-pair Blender render** → DC6 `pair3d-invtbt`, saved
not-activated. **Verdict: "multi" is the default and mirroring applies to BOTH modes** — the
2-image mode does not produce a two-boot model; it produces a better single boot. §5's
"mirror into a pair" checkbox defaults on.

**Glove path E2E (mechanically proven).** Light Gauntlets (tgl, the item with the saved
`invtgl` mask): generate-masked draft (~40s) → texture → mirrored-pair Blender render → DC6,
activate:false honored. **Quality caveat:** a fresh draft has no per-model pose (gap/yaw/depth/
squaring live on the LINK, tuned in the pairing page) so the default-pose render came out dark/
uninformative. The chain works; good glove output still wants a pose-tuning pass (pairing page,
or a future §5 embed of the pair3d tuner). The user's previous tuned `pair3d-invtgl` alternate
was backed up and restored after the proof (`alternates/base/armor/tgl/_backup_pre_e2e/`).

**Shipped this phase**
- `app/boot_split.py` — components-then-gap auto-split, label-space ≤256px BFS, masks scaled
  back to full res; works identically on 56px originals and 1024px masters (~0.2s).
- `meshy_web.create_draft` accepts an imageIds **list** (multi-image draft).
- `_draft_from_sprite` accepts a sprite list; first image = remembered image_id (drives
  image-guided texturing).
- Endpoints: `POST /api/boots/split/<id>` (+ cached `boots-left/right.png` +
  `GET /api/upscale/<id>/boot/<side>.png`), `generate-upscale` gained
  `boots_mode: multi|single` (auto-splits the selected variant's master, re-splits when the
  variant changed).
- Panel: boots block in §3 (split preview thumbs + method/confidence + Meshy-input radio),
  §5 mirror-pair checkbox routing to `pair/build3d/blender` vs `studio/accept`.
- **Invfile-owner fix:** pair alternates land on the item that owns the shared invfile (xtb's
  art = tbt's `invtbt`); ship() tracks `alt_item` from the endpoint's `item_id` and §5
  preview/Activate target it.
- `scripts/caption_catalog.py` — resumable bulk Florence captioning of the catalog
  (skips existing + user-edited; ~4-6s/item warm; full catalog ≈ 2.5h GPU — run when idle).

---

## 6f. DC6 conversion quality pass (2026-07-22) — cream-speck root causes + fixes

User report: yellow/cream specks in converted DC6 art. Diagnosis (measured, not guessed —
speck scanner over all alternates + pixel-level source tracing):

1. **Fireflies in the source renders (dominant).** Isolated fully-opaque near-white pixels
   (e.g. RGB 255,255,242 on a neighborhood of median-luminance 8) from the old browser-engine
   captures (1.45 tone gain) and low-sample Cycles renders; 203 such pixels in one 112×224
   render. Conversion preserved them faithfully → cream dots at 58px.
2. **Edge halos hardened opaque.** `alpha≥128 → keep RGB fully opaque` turned anti-aliased
   warm edge pixels into full-brightness rim specks.
3. **Plain RGB nearest-match** snapped grays/desaturated pixels to olive/cream palette
   neighbors ((252,228,164), (252,252,196)…).
4. **LANCZOS ringing** made lone over-bright pixels next to hard highlights.

**Fixes (all in `app/assets.py`, ratified 4+1 decisions):**
- `_despeckle_fireflies()` — pixels whose luminance exceeds the local 5×5 median by >60 are
  pulled back to the median color before downsizing (multi-pixel real highlights survive).
- Dark-rim matte: semi-transparent pixels alpha-composited onto `RIM_RGB=(10,10,10)` before
  quantization — edges darken like vanilla D2 outlines instead of glowing.
- **Oklab perceptual nearest-match** (`_srgb_to_oklab`) replaces RGB Euclidean distance.
- `_resize_clamped()` — LANCZOS + clamp to min/max-filtered source (ringing-free, sharpness kept).
- All benefit every consumer of `_quantize_to_palette` (items, flippies, glove composites).

**Rebuild:** `scripts/reconvert_alternates.py` re-ran PNG→DC6 for **92 alternates** from their
provenance renders (originals backed up as `*.dc6.pre_specfix`; before/after contact sheet at
`<ws>/reconvert_sheet.png`). Measured: total flagged specks −44%, warm(cream) −47% — and the
detector over-counts legitimate 1px-wide bright detail (a spear shaft), so real speck reduction
is higher (worst item: 102→10). 28 skipped: 2D glove composites (`glove-pair`, different layout)
+ alts with no stored render. **Push to game required to see rebuilt art.**

**Render-side (later):** raise Cycles samples / enable denoiser, revisit browser capture gain —
deferred by decision (despeckle already catches it at DC6 scale).

## 6g. Browser render engine in §5 (2026-07-22) — no Blender required

§5 gained an **Engine radio: browser (fast) / Blender**. Browser path: `WF3D.captureSprite()`
(workflow3d.js) runs an offscreen **PairPreview** (pair3d.js — the deliberately Blender-matched
ortho camera + sun rig) at K=8 supersample, then POSTs the capture to the existing browser
endpoints: `/api/pair/build3d` (mirrored pairs) or `/api/pair/build-single` (single items) —
both gained the `activate:false` flag. Blender absent → browser is auto-selected and the
Blender option is disabled. Verified live: boots pair rendered + saved via browser engine in
seconds, no Blender in the path. Engine choice persists in the meshy chain state.

---

## 6h. Firefly elimination at the SOURCE (2026-07-22) — investigation results

Census (firefly detector over every provenance render, grouped by engine):

| engine | renders | fireflies | per 1k visible px |
|---|---|---|---|
| card-image (Meshy's own previews) | 80 | 38,737 | 12.2 |
| studio (our Blender accept) | 2 | 330 | 17.1 |
| browser (pair3d capture) | 5 | 235 | 1.5 |
| meshy+blender | 4 | 10 | 0.6 |

**Our Blender renders: hygiene shipped, and the truth is subtler than "fireflies".**
`render_glb.py setup()` now enables OpenImageDenoise, `sample_clamp_indirect=10`,
`blur_glossy=1.0`, caustics off (render time unchanged, ~7s). A byte-level A/B (same scene
±denoise) proved the flagged pixels in our renders are **deterministic specular glints from
Meshy's shiny PBR textures** — identical locations across renders, survive denoising, and
supersampling (512→128 test: 10.6 → 13.7/1k) doesn't remove them because they're real material
detail, not Monte Carlo noise. They're correctly handled downstream by the conversion despeckle.
The new settings are kept as insurance for future reflective/emissive models.

**Browser captures:** no tone mapping (linear 1.45 gain in the lights → specular clips to
white). Optional improvement: ACESFilmic tone mapping + re-measured exposure — deferred, rate
is already low (1.5/1k) and despeckle catches the rest.

**Card-image renders (the real source, 80 alts): only partially replaceable.**
- GLB availability is fine (66/80 cached; sampled remainder still downloadable).
- BUT the specific tasks behind these alts: **23 have a textured task → clean local re-render**
  (pilot: jug/shield/spear re-rendered nicely; shield arguably better than the card image);
  **57 are draft-only → local re-render = grey clay** (pilot confirmed). Eliminating their
  fireflies at source means TEXTURING 57 tasks first (Meshy credits + per-item eyeballing).
- Look-shift is real even for textured ones (the 9br spear re-rendered thin/vertical vs the
  card's dynamic diagonal — Meshy's preview camera differs from our accept angles).
- **Disposition:** don't batch-replace. The despeckle already suppresses card-image fireflies
  at DC6 scale; replacements happen opportunistically — items worth caring about go through
  the panel flow (draft → texture → render), which produces clean sources by construction.
  Pilot artifacts: scratchpad `pilot/pilot2_sheet.png`.

---

## 6i. "Ancient Armor still speckled in game" (2026-07-22) — two more root causes closed

1. **The overlay was stale.** The game reads `overlay/` (registered @9000), which only
   `assets.activate()` writes — the reconvert pass rebuilt `alternates/` but never re-activated,
   so the game kept serving pre-fix bytes. The reported item was actually **Victor's Silk**
   (unique Ancient Armor, `invaaru.dc6` — overlay bytes matched the `.pre_specfix` backup).
   Fixed: overlay resynced for all ~100 active alternates, and `reconvert_alternates.py` now
   **auto-resyncs the overlay from the manifest** after every run. Push + Full reload still
   needed after a resync.
2. **Glinty PBR metal re-creates specks at cell scale** even from clean renders (proved on
   base Ancient Armor: a 4× denoised re-render actually scored WORSE after downsize — the
   glints are multi-pixel material truth, not noise). Fixed with a **second despeckle pass on
   the fitted cell canvas** (3×3, thresh 50) in `png_to_item_dc6` — kills isolated bright
   pixels at exactly the scale the player sees. Ancient Armor 78 → 15 flagged px,
   Victor's Silk 47 → 8 (warm 41 → 7), remaining ones are legitimate multi-pixel highlights.

---

## 6j. THE REAL IN-GAME FIREFLY CAUSE (2026-07-22) — act-variable palette indexes

User evidence cracked it: art clean in the web app but speckled in game, **Acts 1/3 fine,
Acts 2/4/5 speckled**, only on our alternates, and one item (armet) clean everywhere.

**Mechanism:** the game draws inventory DC6s with the CURRENT ACT's palette. Indexes
**225–254 are act-variable** (the only 30 entries that differ between act palettes; in ACT1
they're dark greens/browns/blues). Our quantizer — especially with the dark-rim matte + Oklab
matcher — picked them for dark pixels. Perfect in our ACT1-based preview (circular: we decode
with the same palette we encode with), wrong-colored specks in any act whose palette differs
at those indexes. **Stock Blizzard item art uses ZERO pixels ≥ 225** — that's the implicit
authoring rule. Counts matched the symptom exactly: Victor's Silk 379 unsafe px (speckled),
armet 21 (clean), stock invaar 0.

**Fixes:**
- `_quantize_to_palette` now matches against **indexes 1..224 only** (`SAFE_MAX = 224`) —
  the act-stable range; art renders identically in every act.
- Full reconvert + a one-off remap pass for the 11 no-provenance DC6s (old magenta/cyan test
  variants — magenta literally IS index 225 in ACT1; plus a few blender-era alts): unsafe
  pixels re-mapped in place to the nearest safe index (Oklab).
- Overlay resync gap #2 found: **txt-edited own-invfile uniques** (Azurewrath → `invcrsu`)
  have manifest entries naming the BASE invfile, so manifest-based resync left the own-file
  overlay copy stale. `reconvert_alternates.py` resync now uses the live catalog's effective
  invfile (manifest fallback).
- Verified: all 98 overlay DC6s act-safe (0 pixels ≥ 225). Push + Full reload to see in game.

---

## 6k. Re-ship & edit flow fixes (2026-07-22)

- **Re-shipping the ACTIVE alternate now refreshes the overlay.** All four ship endpoints
  (`studio/accept`, `pair/build3d`, `pair/build3d/blender`, `pair/build-single`) re-run
  `assets.activate()` when the saved alt is already the active choice, even with
  `activate:false` — re-rendering active art (e.g. a new angle) previously updated
  `alternates/` but left the overlay (and the game) on the old bytes.
- **Engine changes the alt id**: browser ship → `pair-single-<tid>`, Blender ship →
  `studio-<tid>` — switching engines creates an additional alternate rather than replacing
  (acceptable; delete the unwanted one via ⋯).
- **Gallery auto-refresh**: the panel calls `refreshView()` after a ship, so new/updated
  alternates appear in the rail without a manual page reload.
- **⋯ menu gained "🛠 Edit in workflow…"** on item alternates — reopens the slide-over panel,
  which resumes the item's persisted chain (variants, draft/texture, angles) for tweak +
  re-ship.
- Gotcha bitten again: server processes keep OLD code across pipeline edits — the user's
  angle-25 re-ship ran on a pre-SAFE_MAX server and produced 236 act-unsafe px; reconvert
  (idempotent, catalog-resync) cleaned it. **Restart the studio server after edits to
  assets.py/server.py.**

---

## 7. Risks / open items

- **Qwen GGUF speed on the 3090** — estimate is 20–35s/gen; verify in Phase A before wiring the
  auto-router (if it disappoints, Lane B becomes an explicit "slow mode" button).
- **Meshy 2-image fusion quality** for near-identical objects (boots) — unproven; that's why the
  mirror fallback is first-class (decision #10).
- **ComfyUI container GPU + disk** — confirm the container sees the GPU under load alongside
  Ollama (24GB is enough for one lane + a resident 7B VL model, but not both diffusion lanes).
- **Panel real estate** — 4-thumb history strip + 3D viewer in ~500px; may need the strip to
  scroll horizontally.
- **png-upscale-service** stays untouched (the old `/api/studio/mask/upscale` path keeps working)
  until the panel fully replaces the Studio flow.

---

## 7. Small-item generation: what actually controls quality (2026-07-30)

Established by side-by-side user verdicts on ~13 generations plus controlled A/B runs, working the
318-item misc/1x1 section (jewellery, charms, gems, runes, potions, scrolls, maps, quest items).
**Every one of these findings contradicted our fidelity metric.** SSIM rewards reproducing the
original's noise, which is the wrong objective when the source is ~840 pixels of hand-drawn mush;
the user's eye picked the opposite option every single time. `detail_ratio` stayed honest; SSIM did
not. Judge these items by eye.

### 7.1 The lane decides everything
GOOD (11/11) = `d2qwen_*`, Qwen-Image-Edit **with a sampler**. TERRIBLE (2/2) = `d2enhance_*`, whose
graph is `LoadImage -> UpscaleModelLoader -> ImageUpscaleWithModel -> Save` — **no sampler at all**,
so it can only enlarge existing pixels and a 29px sprite becomes a 1024px smear. A diffusion model
must repaint the art. `faithful_upscale` is therefore removed from the picker.

### 7.2 The recipe
**m1** (identity + house style) at **denoise 1.0, steps 4, cfg 1.0**, GAN pre-upscale of the INPUT,
**no colour transfer** (it washed a key's dark stone to pale tan), **no pad** (m7's 0.18 margin is
proportionally huge on a 1x1 sprite and collapsed a rune to iou 0.28).

### 7.3 Negative prompts do not work at all
Every lane runs cfg 1.0 (Lightning/DMD2/schnell all require it) and CFG at 1.0 reduces to the
conditional branch, cancelling the unconditional one. Proven: two runs with opposite negatives
returned **byte-identical** output. Anything to suppress must go in the POSITIVE prompt.

### 7.4 Caption style guide (the biggest single lever)
1. **Never a plain geometric noun** — tablet, plate, panel, sheet. It lost every arm of the noun
   test and constrains the model BELOW what it invents unprompted. My "pentagonal dark stone
   tablet" produced a flat plate and silently dropped the gold frame the original actually has.
2. **Keep the item's own name when the name evokes the right object** ("Uber Ancients Key" ->
   ornate gold-framed relic, detail 1.68). **Substitute an evocative material/relic noun when it
   does not** ("Lucion Key A" means nothing -> "relic sigil of cracked crimson stone").
3. **Always name a specific SURFACE.** "frost-rimed pitting/hoarfrost" moved a key from 0.87 to
   2.36; the generic "soot-stained" moved its sibling by 0.08.
4. **Avoid names containing a plain common word** (Scroll, Book, Rune) — those get transcribed as
   text onto the art. "Identify Scroll" wrote *Scroll*; "Nef Rune" stamped an *N*. Multi-word odd
   names ("Uber Ancients Key B") do not trigger it. Describing the object instead fixes the text
   AND improves design fidelity.
5. **Under-describing is its own failure mode.** A caption that names only geometry yields flat
   art; the QA gate rejected one such generation outright at detail 0.22.

### 7.5 Measured colour beats eyeballing, with a caveat
For families of near-identical sprites, measure the dominant SATURATED hue from the pixels rather
than judging thumbnails by eye (this caught calling a green armour "black" and a blue belt "grey").
Caveat: it measures the whole sprite, so it reports the parchment not the star on a map, and a
potion's gold band not its liquid — sanity-check wherever the subject is not the dominant colour.

### 7.6 Coverage
306 of 318 misc items now carry hand-written descriptions in `descriptions.json` (source `user`,
so the bulk auto-captioner will not overwrite them). The 12 skipped are `Not used` placeholders.
Note `m0` and `m5` IGNORE the description (fidelity-lab no-anchor controls) — a footgun given the
anchor is the strongest lever we have.
