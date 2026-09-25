/* Shared collapsible provenance box -- used by the workflow panel (workflow.js) and the
   alternate inspector (app.js) so the same collapsible-details + row markup isn't hand-rolled
   in both places. Plain global functions (classic script, no bundler, no module system here);
   load this before workflow.js and app.js. */

function pvEsc(s) {
	return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function pvRow(k, v) {
	if (v === undefined || v === null || v === "") return "";
	return `<div class="pvbox-row"><span class="pvbox-k">${pvEsc(k)}</span><span class="pvbox-v">${pvEsc(v)}</span></div>`;
}

// title/headerExtra: pre-escaped HTML (caller controls escaping since it may mix plain text with
// markup, e.g. a button). headerExtra renders inside <summary> next to the title, so it's visible
// even collapsed. rows: joined pvRow(...)/raw HTML string. Closed by default.
function pvBox({ title, headerExtra = "", rows, defaultOpen = false }) {
	return `<details class="pvbox"${defaultOpen ? " open" : ""}>
    <summary>${title}${headerExtra}</summary>
    ${rows}
  </details>`;
}
