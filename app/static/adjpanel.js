/* Shared framing/colour adjustment panel -- sliders (size/nudge x/nudge y/brightness/contrast/
   saturation/warmth/hue) + even-border checkbox + original/adjusted compare images + action
   buttons. Used by workflow.js's accept-2d confirm step and app.js's alternate-inspector adjust
   panel: same controls and live-preview wiring, different preview/commit endpoints and different
   post-commit flow (workflow.js replaces the box with a "saved -> Activate" result; app.js
   refreshes thumbnails elsewhere on the page). That difference is real, not cosmetic, so this
   component owns the generic mechanics and hands committing off to the caller via onCommit --
   it never decides what happens after a commit succeeds.

   Usage:
     container.innerHTML = renderAdjPanel(cfg);
     wireAdjPanel(container, cfg);

   cfg = {
     originalSrc:      URL string for the "original" compare image.
     buildPreviewUrl:   (values, evenBorder) => URL string for the "adjusted" compare image.
                        Called on init and after every slider/checkbox change (debounced).
     footprint:         {fill, dx, dy} -- initial size/nudge slider values AND what "Auto size"
                        resets them to.
     initial:           optional {brightness, contrast, saturation, warmth, hue} starting values
                        (defaults to neutral 1/1/1/1/0).
     evenBorderInit:    optional bool, even-border checkbox initial state.
     note:              optional string/HTML shown above the compare row.
     buttons: [
       {kind: "auto"},                                  // reset size/nudge to footprint
       {kind: "resetColor"},                             // reset colour sliders to neutral
       {kind: "cancel", label, onClick},                 // caller-driven; omit for no Cancel
       {kind: "commit", label, className, onCommit},     // onCommit(values, evenBorder) -> Promise
     ],
   }
   `values` passed to onCommit is {fill, dx, dy, brightness, contrast, saturation, warmth, hue}. */

const AP_SLIDERS = [
	["fill", "size", 0.1, 1.5, 0.01],
	["dx", "nudge x", -1, 1, 0.02],
	["dy", "nudge y", -1, 1, 0.02],
	// degrees CCW, applied before the cell fit -- so "size" still fills the cell after rotating
	["rot", "rotate", -180, 180, 1],
	["brightness", "brightness", 0.5, 1.6, 0.02],
	["contrast", "contrast", 0.5, 1.6, 0.02],
	["saturation", "saturation", 0, 2, 0.02],
	["warmth", "warmth", 0.5, 1.5, 0.02],
	["hue", "hue", -180, 180, 2],
];
const AP_NEUTRAL_COLOR = { brightness: 1, contrast: 1, saturation: 1, warmth: 1, hue: 0 };

// Initial slider value: `initial[k]` if the caller supplied one (e.g. an alternate's own saved
// fill/dx/dy/grade), else `footprint` for fill/dx/dy, else neutral for colour. Deliberately NOT
// the same thing "Auto size" resets to -- that always targets `footprint` (the original artwork's
// true footprint), even when the slider started somewhere else via `initial`.
function _apInit(cfg, k) {
	const iv = cfg.initial || {};
	if (iv[k] != null) return iv[k];
	if (k === "fill") return cfg.footprint.fill;
	if (k === "dx") return cfg.footprint.dx;
	if (k === "dy") return cfg.footprint.dy;
	if (k === "rot") return 0;          // art is authored upright; rotation is always opt-in
	return AP_NEUTRAL_COLOR[k];
}

function _apButtonHTML(b) {
	if (b.kind === "auto") return `<button class="ap-auto" title="Match the original artwork's footprint">Auto size</button>`;
	if (b.kind === "resetColor") return `<button class="ap-resetcolor" title="Reset colour to neutral">Reset colour</button>`;
	if (b.kind === "cancel") return `<button class="ap-cancel">${pvEsc(b.label || "Cancel")}</button>`;
	if (b.kind === "commit") return `<button class="ap-commit ${b.className || "gold"}">${pvEsc(b.label || "Apply")}</button>`;
	return "";
}

