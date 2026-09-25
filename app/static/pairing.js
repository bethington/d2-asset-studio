import { PairPreview } from './pair3d.js';

/* Pairing view, anchored on PD2 DC6 ART FILES.
   LEFT  = the original game artwork (rendered from the DC6).
   RIGHT = the Meshy generations matched to it — several when you re-imagined the same
           sprite more than once (invtgl has four), so you choose which one to use. */
const $ = (s) => document.querySelector(s);
let PAIRS = [], UNPAIRED = [], IGNORED = [], ALL_ITEMS = [];

function toast(msg, bad) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (bad ? " bad" : "");
  setTimeout(() => (t.className = "toast hidden"), 3800);
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const dc6Url = (f) => `/api/dc6/${encodeURIComponent(f)}.png`;
const spriteUrl = (id) => `/api/item/${encodeURIComponent(id)}/original.png`;

async function loadAllItems() {
  try { ALL_ITEMS = (await (await fetch("/api/items")).json()).items || []; }
  catch (e) { ALL_ITEMS = []; }
}

/* Rescan re-derives links from the re-imagined art library, then reloads the view. */
async function rescan() {
  $("#scanState").textContent = "scanning…";
  try {
    const r = await (await fetch("/api/meshy/links/scan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pages: 2 }),
    })).json();
    if (r.ok) $("#scanState").textContent =
      `${r.scanned} scanned · ${(r.auto || []).length} matched exactly`;
  } catch (e) { /* fall through to load() which reports */ }
  await load();
}

async function load() {
  $("#rows").innerHTML = '<div class="empty">Loading…</div>';
  let d;
  try { d = await (await fetch("/api/meshy/pairs")).json(); }
  catch (e) { d = { ok: false, error: String(e) }; }
  if (!d.ok) {
    $("#rows").innerHTML = `<div class="empty">Failed: ${esc(d.error || "")}</div>`;
    return;
  }
  PAIRS = d.pairs || []; UNPAIRED = d.unpaired || []; IGNORED = d.ignored || [];
  const gens = PAIRS.reduce((n, p) => n + p.generations.length, 0);
  $("#scanState").textContent = "matched via your re-imagined art library";
  $("#counts").textContent =
    `${PAIRS.length} art files paired · ${gens} generations · ${UNPAIRED.length} unplaced` +
    (IGNORED.length ? ` · ${IGNORED.length} marked none` : "");
  render();
}

/* GLB url with a cache-busting revision. Re-pointing a link at its TEXTURED model must
   change the URL: the old response was stored under a long-lived Cache-Control, so the
   browser would otherwise keep serving the untextured copy it already has. */
function modelUrl(taskId) {
  let rev = taskId;
  for (const p of PAIRS) {
    const g = (p.generations || []).find((x) => x.task_id === taskId);
    if (g && g.model_rev) { rev = g.model_rev; break; }
  }
  return `/api/pair/model/${encodeURIComponent(taskId)}.glb?v=${encodeURIComponent(rev)}`;
}

/* Pose values held per MODEL rather than per art file. All three interact with the
   individual model's proportions — measured across one row, the same separation gave
   0.124 clearance on one generation and 0.000 on its three siblings — so a shared value
   cannot look right on all four. Camera and margin stay shared. */
const OWN_POSE = ["yaw", "gap", "depth"];

/* The pose to render a given generation with: the row's shared values, with this model's
   own tilt/separation/depth laid over the top. */
function poseFor(row, taskId) {
  const pose = Object.assign({ yaw: 0, gap: 0.55, depth: 0, azim: 0, elev: 0, margin: 1.06 },
                             (row && row.template && row.template.pose3d) || {});
  const g = ((row && row.generations) || []).find((x) => x.task_id === taskId);
  if (g) {
    for (const k of OWN_POSE) {
      if (g[k] !== null && g[k] !== undefined) pose[k] = g[k];
    }
  }
  return pose;
}

