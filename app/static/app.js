const $ = (s) => document.querySelector(s);
let ITEMS = [];
let SELECTED = null;
let HAS_BLENDER = false;
// Which alternate the item panel is PREVIEWING (not necessarily active). Clicking a tile only
// previews; a tile's checkbox is the only thing that activates. Reset when the item changes.
let PREVIEWING = null;
let _stripScroll = {};   // capture/restore strip scrollLeft across same-item re-renders
// bumped whenever an active item's art changes on disk (refit) so gallery tiles — whose cache key
// is otherwise just the active choice NAME — actually re-fetch current.png.
let ART_BUST = 0;
// Remembered detail/Equipped selections — persist across items AND page reloads (localStorage).
// `tab` and `class` are *preferences*: a per-item fallback (an unsupported tab, or a class with no
// art for this item) changes only what's shown, never the stored preference — so you snap back to
// your choice on the next item that supports it.
const EQ_PREFS = (() => {
  const def = { tab: "item", class: "barbarian", mode: "NU", dir: 0, itemOnly: false };
  try { return Object.assign(def, JSON.parse(localStorage.getItem("as_eqprefs") || "{}")); }
  catch (e) { return def; }
})();
const savePrefs = () => { try { localStorage.setItem("as_eqprefs", JSON.stringify(EQ_PREFS)); } catch (e) {} };

function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.add("hidden"), 4200);
}