function renderAdjPanel(cfg) {
	const rows = AP_SLIDERS.map(([k, lbl, mn, mx, st]) => {
		const iv = _apInit(cfg, k);
		return `<div class="ap-row"><label>${lbl}</label>
      <input type="range" data-ap="${k}" min="${mn}" max="${mx}" step="${st}" value="${iv}">
      <span class="ap-val" data-apv="${k}">${(+iv).toFixed(2)}</span></div>`;
	}).join("");
	return `
    ${cfg.note ? `<div class="ap-note">${cfg.note}</div>` : ""}
    <div class="ap-compare">
      <figure><img class="checker" src="${cfg.originalSrc}"><figcaption>original</figcaption></figure>
      <figure><img class="checker ap-prev"><figcaption>adjusted</figcaption></figure>
    </div>
    ${rows}
    <div class="ap-row"><label title="Bake the vanilla 1px near-black rim into the sprite. Turn OFF when the rim reads as a jagged black staircase on a diagonal or rounded edge (small round items like runes usually look cleaner without it).">outline</label>
      <input type="checkbox" data-apc="outline" ${cfg.outlineInit === false ? "" : "checked"} style="margin-right:auto"></div>
    <div class="ap-row"><label title="Fill in a continuous, even 1px black rim around the whole silhouette (fixes broken/uneven borders on diagonal edges). Never thickens an existing border. Requires outline.">even border</label>
      <input type="checkbox" class="ap-even" ${cfg.evenBorderInit ? "checked" : ""} style="margin-right:auto"></div>
    <div class="ap-btns">${(cfg.buttons || []).map(_apButtonHTML).join("")}<span class="ap-status"></span></div>`;
}

function wireAdjPanel(container, cfg) {
	const values = () => {
		const p = {};
		container.querySelectorAll("[data-ap]").forEach((s) => (p[s.dataset.ap] = +s.value));
		// boolean toggles ride along in the same values object (0/1) so callers pass them through
		// to the preview URL and the commit body without a second argument
		container.querySelectorAll("[data-apc]").forEach((b) => (p[b.dataset.apc] = b.checked ? 1 : 0));
		return p;
	};
	const evenOn = () => !!container.querySelector(".ap-even")?.checked;
	const status = container.querySelector(".ap-status");
	const prevImg = container.querySelector(".ap-prev");
	let t = null;
	const refresh = () => { if (prevImg) prevImg.src = cfg.buildPreviewUrl(values(), evenOn()); };
	refresh();
	container.querySelectorAll("[data-ap]").forEach((s) => {
		s.oninput = () => {
			const v = container.querySelector(`[data-apv="${s.dataset.ap}"]`);
			if (v) v.textContent = (+s.value).toFixed(2);
			clearTimeout(t); t = setTimeout(refresh, 300);
		};
	});
	const evenEl = container.querySelector(".ap-even");
	if (evenEl) evenEl.onchange = refresh;
	container.querySelectorAll("[data-apc]").forEach((b) => (b.onchange = refresh));
	const setSlider = (k, val) => {
		const s = container.querySelector(`[data-ap="${k}"]`);
		if (!s) return;
		s.value = val;
		const v = container.querySelector(`[data-apv="${k}"]`);
		if (v) v.textContent = (+val).toFixed(2);
	};
	for (const b of cfg.buttons || []) {
		if (b.kind === "auto") {
			container.querySelector(".ap-auto").onclick = () => {
				setSlider("fill", cfg.footprint.fill); setSlider("dx", cfg.footprint.dx); setSlider("dy", cfg.footprint.dy);
				setSlider("rot", 0);   // geometry reset: upright, at the original's footprint
				refresh();
			};
		} else if (b.kind === "resetColor") {
			container.querySelector(".ap-resetcolor").onclick = () => {
				Object.keys(AP_NEUTRAL_COLOR).forEach((k) => setSlider(k, AP_NEUTRAL_COLOR[k]));
				refresh();
			};
		} else if (b.kind === "cancel") {
			container.querySelector(".ap-cancel").onclick = b.onClick;
		} else if (b.kind === "commit") {
			container.querySelector(".ap-commit").onclick = async () => {
				const savingText = "saving…";
				if (status) status.textContent = savingText;
				try {
					await b.onCommit(values(), evenOn());
				} finally {
					// Only clear it back if onCommit left it untouched -- a caller that set its own
					// message (e.g. a persistent error) or replaced the container entirely (success
					// -> a result view, detaching this status node) owns it from here.
					if (status && status.isConnected && status.textContent === savingText) status.textContent = "";
				}
			};
		}
	}
}