function genCard(p, g) {
  const id = `g_${p.invfile}_${g.task_id}`;
  const label = g.name || g.prompt || g.task_id.slice(0, 8);
  // Render the 3D pair thumbnail whenever a model is available (has_model or a textured child),
  // NOT only when the DRAFT task still has a preview URL \u2014 a draft can 404 after texturing while
  // its textured model is perfectly renderable (that left the primary card blank).
  const canRender = (g.renderable || g.preview) && p.pairable && g.hand;
  return `<div class="cand${g.primary ? " chosen" : ""}">
    <input type="radio" name="p_${p.invfile}" id="${id}" ${g.primary ? "checked" : ""}
           data-invfile="${esc(p.invfile)}" data-task="${esc(g.task_id)}">
    <label for="${id}">
      <div class="ph checker">${canRender
        ? `<img class="pairthumb" data-thumb="${esc(g.task_id)}" data-invfile="${esc(p.invfile)}"
                ${g.has_thumb ? `src="/api/pair/thumb/${encodeURIComponent(g.task_id)}.png"` : 'data-missing="1"'}
                alt="" onerror="this.dataset.missing='1'; this.removeAttribute('src');">`
        : (g.input_image
            ? `<img loading="lazy" src="${esc(g.input_image)}" onerror="this.style.opacity=.15">` : "")}</div>
      <div class="nm" title="${esc(label)}">${esc(label)}</div>

      ${canRender
        ? `<button class="tunebtn" data-tune3d="${esc(p.invfile)}" data-task3d="${esc(g.task_id)}"
                   title="pose this pair \u2014 tilt, separation and depth are this model's own">\u2337 tune</button>` : ""}
    </label>
    <button class="xbtn" data-unlink="${esc(g.task_id)}" title="not this art file — free this generation">✕</button>
  </div>`;
}

// ALL re-imagined variants as slots: generated ones are marked; empty ones show the redraw
// and a Generate button that kicks off a Meshy draft straight from that variant's art.
function variantStrip(p) {
  const av = p.all_variants || [];
  if (!av.length) return "";
  const done = av.filter((v) => v.generated).length;
  return `<div class="variants">
    <div class="vhead">Variants — ${done}/${av.length} generated${done < av.length
      ? ` · empty slots generate from your re-imagined redraws` : ""}</div>
    <div class="vgrid">${av.map((v) => variantSlot(p, v)).join("")}</div>
  </div>`;
}
function variantSlot(p, v) {
  const art = (v.left && v.left.art_file) || (v.right && v.right.art_file) || "";
  const img = art ? `/api/reimagined/${encodeURIComponent(art)}` : "";
  const hands = [v.left.task_id ? "L" : "", v.right.task_id ? "R" : ""].filter(Boolean).join("+");
  return `<div class="vslot ${v.generated ? "done" : "todo"}" title="${esc(v.label)}${v.generated ? " (generated)" : " (not generated)"}">
    <div class="vthumb checker">${img ? `<img loading="lazy" src="${img}" onerror="this.style.opacity=.15">` : ""}
      ${v.generated ? `<span class="vbadge">✓ ${hands || "gen"}</span>` : ""}</div>
    <div class="vlabel">${esc(v.label)}</div>
    ${v.generated ? "" : `<button class="vgen" data-invfile="${esc(p.invfile)}" data-art="${esc(art)}">⚒ generate</button>`}
  </div>`;
}
async function pollVariant(tid, btn) {
  for (let i = 0; i < 90; i++) {
    await new Promise((r) => setTimeout(r, 4000));
    let t; try { t = await (await fetch(`/api/studio/task/${tid}`)).json(); } catch (e) { continue; }
    if (btn) btn.textContent = `… ${t.status || ""} ${t.progress || 0}%`;
    if (t.status === "SUCCEEDED") { toast("variant generated — refreshing"); load(); return; }
    if (t.status === "FAILED" || t.status === "CANCELED") {
      toast("generation " + t.status, true);
      if (btn) { btn.disabled = false; btn.textContent = "⚒ generate"; }
      return;
    }
  }
}