function esc(s) { return (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

async function fetchItems() {
  // full catalog in one pull; search/grouping are client-side so collapse state survives typing
  const r = await fetch(`/api/items`);
  const data = await r.json();
  ITEMS = data.items;
}
async function loadItems() { await fetchItems(); renderGrid(); }

/* Re-pull item data (new alternates / active choice / freshly built artwork) and re-render the
   open detail. Runs when you return to the gallery -- e.g. after building on the pairing page --
   so the alternate images reflect the latest accept without a manual reload. */
let _refreshing = false;
async function refreshView() {
  if (_refreshing) return;
  _refreshing = true;
  try {
    const sel = SELECTED;
    await fetchItems();
    const fresh = sel && ITEMS.find((i) => i.id === sel.id);
    if (fresh) selectItem(fresh);       // re-renders the variants with cache-busted images
  } catch (e) { /* leave the current view */ }
  finally { _refreshing = false; }
}

/* ---------- grouped gallery: Armor / Weapons / Class-Specific / Other ----------
   Rows are item FAMILIES chained by weapons/armor.txt normcode (normal -> exceptional ->
   elite), followed by the family's uniques (tier order) then its set pieces. */
const GROUP_ORDER = ["Armor", "Weapons", "Class-Specific", "Items", "Other"];
const SUB_ORDER = {
  "Armor": ["Helms", "Armor", "Shields", "Gloves", "Boots", "Belts"],
  "Weapons": ["Axes", "Bows", "Crossbows", "Daggers", "Javelins", "Maces", "Polearms",
              "Scepters", "Spears", "Staves", "Swords", "Throwing", "Wands"],
  "Class-Specific": ["Circlets", "Pelts", "Primal Helms", "Heads", "Auric Shields",
                     "Orbs", "Katars", "Amazon Weapons"],
  // non-equipment: every consumable / socketable / material lives here (was the flat "Other")
  "Items": ["Jewelry", "Charms", "Jewels", "Gems", "Runes", "Potions", "Scrolls & Tomes",
            "Arrows & Bolts", "Maps", "Materials", "Cube & Recipes", "Quest Items", "Misc"],
  "Other": ["Other"],
};
// Maps has a third nesting level (SUB_ORDER only lists 2 levels; the tier order lives here)
const SUBSUB_ORDER = { "Items/Maps": ["Tier 1", "Tier 2", "Tier 3", "Tier 4", "Tier 5", "Special"] };
const TYPE_SUB = {
  helm: ["Armor", "Helms"], tors: ["Armor", "Armor"], shie: ["Armor", "Shields"],
  glov: ["Armor", "Gloves"], boot: ["Armor", "Boots"], belt: ["Armor", "Belts"],
  bels: ["Armor", "Belts"],                       // PD2 Troll Belt
  axe: ["Weapons", "Axes"], bow: ["Weapons", "Bows"], xbow: ["Weapons", "Crossbows"],
  knif: ["Weapons", "Daggers"], jave: ["Weapons", "Javelins"],
  club: ["Weapons", "Maces"], mace: ["Weapons", "Maces"], hamm: ["Weapons", "Maces"],
  pole: ["Weapons", "Polearms"], sc9: ["Weapons", "Polearms"],  // PD2 split scythes
  scep: ["Weapons", "Scepters"], spea: ["Weapons", "Spears"], staf: ["Weapons", "Staves"],
  swor: ["Weapons", "Swords"], "2hcs": ["Weapons", "Swords"],   // PD2 2H Phase Blade
  tkni: ["Weapons", "Throwing"], taxe: ["Weapons", "Throwing"], tpot: ["Weapons", "Throwing"],
  wand: ["Weapons", "Wands"],
  circ: ["Class-Specific", "Circlets"], pelt: ["Class-Specific", "Pelts"],
  phlm: ["Class-Specific", "Primal Helms"], head: ["Class-Specific", "Heads"],
  ashd: ["Class-Specific", "Auric Shields"], orb: ["Class-Specific", "Orbs"],
  h2h: ["Class-Specific", "Katars"], h2h2: ["Class-Specific", "Katars"],
  abow: ["Class-Specific", "Amazon Weapons"], aspe: ["Class-Specific", "Amazon Weapons"],
  ajav: ["Class-Specific", "Amazon Weapons"],
  // ---- Items (non-equipment) ----
  amul: ["Items", "Jewelry"], ring: ["Items", "Jewelry"],
  scha: ["Items", "Charms"], mcha: ["Items", "Charms"], lcha: ["Items", "Charms"],
  jewl: ["Items", "Jewels"], jewf: ["Items", "Jewels"],
  gema: ["Items", "Gems"], gemd: ["Items", "Gems"], geme: ["Items", "Gems"],
  gemr: ["Items", "Gems"], gems: ["Items", "Gems"], gemz: ["Items", "Gems"], gemt: ["Items", "Gems"],
  rune: ["Items", "Runes"],
  hpot: ["Items", "Potions"], mpot: ["Items", "Potions"], rpot: ["Items", "Potions"],
  apot: ["Items", "Potions"], spot: ["Items", "Potions"], wpot: ["Items", "Potions"],
  elix: ["Items", "Potions"],
  scro: ["Items", "Scrolls & Tomes"], book: ["Items", "Scrolls & Tomes"],
  bowq: ["Items", "Arrows & Bolts"], xboq: ["Items", "Arrows & Bolts"],
  t1m: ["Items", "Maps", "Tier 1"], t2m: ["Items", "Maps", "Tier 2"],
  t3m: ["Items", "Maps", "Tier 3"], t4m: ["Items", "Maps", "Tier 4"],
  t5m: ["Items", "Maps", "Tier 5"],
  fort: ["Items", "Maps", "Special"], upmp: ["Items", "Maps", "Special"],
  pvpd: ["Items", "Maps", "Special"], pvpm: ["Items", "Maps", "Special"],
  corr: ["Items", "Maps", "Special"],
  ubr: ["Items", "Materials"], ubru: ["Items", "Materials"], crft: ["Items", "Materials"],
  torc: ["Items", "Materials"], cm2f: ["Items", "Materials"],
  box: ["Items", "Cube & Recipes"], imrn: ["Items", "Cube & Recipes"],
  imma: ["Items", "Cube & Recipes"], imra: ["Items", "Cube & Recipes"],
  rera: ["Items", "Cube & Recipes"], upma: ["Items", "Cube & Recipes"],
  scou: ["Items", "Cube & Recipes"], toa: ["Items", "Cube & Recipes"],
  lmal: ["Items", "Cube & Recipes"], scrb: ["Items", "Cube & Recipes"],
  ques: ["Items", "Quest Items"], key: ["Items", "Quest Items"],
  lbox: ["Items", "Quest Items"], lpp: ["Items", "Quest Items"], play: ["Items", "Quest Items"],
  gold: ["Items", "Misc"], herb: ["Items", "Misc"],
};
// Browse-hidden derivatives (chosen in the organize pass). Still reachable via SEARCH, still
// enhanced/accepted — just not shown as their own tiles when idly browsing. Stacks in particular
// are AUTO-DERIVED from their base art (base + '+' badge), so they never need their own tile.
const HIDE_TYPES = new Set([
  "runs",                                                              // rune stacks
  "gg3a", "gg3d", "gg3e", "gg3r", "gg3s", "gg3z", "gg3t",             // flawless gem stacks
  "gg4a", "gg4d", "gg4e", "gg4r", "gg4s", "gg4z", "gg4t",             // perfect gem stacks
  "schp", "mchp", "lchp",                                             // PVP charm copies
  "amus",                                                             // "Amulet [S]" duplicate
  "irma", "irrn", "irra", "rrra", "urma",                            // "…Ready" crafting states
  "body",                                                             // 12 "Not used" placeholders
]);
const isHidden = (it) => HIDE_TYPES.has((it.type || "").trim()) ||
  /\bStack\b/.test(it.name) || /\bUnlimited\b/i.test(it.name) ||
  /\bReady\b/.test(it.name) || it.name === "Not used";

// collapse state: groups open by default, subcategories closed; persisted across reloads
const TREE_KEY = "as_tree";
let TREE_STATE = (() => {
  try { return JSON.parse(localStorage.getItem(TREE_KEY) || "{}"); } catch (e) { return {}; }
})();
const isOpen = (k, def) => (k in TREE_STATE ? !!TREE_STATE[k] : def);
const setOpen = (k, v) => {
  TREE_STATE[k] = v ? 1 : 0;
  try { localStorage.setItem(TREE_KEY, JSON.stringify(TREE_STATE)); } catch (e) {}
};

function tileEl(it, hit) {
  const active = it.active || "original";
  const modded = active !== "original";
  const chip = it.category === "unique" ? ["u", "U", "unique"]
    : it.category === "set" ? ["s", "S", "set"]
    : it.tier === 2 ? ["e", "E", "elite"]
    : it.tier === 1 ? ["x", "X", "exceptional"] : ["n", "N", "normal"];
  const d = document.createElement("div");
  d.className = "tile" + (modded ? " modded" : "") + (hit ? " hit" : "");
  d.title = `${it.name} (${it.code}) · ${chip[2]}${modded ? " · modded" : ""}`;
  // `current.png` serves whichever art is active (original or the selected alternate); the
  // `?v=` cache key changes only when the active choice does, so tiles re-fetch on switch.
  d.innerHTML = `
    <span class="chip ${chip[0]}">${chip[1]}</span>
    <div class="tthumb checker"><img loading="lazy" src="/api/item/${encodeURIComponent(it.id)}/current.png?v=${encodeURIComponent(active)}&b=${ART_BUST}" onerror="this.style.opacity=.15"></div>
    <div class="tnm">${it.name}</div>`;
  d.onclick = () => selectItem(it);
  return d;
}

function renderGrid() {
  const g = $("#grid");
  const q = $("#search").value.trim().toLowerCase();
  const match = (it) => !q || it.name.toLowerCase().includes(q) || it.code.toLowerCase().includes(q);

  // 1) fold the flat catalog into family rows. Hidden derivatives (stacks/placeholders/dupes)
  //    are dropped while browsing but kept when a search is active, so they stay findable.
  const fams = new Map();
  for (const it of ITEMS) {
    if (!q && isHidden(it)) continue;
    const key = it.family || it.code;
    let row = fams.get(key);
    if (!row) fams.set(key, row = { bases: [], uniques: [], sets: [] });
    (it.category === "base" ? row.bases : it.category === "unique" ? row.uniques : row.sets).push(it);
  }
  // 2) place each row in its group/subcategory (decided by the lowest-tier member)
  const byTier = (a, b) => (a.tier - b.tier) || (a.ord - b.ord);
  const tree = new Map();
  let visible = 0;
  for (const row of fams.values()) {
    row.bases.sort(byTier); row.uniques.sort(byTier); row.sets.sort(byTier);
    row.items = [...row.bases, ...row.uniques, ...row.sets];
    if (q && !row.items.some(match)) continue;     // search: keep whole family for context
    const first = row.items[0];
    const [grp, sub, subsub] = TYPE_SUB[first.type] || ["Other", "Other"];
    row.ord = first.ord;
    row.subsub = subsub || null;                   // optional 3rd level (Maps -> Tier N / Special)
    if (!tree.has(grp)) tree.set(grp, new Map());
    const sm = tree.get(grp);
    if (!sm.has(sub)) sm.set(sub, []);
    sm.get(sub).push(row);
    visible += row.items.length;
  }
  $("#count").textContent = q ? `${visible} / ${ITEMS.length} items` : `${ITEMS.length} items`;

  // 3) render (searching forces everything open; toggles are disabled while searching)
  g.innerHTML = "";
  for (const grp of GROUP_ORDER) {
    const sm = tree.get(grp);
    if (!sm) continue;
    const gk = "g:" + grp;
    const gOpen = q ? true : isOpen(gk, true);
    const gEl = document.createElement("div");
    gEl.className = "tgroup";
    const nItems = [...sm.values()].reduce((n, rows) => n + rows.reduce((m, r) => m + r.items.length, 0), 0);
    gEl.innerHTML = `<div class="ghead">${gOpen ? "▾" : "▸"} ${grp}<span class="cnt">${nItems} items</span></div>`;
    gEl.querySelector(".ghead").onclick = () => { if (!q) { setOpen(gk, !gOpen); renderGrid(); } };
    const gBody = document.createElement("div");
    if (!gOpen) gBody.classList.add("hidden");
    // declared order first, then any unexpected subcats
    const declared = SUB_ORDER[grp] || [];
    const subs = [...declared.filter((s) => sm.has(s)), ...[...sm.keys()].filter((s) => !declared.includes(s))];
    for (const sub of subs) {
      const rows = sm.get(sub);
      rows.sort((a, b) => a.ord - b.ord);
      const sk = `s:${grp}/${sub}`;
      const sOpen = q ? true : isOpen(sk, false);
      const sEl = document.createElement("div");
      sEl.className = "subcat";
      const cnt = rows.reduce((m, r) => m + r.items.length, 0);
      sEl.innerHTML = `<div class="shead">${sOpen ? "▾" : "▸"} ${sub}<span class="cnt">${rows.length} rows · ${cnt} items</span></div>`;
      sEl.querySelector(".shead").onclick = () => { if (!q) { setOpen(sk, !sOpen); renderGrid(); } };
      if (sOpen) {
        const sBody = document.createElement("div");
        sBody.className = "sbody";
        // pack family rows into `target`: multi-item families as famrows, 1-item ones grouped
        const packRows = (target, rws) => {
          const singles = document.createElement("div");
          singles.className = "singles";
          for (const row of rws) {
            if (row.items.length === 1) { singles.appendChild(tileEl(row.items[0], q && match(row.items[0]))); continue; }
            const r = document.createElement("div");
            r.className = "famrow";
            for (const b of row.bases) r.appendChild(tileEl(b, q && match(b)));
            if (row.bases.length && (row.uniques.length || row.sets.length)) {
              const sep = document.createElement("div"); sep.className = "vsep"; r.appendChild(sep);
            }
            for (const u of row.uniques) r.appendChild(tileEl(u, q && match(u)));
            for (const s of row.sets) r.appendChild(tileEl(s, q && match(s)));
            target.appendChild(r);
          }
          if (singles.childNodes.length) target.appendChild(singles);
        };
        if (rows.some((r) => r.subsub)) {
          // optional 3rd level (Maps): group rows by subsub, one nested collapsible per tier
          const order = SUBSUB_ORDER[`${grp}/${sub}`] || [];
          const bySS = new Map();
          for (const row of rows) {
            const k = row.subsub || "Other";
            if (!bySS.has(k)) bySS.set(k, []);
            bySS.get(k).push(row);
          }
          const sss = [...order.filter((s) => bySS.has(s)), ...[...bySS.keys()].filter((s) => !order.includes(s))];
          for (const ss of sss) {
            const ssRows = bySS.get(ss); ssRows.sort((a, b) => a.ord - b.ord);
            const ssk = `ss:${grp}/${sub}/${ss}`;
            const ssOpen = q ? true : isOpen(ssk, false);
            const ssEl = document.createElement("div");
            ssEl.className = "subcat subsub";
            const ssc = ssRows.reduce((m, r) => m + r.items.length, 0);
            ssEl.innerHTML = `<div class="shead">${ssOpen ? "▾" : "▸"} ${ss}<span class="cnt">${ssc} items</span></div>`;
            ssEl.querySelector(".shead").onclick = () => { if (!q) { setOpen(ssk, !ssOpen); renderGrid(); } };
            if (ssOpen) { const b = document.createElement("div"); b.className = "sbody"; packRows(b, ssRows); ssEl.appendChild(b); }
            sBody.appendChild(ssEl);
          }
        } else {
          packRows(sBody, rows);
        }
        sEl.appendChild(sBody);
      }
      gBody.appendChild(sEl);
    }
    gEl.appendChild(gBody);
    g.appendChild(gEl);
  }
  if (!g.childNodes.length) g.innerHTML = `<div class="meta" style="padding:20px">no items match "${q}"</div>`;
}

async function selectItem(it) {
  const sameItem = SELECTED && SELECTED.id === it.id;
  if (!sameItem) PREVIEWING = null;   // fresh item -> nothing previewed yet
  // capture strip scroll so a same-item re-render (activate/rename/refresh) doesn't jump to start
  if (sameItem) {
    _stripScroll = {};
    document.querySelectorAll("#detail .variants.strip").forEach((s, i) => (_stripScroll[i] = s.scrollLeft));
  }
  SELECTED = it;
  const d = $("#detail");
  d.classList.remove("hidden");
  // Column 3 (Generate) auto-populates for a genuinely new selection; skip on a same-item
  // re-render (e.g. after activating a variant) so it doesn't reset in-progress generate state.
  if (!sameItem && window.openWorkflow) window.openWorkflow(it);
  const actBox = (choice) =>
    `<label class="actbox" title="Activate this art (Push to game to apply)"><input type="checkbox" class="activate-box" data-choice="${choice}" ${it.active === choice ? "checked" : ""}></label>`;
  const previewCls = (choice) => (PREVIEWING === choice ? " previewing" : "");
  const variants = [
    `<div class="variant ${it.active === "original" ? "active" : ""}${previewCls("original")}" data-choice="original">
       ${actBox("original")}
       <div class="thumb checker"><img src="/api/item/${encodeURIComponent(it.id)}/original.png?b=${ART_BUST}"></div>
       <div class="lbl">original</div></div>`,
    ...it.alts.map((a) => `
      <div class="variant ${it.active === a ? "active" : ""}${previewCls(a)}" data-choice="${a}">
        ${actBox(a)}
        <button class="dots" data-alt="${a}" title="rename / delete">⋯</button>
        <div class="thumb checker"><img src="/api/item/${encodeURIComponent(it.id)}/alt/${a}.png?t=${Date.now()}"></div>
        <div class="lbl">${a}</div></div>`),
    `<div class="variant addcard" id="addAltBtn" title="Add alternate artwork — import a PNG, use Generate (column 3), or link a Meshy task (you can also drop a PNG file here)">
       <div class="plus">+</div>
       <div class="lbl">add alternate</div></div>`,
  ].join("");
  const flippyVariants = it.flippyfile ? [
    `<div class="variant fv ${it.flippy_active === "original" ? "active" : ""}" data-fchoice="original">
       <div class="thumb checker"><img src="/api/item/${encodeURIComponent(it.id)}/flippy/original.gif"></div>
       <div class="lbl">original</div></div>`,
    ...(it.flippy_alts || []).map((a) => `
      <div class="variant fv ${it.flippy_active === a ? "active" : ""}" data-fchoice="${a}">
        <button class="dots" data-alt="${a}" data-flippy="1" title="rename / delete">⋯</button>
        <div class="thumb checker"><img src="/api/item/${encodeURIComponent(it.id)}/flippy/alt/${a}.gif?t=${Date.now()}"></div>
        <div class="lbl">${a}</div></div>`),
  ].join("") : "";
  const txtSection = it.category === "unique" ? `
    <details class="uploader advdetails" id="txtSection">
      <summary>Own art file (advanced) — give this unique its own invfile in uniqueitems.bin
        (it currently ${it.invtransform ? "inherits + tints" : "uses"} <b>${it.invfile}</b>)</summary>
      <div class="anglerow">
        <input type="text" id="ownInvfile" style="width:180px" maxlength="31"
               placeholder="e.g. inv${it.code.trim()}u" spellcheck="false">
        <button id="setInvfileBtn" title="Patch this unique's invfile cell in uniqueitems.bin (goes into the patch.mpq on push; needs Full reload)">Set invfile</button>
        <button id="revertInvfileBtn" title="Revert to the inherited base art file">Revert</button>
      </div>
      <div class="meta" id="txtState"></div>
    </details>` : "";
  // uniques + sets carry a tint (invtransform/chrtransform in their .bin row); the chip opens the picker
  const tintable = it.category === "unique" || it.category === "set";
  const tintChip = tintable
    ? ` · <button class="tintchip${it.invtransform ? "" : " empty"}" id="tintChip" title="Edit this item's tint (inventory / on-body colour)">${it.invtransform ? "tint " + it.invtransform : "+ tint"}</button>`
    : (it.invtransform ? " · tint " + it.invtransform : "");
  d.innerHTML = `
    <div class="colhead">2 · Details</div>
    <h2>${it.name}</h2>
    <div class="metarow">
      <div class="meta">${it.category} · code <b>${it.code}</b> · ${it.invwidth}×${it.invheight} cells · ${it.invfile}.dc6${tintChip}</div>
      ${tintable ? `<div class="tintpop hidden" id="tintPop"></div>` : ""}
    </div>
    <div class="anglerow">
      <div class="splitbtn">
        <button id="dropBtn" title="${dropTitle(it)}">⤓ Drop ${dropNativeLabel(it)}</button>
        <button id="dropMenuBtn" class="split-caret" title="Drop this item forced to another quality">▾</button>
        <div id="dropMenu" class="dropmenu hidden">
          <button data-q="normal">Normal</button>
          <button data-q="superior">Superior</button>
          <button data-q="magic">Magic</button>
          <button data-q="rare">Rare</button>
          <button data-q="unique">Random Unique</button>
          <button data-q="set">Random Set</button>
          <button data-q="low">Low quality</button>
        </div>
      </div>
    </div>

    <div class="tabbar">
      <button class="tab" data-tab="item">Item</button>
      <button class="tab" data-tab="flippy">Flippy</button>
      <button class="tab" data-tab="equipped">Equipped</button>
    </div>

    <div class="tabpanel" data-panel="item">
      ${it.shared_by > 1 ? `<div class="sharedbadge" title="Alternates and the active choice are shared by every item drawn from ${it.invfile}.dc6 — a shared DC6 is one physical file, so it shows one look in-game. Give this item its own invfile (below) to break it out with a private set.">🔗 shared pool · <b>${it.shared_by}</b> items use <b>${it.invfile}.dc6</b></div>` : ""}
      <div class="stripwrap">
        <div class="variants strip">${variants}</div>
        <div class="addpanel hidden" id="addPanel">
          <div class="addrow">
            <button id="importPngBtn" title="Import a PNG as a new alternate — auto-fit to ${it.invwidth}×${it.invheight} cells &amp; quantized to the D2 palette">🖼 Import PNG</button>
            <button id="linkMeshyBtn" title="Link a generation you already made in the Meshy web app to this item, then continue it in Generate (column 3) to re-roll / texture / accept">🔗 Link Meshy task…</button>
          </div>
          <div class="meta addhint">import auto-fits to ${it.invwidth}×${it.invheight} cells · Generate (column 3, always open for the selected item) builds a 3D model · link pairs an existing Meshy generation — or drop a PNG file anywhere on this panel</div>
          <input type="file" id="pngFile" accept="image/png,image/*" class="hidden">
          <div class="meta" id="linkState"></div>
          <div class="variants" id="taskPicker" style="display:none"></div>
          ${txtSection}
        </div>
      </div>
      <div class="itembig checker" id="itemBig"></div>
      <div class="altinspect hidden" id="altInspect"></div>
    </div>

    <div class="tabpanel" data-panel="flippy">
      ${it.flippyfile
        ? `<div class="uploader">
             <label>Ground-drop animation (flippy — ${it.flippyfile}.dc6)</label>
             <div class="stripwrap"><div class="variants strip">${flippyVariants}</div></div>
           </div>`
        : `<div class="meta">This item has no ground-drop (flippy) animation file.</div>`}
    </div>

    <div class="tabpanel" data-panel="equipped">
      <div class="eqstage checker"><img id="eqImg" alt="" onerror="this.style.display='none'"></div>
      <div id="eqNote" class="meta"></div>
      <div class="anglerow"><label title="Hide the character and show only the item, at the size &amp; spot it sits on the body"><input type="checkbox" id="eqBody"> item only (hide character)</label></div>
      <div class="anglerow">class
        <select id="eqClass">
          <option value="barbarian">Barbarian</option><option value="amazon">Amazon</option>
          <option value="paladin">Paladin</option><option value="sorceress">Sorceress</option>
          <option value="necromancer">Necromancer</option><option value="druid">Druid</option>
          <option value="assassin">Assassin</option>
        </select>
        mode
        <select id="eqMode">
          <option value="NU">idle</option><option value="TN">town idle</option>
          <option value="WL">walk</option><option value="RN">run</option>
          <option value="A1">attack</option><option value="SC">cast</option>
        </select>
      </div>
      <div class="anglerow">direction
        <input type="range" id="eqDir" min="0" max="15" step="1" value="0" style="flex:1">
        <span id="eqDirV" style="width:20px">0</span>
      </div>
    </div>`;

  // Equipped tab: lazy-render the on-character preview only when first opened (char graphics are heavy)
  let eqLoaded = false, eqBaseNote = "";
  const updateEquipped = () => {
    const cls = $("#eqClass").value, mode = $("#eqMode").value, dir = $("#eqDir").value;
    const itemOnly = $("#eqBody").checked;
    $("#eqDirV").textContent = dir;
    const img = $("#eqImg");
    img.onload = () => { img.style.display = ""; $("#eqNote").textContent = eqBaseNote + (itemOnly ? " · item only" : ""); };
    img.onerror = () => { img.style.display = "none"; $("#eqNote").textContent = eqBaseNote + " · no art for this class + animation — try idle or walk."; };
    img.style.display = "";
    img.src = `/api/item/${encodeURIComponent(it.id)}/equipped.gif?cls=${cls}&mode=${mode}&dir=${dir}&body=${itemOnly ? 0 : 1}&t=${Date.now()}`;
  };
  const loadEquipped = async () => {
    if (eqLoaded) return; eqLoaded = true;
    const note = $("#eqNote");
    try {
      const info = await (await fetch(`/api/item/${encodeURIComponent(it.id)}/equipped/info`)).json();
      if (!info.ok || !info.supported) {   // base body only (gloves/boots/belt/rings/charms/...)
        note.textContent = "This item type has no on-character graphic (only armor & weapons are worn).";
        $("#eqImg").style.display = "none";
        showTab("item");           // fallback display only — keeps Equipped as the remembered tab
        return;
      }
      // gray out classes with no art for this item; keep your remembered class when it has art, else
      // fall back to the class the item is for (without forgetting your preference).
      const sel = $("#eqClass");
      if (sel) {
        const hasAvail = info.available_classes != null;
        const avail = new Set(info.available_classes || []);
        [...sel.options].forEach((o) => {
          const base = o.textContent.replace(/ — no art$/, "");
          const ok = !hasAvail || avail.has(o.value);
          o.disabled = !ok;
          o.textContent = ok ? base : base + " — no art";
        });
        sel.value = (!hasAvail || avail.has(EQ_PREFS.class)) ? EQ_PREFS.class
                    : (info.default_class || EQ_PREFS.class);
      }
      // restore remembered animation + direction + item-only toggle
      const modeSel = $("#eqMode");
      if (modeSel && [...modeSel.options].some((o) => o.value === EQ_PREFS.mode)) modeSel.value = EQ_PREFS.mode;
      $("#eqDir").value = EQ_PREFS.dir; $("#eqDirV").textContent = EQ_PREFS.dir;
      $("#eqBody").checked = !!EQ_PREFS.itemOnly;
      eqBaseNote = info.kind === "body-armor" ? "Body armor — shown worn on the character."
        : info.kind === "helm" ? "Helm — shown worn on the character's head."
        : info.kind === "weapon" ? "Weapon — shown held in the character's hand."
        : info.kind === "shield" ? "Shield — shown worn on the character's off-hand."
        : "Base character body.";
      note.textContent = eqBaseNote;
      updateEquipped();
    } catch (e) { note.textContent = "preview unavailable"; }
  };
  // changing class / mode / direction updates the remembered preference
  $("#eqClass").oninput = () => { EQ_PREFS.class = $("#eqClass").value; savePrefs(); updateEquipped(); };
  $("#eqMode").oninput = () => { EQ_PREFS.mode = $("#eqMode").value; savePrefs(); updateEquipped(); };
  $("#eqDir").oninput = () => { EQ_PREFS.dir = +$("#eqDir").value; savePrefs(); updateEquipped(); };
  $("#eqBody").onchange = () => { EQ_PREFS.itemOnly = $("#eqBody").checked; savePrefs(); updateEquipped(); };

  // showTab only DISPLAYS a tab (used for the remembered tab + the Equipped->Item fallback); a real
  // click also records it as the preference so it sticks across items and reloads.
  const showTab = (name) => {
    d.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    d.querySelectorAll(".tabpanel").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== name));
    if (name === "equipped") loadEquipped();
  };
  d.querySelectorAll(".tab").forEach((t) => (t.onclick = () => { EQ_PREFS.tab = t.dataset.tab; savePrefs(); showTab(t.dataset.tab); }));
  showTab(EQ_PREFS.tab);

  // Big inline preview below the thumbnail strip: shows the selected artwork large — the hi-res
  // source master when the alt has one, otherwise the exact in-game sprite scaled up crisply.
  // Clicking a thumb updates it instantly (then activate() re-renders with the now-active choice).
  const bigWrap = $("#itemBig");
  // preview whatever was previewed before a same-item re-render, else the active choice
  if (bigWrap) previewAlt(it, PREVIEWING ?? it.active, { silent: true });
  // click a tile = PREVIEW only (never activates); the tile's checkbox is the sole activation control
  d.querySelectorAll(".variant[data-choice]").forEach((v) => {
    v.onclick = () => previewAlt(it, v.dataset.choice);
  });
  d.querySelectorAll(".variant .activate-box").forEach((cb) => {
    cb.onclick = (e) => e.stopPropagation();   // don't trigger the tile's preview
    cb.onchange = () => activate(it, cb.checked ? cb.dataset.choice : "original");
  });
  d.querySelectorAll(".variant[data-fchoice]").forEach((v) => {
    v.onclick = () => activateFlippy(it, v.dataset.fchoice);
  });
  // ⋯ on alternate cards -> rename/delete menu; must not bubble into the card's preview click
  d.querySelectorAll(".variant .dots").forEach((b) => {
    b.onclick = (e) => { e.stopPropagation(); openAltMenu(b, it, b.dataset.alt, !!b.dataset.flippy); };
  });
  wireAddPanel(it, d);
  d.querySelectorAll(".stripwrap").forEach(wireStrip);
  // restore strip scroll captured before a same-item re-render
  if (sameItem) document.querySelectorAll("#detail .variants.strip").forEach((s, i) => {
    if (_stripScroll[i] != null) s.scrollLeft = _stripScroll[i];
  });
  $("#pngFile").onchange = (e) => importPng(it, e.target.files[0]);
  $("#dropBtn").onclick = () => dropInGame(it);           // native quality
  wireDropMenu(it);
  if (it.category === "unique") wireTxtSection(it);
  wireTint(it);
  wireMeshyLinks(it);
}

