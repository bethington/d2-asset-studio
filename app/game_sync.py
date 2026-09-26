"""Keep Studio in step with the installed game version.

Two jobs, both driven by the game's MPQs (Studio stores no copy of the game's item data):

1. Freshness. pyd2.mpq notices when any game archive changes on disk (size/mtime) and reopens
   them; `install()` registers the caches derived from MPQ data so a game update is picked up
   without restarting Studio.

2. Drift. An alternate is generated against the game's ORIGINAL art (its pixels + the item's cell
   size) and pushed as an overlay that outranks the game. If an update changes either, that stale
   overlay would silently override the new official art. Each ACTIVE alternate is therefore
   fingerprinted against the original it was made for (<workspace>/sync_baseline.json). When the
   current original differs, the alternate is flagged and kept OUT of the pushed patch (never
   deleted); `accept()` records the new state ("keep my art") once it has been refit/regenerated.
   Orphans -- alternates, Enhance stores and prompts whose invfile/item no longer exists -- are
   reported, never migrated automatically.

`SyncChecker` takes its dependencies as callables so it is testable with fakes; `install()` wires
the real ones. The module-level helpers are no-ops until installed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time

from pyd2 import mpq

log = logging.getLogger("app.game_sync")

_MISSING = "missing"   # sha1 placeholder: the original file is absent from the game


class _Unreadable(Exception):
	"""The original could not be read for a reason other than 'absent' (e.g. an archive being
	replaced mid-update). Never treated as drift -- we just could not verify this time."""


def _rel(filename: str) -> str:
	return f"data\\global\\items\\{filename}.dc6"   # inventory art and flippies share this dir


def _fmt_cells(cells) -> str:
	return "/".join(f"{w}x{h}" for w, h in cells) or "none"


class SyncChecker:
	def __init__(self, manifest_fn, items_fn, original_fn, baseline_path,
	             store_names_fn=None, expected_store_fn=None, prompt_ids_fn=None):
		self._manifest = manifest_fn
		self._items = items_fn
		self._original = original_fn
		self._baseline_path = baseline_path
		self._store_names = store_names_fn
		self._expected_store = expected_store_fn
		self._prompt_ids = prompt_ids_fn
		self.last_skipped: list = []

	# ---- baseline file ----------------------------------------------------

	def _load(self) -> dict:
		try:
			with open(self._baseline_path, encoding="utf-8") as f:
				d = json.load(f)
			if isinstance(d.get("assets"), dict):
				return d
		except (OSError, ValueError):
			pass
		return {"version": 1, "assets": {}}

	def _save(self, d: dict) -> None:
		os.makedirs(os.path.dirname(os.path.abspath(self._baseline_path)), exist_ok=True)
		tmp = self._baseline_path + ".tmp"
		with open(tmp, "w", encoding="utf-8") as f:
			json.dump(d, f, indent=1)
		os.replace(tmp, self._baseline_path)

	# ---- what is being tracked --------------------------------------------

	def _tracked(self):
		"""(key, kind, filename) for every manifest asset that has an ACTIVE alternate."""
		for key, e in (self._manifest().get("assets") or {}).items():
			if key.startswith("dc6/") and e.get("active") not in (None, "", "original"):
				yield key, "inv", e.get("invfile") or key[4:]
			elif key.startswith("flip/") and e.get("flippy_active") not in (None, "", "original"):
				yield key, "flip", e.get("flippyfile") or key[5:]

	def _fingerprint(self, kind: str, filename: str, items) -> dict:
		try:
			sha = hashlib.sha1(self._original(filename)).hexdigest()
		except FileNotFoundError:
			sha = _MISSING
		except (OSError, ValueError) as e:
			raise _Unreadable(str(e)) from e
		fp = {"sha1": sha}
		if kind == "inv":
			want = filename.lower()
			cells = sorted({(int(i["invwidth"]), int(i["invheight"])) for i in items
			                if (i.get("invfile") or "").lower() == want})
			fp["cell"] = [list(c) for c in cells]
		return fp

	@staticmethod
	def _reasons(kind: str, base: dict, cur: dict) -> list:
		out = []
		if base.get("sha1") != cur["sha1"]:
			out.append("original art no longer exists in the game" if cur["sha1"] == _MISSING
			           else "original art changed in the game")
		if kind == "inv" and base.get("cell") and cur.get("cell") and base["cell"] != cur["cell"]:
			out.append(f"cell size changed: {_fmt_cells(base['cell'])} -> {_fmt_cells(cur['cell'])}")
		return out

	# ---- the check --------------------------------------------------------

	def check(self) -> dict:
		"""Compare every active alternate with the game's current original.

		Alternates seen for the first time are baselined against the game AS IT IS NOW (i.e. assumed
		in sync). Returns {drifted, orphans, baselined, unverified}.
		"""
		base = self._load()
		stored = base["assets"]
		items = list(self._items() or [])
		drifted, baselined, unverified, orphan_buckets = [], [], [], []
		for key, kind, filename in self._tracked():
			try:
				cur = self._fingerprint(kind, filename, items)
			except _Unreadable:
				unverified.append(key)
				continue
			prev = stored.get(key)
			if prev is None:
				stored[key] = {**cur, "ts": time.time()}
				baselined.append(key)
				continue
			if kind == "inv" and not cur["cell"]:
				orphan_buckets.append(key)       # no item uses this invfile any more: harmless, report
				continue
			reasons = self._reasons(kind, prev, cur)
			if reasons:
				drifted.append({"key": key, "kind": kind, "file": filename, "reasons": reasons})
		if baselined:
			self._save(base)

		item_ids = {i["id"] for i in items}
		orphans = {"buckets": sorted(orphan_buckets), "item_stores": [], "prompts": []}
		if self._store_names and self._expected_store:
			have = {self._expected_store(i) for i in items}
			orphans["item_stores"] = sorted(n for n in self._store_names() if n not in have)
		if self._prompt_ids:
			orphans["prompts"] = sorted(p for p in self._prompt_ids() if p not in item_ids)
		return {"drifted": drifted, "orphans": orphans, "baselined": baselined,
		        "unverified": unverified}

	def excluded_rels(self) -> set:
		"""Overlay files (archive paths) whose alternate has drifted and must not be pushed."""
		return {_rel(d["file"]) for d in self.check()["drifted"]}

	def filter_overlay(self, files: dict) -> list:
		"""Remove drifted alternates from a {archive path: disk path} build map, in place."""
		gone = {r.lower() for r in self.excluded_rels()}
		skipped = [k for k in list(files) if k.lower() in gone]
		for k in skipped:
			del files[k]
		self.last_skipped = skipped
		if skipped:
			log.warning("Not pushing %d alternate(s) made for art the game has since changed: %s "
			            "(refit/regenerate, then accept in the sync banner)",
			            len(skipped), ", ".join(skipped))
		return skipped

	# ---- baseline management ----------------------------------------------

	def _kind_file(self, key: str, filename: str | None = None):
		kind = "flip" if key.startswith("flip/") else "inv"
		return kind, filename or key.split("/", 1)[1]

	def note_activation(self, key: str, filename: str) -> None:
		"""An alternate is being activated: baseline its bucket if it has none yet. Never
		overwrites an existing baseline -- re-activating must not hide a drift."""
		base = self._load()
		if key in base["assets"]:
			return
		kind, fname = self._kind_file(key, filename)
		try:
			cur = self._fingerprint(kind, fname, list(self._items() or []))
		except _Unreadable:
			return
		base["assets"][key] = {**cur, "ts": time.time()}
		self._save(base)

	def accept(self, key: str) -> dict:
		"""Adopt the game's current original as the new baseline for `key` ("keep my art")."""
		kind, fname = self._kind_file(key)
		for k, kd, fn in self._tracked():
			if k == key:
				kind, fname = kd, fn
				break
		cur = self._fingerprint(kind, fname, list(self._items() or []))
		base = self._load()
		base["assets"][key] = {**cur, "ts": time.time()}
		self._save(base)
		return cur

	def accept_all(self) -> list:
		keys = [d["key"] for d in self.check()["drifted"]]
		for k in keys:
			self.accept(k)
		return keys