function render() {
  const rows = $("#rows");
  if (!PAIRS.length && !UNPAIRED.length) {
    rows.innerHTML = '<div class="empty">Nothing paired yet — hit Rescan.</div>';
    return;
  }
  const paired = PAIRS.map((p) => `
    <div class="prow" data-row="${esc(p.invfile)}">
      <div class="head">
        <span class="tname">${esc(p.invfile)}.dc6</span>
        <span class="tmeta">${p.item_count
          ? esc(p.items.slice(0, 4).join(", ")) + (p.item_count > 4 ? ` +${p.item_count - 4} more` : "")
          : "no catalog item uses this file"}</span>
        <span class="rowact">
          <a href="/?item=${encodeURIComponent(p.item_id || "")}"><button title="Open this item in the gallery — Details + Generate (column 3) populate automatically">⚒ Open in gallery</button></a>
        </span>
      </div>
      <div class="body">
        <div class="anchor">
          <figure>
            <img class="checker" src="${p.item_id ? spriteUrl(p.item_id) : dc6Url(p.invfile)}"
                 onerror="this.src='${dc6Url(p.invfile)}'">
            <figcaption>original game art</figcaption>
          </figure>
        </div>
        <div class="cands">
          ${p.generations.map((g) => genCard(p, g)).join("")}
          <div class="cand none">
            <input type="radio" name="p_${p.invfile}" id="none_${p.invfile}"
                   data-none="${esc(p.invfile)}">
            <label for="none_${p.invfile}">none —<br>don't link<br>this art file</label>
          </div>
        </div>
      </div>
      ${variantStrip(p)}
    </div>`).join("");

  const unp = UNPAIRED.length ? `
    <div class="prow unplaced">
      <div class="head">
        <span class="tname">Unplaced generations</span>
        <span class="tmeta">no re-imagined art file matched these — assign them by hand</span>
      </div>
      <div class="body"><div class="cands">
        ${UNPAIRED.map((u) => `
          <div class="cand">
            <label class="static">
              <div class="ph checker">${u.input_image
                ? `<img loading="lazy" src="${esc(u.input_image)}">` : ""}</div>
              <div class="nm">${esc(u.name || u.prompt || u.task_id.slice(0, 8))}</div>
            </label>
            <input type="search" class="assign" data-task="${esc(u.task_id)}"
                   placeholder="assign to item…" spellcheck="false">
            <button class="nonebtn" data-ignore="${esc(u.task_id)}"
                    title="never pair this one">none</button>
            <div class="hits" data-hits="${esc(u.task_id)}"></div>
          </div>`).join("")}
      </div></div>
    </div>` : "";

  const ign = IGNORED.length ? `
    <div class="prow ignored">
      <div class="head">
        <span class="tname">Marked "none"</span>
        <span class="tmeta">kept out of pairing and out of rescans — restore any time</span>
      </div>
      <div class="body"><div class="cands">
        ${IGNORED.map((u) => `
          <div class="cand">
            <label class="static">
              <div class="ph checker">${u.input_image
                ? `<img loading="lazy" src="${esc(u.input_image)}">` : ""}</div>
              <div class="nm">${esc(u.name || u.prompt || u.task_id.slice(0, 8))}</div>
            </label>
            <button class="nonebtn" data-restore="${esc(u.task_id)}">restore</button>
          </div>`).join("")}
      </div></div>
    </div>` : "";

  rows.innerHTML = paired + unp + ign;

  rows.querySelectorAll('input[type=radio]').forEach((r) => {
    r.onchange = async () => {
      const res = await (await fetch("/api/meshy/primary", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: r.dataset.task, invfile: r.dataset.invfile }),
      })).json();
      if (!res.ok) return toast("could not set: " + (res.error || ""), true);
      r.closest(".cands").querySelectorAll(".cand").forEach((c) => c.classList.remove("chosen"));
      r.closest(".cand").classList.add("chosen");
      toast(`${r.dataset.invfile}.dc6 will use this generation`);
    };
  });
  rows.querySelectorAll(".assign").forEach((inp) => {
    inp.oninput = () => renderHits(inp.dataset.task, inp.value.trim());
  });
  rows.querySelectorAll("[data-none]").forEach((r) => {
    r.onchange = async () => {
      const f = r.dataset.none;
      const res = await (await fetch("/api/meshy/none", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ invfile: f }),
      })).json();
      if (!res.ok) return toast("failed: " + (res.error || ""), true);
      toast(`${f}.dc6 unlinked — ${res.freed.length} generation(s) moved to Unplaced`);
      load();
    };
  });
  rows.querySelectorAll("[data-unlink]").forEach((b) => {
    b.onclick = async () => {
      await fetch(`/api/meshy/links/${b.dataset.unlink}`, { method: "DELETE" });
      toast("freed — it's back in Unplaced");
      load();
    };
  });
  rows.querySelectorAll(".vgen").forEach((b) => {
    b.onclick = async () => {
      b.disabled = true; b.textContent = "submitting…";
      const res = await (await fetch("/api/studio/generate-from-art", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ invfile: b.dataset.invfile, art_file: b.dataset.art }),
      })).json();
      if (!res.ok) { toast("generate failed: " + (res.error || ""), true); b.disabled = false; b.textContent = "⚒ generate"; return; }
      toast(`generating ${res.art_file}… this slot fills when Meshy finishes`);
      pollVariant(res.task_id, b);
    };
  });
  rows.querySelectorAll("[data-ignore]").forEach((b) => {
    b.onclick = async () => {
      await fetch("/api/meshy/ignore", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: b.dataset.ignore, ignored: true }),
      });
      toast("marked none — it won't come back on rescan");
      load();
    };
  });
  // per-card tune: pose using THIS card's model, with its own tilt/separation/depth
  rows.querySelectorAll("[data-tune3d]").forEach((b) => {
    b.onclick = (e) => {
      // the button lives inside the card's <label>, so a bare click would also toggle
      // the radio — suppress that so tuning doesn't re-pick the generation
      e.preventDefault();
      e.stopPropagation();
      const p = PAIRS.find((x) => x.invfile === b.dataset.tune3d);
      if (p) openTuner3d(p, { left: b.dataset.task3d });
    };
  });
  queueThumbs();
  rows.querySelectorAll("[data-restore]").forEach((b) => {
    b.onclick = async () => {
      await fetch("/api/meshy/ignore", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: b.dataset.restore, ignored: false }),
      });
      toast("restored");
      load();
    };
  });
}