// The main drop button drops the item's NATIVE quality; label reflects it.
function dropNativeLabel(it) {
  return it.category === "unique" ? "unique" : it.category === "set" ? "set piece" : "in game";
}
function dropTitle(it) {
  const base = "Spawn at your feet (be in a game), then pick it up to see the inventory art.";
  if (it.category === "unique") return "Drop this exact unique, identified — " + base;
  if (it.category === "set") return "Drop this exact set piece — " + base;
  return base;
}
function wireDropMenu(it) {
  const menu = $("#dropMenu"), btn = $("#dropMenuBtn");
  if (!menu || !btn) return;
  btn.onclick = (e) => { e.stopPropagation(); menu.classList.toggle("hidden"); };
  menu.querySelectorAll("[data-q]").forEach((b) => {
    b.onclick = () => { menu.classList.add("hidden"); dropInGame(it, b.dataset.q); };
  });
  // Outside-click close is handled by a single persistent document listener (see below); we must
  // NOT register it here -- wireDropMenu runs during selectItem, i.e. inside the tile-click that is
  // still bubbling to document, so a listener added now would fire on that same click and (if
  // {once}) remove itself before the menu is ever opened.
}

// One document-level handler closes an open quality menu when you click anywhere outside the split
// button. The caret toggles it; menu items close it explicitly -- both live inside .splitbtn, so
// this handler ignores them. Registered once at load, it survives detail-panel re-renders because
// it re-queries #dropMenu each time.
document.addEventListener("click", (e) => {
  const menu = document.querySelector("#dropMenu");
  if (menu && !menu.classList.contains("hidden") && !e.target.closest(".splitbtn")) {
    menu.classList.add("hidden");
  }
});