# ---- installed (real) instance ------------------------------------------------

_STATE = {"checker": None, "clears": [], "rebuild": None, "bin_report": None}


def _default_clears() -> list:
	"""Clear every cache derived from MPQ data. Imported lazily; a missing module is skipped."""
	fns = []
	try:
		from app import assets
		fns += [assets._PAL_CACHE.clear, assets._XFORM_PAL_CACHE.clear, assets.original_footprint.cache_clear]
	except Exception:  # noqa: BLE001
		pass
	try:
		from pyd2 import chars
		fns += [c.clear for c in (getattr(chars, "_TXT_CACHE", None), getattr(chars, "_DCC_CACHE", None))
		        if c is not None]
	except Exception:  # noqa: BLE001
		pass
	try:
		from app import catalog
		fns.append(catalog.clear_caches)
	except Exception:  # noqa: BLE001
		pass
	return fns


def _default_checker(items_fn) -> SyncChecker:
	from app import assets, gen_prompts, upscale_store

	def stores():
		try:
			return [n for n in os.listdir(upscale_store.ROOT)
			        if os.path.isdir(os.path.join(upscale_store.ROOT, n))]
		except OSError:
			return []

	return SyncChecker(
		manifest_fn=assets._load_manifest, items_fn=items_fn, original_fn=assets.read_original_dc6,
		baseline_path=os.path.join(assets.WORKSPACE, "sync_baseline.json"),
		store_names_fn=stores, expected_store_fn=lambda i: upscale_store._key(i["id"]),
		prompt_ids_fn=lambda: list(gen_prompts._load().keys()))