function renderHits(taskId, q) {
  const box = document.querySelector(`[data-hits="${CSS.escape(taskId)}"]`);
  if (!box) return;
  if (q.length < 2) { box.innerHTML = ""; return; }
  const ql = q.toLowerCase();
  const seen = new Set(), hits = [];
  for (const i of ALL_ITEMS) {
    const f = (i.invfile || "").toLowerCase();
    if (seen.has(f)) continue;                    // one entry per ART FILE, not per item
    if (i.name.toLowerCase().includes(ql) || i.code.toLowerCase().includes(ql) || f.includes(ql)) {
      seen.add(f); hits.push(i);
      if (hits.length >= 6) break;
    }
  }
  box.innerHTML = hits.map((i) => `
    <div class="hit" data-item="${esc(i.id)}" data-invfile="${esc((i.invfile || "").toLowerCase())}"
         data-task="${esc(taskId)}" title="${esc(i.name)}">
      <img src="${spriteUrl(i.id)}" onerror="this.style.opacity=.15">
      <span>${esc((i.invfile || "").toLowerCase())}.dc6</span>
    </div>`).join("") || "<div class='why'>no match</div>";
  box.querySelectorAll(".hit").forEach((h) => {
    h.onclick = async () => {
      const r = await (await fetch("/api/meshy/links", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: h.dataset.task, item_id: h.dataset.item,
                               invfile: h.dataset.invfile, source: "manual" }),
      })).json();
      if (!r.ok) return toast("link failed: " + (r.error || ""), true);
      toast(`assigned to ${h.dataset.invfile}.dc6`);
      load();
    };
  });
}

$("#rescanBtn").onclick = rescan;
$("#acceptAllBtn").onclick = acceptAll;
$("#texBtn").onclick = async () => {
  const btn = $("#texBtn"); btn.disabled = true; const t0 = btn.textContent; btn.textContent = "resolving…";
  try {
    const r = await (await fetch("/api/meshy/use-textured", { method: "POST" })).json();
    if (!r.ok) { toast("resolve failed: " + (r.error || ""), true); return; }
    toast(r.updated ? `${r.updated} generation(s) now use their textured model` : "all generations already textured (or none textured yet)");
    if (r.updated) await load();
  } finally { btn.disabled = false; btn.textContent = t0; }
};
loadAllItems().then(load);




/* ---------- 3D pair: engine choice, live preview, build ---------- */

let ENGINES = null;
async function engines() {
  if (!ENGINES) {
    try { ENGINES = await (await fetch("/api/pair/engines")).json(); }
    catch (e) { ENGINES = { browser: true, blender: false }; }
  }
  return ENGINES;
}

/* Asked on EVERY build so the choice is never made for you. Blender is optional:
   when it is absent the option is shown disabled with the reason, never hidden. */
async function askEngine() {
  const e = await engines();
  return new Promise((resolve) => {
    const m = document.createElement("div");
    m.className = "engineask";
    m.innerHTML = `
      <div class="box">
        <h4>Render with…</h4>
        <div class="opt" data-e="browser">
          <b>Browser</b>
          <span>Fast — WebGL, same lights and camera as Blender. Downloads each model, renders instantly.</span>
        </div>
        <div class="opt ${e.blender ? "" : "disabled"}" data-e="${e.blender ? "blender" : ""}">
          <b>Blender</b>
          <span>${e.blender
            ? "Slower (~15-30s each) — path-traced shadows, deterministic. Matches your existing art."
            : (e.blender_note || "Not installed")}</span>
        </div>
        <div class="anglerow"><button data-e="">Cancel</button></div>
      </div>`;
    document.body.appendChild(m);
    m.querySelectorAll("[data-e]").forEach((el) => {
      el.onclick = () => {
        if (el.classList.contains("disabled")) return;
        m.remove();
        resolve(el.dataset.e || null);
      };
    });
  });
}

/* Fetch a cached preview thumbnail as a data URL, or null if none is cached. */
async function thumbDataURL(taskId) {
  try {
    const r = await fetch(`/api/pair/thumb/${encodeURIComponent(taskId)}.png?t=${Date.now()}`);
    if (!r.ok) return null;
    const blob = await r.blob();
    return await new Promise((res) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.readAsDataURL(blob); });
  } catch (e) { return null; }
}

/* Build ONE pair from a generation and activate it as the item's DC6. Returns the server
   JSON ({ok,error}). Shared by the single-build path and Accept-all.

   Browser engine: REUSE the exact preview thumbnail you already see (no re-render) when one is
   cached -- so the accepted DC6 is literally the image on the card. Only render when there is no
   cached preview (or Blender was chosen). */