/* ---------- Add-alternate pop-down (+ card), variant strips & ⋯ card actions ---------- */

// Remember the open pop-down across the re-renders selectItem does (activate/import both
// re-render); keyed by item id so switching items always starts collapsed.
let ADD_OPEN_FOR = null;

// Same pattern as the #dropMenu handler above: closes on any click outside the overlay, except the
// "+" card itself (its own onclick already toggles open/closed -- letting this handler also act on
// that click would immediately re-close what the toggle just opened).
document.addEventListener("click", (e) => {
  const panel = document.querySelector("#addPanel");
  if (panel && !panel.classList.contains("hidden") && !e.target.closest("#addPanel") && !e.target.closest("#addAltBtn")) {
    panel.classList.add("hidden");
    document.querySelector("#addAltBtn")?.classList.remove("open");
    ADD_OPEN_FOR = null;
  }
});

function wireAddPanel(it, d) {
  const panel = $("#addPanel"), card = $("#addAltBtn");
  const setOpenState = (open) => {
    panel.classList.toggle("hidden", !open);
    card.classList.toggle("open", open);
    ADD_OPEN_FOR = open ? it.id : null;
  };
  card.onclick = () => setOpenState(panel.classList.contains("hidden"));
  setOpenState(ADD_OPEN_FOR === it.id);
  $("#importPngBtn").onclick = () => $("#pngFile").click();
  // drag & drop a PNG onto the + card or anywhere on the open pop-down
  for (const el of [card, panel]) {
    el.ondragover = (e) => { e.preventDefault(); el.classList.add("dragging"); };
    el.ondragleave = () => el.classList.remove("dragging");
    el.ondrop = (e) => {
      e.preventDefault();
      el.classList.remove("dragging");
      const f = e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) importPng(it, f);
    };
  }
}

