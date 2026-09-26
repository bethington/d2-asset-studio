// Game-sync banner: tells you when the installed game changed under Studio (files reloaded, an
// archive Studio doesn't recognise, or an alternate made for art the game has since changed).
// Backed by /api/sync/status + /api/sync/accept (app/game_sync.py). Hidden when all is in sync.
(function () {
  const POLL_MS = 60000;
  let startGen = null;
  const bar = document.createElement("div");
  bar.id = "syncBanner";
  bar.hidden = true;
  const css = document.createElement("style");
  css.textContent = `
    #syncBanner { background: var(--panel2, #2a2318); color: var(--ink, #e8dcc0);
      border-bottom: 2px solid var(--gold, #c9a44a); padding: 8px 16px; font-size: 13px;
      position: sticky; top: 0; z-index: 6; }
    #syncBanner[hidden] { display: none; }
    #syncBanner .row { display: flex; align-items: center; gap: 10px; margin: 2px 0; flex-wrap: wrap; }
    #syncBanner .warn { color: #e08a68; font-weight: 600; }
    #syncBanner button { padding: 3px 9px; font-size: 12px; }
    #syncBanner code { color: var(--gold, #c9a44a); }`;
  document.head.appendChild(css);
  document.body.insertBefore(bar, document.body.firstChild);

  function row(text, cls) {
    const r = document.createElement("div");
    r.className = "row";
    const s = document.createElement("span");
    if (cls) s.className = cls;
    s.textContent = text;
    r.appendChild(s);
    bar.appendChild(r);
    return r;
  }
  function button(r, label, onclick) {
    const b = document.createElement("button");
    b.textContent = label;
    b.onclick = onclick;
    r.appendChild(b);
  }
  async function accept(body) {
    try {
      await fetch("/api/sync/accept", { method: "POST", headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify(body) });
    } catch (e) { /* the next poll shows the real state */ }
    refresh();
  }

  function render(st) {
    bar.replaceChildren();
    const gen = st.mpq.generation;
    if (startGen === null) startGen = gen;
    if (gen > startGen) {
      const r = row("The game's files changed and Studio reloaded them. Reload this page to refresh the gallery.", "warn");
      button(r, "Reload page", () => location.reload());
    }
    if (st.mpq.unknown.length) {
      const names = st.mpq.unknown.map(p => p.split(/[\\/]/).pop()).join(", ");
      row(`Unrecognised PD2 archive(s) not being read: ${names}. If they carry game data, list them in PD2_EXTRA_MPQS and restart Studio.`, "warn");
    }
    const be = st.bin_edits;
    if (be && be.error) {
      row(`Could not check your uniqueitems/setitems edits against the game: ${be.error}`, "warn");
    } else if (be) {
      for (const [table, r] of Object.entries(be)) {
        if (r.schema_drift) row(`${table}: the game's table format changed, so your edits to it are NOT applied (the game's own table is used).`, "warn");
        if (r.missing && r.missing.length) row(`${table}: ${r.missing.length} edited row(s) no longer exist in the game (${r.missing.slice(0, 5).join(", ")}${r.missing.length > 5 ? ", ..." : ""}). Not applied; the edits are still recorded.`, "warn");
        if (r.invalid && r.invalid.length) row(`${table}: ${r.invalid.length} recorded edit(s) are invalid and skipped (${r.invalid.slice(0, 5).join(", ")}).`, "warn");
      }
    }
    const d = st.drift;
    if (d) {
      for (const x of d.drifted) {
        const r = row(`Alternate for ${x.key} is NOT being pushed: ${x.reasons.join("; ")}. Refit or regenerate it, then:`, "warn");
        button(r, "Keep my art", () => accept({ key: x.key }));
      }
      if (d.drifted.length > 1) {
        const r = row("");
        button(r, `Keep all ${d.drifted.length}`, () => accept({ all: true }));
      }
      const o = d.orphans;
      const n = o.buckets.length + o.item_stores.length + o.prompts.length;
      if (n) row(`${n} stored item(s) no longer match the game: ${o.buckets.length} alternate bucket(s), ${o.item_stores.length} Enhance store(s), ${o.prompts.length} prompt(s). Nothing was changed or deleted.`);
    }
    bar.hidden = bar.childElementCount === 0;
  }

  async function refresh() {
    try {
      const res = await fetch("/api/sync/status");
      if (res.ok) render(await res.json());
    } catch (e) { /* server restarting; try again next poll */ }
  }
  refresh();
  setInterval(refresh, POLL_MS);
  window.addEventListener("focus", refresh);
})();