async function buildPair(invfile, taskId, engine, gen) {
  const pose = poseFor(PAIRS.find((x) => x.invfile === invfile), taskId);
  if (engine === "blender") {
    return await (await fetch("/api/pair/build3d/blender", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invfile, task_id: taskId, pose }),
    })).json();
  }
  if (gen && gen.has_thumb) {           // reuse the exact preview image
    const png = await thumbDataURL(taskId);
    if (png) return await (await fetch("/api/pair/build3d", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invfile, png, pose, engine: "browser" }),
    })).json();
  }
  const row = PAIRS.find((x) => x.invfile === invfile) || {};
  const cells = row.cells || [2, 2];
  const K = 8;                          // supersample, then the server fits it down
  const cv = document.createElement("canvas");
  cv.width = cells[0] * 29 * K; cv.height = cells[1] * 29 * K;
  const prev = new PairPreview(cv);
  try {
    await prev.load(modelUrl(taskId));
    const gsq = ((row.generations || []).find((x) => x.task_id === taskId) || {}).squaring;
    prev.square(gsq);                   // build with the same squaring the preview showed
    prev.pose(pose).setFrameArgs(pose).frame(pose).render();
    const png = cv.toDataURL("image/png");
    return await (await fetch("/api/pair/build3d", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invfile, png, pose, engine: "browser" }),
    })).json();
  } finally {
    prev.dispose();                     // free the WebGL context (batch builds would leak them)
  }
}

async function build3d(invfile, taskId, engine, btn) {
  const label = btn.textContent; btn.disabled = true;
  btn.textContent = engine === "blender" ? "rendering in Blender…" : "rendering…";
  try {
    const r = await buildPair(invfile, taskId, engine);
    if (!r || !r.ok) return toast("build failed: " + ((r && r.error) || ""), true);
    toast(`${invfile}.dc6 built — Push to game to see it`);
  } catch (err) {
    toast("3D build failed: " + String(err).slice(0, 140), true);
  } finally {
    btn.disabled = false; btn.textContent = label;
  }
}

/* Build a NON-pair (single-model) generation and activate it. Honors the engine choice:
   Browser renders one model in WebGL (same as the pair path) and posts the PNG; Blender renders
   it server-side. */
/* Single-model items (weapons, armour, amulets...) show their 2D card image (the redraw fed to
   Meshy), not a 3D render. Build the DC6 straight FROM that image -- exact WYSIWYG, and it needs
   no texture or pose since the picture already has both. Falls back to rendering the 3D model
   only if there is no card image. */
async function buildSingle(taskId, engine, gen) {
  if (gen && gen.input_image) {
    return await (await fetch("/api/pair/build-single", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: taskId, image_url: gen.input_image }),
    })).json();
  }
  if (engine === "blender") {
    return await (await fetch("/api/pair/build-single", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: taskId }),
    })).json();
  }
  const cv = document.createElement("canvas");
  cv.width = 512; cv.height = 512;
  const prev = new PairPreview(cv);
  try {
    await prev.load(modelUrl(taskId), { single: true });   // one model, no mirror
    prev.pose({}).frame({ azim: 25, elev: 15, margin: 1.06 }).render();
    const png = cv.toDataURL("image/png");
    return await (await fetch("/api/pair/build-single", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: taskId, png, azim: 25, elev: 15 }),
    })).json();
  } finally {
    prev.dispose();
  }
}

/* Accept ALL selections: build + activate every row's chosen (primary) generation — gloves as
   PAIRS, everything else as a single model. You pick the engine (Browser or Blender) and it
   applies to the whole batch; sequential so the renders don't pile up. */
async function acceptAll() {
  const targets = [];
  for (const p of PAIRS) {
    const prim = (p.generations || []).find((g) =>
      g.primary && (g.renderable || g.preview || g.has_model) && (p.pairable ? g.hand : true));
    if (prim) targets.push({ invfile: p.invfile, taskId: prim.task_id, pairable: !!p.pairable, gen: prim });
  }
  const btn = $("#acceptAllBtn");
  if (!targets.length) return toast("no selections to accept", true);
  const engine = await askEngine();                 // Browser or Blender — your choice
  if (!engine) return;
  const reused = engine === "browser" ? targets.filter((t) => t.gen && t.gen.has_thumb).length : 0;
  btn.disabled = true;
  let ok = 0, fail = 0;
  for (let i = 0; i < targets.length; i++) {
    const t = targets[i];
    const verb = (engine === "browser" && t.gen && t.gen.has_thumb) ? "using preview" : "building";
    btn.textContent = `${verb} ${i + 1}/${targets.length} — ${t.invfile}…`;
    try {
      const r = t.pairable ? await buildPair(t.invfile, t.taskId, engine, t.gen)
                           : await buildSingle(t.taskId, engine, t.gen);
      if (r && r.ok) ok++; else { fail++; toast(`${t.invfile}: ${(r && r.error) || "failed"}`, true); }
    } catch (err) { fail++; toast(`${t.invfile}: ${String(err).slice(0, 80)}`, true); }
  }
  btn.disabled = false; btn.textContent = "✓ Accept all";
  toast(`Accepted ${ok}/${targets.length}${reused ? ` · ${reused} reused the preview` : ""}${fail ? ` · ${fail} failed` : ""} — Push to game`);
}

