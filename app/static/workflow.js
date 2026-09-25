/* AI upscale -> 3D workflow panel — column 3 ("Generate") of the gallery page.
   Persistent column, auto-populated by selectItem(item) in app.js whenever the selected
   item changes (see index.html's #workflow aside).

   Top-to-bottom flow:
     1 Source        — the original art. Gloves needing a mask brush the single-hand mask here.
     2 Generate      — all recipes exposed directly, named by internal id (m7_combo_ct default;
                       see app/enhance_recipes.py). Every result is scored for fidelity (badge on
                       each thumbnail) and the item's restyle/nudge text persists server-side
                       (gen_prompts.py).
     3 3D model      — Meshy draft from the selected variant's hi-res master; free ×8 re-rolls;
                       live three.js viewer (workflow3d.js). Gloves feed the saved single-hand
                       mask instead (pair is one merged blob).
     4 Texture       — texture the approved draft; the GLB downloads (proxied) once it's done.
     5 Final render  — Blender render at the inventory angle -> DC6 alternate (downsized to the
                       original art size) -> manual Activate. Gloves route through the
                       mirrored-pair Blender build.

   Meshy chain state persists server-side in the item's upscale index (survives reloads). */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const enc = encodeURIComponent;
  let ITEM = null, STATE = null, capPoll = null;
  let MESHY = {};                       // {draft_tid, texture_tid, phase, source_vid, alt_id}
  // Enhance-picker state. GEN_PROMPTS is loaded from (and saved to) the server per item —
  // see gen_prompts.py — so a restyle/nudge you type NEVER bleeds into the next item you open
  // and survives a page reload (the old shared `GEN` object did neither).
  let METHOD = "m7";              // current picker selection
  let GEN_PROMPTS = { restyle: "", nudge: "", negative: "", last_method: "" };
  let saveDebounce = null;
  let TASK = { draft: null, texture: null };  // last poll snapshots
  let taskPoll = null, BUSY = null;     // BUSY: "gen3d" | "texture" | "ship" | null
  // Glove single-hand mask brush (ported from the removed /studio power tool).
  let MASK = { W: 0, H: 0, scale: 1, orig: null, work: null, ctx: null, painting: false, undo: [], editing: false };

  function ensurePanel() { return $("#workflow"); }

  async function _loadImg(src) {
    const img = new Image();
    await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = src; });
    return img;
  }

  async function api(method, url, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    const r = await fetch(url, opt);
    return r.json();
  }

  function randSeed() { return Math.floor(Math.random() * 2 ** 31); }
  function fmt(x) { return (x == null ? "" : Number(x).toFixed(2)); }
  function esc(s) { return (s || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

  function chip(state, label) {
    if (state === "busy") return `<span class="wf-chip busy">${label || "working…"}</span>`;
    if (state === "done") return `<span class="wf-chip done">${label || "done"}</span>`;
    return `<span class="wf-chip">${label || "empty"}</span>`;
  }

  // Meshy textures from EITHER the reference image OR a text prompt (mutually exclusive).
  // Default = empty prompt = guided by your selected art image, which is what the flow wants.

  // ---------- section markup ----------

  function section3() {
    const it = ITEM, up = STATE.upscale || { variants: [] };
    const hasSel = !!up.selected;
    const glove = it.is_glove, boot = it.is_boot;
    const enabled = glove ? it.has_mask : hasSel;
    const d = TASK.draft;
    const running = MESHY.draft_tid && d && (d.status === "PENDING" || d.status === "IN_PROGRESS");
    const done = d && d.status === "SUCCEEDED";
    const rr = d ? d.rerollsLeft : 8;
    let src;
    if (glove) {
      const showBrush = !it.has_mask || MASK.editing;
      src = showBrush
        ? `<div class="wf-sub">Gloves are one merged left/right blob — brush away the underneath
             hand so the 3D model is built from a single clean hand (mirrored into the pair later).</div>
           <div class="wf-maskwrap"><canvas id="wfMaskCanvas"></canvas></div>
           <div class="wf-masktools">
             <label>brush <input type="range" id="wfMaskBrush" min="1" max="16" step="1" value="4"><span class="val" id="wfMaskBrushV">4</span></label>
             <label><input type="radio" name="wfMaskMode" value="erase" checked> erase</label>
             <label><input type="radio" name="wfMaskMode" value="restore"> restore</label>
             <button id="wfMaskUndo">↶ undo</button>
             <button id="wfMaskReset">reset</button>
           </div>
           <div class="wf-gen">
             <button id="wfMaskSave" class="gold">💾 Save mask</button>
             <span id="wfMaskNote" class="wf-sub" style="margin:0"></span>
           </div>`
        : `<div class="wf-sub">Gloves: the 3D model is built from your saved <b>single-hand mask</b>
             (the pair is one merged blob). The §2 art is still used for the DC6 look.
             <button id="wfMaskEdit" style="margin-left:6px">✏ edit mask</button></div>`;
    } else if (boot) {
      const bs = STATE.boots || {};
      const mode = MESHY.boots_mode || "multi";
      src = `
        <div class="wf-sub">Boots: the art holds both boots — they're auto-split and the model is
          built from the split sides.</div>
        <div class="wf-gen" style="margin-top:6px">
          <button id="wfSplit" ${BUSY ? "disabled" : ""}>🥾 Split preview</button>
          <span id="wfSplitNote" class="wf-sub" style="margin:0">
            ${bs.method ? `${bs.method} split · confidence ${bs.confidence} · ${esc(bs.source || "")}` : "not split yet"}</span>
        </div>
        <div id="wfSplitRow" class="wf-src" style="margin-top:6px; ${bs.method ? "" : "display:none"}">
          <div class="thumb checker"><img id="wfBootL" src="/api/upscale/${enc(it.id)}/boot/left.png?t=${Date.now()}"></div>
          <div class="thumb checker"><img id="wfBootR" src="/api/upscale/${enc(it.id)}/boot/right.png?t=${Date.now()}"></div>
        </div>
        <div class="wf-row" style="margin-top:6px"><label>Meshy input</label>
          <label style="width:auto"><input type="radio" name="wfBootsMode" value="multi" ${mode === "multi" ? "checked" : ""}>
            both boots (2-image)</label>
          <label style="width:auto"><input type="radio" name="wfBootsMode" value="single" ${mode === "single" ? "checked" : ""}>
            one boot + mirror</label>
        </div>`;
    } else {
      src = hasSel
        ? `<div class="wf-sub">Feeds the selected variant's hi-res master (${esc(up.selected)}).</div>`
        : `<div class="wf-sub">Select or generate a variant in §2 first.</div>`;
    }
    return `
      <div class="wf-section ${enabled ? "" : "disabled"}">
        <div class="wf-shead"><span class="num">3</span>3D model (Meshy)
          ${done ? chip("done") : running ? chip("busy", (d && d.progress != null ? d.progress + "%" : "…")) : chip("empty")}</div>
        <div class="wf-body">
          ${src}
          <div class="wf-gen">
            <button id="wfGen3d" class="gold" ${running || BUSY ? "disabled" : ""}>
              ${MESHY.draft_tid ? "⟳ New draft" : "🧊 Generate 3D"}</button>
            ${done ? `<button id="wfReroll" ${BUSY || !rr ? "disabled" : ""}
                title="Free in-place re-roll (Meshy replaces the draft)">🎲 Re-roll (${rr} left)</button>` : ""}
            <span id="wf3dNote" class="wf-sub" style="margin:0">${running ? `<span class="wf-spinner"></span> generating… ${d && d.progress != null ? d.progress + "%" : ""}` : ""}</span>
          </div>
          <div id="wf3dHost" class="wf-3dhost" ${MESHY.draft_tid && done ? "" : 'style="display:none"'}></div>
        </div>
      </div>`;
  }

  function section4() {
    const d = TASK.draft, t = TASK.texture;
    const draftDone = d && d.status === "SUCCEEDED";
    const running = MESHY.texture_tid && t && (t.status === "PENDING" || t.status === "IN_PROGRESS");
    const done = t && t.status === "SUCCEEDED";
    return `
      <div class="wf-section ${draftDone ? "" : "disabled"}">
        <div class="wf-shead"><span class="num">4</span>Texture
          ${done ? chip("done", "textured + downloaded") : running ? chip("busy", (t && t.progress != null ? t.progress + "%" : "…")) : chip("empty")}</div>
        <div class="wf-body">
          <div class="wf-sub" style="margin-bottom:6px">Leave the prompt empty to texture from
            your selected art image (recommended). Typing a prompt switches Meshy to text-guided
            texturing instead — they're mutually exclusive.</div>
          <textarea id="wfTexPrompt" class="wf-panel-full" style="min-height:44px"
            placeholder="Optional text override — empty = guided by the art image"></textarea>
          <div class="wf-gen">
            <button id="wfTex" class="gold" ${running || BUSY ? "disabled" : ""}>
              ${MESHY.texture_tid ? "⟳ Re-texture" : "🎨 Generate texture"}</button>
            <span id="wfTexNote" class="wf-sub" style="margin:0">${running ? `<span class="wf-spinner"></span> texturing… ${t && t.progress != null ? t.progress + "%" : ""}` : done ? "GLB ready — preview above shows the textured model" : ""}</span>
          </div>
        </div>
      </div>`;
  }

  function section5() {
    const it = ITEM, t = TASK.texture;
    const texDone = t && t.status === "SUCCEEDED";
    const alt = MESHY.alt_id;
    const isActive = alt && it.active === alt;
    const paired = it.is_glove || it.is_boot;
    const bootPairRow = it.is_boot ? `
      <div class="wf-row"><label title="Mirror the model into a left+right pair. Proven 2026-07-22: the 2-image draft also yields ONE boot (Meshy reads the two images as two views of one object), so mirroring is right for both modes">Pair</label>
        <label style="width:auto"><input type="checkbox" id="wfMirror" checked>
          mirror into a pair</label></div>` : "";
    const engineRow = `
      <div class="wf-row"><label title="Browser = instant three.js capture (same camera/light rig as Blender, no install needed). Blender = Cycles final quality with real mirrored-geometry shadows.">Engine</label>
        <label style="width:auto"><input type="radio" name="wfEngine" value="browser"
          ${!STATE.blender || MESHY.engine === "browser" ? "checked" : ""}> browser (fast)</label>
        <label style="width:auto"><input type="radio" name="wfEngine" value="blender"
          ${STATE.blender && MESHY.engine !== "browser" ? "checked" : ""} ${STATE.blender ? "" : "disabled"}>
          Blender${STATE.blender ? "" : " (not installed)"}</label></div>`;
    const shipBtn = paired
      ? `<button id="wfShip" class="gold" ${BUSY ? "disabled" : ""}
           title="Render the pair, build the DC6 at the original art size">🎬 Render pair → DC6</button>`
      : `<button id="wfShip" class="gold" ${BUSY ? "disabled" : ""}
           title="Render at this angle, build the DC6 at the original art size">🎬 Render → DC6</button>`;
    const angles = it.is_glove ? "" : `
      <div class="wf-row"><label>Angle</label>
        <input type="range" id="wfAzim" min="-90" max="90" step="1" value="25">
        <span class="val" id="wfAzimV">25</span></div>
      <div class="wf-row"><label>Elevation</label>
        <input type="range" id="wfElev" min="-30" max="60" step="1" value="15">
        <span class="val" id="wfElevV">15</span></div>`;
    return `
      <div class="wf-section ${texDone ? "" : "disabled"}">
        <div class="wf-shead"><span class="num">5</span>Final render & ship
          ${isActive ? chip("done", "ACTIVE") : alt ? chip("done", "saved") : chip("empty")}</div>
        <div class="wf-body">
          ${engineRow}
          ${angles}
          ${bootPairRow}
          <div class="wf-gen">${shipBtn}
            <span id="wfShipNote" class="wf-sub" style="margin:0"></span></div>
          ${alt ? `
          <div class="wf-src" style="margin-top:10px">
            <div class="thumb checker" title="original"><img src="/api/item/${enc(it.id)}/original.png"></div>
            <div class="thumb checker" title="new alternate (framed like the original)">
              <img src="/api/item/${enc(MESHY.alt_item || it.id)}/alt/${enc(alt)}.png?t=${Date.now()}"></div>
            <div class="wf-desc wf-sub" style="justify-content:center">
              saved as <b>${esc(alt)}</b>${isActive ? " · ACTIVE" : ""}<br>
              ${isActive
                ? `<span>use <b>Push to game</b> in the header to see it in game</span>`
                : `<button id="wfActivate" class="gold" style="margin-top:6px">✔ Activate</button>`}
            </div>
          </div>` : ""}
        </div>
      </div>`;
  }

  // ---------- render ----------

  function pickOpt(value, label) {
    return `<label class="wf-pickopt"><input type="radio" name="wfMethod" value="${value}"
      ${METHOD === value ? "checked" : ""}>${esc(label)}</label>`;
  }

  function render() {
    const p = ensurePanel();
    const it = ITEM, up = STATE.upscale || { variants: [], selected: null };
    const desc = STATE.description;
    const hasVariants = up.variants.length > 0;
    const sel = up.selected;
    p.innerHTML = `
      <div class="colhead">3 · Generate</div>
      <div class="wf-head">
        <h2>${esc(it.name)}</h2>
      </div>
      <div class="wf-sub">${esc(it.code)} · ${it.cells[0]}×${it.cells[1]} cells · AI upscale → 3D</div>

      <div class="wf-section">
        <div class="wf-shead"><span class="num">1</span>Source ${chip("done")}</div>
        <div class="wf-body">
          <div class="wf-src">
            <div class="thumb checker"><img src="/api/item/${enc(it.id)}/original.png"></div>
            <div class="wf-desc wf-sub" style="justify-content:center">
              ${it.cells[0]}×${it.cells[1]} cells · the original inventory art.<br>
              Pick a recipe below and Generate — every result is scored against this original.
            </div>
          </div>
        </div>
      </div>

      <div class="wf-section">
        <div class="wf-shead"><span class="num">2</span>Generate
          ${chip(hasVariants ? "done" : "empty")}</div>
        <div class="wf-body">
          ${it.is_thin_weapon && !GEN_PROMPTS.last_method ? `<div class="wf-sub" style="margin-bottom:4px">
            This item's shape is thin/elongated — pre-selected "m3_anchor_ct" below
            (the margin-pad + vivid-style recipes tend to loosen thin blade silhouettes).</div>` : ""}
          <div class="wf-picker" id="wfPicker">
            ${pickOpt("m0", "m0_base")}
            ${pickOpt("m1", "m1_anchor")}
            ${pickOpt("m2", "m2_catstyle")}
            ${pickOpt("m3", "m3_anchor_ct")}
            ${pickOpt("m5", "m5_pad")}
            ${pickOpt("m6", "m6_combo")}
            ${pickOpt("m7", "m7_combo_ct")}
            ${pickOpt("sdxl_lock", "Structure-locked (SDXL)")}
            ${pickOpt("flux_lock", "Structure-locked (Flux)")}
          </div>

          <div class="wf-row" style="align-items:flex-start;margin-top:8px"><label>Nudge</label>
            <textarea id="wfNudge" class="wf-panel-full" style="min-height:40px"
              placeholder="Optional: nudge the result — e.g. 'more ornate', 'brighter metal'.">${esc(GEN_PROMPTS.nudge)}</textarea></div>

          <div class="wf-row" style="align-items:flex-start" data-method="m2 sdxl_lock flux_lock"><label>Restyle</label>
            <textarea id="wfRestyle" class="wf-panel-full" style="min-height:64px"
              placeholder="Describe the new look — e.g. 'molten fiery iron with glowing embers', 'royal gold with blue sapphires', 'frostbitten ice'.">${esc(GEN_PROMPTS.restyle)}</textarea></div>

          <div class="wf-row" style="margin-top:8px" data-method="flux_lock">
            <label title="ControlNet strength. Higher locks the original outline harder while the prompt restyles the material/colour.">Shape lock</label>
            <input type="range" id="wfShape" min="0.2" max="1" step="0.05" value="0.65">
            <span class="val" id="wfShapeV">0.65</span></div>

          <label class="wf-sub" style="margin-top:8px;display:block">Negative prompt override</label>
          <textarea id="wfNeg" class="wf-panel-full" style="min-height:40px;margin-top:2px"
            placeholder="Leave blank to use the method's default negative prompt">${esc(GEN_PROMPTS.negative)}</textarea>
          <div class="wf-gen">
            <label class="wf-sub" style="margin:0">seed</label>
            <input type="number" id="wfSeed" class="seedbox wf-panel-full" value="${randSeed()}">
            <button id="wfDice" title="Randomize seed">🎲</button>
            <button id="wfGen" class="gold" title="Generate one image with the selected recipe">✨ Generate</button>
            <button id="wfGenCfg" title="View / edit the model, style prompt and every parameter used to generate images">⚙ Settings</button>
            <span id="wfGenNote" class="wf-sub" style="margin:0"></span>
          </div>
          <div class="wf-genset hidden" id="wfGenSet"></div>
          <div class="wf-strip" id="wfStrip">${stripHTML(up)}</div>
          <div class="wf-selbig checker" id="wfSelBig">${sel ? `<img src="/api/upscale/${enc(it.id)}/variant/${sel}.png?master=1&t=${Date.now()}" title="selected variant ${sel} — full resolution">` : ""}</div>
          <div class="wf-prov" id="wfProv">${provenanceHTML((up.variants || []).find((v) => v.id === sel))}</div>
          <div class="wf-2d" id="wf2dWrap" ${sel ? "" : 'style="display:none"'}>
            <div class="wf-gen" style="margin-top:2px">
              <button id="wfAccept2d" class="gold"
                title="Skip 3D — ship the selected image straight to this item's inventory art. Background is already cut; you confirm a fitted preview first.">✓ Accept image as item art → DC6</button>
              <span class="wf-sub" style="margin:0">No 3D model needed — the selected §2 image becomes the item's art.</span>
            </div>
            <div class="appanel hidden" id="wf2dConfirm"></div>
          </div>
        </div>
      </div>

      ${section3()}
      ${section4()}
      ${section5()}
    `;
    wire();
    p.classList.remove("hidden");
    mountViewerIfReady();
    if (it.is_glove && (!it.has_mask || MASK.editing)) mountMaskCanvasIfNeeded();
  }

  function stripHTML(up) {
    if (!up.variants.length) return `<div class="wf-sub" style="padding:6px">Nothing generated yet — press Generate.</div>`;
    return up.variants.map((v) => {
      // new variants carry a fidelity score + method_label (enhance_recipes.py); older ones
      // fall back to the legacy mode-based icon so the strip never breaks for existing art.
      const isDesc = v.mode === "desc";
      const badge = v.score
        ? `<span class="wf-score" title="fidelity ${fmt(v.score.score)} — iou ${fmt(v.score.iou)} · colour ${fmt(v.score.color)} · ssim ${fmt(v.score.ssim)}">${fmt(v.score.score)}</span>`
        : `<span class="badge" title="${isDesc ? "from description" : "direct upscale"}">${isDesc ? "✎" : "↑"}</span>`;
      const capBits = v.method_label || (isDesc ? `ref ${fmt(v.ref_strength)}` : `crea ${fmt(v.creativity)}`);
      return `
      <div class="wf-thumb ${v.id === up.selected ? "sel" : ""}" data-vid="${v.id}">
        <div class="t"><img src="/api/upscale/${enc(ITEM.id)}/variant/${v.id}.png?t=${v.ts || 0}"></div>
        ${badge}
        <button class="x" data-del="${v.id}" title="Delete this variant">✕</button>
        <div class="cap" title="seed ${v.seed}">${v.id} · ${esc(capBits)}</div>
      </div>`;
    }).join("");
  }

  // Provenance: EXACTLY what produced the selected image. New variants carry a full snapshot
  // (method/instruction/negative + fidelity score); older ones show what was recorded then.
  // Rendered via the shared pvBox/pvRow component (provbox.js) -- same collapsible markup app.js
  // uses for the alternate inspector's provenance box.
  function provenanceHTML(v) {
    if (!v) return "";
    const params = [
      v.method_label || "", v.model ? `model ${v.model}` : "", v.steps ? `${v.steps} steps` : "",
      (v.gan !== undefined) ? (v.gan ? "GAN 4×" : "LANCZOS") : "", v.px ? `${v.px}px` : "",
      `seed ${v.seed}`, !v.method_label && v.preset ? v.preset : "",
      !v.method_label && v.engine ? v.engine : "",
    ].filter(Boolean).join(" · ");
    const full = v.instruction || v.positive || v.description || (v.prompt || "");
    const rows = [
      pvRow("params", params),
      v.score ? pvRow("fidelity score", `${fmt(v.score.score)}  (iou ${fmt(v.score.iou)} · colour ${fmt(v.score.color)} · ssim ${fmt(v.score.ssim)})`) : "",
      pvRow("your prompt", v.prompt),
      v.style ? pvRow("style", v.style) : "",
      full ? pvRow("full instruction", full) : "",
      v.negative ? pvRow("negative", v.negative) : "",
      !v.instruction && !v.style && !v.method_label ? `<div class="wf-sub" style="padding:4px 0">(generated before provenance capture — only basic params recorded)</div>` : "",
    ].join("");
    return pvBox({ title: `ⓘ How ${pvEsc(v.id)} was generated`, rows });
  }

  // ---------- generation settings (transparent + editable) ----------
  const NUMF = { steps: 1, px: 1, accept_brightness: 0.05 };
  async function openGenSettings() {
    const box = $("#wfGenSet");
    if (!box.classList.contains("hidden")) { box.classList.add("hidden"); box.innerHTML = ""; return; }
    const r = await api("GET", "/api/gen/settings");
    const s = r.settings, d = r.defaults;
    const ta = (k, rows, hint) =>
      `<label class="wf-sub">${k}${hint ? ` — <span style="opacity:.7">${hint}</span>` : ""}</label>
       <textarea class="wf-panel-full" data-gk="${k}" rows="${rows}">${esc(s[k])}</textarea>`;
    const num = (k, hint) =>
      `<label class="wf-sub">${k}${hint ? ` — <span style="opacity:.7">${hint}</span>` : ""}</label>
       <input type="number" step="${NUMF[k] || 1}" class="seedbox" data-gk="${k}" value="${s[k]}">`;
    box.innerHTML = `
      <div class="wf-setgrid">
        ${ta("style", 3, "the house look prepended to every image — remove 'dark/gritty/muted' words to brighten")}
        ${ta("enhance_tail", 2, "appended on the Enhance tab")}
        ${ta("restyle_tail", 2, "appended on the Restyle tab")}
        ${ta("negative", 2, "what to avoid")}
        <div class="wf-setnums">
          ${num("steps", "sampler steps")}
          ${num("px", "resolution")}
          ${num("accept_brightness", "1.0=off; >1 brightens at accept (fixes existing art, no re-gen)")}
          <label class="wf-sub">gan<input type="checkbox" data-gk="gan" ${s.gan ? "checked" : ""}></label>
          <label class="wf-sub">model</label>
          <input type="text" class="wf-panel-full" data-gk="model" value="${esc(s.model)}">
        </div>
        <div class="wf-setprev"><b>Enhance sends:</b> <span id="wfSetPrevE">${esc(r.preview.enhance)}</span></div>
        <div class="wf-gen">
          <button id="wfSetSave" class="gold">Save</button>
          <button id="wfSetReset" title="Back to the shipped defaults">Reset to defaults</button>
          <span id="wfSetNote" class="wf-sub"></span>
        </div>
      </div>`;
    box.classList.remove("hidden");
    const collect = () => {
      const o = {};
      box.querySelectorAll("[data-gk]").forEach((el) => {
        o[el.dataset.gk] = el.type === "checkbox" ? el.checked
          : (el.type === "number" ? parseFloat(el.value) : el.value);
      });
      return o;
    };
    box.querySelector("#wfSetSave").onclick = async () => {
      const rr = await api("PUT", "/api/gen/settings", collect());
      $("#wfSetPrevE").textContent = rr.preview.enhance;
      $("#wfSetNote").textContent = "saved — applies to the next image";
    };
    box.querySelector("#wfSetReset").onclick = async () => {
      await api("POST", "/api/gen/settings/reset", {});
      $("#wfGenSet").innerHTML = ""; $("#wfGenSet").classList.add("hidden"); openGenSettings();
    };
  }

  // ---------- wiring ----------

  function toggleMethod() {
    const m = (document.querySelector("input[name=wfMethod]:checked") || {}).value || METHOD;
    document.querySelectorAll("[data-method]").forEach((el) =>
      (el.style.display = el.dataset.method.split(" ").includes(m) ? "" : "none"));
  }

  function scheduleSavePrompts() {
    clearTimeout(saveDebounce);
    saveDebounce = setTimeout(async () => {
      const nu = $("#wfNudge"), re = $("#wfRestyle"), ne = $("#wfNeg");
      if (nu) GEN_PROMPTS.nudge = nu.value;
      if (re) GEN_PROMPTS.restyle = re.value;
      if (ne) GEN_PROMPTS.negative = ne.value;
      await api("PUT", `/api/upscale/${enc(ITEM.id)}/prompts`, GEN_PROMPTS);
    }, 800);
  }

  function wire() {
    document.querySelectorAll("input[name=wfMethod]").forEach((r) => {
      r.onchange = () => {
        METHOD = r.value;
        toggleMethod();
        GEN_PROMPTS.last_method = METHOD;
        api("PUT", `/api/upscale/${enc(ITEM.id)}/prompts`, { last_method: METHOD });
      };
    });
    toggleMethod();
    const shape = $("#wfShape"); if (shape) shape.oninput = () => ($("#wfShapeV").textContent = (+shape.value).toFixed(2));
    const nudgeEl = $("#wfNudge"); if (nudgeEl) nudgeEl.oninput = scheduleSavePrompts;
    const restyleEl = $("#wfRestyle"); if (restyleEl) restyleEl.oninput = scheduleSavePrompts;
    const negEl = $("#wfNeg"); if (negEl) negEl.oninput = scheduleSavePrompts;
    $("#wfDice").onclick = () => ($("#wfSeed").value = randSeed());
    const cap = $("#wfCaption"); if (cap) cap.onclick = caption;
    const dsave = $("#wfDescSave"); if (dsave) dsave.onclick = saveDesc;
    $("#wfGen").onclick = generate;
    { const c = $("#wfGenCfg"); if (c) c.onclick = openGenSettings; }
    $("#wfStrip").querySelectorAll(".wf-thumb .t").forEach((t) => {
      t.onclick = () => selectVariant(t.parentElement.dataset.vid);
    });
    $("#wfStrip").querySelectorAll("[data-del]").forEach((b) => {
      b.onclick = (e) => { e.stopPropagation(); delVariant(b.dataset.del); };
    });
    const a2 = $("#wfAccept2d"); if (a2) a2.onclick = accept2dPreview;
    const g3 = $("#wfGen3d"); if (g3) g3.onclick = gen3d;
    const rr = $("#wfReroll"); if (rr) rr.onclick = reroll3d;
    const sp = $("#wfSplit"); if (sp) sp.onclick = splitBoots;
    document.querySelectorAll("input[name=wfBootsMode]").forEach((r) => {
      r.onchange = () => {
        MESHY.boots_mode = r.value;
        api("PUT", `/api/upscale/${enc(ITEM.id)}/meshy`, { boots_mode: r.value });
      };
    });
    const tx = $("#wfTex"); if (tx) tx.onclick = genTexture;
    const az = $("#wfAzim"), el = $("#wfElev");
    if (az) az.oninput = () => ($("#wfAzimV").textContent = az.value);
    if (el) el.oninput = () => ($("#wfElevV").textContent = el.value);
    const sh = $("#wfShip"); if (sh) sh.onclick = ship;
    const act = $("#wfActivate"); if (act) act.onclick = activateAlt;
    const me = $("#wfMaskEdit"); if (me) me.onclick = () => { MASK.editing = true; render(); };
    const mc = $("#wfMaskCanvas");
    if (mc) {
      const brush = $("#wfMaskBrush"); if (brush) brush.oninput = () => ($("#wfMaskBrushV").textContent = brush.value);
      const mu = $("#wfMaskUndo"); if (mu) mu.onclick = () => { if (MASK.undo.length) { MASK.work.data.set(MASK.undo.pop()); maskRedraw(); } };
      const mr = $("#wfMaskReset"); if (mr) mr.onclick = () => { maskPushUndo(); MASK.work.data.set(MASK.orig.data); maskRedraw(); };
      const ms = $("#wfMaskSave"); if (ms) ms.onclick = saveMask;
      mc.addEventListener("pointerdown", (e) => {
        MASK.painting = true; maskPushUndo(); const [x, y] = maskPos(e); maskPaint(x, y); maskRedraw();
        mc.setPointerCapture(e.pointerId);
      });
      mc.addEventListener("pointermove", (e) => { if (MASK.painting) { const [x, y] = maskPos(e); maskPaint(x, y); maskRedraw(); } });
    }
  }
  addEventListener("pointerup", () => { MASK.painting = false; });

  // ---------- section 2 actions ----------

  async function reloadState() {
    const r = await api("GET", `/api/upscale/${enc(ITEM.id)}/state`);
    if (r.ok) { STATE = r; ITEM = r.item; MESHY = r.meshy || {}; }
    return r;
  }

  async function caption() {
    if ($("#wfCapNote")) $("#wfCapNote").innerHTML = `<span class="wf-spinner"></span> captioning…`;
    await api("POST", `/api/describe/${enc(ITEM.id)}?force=1`);
    startCapPoll();
  }
  function startCapPollIfNeeded() { if (STATE.caption_status === "running") startCapPoll(); }
  function startCapPoll() {
    clearInterval(capPoll);
    capPoll = setInterval(async () => {
      const r = await api("GET", `/api/upscale/${enc(ITEM.id)}/state`);
      if (!r.ok) return;
      if (r.caption_status === "running") return;
      clearInterval(capPoll); capPoll = null;
      STATE = r; ITEM = r.item; MESHY = r.meshy || {};
      if (String(r.caption_status || "").startsWith("error")) {
        $("#wfCapNote") && ($("#wfCapNote").textContent = r.caption_status);
      } else { render(); }
    }, 3000);
  }

  async function saveDesc() {
    const text = $("#wfDesc").value;
    const r = await api("PUT", `/api/describe/${enc(ITEM.id)}`, { text });
    if (r.ok) { STATE.description = r.description; $("#wfCapNote").textContent = "saved"; }
  }

  async function generate() {
    const btn = $("#wfGen"); btn.disabled = true;
    $("#wfGenNote").innerHTML = `<span class="wf-spinner"></span> generating…`;
    const method = (document.querySelector("input[name=wfMethod]:checked") || {}).value || METHOD;
    const nudge = ($("#wfNudge") || {}).value?.trim() || "";
    const restyle = ($("#wfRestyle") || {}).value?.trim() || "";
    const negative = ($("#wfNeg") || {}).value?.trim() || "";
    const shape_strength = $("#wfShape") ? +$("#wfShape").value : 0.65;
    const body = { method, nudge, restyle, negative, shape_strength, seed: +$("#wfSeed").value };
    try {
      const r = await api("POST", `/api/upscale/${enc(ITEM.id)}/generate`, body);
      if (r.ok) {
        STATE.upscale = r.upscale;
        GEN_PROMPTS = { ...GEN_PROMPTS, nudge, restyle, negative, last_method: method };
        METHOD = method;
        render();
      } else { $("#wfGenNote").textContent = "error: " + (r.error || "failed"); btn.disabled = false; }
    } catch (e) { $("#wfGenNote").textContent = "error: " + e; btn.disabled = false; }
  }

  async function selectVariant(vid) {
    const r = await api("POST", `/api/upscale/${enc(ITEM.id)}/select`, { vid });
    if (r.ok) { STATE.upscale = r.upscale; render(); }
  }

  // ---------- accept a §2 image straight to item art (skip 3D) ----------
  // Kept entirely inside §2 so it never touches MESHY / lights up the 3D "Final render & ship"
  // section. Shows the true fitted DC6 preview via the shared adjust panel (adjpanel.js), then
  // commits as a new alternate on confirm.
  function accept2dPreview() {
    const sel = (STATE.upscale || {}).selected;
    if (!sel) return;
    const box = $("#wf2dConfirm");
    if (!box) return;
    box.classList.remove("hidden");
    const fp = (STATE.item && STATE.item.footprint) || { fill: 0.94, dx: 0, dy: 0 };
    const cfg = {
      originalSrc: `/api/item/${enc(ITEM.id)}/original/cell.png?dc6=1&t=${Date.now()}`,
      buildPreviewUrl: (v, evenBorder) => {
        const q = new URLSearchParams({ vid: sel, fill: v.fill, dx: v.dx, dy: v.dy, rot: v.rot, outline: v.outline,
          brightness: v.brightness, contrast: v.contrast, saturation: v.saturation,
          warmth: v.warmth, hue: v.hue, even_border: evenBorder ? 1 : 0, t: Date.now() });
        return `/api/upscale/${enc(ITEM.id)}/accept-2d/preview.png?${q}`;
      },
      footprint: fp,
      note: `Exactly how the art ships in-game — ${ITEM.cells[0]}×${ITEM.cells[1]} cells, palette + framing applied.
        Default size matches the original art (${Math.round(fp.fill * 100)}% of the cell).`,
      buttons: [
        { kind: "commit", label: "✓ Accept → DC6", className: "gold", onCommit: (v, evenBorder) =>
            accept2dCommit(sel, { fill: v.fill, dx: v.dx, dy: v.dy, rot: v.rot, outline: !!v.outline, even_border: evenBorder,
              grade: { brightness: v.brightness, contrast: v.contrast, saturation: v.saturation, warmth: v.warmth, hue: v.hue } }) },
        { kind: "auto" },
        { kind: "resetColor" },
        { kind: "cancel", onClick: () => { box.classList.add("hidden"); box.innerHTML = ""; } },
      ],
    };
    box.innerHTML = renderAdjPanel(cfg);
    wireAdjPanel(box, cfg);
  }

  async function accept2dCommit(vid, opts) {
    const box = $("#wf2dConfirm");
    const r = await api("POST", `/api/upscale/${enc(ITEM.id)}/accept-2d`, { vid, ...(opts || {}) });
    if (!r.ok) {
      const status = box && box.querySelector(".ap-status");
      if (status) status.textContent = "error: " + (r.error || "failed");
      return;
    }
    // Landed as a new alternate (not auto-activated) — same manual-Activate pattern as the 3D ship.
    box.innerHTML = `
      <div class="wf-src">
        <div class="thumb checker" title="original"><img src="/api/item/${enc(r.item_id)}/original.png"></div>
        <div class="thumb checker" title="new alternate">
          <img src="/api/item/${enc(r.item_id)}/alt/${enc(r.alt_id)}.png?t=${Date.now()}"></div>
        <div class="wf-desc wf-sub" style="justify-content:center">
          saved as <b>${esc(r.alt_id)}</b><br>
          <button id="wf2dActivate" class="gold" style="margin-top:6px">✔ Activate</button>
        </div>
      </div>`;
    $("#wf2dActivate").onclick = async () => {
      const a = await api("POST", `/api/item/${enc(r.item_id)}/activate`, { choice: r.alt_id });
      if (a.ok) {
        ITEM.active = r.alt_id;
        $("#wf2dActivate").outerHTML = `<span class="wf-sub">✔ active — use <b>Push to game</b> to see it</span>`;
        if (window.refreshView) window.refreshView();
      }
    };
    if (window.refreshView) window.refreshView();  // the item panel/gallery behind lists alternates
  }
  async function delVariant(vid) {
    const r = await api("DELETE", `/api/upscale/${enc(ITEM.id)}/variant/${vid}`);
    if (r.ok) { STATE.upscale = r.upscale; render(); }
  }

  // ---------- section 3: Meshy draft ----------

  async function splitBoots() {
    const note = $("#wfSplitNote");
    note.innerHTML = `<span class="wf-spinner"></span> splitting…`;
    const r = await api("POST", `/api/boots/split/${enc(ITEM.id)}`, {});
    if (!r.ok) { note.textContent = "split failed: " + (r.error || "?") + " — try again, or feed a single boot + mirror"; return; }
    STATE.boots = r.boots;
    render();
  }

  async function gen3d() {
    BUSY = "gen3d";
    const note = $("#wf3dNote");
    note.innerHTML = `<span class="wf-spinner"></span> creating draft…`;
    try {
      let r;
      if (ITEM.is_glove) {
        r = await api("POST", `/api/studio/generate-masked`, { item_id: ITEM.id, opts: {} });
        if (r.ok) await api("PUT", `/api/upscale/${enc(ITEM.id)}/meshy`,
                            { draft_tid: r.task_id, phase: "draft" });
      } else {
        const body = { item_id: ITEM.id, opts: {} };
        if (ITEM.is_boot) {
          const mode = document.querySelector("input[name=wfBootsMode]:checked");
          body.boots_mode = mode ? mode.value : "multi";
        }
        r = await api("POST", `/api/studio/generate-upscale`, body);
        if (r.ok && r.boots) STATE.boots = r.boots;
      }
      if (!r.ok) { note.textContent = "error: " + (r.error || "failed"); BUSY = null; return; }
      MESHY = { ...MESHY, draft_tid: r.task_id, phase: "draft", texture_tid: null, alt_id: null,
                ...(ITEM.is_boot ? { boots_mode: (document.querySelector("input[name=wfBootsMode]:checked") || {}).value || "multi" } : {}) };
      TASK = { draft: { status: "PENDING", progress: 0, rerollsLeft: 8 }, texture: null };
      BUSY = null;
      render();
      startTaskPoll();
    } catch (e) { note.textContent = "error: " + e; BUSY = null; }
  }

  async function reroll3d() {
    BUSY = "gen3d";
    $("#wf3dNote").innerHTML = `<span class="wf-spinner"></span> re-rolling…`;
    try {
      const r = await api("POST", `/api/studio/reroll`, { task_id: MESHY.draft_tid });
      if (!r.ok) { $("#wf3dNote").textContent = "error: " + (r.error || "failed"); BUSY = null; return; }
      await api("PUT", `/api/upscale/${enc(ITEM.id)}/meshy`, { draft_tid: r.task_id, phase: "draft" });
      MESHY = { ...MESHY, draft_tid: r.task_id, texture_tid: null, alt_id: null };
      TASK = { draft: { status: "PENDING", progress: 0 }, texture: null };
      BUSY = null;
      render();
      startTaskPoll();
    } catch (e) { $("#wf3dNote").textContent = "error: " + e; BUSY = null; }
  }

  // ---------- section 4: texture ----------

  async function genTexture() {
    BUSY = "texture";
    $("#wfTexNote").innerHTML = `<span class="wf-spinner"></span> creating texture task…`;
    try {
      const r = await api("POST", `/api/studio/texture`, {
        task_id: MESHY.draft_tid,
        opts: { prompt: $("#wfTexPrompt").value, artStyle: "realistic", enablePBR: true },
      });
      if (!r.ok) { $("#wfTexNote").textContent = "error: " + (r.error || "failed"); BUSY = null; return; }
      await api("PUT", `/api/upscale/${enc(ITEM.id)}/meshy`, { texture_tid: r.task_id, phase: "texture" });
      MESHY = { ...MESHY, texture_tid: r.task_id, phase: "texture", alt_id: null };
      TASK.texture = { status: "PENDING", progress: 0 };
      BUSY = null;
      render();
      startTaskPoll();
    } catch (e) { $("#wfTexNote").textContent = "error: " + e; BUSY = null; }
  }

  // ---------- section 5: render & ship ----------

  async function ship() {
    BUSY = "ship";
    const note = $("#wfShipNote");
    const engineSel = document.querySelector("input[name=wfEngine]:checked");
    const engine = engineSel ? engineSel.value : (STATE.blender ? "blender" : "browser");
    MESHY.engine = engine;
    note.innerHTML = engine === "browser"
      ? `<span class="wf-spinner"></span> rendering in browser…`
      : `<span class="wf-spinner"></span> downloading GLB + Blender rendering… (1-4 min)`;
    try {
      let r;
      const tid = MESHY.texture_tid || MESHY.draft_tid;
      const mirror = ITEM.is_glove || (ITEM.is_boot && $("#wfMirror") && $("#wfMirror").checked);
      const azim = +($("#wfAzim") ? $("#wfAzim").value : 25);
      const elev = +($("#wfElev") ? $("#wfElev").value : 15);
      if (engine === "browser") {
        // three.js capture with the Blender-matched camera/light rig (pair3d.js) — no Blender
        const K = 8;
        const w = ITEM.cells[0] * 29 * K, h = ITEM.cells[1] * 29 * K;
        const png = await window.WF3D.captureSprite({
          glbUrl: `/api/studio/glb/${tid}.glb`, single: !mirror,
          frame: { azim: ITEM.is_glove ? 0 : azim, elev: ITEM.is_glove ? 0 : elev },
          w, h,
        });
        r = mirror
          ? await api("POST", `/api/pair/build3d`, {
              invfile: ITEM.invfile, png, engine: "browser", activate: false,
              pose: { azim, elev } })
          : await api("POST", `/api/pair/build-single`, {
              task_id: tid, azim, elev, fill: 0.94, png, activate: false });
      } else if (mirror) {
        const pose = ITEM.is_boot ? { azim, elev } : undefined;  // gloves use their template pose
        r = await api("POST", `/api/pair/build3d/blender`,
                      { invfile: ITEM.invfile, task_id: tid, activate: false,
                        ...(pose ? { pose } : {}) });
      } else {
        r = await api("POST", `/api/studio/accept`, {
          task_id: tid, azim, elev, fill: 0.94, dx: 0, dy: 0, activate: false,
        });
      }
      if (!r.ok) { note.textContent = "error: " + (r.error || "failed"); BUSY = null; return; }
      // pair alternates land on the item that OWNS the invfile (art files are shared across an
      // item family: Battle Boots xtb art lives on tbt's invtbt) — track it for preview/activate
      const owner = r.item_id || ITEM.id;
      await api("PUT", `/api/upscale/${enc(ITEM.id)}/meshy`,
                { alt_id: r.alt_id, phase: "shipped", alt_item: owner, engine });
      MESHY = { ...MESHY, alt_id: r.alt_id, alt_item: owner, phase: "shipped", engine };
      BUSY = null;
      await reloadState();  // pick up the fresh alt list
      MESHY = { ...MESHY, alt_id: r.alt_id, alt_item: owner, engine };
      render();
      // the gallery + detail rail behind the panel also list alternates — refresh them so a
      // newly saved/updated alt shows up without a manual page reload
      if (window.refreshView) window.refreshView();
    } catch (e) { note.textContent = "error: " + e; BUSY = null; }
  }

  async function activateAlt() {
    const owner = MESHY.alt_item || ITEM.id;
    const r = await api("POST", `/api/item/${enc(owner)}/activate`, { choice: MESHY.alt_id });
    if (r.ok) {
      ITEM.active = MESHY.alt_id;
      render();
      if (window.refreshView) window.refreshView();  // let the gallery reflect the change
    }
  }

  // ---------- task polling + viewer ----------

  function startTaskPoll() {
    clearInterval(taskPoll);
    taskPoll = setInterval(pollTasks, 5000);
    pollTasks();
  }

  async function pollTasks() {
    const jobs = [];
    if (MESHY.draft_tid && (!TASK.draft || ["PENDING", "IN_PROGRESS"].includes(TASK.draft.status)))
      jobs.push(["draft", MESHY.draft_tid]);
    if (MESHY.texture_tid && (!TASK.texture || ["PENDING", "IN_PROGRESS"].includes(TASK.texture.status)))
      jobs.push(["texture", MESHY.texture_tid]);
    if (!jobs.length) { clearInterval(taskPoll); taskPoll = null; return; }
    for (const [kind, tid] of jobs) {
      try {
        const r = await api("GET", `/api/studio/task/${tid}`);
        if (!r.ok) continue;
        const prev = TASK[kind];
        TASK[kind] = r;
        const transitioned = !prev || prev.status !== r.status;
        if (transitioned) render();
        else {
          const el = kind === "draft" ? $("#wf3dNote") : $("#wfTexNote");
          if (el && ["PENDING", "IN_PROGRESS"].includes(r.status))
            el.innerHTML = `<span class="wf-spinner"></span> ${kind === "draft" ? "generating" : "texturing"}… ${r.progress != null ? r.progress + "%" : ""}`;
        }
      } catch (e) { /* transient poll error — keep trying */ }
    }
  }

  function mountViewerIfReady() {
    const host = $("#wf3dHost");
    if (!host || !window.WF3D) return;
    const d = TASK.draft, t = TASK.texture;
    const tid = (t && t.status === "SUCCEEDED" && t.hasGlb) ? MESHY.texture_tid
      : (d && d.status === "SUCCEEDED" && d.hasGlb) ? MESHY.draft_tid : null;
    if (!tid) return;
    host.style.display = "";
    window.WF3D.mount(host);
    if (host.dataset.loaded !== tid) {
      host.dataset.loaded = tid;
      window.WF3D.load(`/api/studio/glb/${tid}.glb`).catch(() => { host.dataset.loaded = ""; });
    }
  }

  // ---------- glove single-hand mask brush (ported from the removed /studio power tool) ----------

  function maskRedraw() {
    const c = $("#wfMaskCanvas"); if (!c || !MASK.ctx) return;
    const tmp = document.createElement("canvas"); tmp.width = MASK.W; tmp.height = MASK.H;
    tmp.getContext("2d").putImageData(MASK.work, 0, 0);
    MASK.ctx.imageSmoothingEnabled = false;
    MASK.ctx.clearRect(0, 0, c.width, c.height);
    MASK.ctx.drawImage(tmp, 0, 0, MASK.W, MASK.H, 0, 0, c.width, c.height);
  }
  function maskPushUndo() { MASK.undo.push(new Uint8ClampedArray(MASK.work.data)); if (MASK.undo.length > 24) MASK.undo.shift(); }
  function maskPaint(px, py) {
    const mode = (document.querySelector("input[name=wfMaskMode]:checked") || {}).value || "erase";
    const r = +($("#wfMaskBrush") || {}).value || 4, r2 = r * r, W = MASK.W, H = MASK.H;
    const wd = MASK.work.data, od = MASK.orig.data;
    for (let y = Math.max(0, Math.floor(py - r)); y <= Math.min(H - 1, Math.ceil(py + r)); y++)
      for (let x = Math.max(0, Math.floor(px - r)); x <= Math.min(W - 1, Math.ceil(px + r)); x++) {
        const dx = x - px, dy = y - py; if (dx * dx + dy * dy > r2) continue;
        const i = (y * W + x) * 4;
        if (mode === "erase") wd[i + 3] = 0;
        else { wd[i] = od[i]; wd[i + 1] = od[i + 1]; wd[i + 2] = od[i + 2]; wd[i + 3] = od[i + 3]; }
      }
  }
  function maskPos(e) {
    const c = $("#wfMaskCanvas");
    const rect = c.getBoundingClientRect();
    return [(e.clientX - rect.left) * (c.width / rect.width), (e.clientY - rect.top) * (c.height / rect.height)];
  }
  function maskDataURL() {
    const oc = document.createElement("canvas"); oc.width = MASK.W; oc.height = MASK.H;
    oc.getContext("2d").putImageData(MASK.work, 0, 0);
    return oc.toDataURL("image/png");
  }
  async function mountMaskCanvasIfNeeded() {
    const c = $("#wfMaskCanvas");
    if (!c) return;
    const img = await _loadImg(`/api/item/${enc(ITEM.id)}/original.png?t=${Date.now()}`);
    MASK.W = img.naturalWidth; MASK.H = img.naturalHeight;
    MASK.scale = Math.max(4, Math.min(10, Math.floor(260 / Math.max(MASK.W, MASK.H))));
    c.width = MASK.W * MASK.scale; c.height = MASK.H * MASK.scale;
    MASK.ctx = c.getContext("2d");
    const off = document.createElement("canvas"); off.width = MASK.W; off.height = MASK.H;
    const octx = off.getContext("2d"); octx.drawImage(img, 0, 0);
    MASK.orig = octx.getImageData(0, 0, MASK.W, MASK.H);
    MASK.work = new ImageData(new Uint8ClampedArray(MASK.orig.data), MASK.W, MASK.H);
    MASK.undo = [];
    // overlay a previously-saved mask (if any) as the starting working layer, so reopening
    // to tweak an existing mask doesn't lose prior brushing
    try {
      const r = await fetch(`/api/studio/mask/${enc(ITEM.id)}.png?t=${Date.now()}`);
      if (r.ok) {
        const saved = await _loadImg(URL.createObjectURL(await r.blob()));
        if (saved.naturalWidth === MASK.W && saved.naturalHeight === MASK.H) {
          octx.clearRect(0, 0, MASK.W, MASK.H); octx.drawImage(saved, 0, 0);
          MASK.work = octx.getImageData(0, 0, MASK.W, MASK.H);
        }
      }
    } catch (e) { /* no saved mask yet — starting fresh from the original is correct */ }
    maskRedraw();
  }
  async function saveMask() {
    const note = $("#wfMaskNote"); if (note) note.textContent = "saving…";
    const r = await api("POST", "/api/studio/mask/save", { item_id: ITEM.id, png: maskDataURL() });
    if (!r.ok) { if (note) note.textContent = "error: " + (r.error || "failed"); return; }
    ITEM.has_mask = true;
    MASK.editing = false;
    render();
  }

  // ---------- open/reset ----------

  // Stop this item's polling/viewer before loading a different one — no explicit close button
  // now that Generate is a persistent column, so this runs at the top of every (re)open instead.
  function resetPollers() {
    clearInterval(capPoll); capPoll = null;
    clearInterval(taskPoll); taskPoll = null;
    if (window.WF3D) window.WF3D.unmount();
  }

  window.openWorkflow = async function (item) {
    resetPollers();
    MASK.editing = false;
    ITEM = { id: item.id, name: item.name, code: item.code,
             cells: [item.invwidth, item.invheight], type: item.type };
    ensurePanel();
    const r = await reloadState();
    if (!r.ok) { alert("workflow: " + (r.error || "failed to load")); return; }
    GEN_PROMPTS = { restyle: "", nudge: "", negative: "", last_method: "", ...(r.gen_prompts || {}) };
    // last-used method wins on reopen; a never-generated thin weapon defaults to m3 (see
    // _THIN_WEAPON_TYPES in server.py) instead of m7's margin-pad + vivid-style recipe.
    METHOD = GEN_PROMPTS.last_method || (STATE.item.is_thin_weapon ? "m3" : "m7");
    TASK = { draft: null, texture: null };
    render();
    startCapPollIfNeeded();
    if (MESHY.draft_tid || MESHY.texture_tid) startTaskPoll();
  };

  // Continue a Meshy task linked from outside the app (openTaskPicker in app.js) or resumed
  // from the per-item linked-tasks list — loads the item normally, then attaches the task_id
  // as the current draft/texture so the existing poll/render machinery picks it up.
  window.adoptMeshyTask = async function (item, taskId, phase) {
    await window.openWorkflow(item);
    const isTexture = phase === "texture";
    MESHY = { ...MESHY, [isTexture ? "texture_tid" : "draft_tid"]: taskId, phase: isTexture ? "texture" : "draft" };
    await api("PUT", `/api/upscale/${enc(item.id)}/meshy`,
              isTexture ? { texture_tid: taskId, phase: "texture" } : { draft_tid: taskId, phase: "draft" });
    TASK = { draft: null, texture: null };
    render();
    startTaskPoll();
  };
})();