// Big inline preview for the Item tab. `original` has no hi-res source, so it shows the in-game
// sprite scaled up crisply (pixelated). Alternates try their full-res render master first and fall
// back to the DC6 sprite if none was saved (a 404 on render.png) — the label reflects which it is.
function renderItemBig(it, choice, wrap, { sprite = false } = {}) {
  const id = encodeURIComponent(it.id);
  const img = document.createElement("img");
  const lbl = document.createElement("div");
  lbl.className = "biglbl";
  if (choice === "original") {
    img.className = "pix";
    img.src = `/api/item/${id}/original.png`;
    lbl.textContent = "original · in-game sprite";
  } else if (sprite) {
    // the actual in-game DC6 sprite (reflects framing/size/grade) — shown after an Apply so the
    // change is visible, unlike the hi-res master which refit never alters.
    const c = encodeURIComponent(choice);
    img.classList.add("pix");
    img.src = `/api/item/${id}/alt/${c}.png?t=${Date.now()}`;
    lbl.textContent = `${choice} · in-game sprite`;
  } else {
    const c = encodeURIComponent(choice);
    img.src = `/api/item/${id}/alt/${c}/render.png`;          // hi-res source master
    lbl.textContent = `${choice} · hi-res master`;
    img.onerror = () => {                                     // no master saved -> crisp sprite
      img.onerror = null;
      img.classList.add("pix");
      img.src = `/api/item/${id}/alt/${c}.png?t=${Date.now()}`;
      lbl.textContent = `${choice} · in-game sprite`;
    };
  }
  wrap.replaceChildren(img, lbl);
}

// Preview an alternate WITHOUT activating it: highlight the tile (gold, distinct from the green
// active outline), show its big image, and load its provenance + adjustment inspector. Never
// re-renders the panel, so the strip scroll position is preserved. `silent` skips the class
// toggles (used on first render where the template already set them).
function previewAlt(it, choice, { silent = false } = {}) {
  PREVIEWING = choice;
  const bigWrap = $("#itemBig");
  if (bigWrap) renderItemBig(it, choice, bigWrap);
  if (!silent) {
    document.querySelectorAll("#detail .variant[data-choice]").forEach((v) =>
      v.classList.toggle("previewing", v.dataset.choice === choice));
  }
  renderInspector(it, choice);
}

// The provenance + adjustment inspector under the big preview. `original` and alternates with no
// saved render show provenance only (no sliders). Everything is fetched on preview (not bundled
// into the item list), and every field renders gracefully when absent (old alternates).
async function renderInspector(it, choice) {
  const box = $("#altInspect");
  if (!box) return;
  if (choice === "original") {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML = `<div class="meta">loading ${choice}…</div>`;
  let data;
  try {
    data = await (await fetch(`/api/item/${encodeURIComponent(it.id)}/alt/${encodeURIComponent(choice)}/meta`)).json();
  } catch (e) { box.innerHTML = `<div class="meta">provenance unavailable</div>`; return; }
  if (!data.ok) { box.innerHTML = `<div class="meta">provenance unavailable</div>`; return; }
  if (PREVIEWING !== choice) return;   // a newer preview won the race
  box.innerHTML = provenancePanel(choice, data) + adjustPanel(it, choice, data);
  wireInspector(it, choice, data);
}

// Provenance box: shared pvBox/pvRow component (provbox.js) -- same collapsible markup
// workflow.js uses for its "how this was generated" box. "use these settings" rides inside the
// <summary> via headerExtra so it's visible without expanding; wireInspector() stops its click
// from also toggling the details (a nested button inside <summary> would otherwise do both).
function provenancePanel(choice, data) {
  const m = data.meta || {};
  const method = m.method_label || m.method;
  const score = m.score && (m.score.score != null) ? m.score.score.toFixed(3) : undefined;
  const prompt = m.instruction || m.positive || m.description || m.restyle || m.prompt;
  const grade = m.grade ? Object.entries(m.grade).filter(([, v]) => +v !== 1 && +v !== 0)
    .map(([k, v]) => `${k} ${(+v).toFixed(2)}`).join(" · ") : "";
  const empty = !method && !m.engine && !m.seed && !prompt && !m.source;
  const canReuse = !!(m.method || m.restyle || m.nudge || m.negative || m.instruction);
  const rows = [
    pvRow("method", method), pvRow("engine", m.engine), pvRow("model", m.model),
    pvRow("seed", m.seed), pvRow("fidelity", score), pvRow("source", m.source),
    pvRow("footprint", `fill ${data.footprint.fill}${m.fit_auto ? " (auto)" : ""}`),
    pvRow("grade", grade), pvRow("prompt", prompt), pvRow("negative", m.negative),
  ].join("");
  const headerExtra = canReuse ? `<button class="linkbtn" id="useSettings">⚙ use these settings</button>` : "";
  return pvBox({ title: `ⓘ How ${pvEsc(choice)} was generated${empty ? " — no provenance recorded" : ""}`,
                headerExtra, rows });
}

// Shared adjust-panel config (adjpanel.js) for adjusting an EXISTING alternate in place via the
// refit endpoint. Built once per render/wire pair so both use identical closures over (it, choice).
function _adjCfg(it, choice, data) {
  const m = data.meta || {};
  return {
    originalSrc: `/api/item/${encodeURIComponent(it.id)}/original/cell.png?t=${Date.now()}`,
    buildPreviewUrl: (v, evenBorder) => {
      const q = new URLSearchParams({ fill: v.fill, dx: v.dx, dy: v.dy, rot: v.rot, outline: v.outline,
        brightness: v.brightness, contrast: v.contrast, saturation: v.saturation, warmth: v.warmth,
        hue: v.hue, even_border: evenBorder ? 1 : 0, t: Date.now() });
      return `/api/item/${encodeURIComponent(it.id)}/alt/${encodeURIComponent(choice)}/cell.png?${q}`;
    },
    footprint: data.footprint,
    initial: { fill: m.fill, dx: m.dx, dy: m.dy, rot: m.rot, ...(m.grade || {}) },
    outlineInit: m.outline,
    evenBorderInit: !!m.even_border,
    note: `original occupied ${Math.round(data.footprint.fill * 100)}% of its cell`,
    buttons: [
      { kind: "commit", label: "Apply", className: "gold", onCommit: async (v, evenBorder) => {
          const grade = { brightness: v.brightness, contrast: v.contrast, saturation: v.saturation, warmth: v.warmth, hue: v.hue };
          const r = await (await fetch(`/api/item/${encodeURIComponent(it.id)}/alt/${encodeURIComponent(choice)}/refit`,
            { method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ fill: v.fill, dx: v.dx, dy: v.dy, rot: v.rot, outline: !!v.outline, grade, even_border: evenBorder }) })).json();
          if (!r.ok) { toast(r.error || "adjust failed"); return; }
          toast("adjusted " + choice);
          // NO panel re-render (keeps scroll + preview) — just refresh every image showing this alt:
          const bust = "?t=" + Date.now();
          // 1) the alternate's thumbnail in the detail strip
          document.querySelectorAll(`#detail .variant[data-choice="${cssEsc(choice)}"] .thumb img`).forEach((im) =>
            (im.src = `/api/item/${encodeURIComponent(it.id)}/alt/${encodeURIComponent(choice)}.png${bust}`));
          // 2) the big preview -> the actual sprite (the master never reflects framing/size/grade)
          renderItemBig(it, choice, $("#itemBig"), { sprite: true });
          // 3) the side-by-side "adjusted" tile (already the live cell.png, but re-sync to the saved fit)
          const ap = document.querySelector("#altInspect .ap-prev");
          if (ap) ap.src = ap.src.replace(/([?&]t=)\d+/, `$1${Date.now()}`);
          // 4) the left gallery grid tile — only reflects this art when the alt is the active choice
          if (SELECTED && SELECTED.active === choice) { ART_BUST = Date.now(); renderGrid(); }
        } },
      { kind: "auto" },
      { kind: "resetColor" },
    ],
  };
}