// pose shared by preview and both renderers; matches make_pair()'s contract
const POSE = { yaw: 0, gap: 0.55, depth: 0, azim: 0, elev: 0, margin: 1.06 };


/* ---------- live 3D pair tuner ---------- */

function openTuner3d(p, pick) {
  const invfile = p.invfile;
  const taskId = pick.left || pick.right;
  const pose = poseFor(p, taskId);
  const saved = JSON.parse(JSON.stringify(pose));

  const m = document.createElement("div");
  m.className = "tuner";
  const CTL = [
    ["yaw", "tilt apart", -60, 60, 1, "\u00b0"],
    ["gap", "separation", 0, 1.6, 0.01, "\u00d7"],
    ["depth", "one hand forward", -1, 1.5, 0.01, "\u00d7"],
    ["azim", "camera around (0 = straight on)", -180, 180, 1, "\u00b0"],
    ["elev", "camera height", -60, 80, 1, "\u00b0"],
  ];
  m.innerHTML = `
    <div class="tunerbox">
      <h3>${esc(invfile)}.dc6 \u2014 pose the 3D pair</h3>
      <div class="tunerwrap">
        <div class="spriteview">
          <canvas class="stage3d" width="420" height="420"></canvas>
          <canvas class="stageout" width="420" height="420"></canvas>
          <div class="spritepx"><canvas class="stagepx"></canvas><span>actual size</span></div>
        </div>
      </div>
      <div class="tunerhelp" id="t3state">loading model\u2026</div>
      ${CTL.map(([k, lbl, lo, hi, st, unit]) => `
        <div class="ctlrow">
          <span class="ctlname">${lbl}</span>
          <input type="range" data-p3="${k}" min="${lo}" max="${hi}" step="${st}">
          <output data-o3="${k}"></output><span class="why">${unit}</span>
        </div>`).join("")}
      <div class="tuneracts">
        <button id="t3square" title="dial in a true top-down view with the camera sliders, then press this: the picture stays put but the axes are re-based, so tilt apart fans the hands out instead of curling them">⌖ Set as top-down</button>
        <button id="t3unsquare" class="ghost" title="drop this model's squaring and go back to the raw model">clear squaring</button>
        <button id="t3reset">reset pose</button>
        <span class="count" id="t3note"></span>
        <button id="t3cancel">cancel</button>
        <button class="gold" id="t3save">Save pose</button>
      </div>
    </div>`;
  document.body.appendChild(m);

  const canvas = m.querySelector(".stage3d");      // hidden: the raw render
  const out = m.querySelector(".stageout");        // shown: the sprite as it will be built
  const px = m.querySelector(".stagepx");          // 1:1 sprite-sized inset
  const cells = p.cells || [2, 2];
  const CELL = 29;
  const spriteW = cells[0] * CELL, spriteH = cells[1] * CELL;
  // the large view keeps the sprite's aspect ratio, so the border is the real edge
  const viewH = 420, viewW = Math.max(80, Math.round(viewH * spriteW / spriteH));
  out.width = viewW; out.height = viewH;
  px.width = spriteW; px.height = spriteH;
  let prev = null, raf = 0;

  /* Reproduce assets.fit_png_to_cell(): crop to the alpha bbox, scale so the constraining
     side reaches `fill` of the cell (aspect preserved), centre. Without this the border
     would be decorative — the build discards empty space and rescales, so a pose that
     looks tight here would come out larger than expected. */
  // the render canvas is WebGL, so getContext("2d") on it returns null — copy through an
  // offscreen 2D canvas to read the alpha
  const scratch = document.createElement("canvas");
  function projectToSprite() {
    const src = canvas;
    const w = src.width, h = src.height;
    scratch.width = w; scratch.height = h;
    const sctx = scratch.getContext("2d", { willReadFrequently: true });
    sctx.clearRect(0, 0, w, h);
    sctx.drawImage(src, 0, 0);
    let data;
    try { data = sctx.getImageData(0, 0, w, h).data; } catch (e) { return; }
    let minX = w, minY = h, maxX = -1, maxY = -1;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (data[(y * w + x) * 4 + 3] > 16) {
          if (x < minX) minX = x;
          if (x > maxX) maxX = x;
          if (y < minY) minY = y;
          if (y > maxY) maxY = y;
        }
      }
    }
    for (const [cv, cw, ch, smooth] of [[out, viewW, viewH, true], [px, spriteW, spriteH, false]]) {
      const g = cv.getContext("2d");
      g.imageSmoothingEnabled = smooth;      // the inset stays hard-edged: real pixels
      g.fillStyle = "#000";
      g.fillRect(0, 0, cw, ch);
      if (maxX < 0) continue;
      const bw = maxX - minX + 1, bh = maxY - minY + 1;
      const FILL = 1.0;                                  // matches the 3D build path
      const s = Math.min(cw * FILL / bw, ch * FILL / bh);
      const dw = Math.max(1, Math.round(bw * s)), dh = Math.max(1, Math.round(bh * s));
      g.drawImage(src, minX, minY, bw, bh,
                  Math.round((cw - dw) / 2), Math.round((ch - dh) / 2), dw, dh);
    }
  }

  function draw() {
    if (!prev) return;
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      prev.pose(pose).setFrameArgs(pose).frame(pose).render();
      projectToSprite();
    });
  }
  function syncControls() {
    for (const [k] of CTL) {
      const r = m.querySelector(`[data-p3="${k}"]`);
      const o = m.querySelector(`[data-o3="${k}"]`);
      r.value = pose[k];
      o.textContent = (k === "gap" || k === "depth") ? pose[k].toFixed(2) : Math.round(pose[k]);
    }
  }
  syncControls();

  // the squaring lives on the generation, not the art file: how crooked a model sits is
  // a property of that one Meshy result
  const gen = (p.generations || []).find((g) => g.task_id === taskId) || {};
  let squaring = gen.squaring || null;

  function squareNote() {
    const el = m.querySelector("#t3note");
    if (el) el.textContent = squaring ? "squared \u2713" : "not squared yet";
    const btn = m.querySelector("#t3unsquare");
    if (btn) btn.disabled = !squaring;
  }

  import("/static/pair3d.js").then(async (mod) => {
    try {
      prev = new mod.PairPreview(canvas);
      await prev.load(modelUrl(taskId));
      prev.square(squaring);
      m.querySelector("#t3state").innerHTML =
        "this is the <b>built sprite</b> \u2014 cropped and scaled to fill the cell exactly as " +
        "the build does, so the border is the real edge and the inset is actual size. " +
        "Set the camera height for a true top-down view, then press <b>Set as top-down</b>: " +
        "the picture stays put but the axes are re-based, so <i>tilt apart</i> fans the " +
        "hands out instead of curling them together.";
      squareNote();
      draw();
      setTimeout(refreshDc6, 120);       // show the real DC6 as soon as the pose is up
    } catch (e) {
      m.querySelector("#t3state").innerHTML =
        `<b>could not load the 3D model</b> \u2014 ${esc(String(e).slice(0, 120))}`;
    }
  });

  /* The actual-size inset, run through the REAL DC6 encoder so it shows the palette
     quantisation rather than an approximation of it. Fired on release, not on every drag
     frame, so posing stays smooth. */
  let dc6Busy = false;
  async function refreshDc6() {
    if (dc6Busy || !prev) return;
    dc6Busy = true;
    try {
      const src = m.querySelector(".stageout");
      const r = await (await fetch("/api/pair/dc6preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ invfile, png: src.toDataURL("image/png") }),
      })).json();
      if (!r.ok) { dc6Note("DC6 preview failed"); return; }
      const img = new Image();
      img.onload = () => {
        const g = px.getContext("2d");
        g.imageSmoothingEnabled = false;
        g.fillStyle = "#000";
        g.fillRect(0, 0, px.width, px.height);
        g.drawImage(img, 0, 0, px.width, px.height);
        dc6Note(`DC6 · ${(r.bytes / 1024).toFixed(1)} KB`);
      };
      img.src = r.png;
    } catch (e) {
      dc6Note("DC6 preview failed");
    } finally {
      dc6Busy = false;
    }
  }
  function dc6Note(t) {
    const el = m.querySelector(".spritepx span");
    if (el) el.textContent = t;
  }

  m.querySelectorAll("[data-p3]").forEach((r) => {
    r.oninput = () => { pose[r.dataset.p3] = parseFloat(r.value); syncControls(); draw(); };
    // on release: what the game will actually get, palette and all
    r.onchange = () => { dc6Note("quantising…"); setTimeout(refreshDc6, 60); };
  });
  // reset returns the LAYOUT to defaults but keeps the squaring, which is the hard-won
  // part; "clear squaring" is the explicit way back to the raw model
  m.querySelector("#t3reset").onclick = () => {
    Object.assign(pose, { yaw: 0, gap: 0.55, depth: 0, azim: 0, elev: 0, margin: 1.06 });
    syncControls(); draw();
  };

  m.querySelector("#t3square").onclick = async () => {
    const r = await (await fetch(`/api/pair/square/${encodeURIComponent(taskId)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ azim: pose.azim, elev: pose.elev }),
    })).json();
    if (!r.ok) return toast(r.error || "could not set top-down", true);
    squaring = r.squaring;
    gen.squaring = r.squaring;
    // the camera correction now lives in the model, so the sliders go back to zero and
    // the picture is unchanged
    pose.azim = 0; pose.elev = 0;
    if (prev) prev.square(squaring);
    syncControls(); squareNote(); draw();
    toast("top-down set — camera back to 0, tilt now fans the hands out");
  };

  m.querySelector("#t3unsquare").onclick = async () => {
    const r = await (await fetch(`/api/pair/square/${encodeURIComponent(taskId)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clear: true }),
    })).json();
    if (!r.ok) return toast(r.error || "could not clear", true);
    squaring = null;
    delete gen.squaring;
    if (prev) prev.square(null);
    squareNote(); draw();
    toast("squaring cleared — back to the raw model");
  };
  const close = () => { cancelAnimationFrame(raf); m.remove(); };
  m.querySelector("#t3cancel").onclick = () => { Object.assign(pose, saved); close(); };
  m.querySelector("#t3save").onclick = async () => {
    // Tilt, separation and depth belong to THIS model; only the camera is shared by the
    // art file. Saving them to the row would move the other generations, which is exactly
    // how tuning one glove used to wreck its siblings.
    const shared = Object.assign({}, pose);
    for (const k of OWN_POSE) delete shared[k];
    const body = Object.assign({}, p.template || {},
                               { pose3d: Object.assign({}, (p.template || {}).pose3d, shared) });
    const r = await (await fetch(`/api/pair/template/${encodeURIComponent(invfile)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })).json();
    if (!r.ok) return toast("save failed", true);
    const own = {};
    for (const k of OWN_POSE) own[k] = pose[k];
    const rg = await (await fetch(`/api/pair/gap/${encodeURIComponent(taskId)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(own),
    })).json();
    if (!rg.ok) return toast("per-model pose save failed", true);
    p.template = r.template;
    const gen = (p.generations || []).find((x) => x.task_id === taskId);
    if (gen) Object.assign(gen, rg.own || {});
    toast("pose saved — tilt, separation and depth apply to this model only");
    close();
    refreshThumbs(invfile);
  };
}