def install(items_fn, extra_clears=(), checker: SyncChecker | None = None,
            rebuild_fn=None, bin_report_fn=None, rebuild_now: bool = False) -> None:
	"""Wire game-update handling: on an MPQ change, clear the derived caches (`extra_clears`
	are the caller's own, e.g. the server's catalog), re-derive the overlay .bin edits
	(`rebuild_fn`, excel.rebuild_overlay_bins) then re-run the drift check. `bin_report_fn` is the
	read-only "edits that no longer apply" report shown in status(). `rebuild_now` also rebuilds
	once immediately, to catch an update that happened while Studio was closed."""
	_STATE["checker"] = checker or _default_checker(items_fn)
	_STATE["clears"] = list(extra_clears)
	_STATE["rebuild"], _STATE["bin_report"] = rebuild_fn, bin_report_fn
	if on_game_changed not in mpq._CHANGE_CALLBACKS:
		mpq.on_change(on_game_changed)
	if rebuild_now:
		_rebuild_bins()


def uninstall() -> None:
	if on_game_changed in mpq._CHANGE_CALLBACKS:
		mpq._CHANGE_CALLBACKS.remove(on_game_changed)
	_STATE.update(checker=None, clears=[], rebuild=None, bin_report=None)


def _rebuild_bins() -> None:
	"""Re-derive the overlay table edits from the current stock tables. A failure is logged, not
	raised: the push path re-runs it and fails loudly there, and it must not stop Studio starting."""
	fn = _STATE["rebuild"]
	if not fn:
		return
	try:
		for table, rep in (fn() or {}).items():
			if rep.get("missing") or rep.get("invalid") or rep.get("schema_drift"):
				log.warning("%s edits not fully applied to the current game table: %s", table, rep)
	except Exception:  # noqa: BLE001
		log.exception("re-deriving the overlay table edits failed")


def on_game_changed() -> None:
	for fn in _default_clears() + _STATE["clears"]:
		try:
			fn()
		except Exception:  # noqa: BLE001
			log.exception("cache clear %r failed", fn)
	_rebuild_bins()
	c = _STATE["checker"]
	if c:
		try:
			res = c.check()
			log.warning("Game update handled: %d alternate(s) drifted, %d orphaned bucket(s)",
			            len(res["drifted"]), len(res["orphans"]["buckets"]))
		except Exception:  # noqa: BLE001
			log.exception("drift check after a game update failed")


def filter_overlay(files: dict) -> list:
	c = _STATE["checker"]
	return c.filter_overlay(files) if c else []


def last_skipped() -> list:
	c = _STATE["checker"]
	return list(c.last_skipped) if c else []


def note_activation(key: str, filename: str) -> None:
	c = _STATE["checker"]
	if c:
		c.note_activation(key, filename)


def status() -> dict:
	mpq.refresh_if_changed()
	c = _STATE["checker"]
	bin_edits = None
	if _STATE["bin_report"]:
		try:
			bin_edits = _STATE["bin_report"]()
		except Exception as e:  # noqa: BLE001 - a status poll must never 500
			bin_edits = {"error": str(e)}
	return {"mpq": mpq.status(), "drift": c.check() if c else None, "bin_edits": bin_edits}


def register_routes(flask_app) -> None:
	from flask import jsonify, request

	@flask_app.get("/api/sync/status")
	def api_sync_status():
		return jsonify(status())

	@flask_app.post("/api/sync/accept")
	def api_sync_accept():
		c = _STATE["checker"]
		if not c:
			return jsonify({"ok": False, "error": "sync not installed"}), 503
		body = request.get_json(silent=True) or {}
		try:
			if body.get("all"):
				return jsonify({"ok": True, "accepted": c.accept_all()})
			key = (body.get("key") or "").strip()
			if not key:
				return jsonify({"ok": False, "error": "want {\"key\": \"dc6/<invfile>\"} or {\"all\": true}"}), 400
			c.accept(key)
			return jsonify({"ok": True, "accepted": [key]})
		except _Unreadable as e:
			return jsonify({"ok": False, "error": f"cannot read the game's original right now: {e}"}), 503