function adjustPanel(it, choice, data) {
  if (!data.has_render) {
    return `<div class="meta adjnote">No hi-res source saved for this alternate — adjustments unavailable (regenerate via Generate, column 3, to enable).</div>`;
  }
  return `<div class="appanel">${renderAdjPanel(_adjCfg(it, choice, data))}</div>`;
}

function wireInspector(it, choice, data) {
  const use = $("#useSettings");
  // lives inside <summary> now -- stop the click from also toggling the details open/closed
  if (use) use.onclick = (e) => { e.preventDefault(); e.stopPropagation(); useAltSettings(it, data.meta || {}); };
  if (!data.has_render) return;
  const box = document.querySelector("#altInspect .appanel");
  if (box) wireAdjPanel(box, _adjCfg(it, choice, data));
}

async function useAltSettings(it, meta) {
  // map a stored method id to a picker method (lab ids like m7_combo_ct -> m7); unknown -> omit
  const raw = meta.method || "";
  let method;
  if (["m0", "m1", "m2", "m3", "m5", "m6", "m7", "sdxl_lock", "flux_lock"].includes(raw)) method = raw;
  else { const mm = raw.match(/^m(\d)/); if (mm) method = "m" + mm[1]; }
  const body = {};
  if (method) body.last_method = method;
  if (meta.restyle != null) body.restyle = meta.restyle;
  if (meta.nudge != null) body.nudge = meta.nudge;
  if (meta.negative != null) body.negative = meta.negative;
  try {
    await fetch(`/api/upscale/${encodeURIComponent(it.id)}/prompts`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (e) {}
  if (window.openWorkflow) window.openWorkflow(it);
}

function cssEsc(s) { return (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/["\\]/g, "\\$&"); }

// Horizontal variant strip: edge fades signal clipped cards, and the mouse wheel scrolls
// the row sideways while hovering it (no trackpad needed).
function wireStrip(wrap) {
  const strip = wrap.querySelector(".strip");
  if (!strip) return;
  const upd = () => {
    wrap.classList.toggle("can-left", strip.scrollLeft > 2);
    wrap.classList.toggle("can-right", strip.scrollLeft + strip.clientWidth < strip.scrollWidth - 2);
  };
  strip.addEventListener("scroll", upd, { passive: true });
  strip.addEventListener("wheel", (e) => {
    if (!e.deltaY || strip.scrollWidth <= strip.clientWidth) return;
    e.preventDefault();
    strip.scrollLeft += e.deltaY;
  }, { passive: false });
  // card widths settle as thumbnails load — re-check the fades then
  requestAnimationFrame(upd);
  strip.querySelectorAll("img").forEach((im) => im.addEventListener("load", upd, { once: true }));
}

function closeAltMenu() { document.querySelectorAll(".altmenu").forEach((m) => m.remove()); }
document.addEventListener("click", (e) => {
  if (!e.target.closest(".altmenu") && !e.target.closest(".dots")) closeAltMenu();
});

function openAltMenu(btn, it, altId, flippy) {
  const had = document.querySelector(".altmenu");
  closeAltMenu();
  if (had && had.dataset.for === altId + (flippy ? "/f" : "")) return;   // second click toggles off
  const m = document.createElement("div");
  m.className = "altmenu";
  m.dataset.for = altId + (flippy ? "/f" : "");
  m.innerHTML = `<button data-act="rename">✏ Rename…</button><button data-act="delete">🗑 Delete</button>`;
  document.body.appendChild(m);
  const r = btn.getBoundingClientRect();
  m.style.left = Math.min(r.left, window.innerWidth - m.offsetWidth - 8) + "px";
  m.style.top = (r.bottom + 4) + "px";
  m.querySelector('[data-act="rename"]').onclick = () => { closeAltMenu(); renameAlt(it, altId, flippy); };
  m.querySelector('[data-act="delete"]').onclick = () => { closeAltMenu(); deleteAlt(it, altId, flippy); };
}

async function renameAlt(it, altId, flippy) {
  const nn = (prompt(`Rename ${flippy ? "flippy " : ""}alternate "${altId}" to:`, altId) || "").trim();
  if (!nn || nn === altId) return;
  const r = await fetch(`/api/item/${encodeURIComponent(it.id)}/alt/${encodeURIComponent(altId)}/rename${flippy ? "?flippy=1" : ""}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_id: nn }),
  });
  const data = await r.json();
  if (!data.ok) return toast("rename failed: " + (data.error || ""), true);
  toast(`renamed "${altId}" → "${nn}"`);
  await refreshView();
}

async function deleteAlt(it, altId, flippy) {
  const active = flippy ? it.flippy_active === altId : it.active === altId;
  if (!confirm(`Delete ${flippy ? "flippy " : ""}alternate "${altId}"?`
      + (active ? "\n\nIt is the ACTIVE choice — the item reverts to original art." : ""))) return;
  const r = await fetch(`/api/item/${encodeURIComponent(it.id)}/alt/${encodeURIComponent(altId)}${flippy ? "?flippy=1" : ""}`,
    { method: "DELETE" });
  const data = await r.json();
  if (!data.ok) return toast("delete failed: " + (data.error || ""), true);
  toast(`deleted alternate "${altId}"${active ? " — reverted to original" : ""}`);
  await refreshView();
  renderGrid();          // active-state chip on the gallery tile may have changed
}

/* ---------- Meshy pairing ---------- */
async function wireMeshyLinks(it) {
  $("#linkMeshyBtn").onclick = () => openTaskPicker(it);
  refreshLinkState(it);
}

async function refreshLinkState(it) {
  const state = $("#linkState");
  try {
    const d = await (await fetch("/api/meshy/links")).json();
    const mine = (d.links || []).filter((l) => l.item_id === it.id);
    if (!mine.length) { state.textContent = "no Meshy task linked yet"; return; }
    // compact count + expander; an unlink re-renders this, so carry the open state over
    const wasOpen = !!state.querySelector("details[open]");
    state.innerHTML = `<details class="linklist"${wasOpen ? " open" : ""}>
      <summary>${mine.length} Meshy task${mine.length === 1 ? "" : "s"} linked</summary>
      ${mine.map((l) => `<div class="linkrow">
        <span class="linkdesc" title="${l.phase || "draft"}, ${l.source || "manual"}"><b>${l.name || l.task_id.slice(0, 8)}</b>
          → ${l.invfile ? l.invfile + ".dc6" : "?"} (${l.phase || "draft"})</span>
        <button data-adopt="${l.task_id}" data-phase="${l.phase || "draft"}" title="Continue this generation in Generate (column 3)">⤴ Continue</button>
        <button data-unlink="${l.task_id}" title="Unlink this Meshy task from ${it.name}">✕</button>
      </div>`).join("")}
    </details>`;
    state.querySelectorAll("[data-adopt]").forEach((b) => {
      b.onclick = () => window.adoptMeshyTask && window.adoptMeshyTask(it, b.dataset.adopt, b.dataset.phase);
    });
    state.querySelectorAll("[data-unlink]").forEach((b) => {
      b.onclick = async () => {
        await fetch(`/api/meshy/links/${b.dataset.unlink}`, { method: "DELETE" });
        refreshLinkState(it);
      };
    });
  } catch (e) { state.textContent = ""; }
}

async function openTaskPicker(it) {
  const p = $("#taskPicker");
  if (p.style.display !== "none") { p.style.display = "none"; return; }
  p.style.display = ""; p.innerHTML = "<div class='meta'>loading your Meshy workspace…</div>";
  const d = await (await fetch("/api/meshy/tasks")).json();
  if (!d.ok) { p.innerHTML = `<div class='meta'>${d.error || "failed"}</div>`; return; }
  const rows = d.tasks.filter((t) => t.status === "SUCCEEDED");
  p.innerHTML = rows.map((t) => `
    <div class="variant" data-task="${t.id}" data-phase="${t.phase || "draft"}" title="${t.phase} · ${8 - (t.retryCount || 0)} free re-rolls left${t.linked_item_name ? " · already linked to " + t.linked_item_name : ""}">
      <div class="thumb checker">${t.preview ? `<img src="${t.preview}" loading="lazy">` : ""}</div>
      <div class="lbl">${t.name || t.id.slice(0, 8)}${t.linked_item_name ? " 🔗" : ""}</div>
    </div>`).join("") || "<div class='meta'>no finished tasks found</div>";
  p.querySelectorAll("[data-task]").forEach((v) => {
    v.onclick = async () => {
      const r = await (await fetch("/api/meshy/links", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: v.dataset.task, item_id: it.id }),
      })).json();
      if (!r.ok) return toast("link failed: " + (r.error || ""), true);
      toast(`linked to ${it.name} — loading into Generate…`);
      p.style.display = "none";
      refreshLinkState(it);
      if (window.adoptMeshyTask) window.adoptMeshyTask(it, v.dataset.task, v.dataset.phase);
    };
  });
}

async function wireTxtSection(it) {
  const state = $("#txtState");
  try {
    const u = await (await fetch(`/api/item/${encodeURIComponent(it.id)}/txt`)).json();
    if (u.ok) {
      $("#ownInvfile").value = u.invfile || "";
      state.textContent = u.invfile
        ? `own invfile set: ${u.invfile}.dc6 (stock: ${u.stock_invfile || "inherited"})`
        : `no own invfile — inherits ${u.effective_invfile}.dc6`;
    } else state.textContent = u.error || "";
  } catch (e) { state.textContent = ""; }
  $("#setInvfileBtn").onclick = () => setOwnInvfile(it, $("#ownInvfile").value.trim());
  $("#revertInvfileBtn").onclick = () => setOwnInvfile(it, "");
}

async function setOwnInvfile(it, value) {
  $("#setInvfileBtn").disabled = true;
  try {
    const r = await fetch(`/api/item/${encodeURIComponent(it.id)}/txt`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ field: "invfile", value }),
    });
    const u = await r.json();
    if (!u.ok) return toast("invfile edit failed: " + (u.error || ""), true);
    toast(value
      ? `${it.name} now has its own art file "${value}.dc6"${u.seeded ? " (seeded with current art)" : ""} — Push + Full reload to apply`
      : `${it.name} reverted to inherited art file`);
    await loadItems();
    const fresh = ITEMS.find((x) => x.id === it.id);
    if (fresh) selectItem(fresh);
  } finally {
    if ($("#setInvfileBtn")) $("#setInvfileBtn").disabled = false;
  }
}

/* ---------- Tint picker (the `tint xxxx` chip) ----------
   Edits the item's inventory (invtransform) and on-body (chrtransform) colour bytes in its
   uniqueitems/setitems .bin row. One swatch grid drives both sides while they match; when they
   differ (or "separate on-body colour" is ticked) each side gets its own grid. A side whose base
   item can't render a tint (InvTrans / Transform = 0) is left out. */
let TINT_OPEN_FOR = null;         // item id whose picker is open; survives selectItem re-renders
const TINT_SPLIT = new Map();     // item id -> user's "separate on-body colour" choice

document.addEventListener("click", (e) => {
  // the popover stops its own clicks (it re-renders under the cursor), so anything reaching here is outside it
  const pop = document.querySelector("#tintPop");
  if (pop && !pop.classList.contains("hidden") && !e.target.closest("#tintChip")) closeTintPop();
});

function closeTintPop() {
  TINT_OPEN_FOR = null;
  $("#tintPop")?.classList.add("hidden");
}

function wireTint(it) {
  const chip = $("#tintChip"), pop = $("#tintPop");
  if (!chip || !pop) return;
  pop.onclick = (e) => e.stopPropagation();
  chip.onclick = (e) => { e.stopPropagation(); TINT_OPEN_FOR === it.id ? closeTintPop() : openTintPop(it); };
  if (TINT_OPEN_FOR === it.id) openTintPop(it);
}

// Which swatch grids to show: [{label, cur, sides}] -- `sides` are the .bin fields a click writes.
function tintGroups(t, split) {
  if (t.can_inv && t.can_chr) {
    return split
      ? [{ label: "Inventory", cur: t.inv, sides: ["inv"] }, { label: "On body", cur: t.chr, sides: ["chr"] }]
      : [{ label: "Inventory + on body", cur: t.inv, sides: ["inv", "chr"] }];
  }
  if (t.can_inv) return [{ label: "Inventory", cur: t.inv, sides: ["inv"] }];
  if (t.can_chr) return [{ label: "On body", cur: t.chr, sides: ["chr"] }];
  return [];
}

async function openTintPop(it) {
  const pop = $("#tintPop");
  TINT_OPEN_FOR = it.id;
  pop.classList.remove("hidden");
  pop.innerHTML = `<div class="meta">loading…</div>`;
  let t;
  try {
    const j = await (await fetch(`/api/item/${encodeURIComponent(it.id)}/txt`)).json();
    if (!j.ok) throw new Error(j.error || "no tint data");
    t = j.tint;
  } catch (e) {
    pop.innerHTML = `<div class="meta">couldn't load tint: ${esc(String(e.message || e))}</div>`;
    return;
  }
  if (TINT_OPEN_FOR !== it.id || !$("#tintPop")) return;   // closed / switched item while loading
  renderTintPop(it, t);
}

function renderTintPop(it, t) {
  const pop = $("#tintPop");
  const both = t.can_inv && t.can_chr;
  const split = both && (TINT_SPLIT.has(it.id) ? TINT_SPLIT.get(it.id) : !t.linked);
  const groups = tintGroups(t, split);
  const enc = encodeURIComponent(it.id);
  const label = (c) => c || "none";
  const swatch = (g, gi, code) => `
    <button class="tp-sw${g.cur === code ? " sel" : ""}" data-g="${gi}" data-code="${code || "none"}" title="${label(code)}">
      ${t.can_inv ? `<img loading="lazy" src="/api/item/${enc}/tint/${code || "none"}.png?b=${ART_BUST}" alt="" onerror="this.style.visibility='hidden'">` : `<span class="tp-blank"></span>`}
      <span class="tp-nm">${label(code)}</span>
    </button>`;
  const grids = groups.map((g, gi) => `
    <div class="tp-group"><div class="tp-label">${g.label}</div>
      <div class="tp-grid">${["", ...t.colors].map((c) => swatch(g, gi, c)).join("")}</div>
    </div>`).join("");
  const linkedNote = both && !split && !t.linked
    ? `<div class="meta">on-body is currently <b>${label(t.chr)}</b> — picking a colour sets both.</div>` : "";
  const customArt = it.active && it.active !== "original" && t.can_inv && t.inv;
  pop.innerHTML = `
    <div class="tp-head">Tint <span class="meta">${t.edited
      ? `edited · stock ${label(t.stock_inv)}${t.stock_inv === t.stock_chr ? "" : " / " + label(t.stock_chr) + " on body"}`
      : "stock"}</span></div>
    ${groups.length ? grids : `<div class="meta">This item's base has no colour transform (InvTrans and Transform are 0), so a tint can't show in-game.</div>`}
    ${linkedNote}
    ${both ? `<label class="tp-split"><input type="checkbox" id="tpSplit" ${split ? "checked" : ""}> separate on-body colour</label>` : ""}
    ${customArt ? `<div class="tp-hint">Custom art is active — a tint recolours it by palette index and can look off.
      <button id="tpClear">Clear tint</button></div>` : ""}
    ${t.edited ? `<div class="tp-actions"><button id="tpReset">Reset to stock</button></div>` : ""}
    <div class="meta tp-foot">Applies on Push + Full reload.</div>`;

  pop.querySelectorAll(".tp-sw").forEach((b) => {
    b.onclick = () => {
      const g = groups[+b.dataset.g];
      applyTint(it, Object.fromEntries(g.sides.map((s) => [s, b.dataset.code])));
    };
  });
  $("#tpSplit")?.addEventListener("change", (e) => { TINT_SPLIT.set(it.id, e.target.checked); renderTintPop(it, t); });
  $("#tpReset")?.addEventListener("click", () => applyTint(it, { inv: "stock", chr: "stock" }));
  $("#tpClear")?.addEventListener("click", () =>
    applyTint(it, both && !split ? { inv: "none", chr: "none" } : { inv: "none" }));
}

async function applyTint(it, sides) {
  const r = await fetch(`/api/item/${encodeURIComponent(it.id)}/txt`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ field: "tint", ...sides }),
  });
  const j = await r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` }));
  if (!j.ok) return toast("tint edit failed: " + (j.error || ""), true);
  toast(`${it.name} tint updated — Push + Full reload to apply`);
  ART_BUST = Date.now();       // gallery tiles + swatches re-fetch the recoloured art
  await loadItems();
  const fresh = ITEMS.find((x) => x.id === it.id);
  if (fresh) selectItem(fresh);   // re-renders the header; the open picker re-opens via TINT_OPEN_FOR
}

async function activateFlippy(it, choice) {
  const r = await fetch(`/api/item/${encodeURIComponent(it.id)}/activate-flippy`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ choice }),
  });
  const data = await r.json();
  if (!data.ok) return toast("flippy activate failed: " + (data.error || ""), true);
  it.flippy_active = choice;
  toast(`${it.name} flippy: ${choice === "original" ? "reverted to original" : "using " + choice} (Push to game to apply)`);
  selectItem(it);
}

async function dropInGame(it, quality) {
  const btn = $("#dropBtn");
  btn.disabled = true;
  toast(`dropping ${it.name}${quality ? " (" + quality + ")" : ""} at your feet…`);
  try {
    const r = await fetch(`/api/item/${encodeURIComponent(it.id)}/drop`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(quality ? { quality } : {}),
    });
    const d = await r.json();
    toast(d.ok ? `${it.name}: ${d.note}` : "drop failed: " + (d.error || ""), !d.ok);
  } finally {
    btn.disabled = false;
  }
}

let MESHY_LOGGING_IN = false;

async function meshyLogin() {
  if (MESHY_LOGGING_IN) return;
  MESHY_LOGGING_IN = true;
  toast("launching the Meshy login browser…");
  try {
    await fetch("/api/studio/session/launch", { method: "POST" });
    toast("log in to Meshy in the window that opened — it'll connect automatically.");
    const t = setInterval(async () => {
      const s = await (await fetch("/api/studio/session")).json();
      if (s.loggedIn) { clearInterval(t); MESHY_LOGGING_IN = false; toast("meshy connected ✓"); pollMeshy(); }
    }, 3000);
  } catch (e) {
    MESHY_LOGGING_IN = false;
    toast("meshy login failed: " + e, true);
  }
}

async function pollMeshy() {
  try {
    const s = await (await fetch("/api/studio/session")).json();
    const el = $("#meshyStatus");
    HAS_BLENDER = !!s.blender;
    const bl = s.blender ? " · blender ✓" : " · no blender";
    if (s.loggedIn) {
      el.textContent = `meshy: ${s.tier || "session"} ✓${bl}`;
      el.className = "game ok";
      el.onclick = null;
      el.title = "";
      el.style.cursor = "";
    } else {
      el.innerHTML = '<a href="#" class="meshy-login">meshy: log in</a>' + bl;
      el.className = "game bad";
      el.title = "Click to launch the Meshy login browser";
      el.style.cursor = "pointer";
      el.onclick = (ev) => { ev.preventDefault(); meshyLogin(); };
    }
  } catch (e) { /* ignore */ }
}

async function activate(it, choice) {
  const r = await fetch(`/api/item/${encodeURIComponent(it.id)}/activate`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ choice }),
  });
  const data = await r.json();
  if (!data.ok) return toast("activate failed", true);
  // The active choice is stored per shared DC6 bucket (invfile), so every item drawn from
  // the same DC6 now shows this art in-game — update all their tiles, not just the one clicked.
  const bucket = (it.invfile || "").toLowerCase();
  for (const other of ITEMS) {
    if ((other.invfile || "").toLowerCase() === bucket) other.active = choice;
  }
  toast(`${it.name}: ${choice === "original" ? "reverted to original" : "using " + choice} (Push to game to apply)`);
  // Update the active state IN PLACE (no selectItem re-render) so the strip scroll and the current
  // preview are untouched: move the green .active outline + sync every checkbox.
  it.active = choice;
  document.querySelectorAll("#detail .variant[data-choice]").forEach((v) => {
    const isActive = v.dataset.choice === choice;
    v.classList.toggle("active", isActive);
    const cb = v.querySelector(".activate-box");
    if (cb) cb.checked = isActive;
  });
  renderGrid();   // gallery tiles render current.png -> reflect the new active art
}

async function importPng(it, file) {
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`/api/item/${encodeURIComponent(it.id)}/import`, { method: "POST", body: fd });
  const data = await r.json();
  if (!data.ok) return toast("import failed: " + (data.error || ""), true);
  it.alts = data.alts;
  toast(`imported alternate "${data.alt_id}"`);
  await activate(it, data.alt_id);
}

async function push() {
  $("#pushBtn").disabled = true;
  toast("building patch.mpq & registering…");
  try {
    const r = await fetch("/api/push", { method: "POST" });
    const data = await r.json();
    if (data.ok) toast(`pushed ${data.built} file(s) → registered live. Reload game (or re-enter) to see it.`);
    else toast(`push failed (${data.stage}): ${data.error}`, true);
  } finally {
    $("#pushBtn").disabled = false;
  }
}

async function reload() {
  $("#reloadBtn").disabled = true;
  toast("soft reload: exiting to menu & re-entering…");
  try {
    const r = await fetch("/api/reload", { method: "POST" });
    const data = await r.json();
    toast(data.ok ? "soft reload done — " + (data.note || "") : "reload issue: " + (data.error || ""), !data.ok);
  } finally {
    $("#reloadBtn").disabled = false;
  }
}

async function fullReload() {
  $("#fullReloadBtn").disabled = true;
  toast("full reload: relaunching a fresh game (accept the UAC prompt)… ~60-90s");
  try {
    const r = await fetch("/api/full-reload", { method: "POST" });
    const data = await r.json();
    toast(data.ok ? "full reload done — art loaded fresh (" + (data.note || "") + ")" : "full reload issue: " + (data.note || ""), !data.ok);
  } finally {
    $("#fullReloadBtn").disabled = false;
  }
}

let GAME_LAUNCHING = false;

async function launchGame() {
  if (GAME_LAUNCHING) return;
  GAME_LAUNCHING = true;
  toast("starting PD2 with the debugger — accept the UAC prompt…");
  try {
    const r = await fetch("/api/game/launch", { method: "POST" });
    const d = await r.json();
    if (!d.ok) { GAME_LAUNCHING = false; return toast("launch failed: " + (d.error || ""), true); }
    toast("launching — the game will connect in ~60-90s.");
    const t = setInterval(async () => {
      const s = await (await fetch("/api/game/status")).json();
      if (s.reachable) { clearInterval(t); GAME_LAUNCHING = false; toast("game connected ✓"); pollGame(); }
    }, 4000);
  } catch (e) {
    GAME_LAUNCHING = false;
    toast("launch failed: " + e, true);
  }
}

async function pollGame() {
  try {
    const r = await fetch("/api/game/status");
    const data = await r.json();
    const el = $("#gameStatus");
    if (data.reachable) {
      const a = data.asset || {};
      el.textContent = `game: connected${a.registered ? " · overlay registered @" + a.priority : ""}`;
      el.className = "game ok";
      el.onclick = null;
      el.title = "";
      el.style.cursor = "";
    } else {
      el.innerHTML = 'game: not running — <a href="#" class="game-launch">start PD2 with the debugger</a>';
      el.className = "game bad";
      el.title = "Click to launch PD2 with the debugger (1 UAC prompt)";
      el.style.cursor = "pointer";
      el.onclick = (ev) => { ev.preventDefault(); launchGame(); };
    }
  } catch (e) { /* ignore */ }
}

$("#search").oninput = debounce(renderGrid, 200);  // client-side filter — no refetch
$("#pushBtn").onclick = push;
$("#reloadBtn").onclick = reload;
$("#fullReloadBtn").onclick = fullReload;
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// Refresh when the gallery regains focus (returning from the pairing page after a build)
// and on back/forward-cache restore, so accepted alternates show up without a manual reload.
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshView(); });
window.addEventListener("pageshow", (e) => { if (e.persisted) refreshView(); });

// Deep link — e.g. /?item=<id> from the /pairing page's "open in gallery" button —
// pre-selects that item once the catalog has loaded (opening Details + Generate for it).
async function openDeepLink() {
  const id = new URLSearchParams(location.search).get("item");
  if (!id) return;
  const it = ITEMS.find((x) => x.id === id);
  if (it) selectItem(it);
}

loadItems().then(openDeepLink);
pollGame();
pollMeshy();
setInterval(pollGame, 5000);
setInterval(pollMeshy, 15000);