/* ---------- pair thumbnails: rendered once here, cached on the server ----------
 * Each model is ~7MB and a row can hold four, so a generation is rendered at most once
 * per visit and the PNG is uploaded; later visits just load the cached image.
 */
const THUMB_DONE = new Set();
let thumbBusy = false;

async function queueThumbs() {
  if (thumbBusy) return;
  const pending = [...document.querySelectorAll(".pairthumb")]
    .filter((el) => el.dataset.missing === "1" && !THUMB_DONE.has(el.dataset.thumb));
  if (!pending.length) return;
  thumbBusy = true;
  try {
    const mod = await import("/static/pair3d.js");
    for (const el of pending) {
      const taskId = el.dataset.thumb;
      if (THUMB_DONE.has(taskId)) continue;
      THUMB_DONE.add(taskId);                 // one attempt per generation per visit
      const row = PAIRS.find((x) => x.invfile === el.dataset.invfile) || {};
      const pose = poseFor(row, taskId);
      const ph = el.closest(".ph");
      try {
        if (ph) ph.classList.add("rendering");
        const cv = document.createElement("canvas");
        cv.width = 256; cv.height = 256;
        const prev = new mod.PairPreview(cv);
        await prev.load(modelUrl(taskId));
        const g = (row.generations || []).find((x) => x.task_id === taskId);
        prev.square(g && g.squaring);          // per-model top-down correction
        prev.pose(pose).setFrameArgs(pose).frame(pose).render();
        const png = cv.toDataURL("image/png");
        await fetch(`/api/pair/thumb/${encodeURIComponent(taskId)}`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ png }),
        });
        el.src = png;
        delete el.dataset.missing;
      } catch (e) {
        // no model yet, or the GLB failed: leave the card image blank rather than break
        const why = el.closest(".cand") && el.closest(".cand").querySelector(".why");
        if (why && !/preview failed/.test(why.textContent)) why.textContent += " \u00b7 preview failed";
      } finally {
        if (ph) ph.classList.remove("rendering");
      }
    }
  } finally {
    thumbBusy = false;
  }
}

/* A pose change invalidates every cached thumbnail for that art file. */
async function refreshThumbs(invfile) {
  await fetch(`/api/pair/thumb/${encodeURIComponent(invfile)}/all`, { method: "DELETE" });
  THUMB_DONE.clear();
  await load();
}
