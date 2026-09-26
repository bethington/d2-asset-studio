"""PD2 Asset Studio — Flask app.

Browse PD2 items, import PNG alternates for their inventory art, convert to DC6,
build a patch.mpq overlay, and push it live to the running game via the D2Debugger
AssetReload endpoint (:8790).  Run:  python app/server.py   (http://127.0.0.1:5001)
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

from flask import Flask, Response, jsonify, request, send_from_directory

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import io  # noqa: E402

from PIL import Image  # noqa: E402

import app.assets as assets  # noqa: E402
import app.blender as blender  # noqa: E402
import app.excel as excel  # noqa: E402
import app.glove_pairs as glove_pairs  # noqa: E402
import app.meshy_links as meshy_links  # noqa: E402
import app.meshy_web as meshy_web  # noqa: E402
import app.pair_orient3d as pair_orient3d  # noqa: E402
from app.catalog import build_catalog, unique_row  # noqa: E402

MESHY_CACHE = os.path.join(assets.WORKSPACE, "meshy_cache")

# Bump when image PROCESSING changes so browsers drop previously cached renders.
# v2: background cutout keeps interior shadows (was punching holes through the art).
IMAGE_PIPELINE_VERSION = "2"


def _processed_png(png_bytes, *, key):
	"""Serve a generated PNG that must never be served stale from a browser cache."""
	etag = f'W/"{IMAGE_PIPELINE_VERSION}-{key}"'
	if request.headers.get("If-None-Match") == etag:
		return Response(status=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
	return Response(png_bytes, mimetype="image/png",
	                headers={"ETag": etag, "Cache-Control": "no-cache"})

D2DBG = "http://127.0.0.1:8790"
STATIC = os.path.join(os.path.dirname(__file__), "static")

flask_app = Flask(__name__, static_folder=None)

_CATALOG = {"items": None, "by_id": {}}


def catalog():
	if _CATALOG["items"] is None:
		items, _by_code = build_catalog()
		# apply live uniqueitems.bin edits (own invfile/flippyfile) over the txt-derived view;
		# keep the pre-edit names around — a studio-invented invfile has no stock art, so
		# "original" renders fall back to what the item inherited before the edit
		over = excel.unique_overrides()
		for it in items:
			if it["category"] == "unique" and it["name"] in over:
				o = over[it["name"]]
				if o.get("invfile"):
					if o["invfile"] != it["invfile"]:
						it["stock_invfile"] = it["invfile"]
					it["invfile"] = o["invfile"]
				if o.get("flippyfile"):
					if o["flippyfile"] != it["flippyfile"]:
						it["stock_flippyfile"] = it["flippyfile"]
					it["flippyfile"] = o["flippyfile"]
		# live tint edits (uniques + sets): 'none' = the studio cleared the tint (-1)
		tints = {"unique": excel.tint_overrides("uniqueitems"), "set": excel.tint_overrides("setitems")}
		for it in items:
			t = tints.get(it["category"], {}).get(it["name"])
			if t and "invtransform" in t:
				it["invtransform"] = "" if t["invtransform"] == "none" else t["invtransform"]
		_CATALOG["items"] = items
		_CATALOG["by_id"] = {it["id"]: it for it in items}
		# base code -> base item (first wins), for resolving a rune/gem's stack counterpart
		bc = {}
		for it in items:
			if it["category"] == "base" and "#g" not in it["id"]:
				bc.setdefault(it["code"], it)
		_CATALOG["by_code"] = bc
	return _CATALOG


def invalidate_catalog():
	_CATALOG["items"] = None
	_CATALOG["by_id"] = {}


def _shared_group():
	"""invfile (lowercased) -> [item, ...] over the whole catalog, so we can tell how many
	items share each DC6 (the 'shared by N' badge) and rebucket by it."""
	g = {}
	for it in catalog()["items"]:
		g.setdefault((it["invfile"] or "").lower(), []).append(it)
	return g


def _variant_owner(it):
	"""Resolve which upscale store actually holds this item's art. Enhances are generated once
	per invfile (under a representative item), so an item that was deduped out has an empty store
	of its own but shares a sibling's variants. Returns (owner_id, idx, inherited_from) where
	inherited_from is None when the item owns its variants, else the sibling id they live under."""
	own = upscale_store.load(it["id"])
	if own.get("variants"):
		return it["id"], own, None
	inv = (it.get("invfile") or "").lower()
	if inv:
		for sib in _shared_group().get(inv, []):
			if sib["id"] == it["id"]:
				continue
			sidx = upscale_store.load(sib["id"])
			if sidx.get("variants"):
				return sib["id"], sidx, sib["id"]
	return it["id"], own, None


def _store_id(item_id):
	"""Id of the upscale store that reads AND writes must use for item_id (variants, selection,
	meshy chain, boot splits). Writing to the item's own store while reads resolve through
	_variant_owner forks a new history for an inheriting item and hides its siblings' images."""
	it = _item(item_id)
	return _variant_owner(it)[0] if it else item_id


# Bucket alternates by the DC6 file items share (invfile/flippyfile) rather than per item:
# every item whose inventory graphic resolves to the same .dc6 shares one pool + one active
# choice. Resolver reads the LIVE catalog so a studio invfile edit re-buckets automatically.
assets.register_item_resolver(lambda item_id: catalog()["by_id"].get(item_id))


def _dbg(method: str, path: str, body: dict | None = None, timeout: float = 12.0):
	url = D2DBG + path
	data = json.dumps(body).encode() if body is not None else None
	req = urllib.request.Request(url, data=data, method=method,
	                             headers={"Content-Type": "application/json"})
	try:
		with urllib.request.urlopen(req, timeout=timeout) as r:
			return json.loads(r.read().decode()), None
	except Exception as e:  # noqa: BLE001
		return None, str(e)


@flask_app.get("/")
def index():
	return send_from_directory(STATIC, "index.html")


@flask_app.get("/static/<path:p>")
def static_files(p):
	return send_from_directory(STATIC, p)


@flask_app.get("/api/items")
def api_items():
	c = catalog()
	q = (request.args.get("q") or "").lower()
	cat = request.args.get("category") or ""
	group = _shared_group()  # invfile -> items sharing that DC6 (for the 'shared by N' badge)
	out = []
	for i, it in enumerate(c["items"]):
		if q and q not in it["name"].lower() and q not in it["code"].lower():
			continue
		if cat and it["category"] != cat:
			continue
		out.append({
			"id": it["id"], "name": it["name"], "code": it["code"],
			"category": it["category"], "invfile": it["invfile"],
			"shared_by": len(group.get((it["invfile"] or "").lower(), [])),
			"invwidth": it["invwidth"], "invheight": it["invheight"],
			"invtransform": it["invtransform"],
			# an ACTIVE tint (valid colour code + base InvTrans), not just a non-blank cell
			"tinted": _inv_tint_palette(it) is not None,
			"active": assets.active_choice(it["id"]),
			"alts": assets.list_alternates(it["id"]),
			"flippyfile": it["flippyfile"],
			"flippy_active": assets.active_flippy_choice(it["id"]),
			"flippy_alts": assets.list_flippy_alternates(it["id"]),
			# grouping metadata for the gallery's tier-family tree
			"type": it.get("type", ""), "table": it.get("table", ""),
			"family": it.get("family", it["code"]), "tier": it.get("tier", 0),
			"ord": i,  # catalog (txt row) order -- stable family sort key
		})
	out.sort(key=lambda x: (x["category"], x["name"]))
	return jsonify({"count": len(out), "items": out[:3000]})


def _item(item_id):
	return catalog()["by_id"].get(item_id)


def _item_by_code(code: str):
	"""First BASE item with this item code (uniques/sets carry their base's code in `family`)."""
	for it in catalog()["by_id"].values():
		if it.get("category") == "base" and it.get("code") == code:
			return it
	return None


def _inv_tint_palette(it):
	"""The game's `invtransform` recolour for uniques/sets (e.g. Twitchthroe -> green),
	or None for base items / no colour code. Applied to whichever DC6 is active so the
	gallery/side-panel art matches in-game."""
	if it["category"] in ("unique", "set") and it.get("invtransform"):
		return assets.item_transform_palette(it.get("inv_trans", 0), it["invtransform"])
	return None


@flask_app.get("/api/item/<path:item_id>/original.png")
def api_original_png(item_id):
	it = _item(item_id)
	if not it:
		return "no such item", 404
	try:
		# txt-edited uniques point at a studio-invented invfile with no stock art; their
		# "original" is the pre-edit inherited art (still tinted like the unique)
		png = assets.dc6_to_png_bytes(
			assets.read_original_dc6(it.get("stock_invfile") or it["invfile"]),
			palette=_inv_tint_palette(it))
	except Exception as e:  # noqa: BLE001
		return f"render error: {e}", 500
	return Response(png, mimetype="image/png")


def _active_art_dc6(it):
	"""The DC6 the item shows in-game right now: the selected alternate, else the original art."""
	choice = assets.active_choice(it["id"])
	if not choice or choice == "original":
		return assets.read_original_dc6(it.get("stock_invfile") or it["invfile"])
	return assets.alt_dc6_bytes(it["id"], choice)


@flask_app.get("/api/item/<path:item_id>/current.png")
def api_current_png(item_id):
	"""The image the item will actually show in-game right now: the selected alternate
	(with the same invtransform tint as the original), or the original art when nothing
	alternate is active. Lets a gallery tile reflect the active choice without a reload."""
	it = _item(item_id)
	if not it:
		return "no such item", 404
	try:
		png = assets.dc6_to_png_bytes(_active_art_dc6(it), palette=_inv_tint_palette(it))
	except Exception as e:  # noqa: BLE001
		return f"render error: {e}", 500
	return Response(png, mimetype="image/png")


@flask_app.get("/api/item/<path:item_id>/tint/<code>.png")
def api_tint_preview(item_id, code):
	"""The item's active art recoloured with colour `code` (or 'none' = untinted): one swatch of
	the tint picker. Uses the same remap the game applies (base InvTrans + colour code)."""
	from pyd2 import colortransform
	it = _item(item_id)
	if not it:
		return "no such item", 404
	if it["category"] not in ("unique", "set"):
		return "only uniques and sets carry a tint", 400
	code = code.lower()
	pal = None
	if code != "none":
		if code not in colortransform.COLOR_INDEX:
			return f"unknown colour code {code!r}", 400
		pal = assets.item_transform_palette(it.get("inv_trans", 0), code)
		if pal is None:
			return "this item's base has no inventory colour transform (InvTrans 0)", 409
	try:
		png = assets.dc6_to_png_bytes(_active_art_dc6(it), palette=pal)
	except Exception as e:  # noqa: BLE001
		return f"render error: {e}", 500
	return Response(png, mimetype="image/png", headers={"Cache-Control": "no-cache"})


@flask_app.get("/api/item/<path:item_id>/alt/<alt_id>.png")
def api_alt_png(item_id, alt_id):
	it = _item(item_id)
	try:
		# same tint the gallery tile (current.png) applies: the game recolours whichever DC6 is
		# active, so an alternate must preview tinted too (its base art is tintless)
		png = assets.dc6_to_png_bytes(assets.alt_dc6_bytes(item_id, alt_id),
		                              palette=_inv_tint_palette(it) if it else None)
	except Exception as e:  # noqa: BLE001
		return f"render error: {e}", 500
	return Response(png, mimetype="image/png")


# ---- Equipped ("paper doll") on-character preview -------------------------
def _rgba_frames_to_gif(frames, palette, duration=130):
	"""Animated GIF from RGBA frames, quantized to the fixed act palette; index 0 = transparent."""
	flat = []
	for i in range(256):
		flat += list(palette[i])
	pal_im = Image.new("P", (16, 16))
	pal_im.putpalette(flat)
	p_frames = []
	for im in frames:
		rgb = Image.new("RGB", im.size, palette[0])
		rgb.paste(im, (0, 0), im)                                  # composite over palette[0]
		p = rgb.quantize(palette=pal_im, dither=Image.NONE)
		p.paste(0, im.split()[3].point(lambda a: 255 if a < 128 else 0))
		p_frames.append(p)
	buf = io.BytesIO()
	p_frames[0].save(buf, format="GIF", save_all=True, append_images=p_frames[1:],
	                 duration=duration, loop=0, transparency=0, disposal=2, optimize=False)
	return buf.getvalue()


@flask_app.get("/api/item/<path:item_id>/equipped/info")
def api_item_equipped_info(item_id):
	it = _item(item_id)
	if not it:
		return jsonify({"ok": False}), 404
	from pyd2 import chars
	classes = list(chars.CLASS_TOKENS.keys())
	code = (it.get("code") or "").strip()
	# Determine the item's worn kind AND which classes render it in one class-spanning probe. Doing
	# both together is what lets class-locked items (Assassin claws, Sorceress orbs, ...) report their
	# real kind — a Barbarian-only probe finds no COF for those and would wrongly say "none". Probe
	# barbarian first so it stays the default whenever it qualifies.
	try:
		probe = ["barbarian"] + [c for c in classes if c != "barbarian"]
		kind, available = chars.describe(code, probe, "NU")
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": True, "supported": False, "kind": "none", "error": str(e)[:120]})
	# require at least one class that actually renders it — a worn kind whose art is missing everywhere
	# (no decodable DCC) would otherwise claim 'supported' yet show a bare body.
	supported = kind in ("body-armor", "helm", "weapon", "shield") and bool(available)
	default_class = available[0] if available else "barbarian"
	return jsonify({"ok": True, "kind": kind,
	                # every drawn slot renders the real item art; only base (gloves/boots/belt) can't be worn-shown
	                "supported": supported,
	                "default_class": default_class,
	                "available_classes": [c for c in classes if c in available],   # canonical order
	                "classes": classes,
	                "modes": ["NU", "TN", "WL", "RN", "A1", "A2", "SC", "TH", "S1"]})


_EQUIPPED_GIF_CACHE = {}   # (code, cls, mode, dir) -> gif bytes (composited on demand, then reused)


@flask_app.get("/api/item/<path:item_id>/equipped.gif")
def api_item_equipped_gif(item_id):
	it = _item(item_id)
	if not it:
		return ("no item", 404)
	from pyd2 import chars
	cls = request.args.get("cls", "barbarian")
	mode = (request.args.get("mode", "NU") or "NU").upper()
	try:
		direction = int(request.args.get("dir", "0"))
	except ValueError:
		direction = 0
	code = (it.get("code") or "").strip()
	item_only = request.args.get("body", "1") == "0"   # body=0 -> hide the character, show the item alone
	# unique/set worn recolour (None for normal items); key by item id since uniques share a base code
	colormap = chars.worn_colormap(code, it.get("category", ""), it.get("name", ""))
	# the colormap itself is part of the key: a tint edit changes it without changing the item id
	ckey = (item_id, cls, mode, direction, item_only,
	        hash(tuple(colormap)) if colormap is not None else None)
	gif = _EQUIPPED_GIF_CACHE.get(ckey)
	if gif is None:
		try:
			spec = chars.resolve(code, cls, mode)
			if not spec:
				return ("no equipped art for this item type", 404)
			frames, _ax, _ay = chars.animate(spec, direction, item_only=item_only, colormap=colormap)
			if not frames:
				return ("no frames (missing char graphics for this combo)", 404)
			gif = _rgba_frames_to_gif(frames, assets._palette())
			_EQUIPPED_GIF_CACHE[ckey] = gif
		except Exception as e:  # noqa: BLE001
			return (f"equipped render error: {e}", 500)
	return Response(gif, mimetype="image/gif", headers={"Cache-Control": "no-cache"})


@flask_app.post("/api/item/<path:item_id>/import")
def api_import(item_id):
	it = _item(item_id)
	if not it:
		return "no such item", 404
	f = request.files.get("file")
	if not f:
		return jsonify({"ok": False, "error": "no file"}), 400
	alt_id = request.form.get("alt_id") or f"png-{len(assets.list_alternates(item_id)) + 1}"
	try:
		raw = f.read()
		fp = _footprint_for(it)   # match the original art's footprint by default (chip stays small)
		dc6_bytes = assets.png_to_item_dc6(raw, it["invwidth"], it["invheight"],
		                                   fill=fp["fill"], dx=fp["dx"], dy=fp["dy"], thin=_is_thin(it))
		assets.save_alternate_dc6(item_id, alt_id, dc6_bytes)
		# keep the source PNG as the alt's render so the framing sliders work on imports too
		assets.save_alt_provenance(item_id, alt_id, render_png=raw,
		                           meta={"source": "png-import", "cell": [it["invwidth"], it["invheight"]],
		                                 "fill": fp["fill"], "dx": fp["dx"], "dy": fp["dy"], "fit_auto": True})
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 500
	return jsonify({"ok": True, "alt_id": alt_id, "alts": assets.list_alternates(item_id)})


@flask_app.post("/api/item/<path:item_id>/activate")
def api_activate(item_id):
	it = _item(item_id)
	if not it:
		return "no such item", 404
	choice = (request.json or {}).get("choice", "original")
	assets.activate(item_id, it["invfile"], choice)
	return jsonify({"ok": True, "active": choice})


# ---- alternate management (delete / rename; ?flippy=1 targets the flippy set) ----

_ALT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,40}$")


def _alt_set(item_id, flippy):
	return assets.list_flippy_alternates(item_id) if flippy else assets.list_alternates(item_id)


@flask_app.delete("/api/item/<path:item_id>/alt/<alt_id>")
def api_alt_delete(item_id, alt_id):
	it = _item(item_id)
	if not it:
		return "no such item", 404
	flippy = request.args.get("flippy") == "1"
	if alt_id not in _alt_set(item_id, flippy):
		return jsonify({"ok": False, "error": "no such alternate"}), 404
	# an active alternate reverts to original first so no overlay dangles on the deleted file
	if flippy:
		if assets.active_flippy_choice(item_id) == alt_id:
			assets.activate_flippy(item_id, it["flippyfile"], "original")
	elif assets.active_choice(item_id) == alt_id:
		assets.activate(item_id, it["invfile"], "original")
	assets.delete_alternate(item_id, alt_id, flippy=flippy)
	return jsonify({"ok": True, "alts": _alt_set(item_id, flippy)})


@flask_app.post("/api/item/<path:item_id>/alt/<alt_id>/rename")
def api_alt_rename(item_id, alt_id):
	it = _item(item_id)
	if not it:
		return "no such item", 404
	flippy = request.args.get("flippy") == "1"
	new_id = ((request.json or {}).get("new_id") or "").strip()
	if alt_id not in _alt_set(item_id, flippy):
		return jsonify({"ok": False, "error": "no such alternate"}), 404
	if not _ALT_ID_RE.match(new_id) or new_id == "original":
		return jsonify({"ok": False, "error": "name must be 1-41 chars of letters/digits/_/- (not 'original')"}), 400
	if new_id != alt_id and new_id in _alt_set(item_id, flippy):
		return jsonify({"ok": False, "error": f"an alternate named {new_id!r} already exists"}), 400
	if new_id != alt_id:
		assets.rename_alternate(item_id, alt_id, new_id, flippy=flippy)
	return jsonify({"ok": True, "alt_id": new_id, "alts": _alt_set(item_id, flippy)})


# ---- uniqueitems.bin cell edits (the txt sliver, plan §7.3) -------------

_TINT_TABLES = {"unique": "uniqueitems", "set": "setitems"}


def _tint_state(it):
	"""The item's current .bin tint (colour codes, '' = none) plus whether each side can render:
	the inventory tint needs the base's InvTrans 1..8, the on-body tint its `Transform` 1..8."""
	from pyd2 import chars, colortransform
	t = excel.get_tint(_TINT_TABLES[it["category"]], it["name"])
	if t is None:
		return None
	t["colors"] = list(colortransform.COLOR_CODES)   # colors.txt order, for the picker
	t["can_inv"] = 1 <= int(it.get("inv_trans", 0) or 0) <= 8
	t["can_chr"] = chars.base_transform((it.get("code") or "").strip()) > 0
	return t


@flask_app.get("/api/item/<path:item_id>/txt")
def api_txt_get(item_id):
	it = _item(item_id)
	if not it:
		return "no such item", 404
	if it["category"] not in _TINT_TABLES:
		return jsonify({"ok": False, "error": "bin edits are supported for uniques and sets only"}), 400
	tint = _tint_state(it)
	if tint is None:
		return jsonify({"ok": False, "error": f"{it['name']!r} not found in {_TINT_TABLES[it['category']]}.bin"}), 404
	if it["category"] == "set":
		return jsonify({"ok": True, "tint": tint})
	u = excel.get_unique(it["name"])
	if not u:
		return jsonify({"ok": False, "error": f"{it['name']!r} not found in uniqueitems.bin"}), 404
	u.update({"ok": True, "effective_invfile": it["invfile"],
	          "effective_flippyfile": it["flippyfile"], "tint": tint})
	return jsonify(u)


def _api_tint_set(it, body):
	"""Set/clear/revert an item's inventory and/or on-body tint. Each of `inv` / `chr`: a colour
	code, 'none' (no tint), 'stock' (revert), or absent (leave that side alone)."""
	sides = {k: body.get(k) for k in ("inv", "chr")}
	if any(v is not None and not isinstance(v, str) for v in sides.values()):
		return jsonify({"ok": False, "error": "inv/chr must be a colour code, 'none' or 'stock'"}), 400
	try:
		excel.set_tint(_TINT_TABLES[it["category"]], it["name"], **sides)
	except (ValueError, KeyError) as e:
		return jsonify({"ok": False, "error": str(e)}), 400
	invalidate_catalog()
	return jsonify({"ok": True, "tint": _tint_state(it),
	                "note": "push + Full reload for the change to reach the game "
	                        "(data tables load at process start)"})


@flask_app.post("/api/item/<path:item_id>/txt")
def api_txt_set(item_id):
	"""Give a unique its own invfile/flippyfile (or '' to revert to inherited).

	Patches the cell in uniqueitems.bin (overlay copy), then keeps the art overlay
	coherent: an active alternate is re-written under the new filename, and if no art
	exists at a brand-new filename yet the current effective art is seeded there so
	the game never dangles on a missing DC6.

	`field: "tint"` instead edits the inventory/on-body colour byte(s) — uniques and sets.
	"""
	it = _item(item_id)
	if not it:
		return "no such item", 404
	body = request.json or {}
	fld = body.get("field", "invfile")
	if fld == "tint" and it["category"] in _TINT_TABLES:
		return _api_tint_set(it, body)
	if it["category"] != "unique":
		return jsonify({"ok": False, "error": "bin edits are supported for uniques only"}), 400
	value = (body.get("value") or "").strip()
	old_file = it["invfile"] if fld == "invfile" else it["flippyfile"]
	try:
		u = excel.set_unique_field(it["name"], fld, value)
	except (ValueError, KeyError) as e:
		return jsonify({"ok": False, "error": str(e)}), 400
	new_file = u[fld] if value else u[f"stock_{fld}"]
	# resolve the inherited fallback when the stock cell is blank
	if not new_file:
		invalidate_catalog()
		new_file = _item(item_id)[fld] if fld == "invfile" else _item(item_id)["flippyfile"]
	seeded = False
	if fld == "invfile" and new_file != old_file:
		choice = assets.active_choice(item_id)
		if choice != "original":
			assets.activate(item_id, old_file, "original")
			if value or choice != "seed-original":
				# move the active alternate's overlay DC6 to the new filename;
				# an auto-seed is dropped on revert (it only existed for the own-file)
				assets.activate(item_id, new_file, choice)
		elif value and assets.try_read_effective(assets.item_dc6_path(new_file)) is None:
			# brand-new filename with no art anywhere: seed it with the current art
			assets.save_alternate_dc6(item_id, "seed-original", assets.read_original_dc6(old_file))
			assets.activate(item_id, new_file, "seed-original")
			seeded = True
	if fld == "flippyfile" and new_file != old_file:
		choice = assets.active_flippy_choice(item_id)
		if choice != "original":
			assets.activate_flippy(item_id, old_file, "original")
			if value or choice != "seed-original":
				assets.activate_flippy(item_id, new_file, choice)
		elif value and assets.try_read_effective(assets.item_dc6_path(new_file)) is None:
			assets.save_flippy_alternate_dc6(item_id, "seed-original",
			                                 assets.read_original_dc6(old_file))
			assets.activate_flippy(item_id, new_file, "seed-original")
			seeded = True
	invalidate_catalog()
	u = excel.get_unique(it["name"])
	u.update({"ok": True, "seeded": seeded, "effective_invfile": _item(item_id)["invfile"],
	          "effective_flippyfile": _item(item_id)["flippyfile"],
	          "note": "push + Full reload for the change to reach the game "
	                  "(data tables load at process start)"})
	return jsonify(u)


# ---- flippy (animated ground-drop) alternates ---------------------------

@flask_app.get("/api/item/<path:item_id>/flippy/original.gif")
def api_flippy_original(item_id):
	it = _item(item_id)
	if not it or not it["flippyfile"]:
		return "no flippy", 404
	try:
		# same pre-edit fallback as original.png for txt-edited flippyfiles
		gif = assets.dc6_to_gif_bytes(assets.read_original_dc6(it.get("stock_flippyfile") or it["flippyfile"]))
	except Exception as e:  # noqa: BLE001
		return f"render error: {e}", 500
	return Response(gif, mimetype="image/gif")


@flask_app.get("/api/item/<path:item_id>/flippy/alt/<alt_id>.gif")
def api_flippy_alt(item_id, alt_id):
	try:
		gif = assets.dc6_to_gif_bytes(assets.flippy_alt_dc6_bytes(item_id, alt_id))
	except Exception as e:  # noqa: BLE001
		return f"render error: {e}", 500
	return Response(gif, mimetype="image/gif")


@flask_app.post("/api/item/<path:item_id>/activate-flippy")
def api_activate_flippy(item_id):
	it = _item(item_id)
	if not it:
		return "no such item", 404
	if not it["flippyfile"]:
		return jsonify({"ok": False, "error": "item has no flippyfile"}), 400
	choice = (request.json or {}).get("choice", "original")
	assets.activate_flippy(item_id, it["flippyfile"], choice)
	return jsonify({"ok": True, "flippy_active": choice})


@flask_app.post("/api/push")
def api_push():
	"""Build patch.mpq from the overlay and register it live at priority 9000.

	The running game keeps the archive open, so we CLOSE it first (if the game is up)
	to release the file lock before rebuilding, then re-register.
	"""
	_dbg("POST", "/asset/close", {"confirm": True}, timeout=8)  # release the lock if held (ok if game down)
	try:
		path, count = assets.build_patch_mpq()
	except PermissionError as e:  # file still locked
		return jsonify({"ok": False, "stage": "build",
		                "error": f"patch.mpq is locked (game holds it) -- {e}"}), 500
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "stage": "build", "error": str(e)}), 500
	res, err = _dbg("POST", "/asset/register",
	                {"path": path.replace("\\", "/"), "priority": 9000, "confirm": True})
	if err:
		return jsonify({"ok": False, "stage": "register", "built": count,
		                "error": f"game not reachable on :8790 ({err})"}), 502
	return jsonify({"ok": True, "built": count, "mpq": path, "register": res})


import subprocess
import time

RELAUNCH_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "relaunch_pd2_direct.ps1")


def _drive_into_game(register_first: bool) -> tuple[bool, str]:
	"""Drive the menu to in-world: (optionally register overlay), single-player, launch.
	Returns (ok, note). Assumes the game is up (menu pump firing)."""
	if register_first:
		assets.build_patch_mpq()  # rebuild from overlay
		res, _ = _dbg("POST", "/asset/register",
		              {"path": os.path.join(assets.EXPORT_DIR, sorted(
			              f for f in os.listdir(assets.EXPORT_DIR) if f.endswith(".mpq"))[-1]).replace("\\", "/"),
		               "priority": 9000, "confirm": True})
	# reach character-select (single-player click; it may fault-but-advance)
	for _ in range(12):
		s, _e = _dbg("GET", "/status", timeout=3)
		if s and s.get("charListLoaded"):
			break
		_dbg("POST", "/action/main-menu-singleplayer", {"confirm": True}, timeout=6)
		time.sleep(5)
	else:
		return False, "never reached character-select"
	# launch
	r, e = _dbg("POST", "/action/launch-character", {"confirm": True}, timeout=15)
	if e:
		return False, f"launch failed: {e}"
	# wait for in-world
	base = None
	for _ in range(12):
		s, _e = _dbg("GET", "/status", timeout=3)
		if s:
			cc = s.get("captureCount", 0)
			if base is None:
				base = cc
			if cc > base + 40:
				return True, "in-world"
		time.sleep(3)
	return True, "launched (world-entry unconfirmed)"


@flask_app.post("/api/reload")
def api_reload():
	"""Soft reload: save-and-exit to menu, then drive back into the game. NOTE: item art
	already loaded this process may not fully refresh (D2CMP cell cache) -- use full-reload
	for guaranteed-clean art."""
	r1, e1 = _dbg("POST", "/action/exit-to-menu", {"confirm": True})
	if e1:
		return jsonify({"ok": False, "stage": "exit", "error": e1}), 502
	time.sleep(6)
	ok, note = _drive_into_game(register_first=False)
	return jsonify({"ok": ok, "note": note, "hint": "if art didn't change, use Full reload (fresh process)"})


@flask_app.post("/api/full-reload")
def api_full_reload():
	"""Guaranteed-clean reload: relaunch a FRESH game process (empty caches) via the
	elevated -direct launcher (1 UAC), register the overlay at the menu, then enter the
	game so art loads fresh. ~60-90s."""
	try:
		subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
		                  "-File", os.path.abspath(RELAUNCH_SCRIPT)])
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "stage": "launch", "error": str(e)}), 500
	# wait for the fresh process to boot (menu pump up)
	for _ in range(40):
		s, _e = _dbg("GET", "/status", timeout=2)
		if s and s.get("menuPumpHooked") and s.get("captureCount", 1) == 0:
			break
		time.sleep(3)
	time.sleep(10)  # let the title screen settle
	ok, note = _drive_into_game(register_first=True)
	return jsonify({"ok": ok, "note": note})


@flask_app.get("/api/item/<path:item_id>/alt/<alt_id>/cell.png")
def api_alt_cell_preview(item_id, alt_id):
	"""Live in-cell framing preview: composite the alt's saved render into its inventory cell
	at ?fill=&dx=&dy= (no re-render, no save). Used by the UI framing sliders."""
	it = _item(item_id)
	if not it:
		return "no such item", 404
	render = assets.alt_render_png(item_id, alt_id)
	if render is None:
		return "no saved render for this alternate", 404
	fill, dx, dy, _auto = _resolve_fit(it, request.args.get("fill", "auto"),
	                                   request.args.get("dx"), request.args.get("dy"))
	rot = float(request.args.get("rot") or 0.0)
	grade = {k: request.args.get(k) for k in ("brightness", "warmth", "saturation", "contrast", "hue")
	         if request.args.get(k) is not None} or None
	even = request.args.get("even_border") in ("1", "true")
	outline = request.args.get("outline", "1") not in ("0", "false")
	try:
		png = assets.cell_preview_png(render, it["invwidth"], it["invheight"], fill=fill, dx=dx, dy=dy,
		                              grade=grade, even_border=even, rot=rot, outline=outline,
		                              palette=_inv_tint_palette(it), thin=_is_thin(it))
	except Exception as e:  # noqa: BLE001
		return f"preview error: {e}", 500
	return Response(png, mimetype="image/png")


@flask_app.get("/api/item/<path:item_id>/alt/<alt_id>/render.png")
def api_alt_render_png(item_id, alt_id):
	"""The alt's full-resolution source render (the hi-res 'master' behind the in-game sprite),
	served raw and uncropped. The Item-tab big preview shows this when it exists; the client
	falls back to the DC6 sprite (a 404 here) for alts that have no saved render (e.g. originals)."""
	render = assets.alt_render_png(item_id, alt_id)
	if render is None:
		return "no saved render for this alternate", 404
	return Response(render, mimetype="image/png")


@flask_app.post("/api/item/<path:item_id>/alt/<alt_id>/refit")
def api_alt_refit(item_id, alt_id):
	"""Apply new framing (fill/dx/dy) + optional color grade to an alt by re-cropping its saved
	render into a new DC6. Instant (no Blender), non-destructive (from the stored render). Body:
	{fill|"auto", dx, dy, grade}. Returns the resolved fill/dx/dy so sliders re-sync."""
	it = _item(item_id)
	if not it:
		return "no such item", 404
	body = request.json or {}
	fill, dx, dy, was_auto = _resolve_fit(it, body.get("fill", "auto"), body.get("dx"), body.get("dy"))
	rot = float(body.get("rot") or 0.0)
	outline = bool(body.get("outline", True))
	ok = assets.refit_alt(item_id, alt_id, it["invwidth"], it["invheight"], fill, dx, dy,
	                      grade=body.get("grade") or None, thin=_is_thin(it), fit_auto=was_auto,
	                      even_border=bool(body.get("even_border")), rot=rot, outline=outline)
	if not ok:
		return jsonify({"ok": False, "error": "no saved render for this alternate (re-render first)"}), 409
	return jsonify({"ok": True, "alt_id": alt_id, "fill": fill, "dx": dx, "dy": dy, "rot": rot})


@flask_app.get("/api/item/<path:item_id>/original/cell.png")
def api_original_cell_preview(item_id):
	"""The ORIGINAL art rendered at its footprint in its cell, at the SAME framing/scale the
	alternate previews use — the left-hand reference for the side-by-side size comparison.
	Default = the 4x reddish cell-grid composite (matches the gallery alt cell.png); ?dc6=1 =
	the 1x palette-quantised DC6 round-trip (matches the accept-2d preview)."""
	it = _item(item_id)
	if not it:
		return "no such item", 404
	orig = _original_png_for(it)
	fp = _footprint_for(it)
	try:
		if request.args.get("dc6"):
			dc6 = assets.png_to_item_dc6(orig, it["invwidth"], it["invheight"],
			                             fill=fp["fill"], dx=fp["dx"], dy=fp["dy"], thin=_is_thin(it))
			png = assets.dc6_to_png_bytes(dc6)
		else:
			png = assets.cell_preview_png(orig, it["invwidth"], it["invheight"],
			                              fill=fp["fill"], dx=fp["dx"], dy=fp["dy"])
	except Exception as e:  # noqa: BLE001
		return f"preview error: {e}", 500
	return Response(png, mimetype="image/png")


@flask_app.get("/api/item/<path:item_id>/alt/<alt_id>/meta")
def api_alt_meta(item_id, alt_id):
	"""Provenance + editability for one alternate: the .meta.json sidecar (method/prompt/seed/
	score/fill/grade… — whatever was recorded), whether a hi-res render exists (drives the
	adjustment sliders), and the auto footprint (for the 'Auto' button / slider init)."""
	it = _item(item_id)
	if not it:
		return jsonify({"ok": False, "error": "no such item"}), 404
	return jsonify({"ok": True,
	                "meta": assets.alt_meta(item_id, alt_id) or {},
	                "has_render": assets.alt_render_png(item_id, alt_id) is not None,
	                "footprint": _footprint_for(it)})


@flask_app.post("/api/item/<path:item_id>/alt/<alt_id>/open-blender")
def api_alt_open_blender(item_id, alt_id):
	"""Open the alt's retained GLB in the Blender GUI for hands-on tweaking."""
	glb = assets.alt_asset(item_id, alt_id, ".glb")
	if not os.path.exists(glb):
		return jsonify({"ok": False, "error": "no GLB saved for this alternate"}), 404
	if not blender.open_gui(glb):
		return jsonify({"ok": False, "error": "Blender not found or GLB missing"}), 501
	return jsonify({"ok": True, "opened": glb})


# ITEMQUAL_* values passed to the showcase verb's "quality" field.
_QUALITY = {"normal": 2, "superior": 3, "magic": 4, "rare": 6, "set": 5, "unique": 7, "low": 1}
# Base-item type codes that are set JEWELRY -- these are the pieces whose identified + recompute
# path crashed the client (AssetStudioPlan §30), so we drop them UNIDENTIFIED as a guard.
_SET_JEWELRY_TYPES = {"amul", "ring"}


def _drop_body_for(it, quality: str | None):
	"""Build the /showcase/item body for dropping `it` as `quality` (or its native quality when
	quality is None). Returns (body, human_label). For a unique/set catalog item, forces that exact
	row; for a base item, drops the requested quality (or plain normal). Sets identify for uniques
	and non-jewelry sets so their own inventory art renders."""
	body = {"code": it["code"], "drop": True, "confirm": True}

	# The item's NATIVE drop (no explicit quality picked): unique -> that unique, set -> that piece,
	# base -> plain base item.
	if quality is None:
		if it["category"] == "unique":
			row = unique_row(it["name"])
			if row is None:
				return None, f"couldn't resolve uniqueitems row for {it['name']!r}"
			body.update({"uniqueRow": row["row"], "identify": True})
			return body, f"{it['name']} (unique, identified)"
		if it["category"] == "set":
			from app.catalog import set_pieces
			match = next((p for p in set_pieces(it["name"]) if p["index"].lower() == it["name"].lower()), None)
			if match is None:
				return None, f"couldn't resolve setitems row for {it['name']!r}"
			identify = it["type"] not in _SET_JEWELRY_TYPES  # jewelry sets stay unID'd (crash guard)
			body.update({"setRow": match["row"], "identify": identify})
			return body, f"{it['name']} (set{'' if identify else ', unidentified — jewelry guard'})"
		return body, f"{it['name']} (base item)"

	# An explicit quality was picked from the split-button menu -> apply it to this base.
	q = quality.lower()
	if q not in _QUALITY:
		return None, f"unknown quality {quality!r}"
	body["quality"] = _QUALITY[q]
	# Identify every quality that carries hidden mods (magic/rare/unique/set) so the full tooltip
	# shows on the ground -- and a unique's own inventory art renders. Superior/normal/low have no
	# affixes, so ID is irrelevant. Guard: a forced SET roll on a jewelry base stays UNidentified
	# (auto-ID'd set jewelry drove a set-bonus recompute crash, AssetStudioPlan §30).
	if q in ("magic", "rare", "unique", "set"):
		body["identify"] = not (q == "set" and it["type"] in _SET_JEWELRY_TYPES)
	return body, f"{it['name']} (forced {q})"


@flask_app.post("/api/item/<path:item_id>/drop")
def api_drop(item_id):
	"""Spawn the item on the ground at the player's feet (to test its art in-game).

	Drops the item at its NATIVE quality by default -- a unique catalog item drops as that exact
	unique (identified, so its own inventory art renders), a set item as that set piece, a base item
	as a plain base. An optional {"quality": "magic"|"rare"|"unique"|...} body forces a different
	quality on the base (the split-button menu). Uses D2Debugger /showcase/item; the drop lands the
	item, then a post-drop notify step faults harmlessly (SEH-caught) -- reported as a success."""
	it = _item(item_id)
	if not it:
		return "no such item", 404
	quality = (request.json or {}).get("quality") if request.is_json else None
	body, label = _drop_body_for(it, quality)
	if body is None:
		return jsonify({"ok": False, "error": label}), 400
	res, err = _dbg("POST", "/showcase/item", body, timeout=12)
	if err:
		return jsonify({"ok": False, "error": f"game not reachable ({err})"}), 502
	note = f"dropped {label} at your feet"
	if res.get("ok"):
		return jsonify({"ok": True, "dropped": True, "note": note})
	emsg = res.get("error", "")
	if "FAULT" in emsg:
		return jsonify({"ok": True, "dropped": True,
		                "note": f"{note} (a post-drop step faults harmlessly) — "
		                        "pick it up to see the inventory art; the flippy plays on landing"})
	if "in-world" in emsg or "IN a game" in emsg:
		return jsonify({"ok": False, "error": "be in-world (enter a game) to drop an item"}), 409
	return jsonify({"ok": False, "error": emsg}), 502


@flask_app.post("/api/set/spawn")
def api_set_spawn():
	"""Spawn every piece of a named set into the player's inventory (forced SET quality,
	auto-identified). Body: {"set": "tal rasha"}. Each piece is created on its correct base
	with setRow forced, dropped, and picked up via the native 0x16 replay. Must be in-world."""
	from app.catalog import set_pieces
	name = (request.json or {}).get("set", "").strip()
	if not name:
		return jsonify({"ok": False, "error": "want {\"set\":\"tal rasha\"}"}), 400
	pieces = set_pieces(name)
	if not pieces:
		return jsonify({"ok": False, "error": f"no set pieces match {name!r}"}), 404
	results = []
	for p in pieces:
		# Drop at the player's feet (dest "feet") -- the proven client-synced path. Set pieces drop
		# unidentified (vanilla); the player IDs + grabs them in-game. (Auto-pickup + auto-identify of
		# set jewelry crashed the client, see AssetStudioPlan §30.)
		res, err = _dbg("POST", "/showcase/item",
		                {"code": p["base"], "dest": "feet", "setRow": p["row"], "confirm": True},
		                timeout=12)
		ok = bool(res and res.get("ok"))
		results.append({"index": p["index"], "base": p["base"], "row": p["row"],
		                "ok": ok, "detail": (err or (res or {}).get("error") or (res or {}).get("note"))})
		time.sleep(0.6)  # pace the drops so the frame-tick pump keeps up
	spawned = sum(1 for r in results if r["ok"])
	return jsonify({"ok": spawned > 0, "set": pieces[0]["set"], "spawned": spawned,
	                "total": len(pieces), "pieces": results})


@flask_app.get("/api/set/list")
def api_set_list():
	"""List the pieces of a named set (row/base/index) without spawning. ?q=tal+rasha"""
	from app.catalog import set_pieces
	q = request.args.get("q", "").strip()
	if not q:
		return jsonify({"ok": False, "error": "want ?q=<set name>"}), 400
	return jsonify({"ok": True, "pieces": set_pieces(q)})


# ==== Generation Studio (Meshy web-session: draft -> preview -> reroll -> texture -> accept) ====

# task_id -> {item_id, image_id, phase}; seeded from meshy_links.json so pairings
# survive restarts, and every mutation writes back through _remember().
_LINKS = meshy_links.load_links()
_STUDIO = {tid: {"item_id": l["item_id"], "image_id": l.get("image_id"),
                 "phase": l.get("phase", "draft")} for tid, l in _LINKS.items()}


def _remember(tid, item_id, image_id, phase, source, name="", invfile=None, art_file=None):
	_STUDIO[tid] = {"item_id": item_id, "image_id": image_id, "phase": phase}
	meshy_links.remember(_LINKS, tid, item_id=item_id, image_id=image_id,
	                     phase=phase, source=source, name=name, invfile=invfile,
	                     art_file=art_file)


@flask_app.get("/api/studio/session")
def api_studio_session():
	s = meshy_web.session_status()
	s["blender"] = blender.available()
	return jsonify(s)


@flask_app.post("/api/studio/session/launch")
def api_studio_launch():
	return jsonify(meshy_web.launch_session())


def _draft_from_sprite(it, sprite_png, opts, source):
	"""Shared core: register prepped sprite PNG(s) + create a DRAFT, remember the link.
	Returns the new draft task id. `sprite_png` is already aspect-preserved
	(prep_image_for_meshy); a list = Meshy multi-image draft (boots 2-image experiment).
	The FIRST image becomes the remembered image_id (drives image-guided texturing later)."""
	sprites = sprite_png if isinstance(sprite_png, list) else [sprite_png]
	ids = [meshy_web.register_image(s, filename=f"{it['code'].strip()}{i or ''}.png")
	       for i, s in enumerate(sprites)]
	tid = meshy_web.create_draft(ids if len(ids) > 1 else ids[0],
	                             ai_model=opts.get("aiModel", "avocado"),
	                             model_type=opts.get("modelType", "standard"),
	                             topology=opts.get("topology", "triangle"),
	                             symmetry=int(opts.get("symmetry", 0)),
	                             seed=int(opts.get("seed", 0)))
	_remember(tid, it["id"], ids[0], "draft", source, name=it["name"],
	          invfile=it["invfile"].lower())
	return tid


@flask_app.post("/api/studio/generate")
def api_studio_generate():
	"""Register the item's sprite (aspect-preserved) + create a DRAFT. Returns the draft task id."""
	body = request.json or {}
	it = _item(body.get("item_id", ""))
	if not it:
		return jsonify({"ok": False, "error": "no such item"}), 404
	opts = body.get("opts") or {}
	try:
		png0 = assets.dc6_to_png_bytes(assets.read_original_dc6(it["invfile"]))
		sprite = assets.prep_image_for_meshy(png0)
		tid = _draft_from_sprite(it, sprite, opts, "studio-generate")
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 502
	return jsonify({"ok": True, "task_id": tid, "phase": "draft"})


# ---- masked single-hand input (overlapping glove pairs) -------------------
# Two identically-dark overlapping gloves can't be split algorithmically (the pair is one
# merged alpha blob), so the studio lets you brush the back hand away by hand. The masked
# single-hand PNG is stored per art file and feeds BOTH generation arms:
#   plain  -> our automated image-to-3D draft (this endpoint), and
#   caption-> Meshy's text-to-3D image-reference UI (download the PNG, drop it in).
MASKS = os.path.join(assets.WORKSPACE, "masks")


def _mask_path(invfile: str) -> str:
	safe = "".join(c if (c.isalnum() or c in "_.-") else "_" for c in (invfile or "").lower())
	return os.path.join(MASKS, f"{safe}.png")


def _decode_data_url(data_url: str) -> bytes:
	import base64
	b64 = data_url.split(",", 1)[1] if "," in data_url else data_url
	return base64.b64decode(b64)


def _mask_up_path(invfile: str) -> str:
	"""Where the UPSCALED (black-framed) hand is cached — separate from the native-res mask so
	the brush still resumes from the hand-painted original, not the 4x diffusion output."""
	base = _mask_path(invfile)
	return base[:-4] + "-up.png"


# The png-upscale-service (Real-ESRGAN + SD x4 + SDXL-refine) on the home Docker box.
UPSCALE_URL = os.environ.get("PNG_UPSCALE_URL", "http://10.0.10.30:8084")


def _black_frame(masked_png: bytes, margin: float = 0.14) -> bytes:
	"""Flatten a masked single hand onto SOLID BLACK with a margin, cropped+centred+squared.

	The mask carries transparency in two places — the erased back hand and the sprite's own
	backdrop — and the upscalers (and Meshy) read a clean, opaque, contrast-y subject far better
	than a checkerboard of alpha. Black also matches the flat backdrop the pair compositor's
	`drop_flat_background` already expects downstream. Squared + margined so the diffusion refine
	sees the whole hand with breathing room."""
	im = Image.open(io.BytesIO(masked_png)).convert("RGBA")
	bb = im.split()[-1].getbbox()
	if bb:
		im = im.crop(bb)
	w, h = im.size
	side = max(w, h)
	pad = max(1, int(round(side * margin)))
	canvas = Image.new("RGBA", (side + 2 * pad, side + 2 * pad), (0, 0, 0, 255))
	canvas.alpha_composite(im, (pad + (side - w) // 2, pad + (side - h) // 2))
	buf = io.BytesIO()
	canvas.convert("RGBA").save(buf, format="PNG")
	return buf.getvalue()


def _upscale_via_service(png: bytes, method: str, scale: int, prompt: str) -> bytes:
	"""POST a PNG to the docker upscaler and return the upscaled PNG bytes."""
	import urllib.parse
	q = urllib.parse.urlencode({"method": method, "scale": scale, "prompt": prompt or ""})
	req = urllib.request.Request(f"{UPSCALE_URL}/upscale/image?{q}", data=png, method="POST",
	                             headers={"Content-Type": "image/png"})
	with urllib.request.urlopen(req, timeout=180) as r:  # diffusion refine can take a while
		return r.read()


@flask_app.post("/api/studio/mask/save")
def api_studio_mask_save():
	"""Persist a hand-painted single-hand PNG (RGBA, original sprite resolution) for an item's
	art file, so it survives restarts and both arms can reuse it. Body: {item_id, png:dataURL}."""
	body = request.json or {}
	it = _item(body.get("item_id", ""))
	if not it:
		return jsonify({"ok": False, "error": "no such item"}), 404
	png = body.get("png") or ""
	if not png:
		return jsonify({"ok": False, "error": "no png"}), 400
	try:
		os.makedirs(MASKS, exist_ok=True)
		path = _mask_path(it["invfile"])
		with open(path, "wb") as f:
			f.write(_decode_data_url(png))
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 500
	return jsonify({"ok": True, "invfile": it["invfile"].lower()})


@flask_app.get("/api/studio/mask/<path:item_id>.png")
def api_studio_mask_png(item_id):
	"""Serve the saved masked single-hand PNG. `?meshy=1` returns the aspect-preserved 512 prep
	that Meshy actually ingests; otherwise the raw painted sprite (for download / caption UI)."""
	it = _item(item_id)
	if not it:
		return ("no such item", 404)
	path = _mask_path(it["invfile"])
	if not os.path.exists(path):
		return ("no mask saved for this art file yet", 404)
	with open(path, "rb") as f:
		raw = f.read()
	if request.args.get("meshy"):
		raw = assets.prep_image_for_meshy(raw)
	return Response(raw, mimetype="image/png")


@flask_app.post("/api/studio/mask/upscale")
def api_studio_mask_upscale():
	"""Black-frame the masked hand then run it through the docker upscaler (Real-ESRGAN /
	SD x4 / SDXL-refine). Caches the result as <invfile>-up.png so it feeds the Meshy draft.
	Body: {item_id, png:dataURL (optional; else the saved mask), method, scale, prompt}."""
	body = request.json or {}
	it = _item(body.get("item_id", ""))
	if not it:
		return jsonify({"ok": False, "error": "no such item"}), 404
	method = body.get("method") or "ultrasharp"
	try:
		scale = int(body.get("scale") or 4)
	except (TypeError, ValueError):
		scale = 4
	prompt = body.get("prompt") or ""
	png = body.get("png")
	try:
		masked = _decode_data_url(png) if png else None
		if masked is None:
			path = _mask_path(it["invfile"])
			if not os.path.exists(path):
				return jsonify({"ok": False, "error": "mask the hand first"}), 400
			with open(path, "rb") as f:
				masked = f.read()
		framed = _black_frame(masked)
		up = _upscale_via_service(framed, method, scale, prompt)
		os.makedirs(MASKS, exist_ok=True)
		with open(_mask_up_path(it["invfile"]), "wb") as f:
			f.write(up)
	except urllib.error.URLError as e:  # noqa: PERF203
		return jsonify({"ok": False, "error": f"upscaler unreachable at {UPSCALE_URL}: {e}"}), 502
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 502
	import base64
	dims = Image.open(io.BytesIO(up)).size
	return jsonify({"ok": True, "method": method, "scale": scale, "size": list(dims),
	                "png": "data:image/png;base64," + base64.b64encode(up).decode()})


@flask_app.get("/api/studio/mask/upscaled/<path:item_id>.png")
def api_studio_mask_upscaled_png(item_id):
	"""Serve the cached upscaled hand (for resuming the brush view). `?meshy=1` = 512 prep."""
	it = _item(item_id)
	if not it:
		return ("no such item", 404)
	path = _mask_up_path(it["invfile"])
	if not os.path.exists(path):
		return ("no upscaled hand cached yet", 404)
	with open(path, "rb") as f:
		raw = f.read()
	if request.args.get("meshy"):
		raw = assets.prep_image_for_meshy(raw)
	return Response(raw, mimetype="image/png")


@flask_app.post("/api/studio/generate-masked")
def api_studio_generate_masked():
	"""Plain image-to-3D arm: draft from the masked single-hand PNG instead of the raw pair.
	Body: {item_id, png:dataURL (optional — the client sends the upscaled hand when it has one),
	opts}. With no png it prefers the cached UPSCALED hand, then the native mask. The native mask
	is owned by /mask/save and never overwritten here (so the brush always resumes the original)."""
	body = request.json or {}
	it = _item(body.get("item_id", ""))
	if not it:
		return jsonify({"ok": False, "error": "no such item"}), 404
	opts = body.get("opts") or {}
	try:
		png = body.get("png")
		if png:
			masked, source = _decode_data_url(png), "studio-generate-masked"
		else:
			up, native = _mask_up_path(it["invfile"]), _mask_path(it["invfile"])
			path = up if os.path.exists(up) else native
			if not os.path.exists(path):
				return jsonify({"ok": False, "error": "no masked hand for this item (mask it first)"}), 400
			with open(path, "rb") as f:
				masked = f.read()
			source = "studio-generate-masked-up" if path == up else "studio-generate-masked"
		sprite = assets.prep_image_for_meshy(masked)
		tid = _draft_from_sprite(it, sprite, opts, source)
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 502
	return jsonify({"ok": True, "task_id": tid, "phase": "draft"})


@flask_app.post("/api/studio/reroll")
def api_studio_reroll():
	"""FREE ×8 in-place re-roll (POST /v2/tasks/{id}/retry, captured 2026-07-19). Meshy
	REPLACES the draft: the returned task_id is NEW and the old one 404s — the caller must
	poll the new id. Costs 0 credits (deducts one of the draft's 8 free retries)."""
	body = request.json or {}
	src = body.get("task_id", "")
	st = _STUDIO.get(src)
	if not st:
		return jsonify({"ok": False, "error": "unknown draft task (generate first)"}), 400
	try:
		tid = meshy_web.retry_task(src)
		if not tid:
			return jsonify({"ok": False, "error": "retry accepted but replacement task id "
			                "not found (re-list tasks)"}), 502
		_remember(tid, st["item_id"], st["image_id"], "draft", "studio-reroll")
		_STUDIO.pop(src, None)  # old id is dead server-side (404)
		meshy_links.forget(_LINKS, src)
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 502
	return jsonify({"ok": True, "task_id": tid, "phase": "draft", "free": True,
	                "note": "in-place free retry; old task id is gone — poll the new id"})


@flask_app.post("/api/studio/texture")
def api_studio_texture():
	"""Texture an approved draft. Returns the texture task id."""
	body = request.json or {}
	src = body.get("task_id", "")
	st = _STUDIO.get(src)
	if not st:
		return jsonify({"ok": False, "error": "unknown draft task"}), 400
	opts = body.get("opts") or {}
	try:
		tid = meshy_web.create_texture(src, st["image_id"], art_style=opts.get("artStyle", "realistic"),
		                               enable_pbr=bool(opts.get("enablePBR", True)),
		                               prompt=opts.get("prompt", ""))
		_remember(tid, st["item_id"], st["image_id"], "texture", "studio-texture")
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 502
	return jsonify({"ok": True, "task_id": tid, "phase": "texture"})


@flask_app.get("/api/studio/task/<tid>")
def api_studio_task(tid):
	try:
		t = meshy_web.get_task(tid)
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 502
	return jsonify({"ok": True, "status": t.get("status"), "phase": t.get("phase"),
	                "progress": t.get("progress"), "hasGlb": bool(meshy_web.task_glb_url(t)),
	                "rerollsLeft": max(0, 8 - int(t.get("retryCount") or 0))})


@flask_app.get("/api/studio/glb/<tid>.glb")
def api_studio_glb(tid):
	"""Proxy the task's GLB (three.js loads it from us; the Meshy URL is signed/short-lived).
	Disk-cached in MESHY_CACHE — textured GLBs run ~28MB and task ids are immutable, so the
	Meshy download only ever happens once per task."""
	if not re.fullmatch(r"[A-Za-z0-9\-]+", tid):
		return "bad task id", 400
	cache = os.path.join(MESHY_CACHE, f"studio_{tid}.glb")
	if os.path.exists(cache):
		with open(cache, "rb") as f:
			return Response(f.read(), mimetype="model/gltf-binary")
	try:
		t = meshy_web.get_task(tid)
		url = meshy_web.task_glb_url(t)
		if not url:
			return "no GLB yet", 404
		data = meshy_web.download(url)
		os.makedirs(MESHY_CACHE, exist_ok=True)
		with open(cache + ".tmp", "wb") as f:
			f.write(data)
		os.replace(cache + ".tmp", cache)
	except Exception as e:  # noqa: BLE001
		return f"glb error: {e}", 502
	return Response(data, mimetype="model/gltf-binary")


@flask_app.post("/api/studio/accept")
def api_studio_accept():
	"""Render the chosen model's GLB at the inventory angle, color-grade, crop-to-fill DC6, save as
	an alternate + activate. Body: {task_id, azim, elev, fill, dx, dy, grade}."""
	body = request.json or {}
	tid = body.get("task_id", "")
	st = _STUDIO.get(tid)
	if not st:
		return jsonify({"ok": False, "error": "unknown task"}), 400
	it = _item(st["item_id"])
	if not it:
		return jsonify({"ok": False, "error": "item gone"}), 404
	if not blender.available():
		return jsonify({"ok": False, "error": "Blender not found"}), 501
	azim = float(body.get("azim", 25)); elev = float(body.get("elev", 15))
	fill = float(body.get("fill", 0.94)); dx = float(body.get("dx", 0)); dy = float(body.get("dy", 0))
	grade = body.get("grade") or None
	iw, ih = it["invwidth"], it["invheight"]
	try:
		os.makedirs(MESHY_CACHE, exist_ok=True)
		t = meshy_web.get_task(tid)
		glb_path = os.path.join(MESHY_CACHE, f"studio_{tid}.glb")
		with open(glb_path, "wb") as f:
			f.write(meshy_web.download(meshy_web.task_glb_url(t)))
		out_png = os.path.join(MESHY_CACHE, f"studio_{tid}_a{int(azim)}e{int(elev)}.png")
		K = 64
		paths = blender.render(glb_path, out_png, azim=azim, elev=elev, margin=1.06,
		                       res_x=iw * K, res_y=ih * K)
		with open(paths[0], "rb") as f:
			png = f.read()
		dc6_bytes = assets.png_to_item_dc6(png, iw, ih, fill=fill, dx=dx, dy=dy, grade=grade)
		alt_id = f"studio-{tid[:8]}"
		assets.save_alternate_dc6(st["item_id"], alt_id, dc6_bytes)
		src = assets.dc6_to_png_bytes(assets.read_original_dc6(it["invfile"]))
		assets.save_alt_provenance(st["item_id"], alt_id, glb=open(glb_path, "rb").read(),
		                           render_png=png, source_png=src,
		                           meta={"source": "studio", "task_id": tid, "azim": azim,
		                                 "elev": elev, "fill": fill, "dx": dx, "dy": dy, "grade": grade})
		# the workflow panel saves without activating (variants+manual-Activate pattern);
		# the studio page keeps its historical activate-on-accept behaviour.
		# When the saved alt is ALREADY the active choice, re-activate regardless: the user is
		# re-rendering the active art (e.g. new angle) and the overlay must pick up the new
		# bytes — activate() is the only overlay writer (bitten 2026-07-22, aar angle change).
		activated = body.get("activate", True)
		if activated or assets.active_choice(st["item_id"]) == alt_id:
			assets.activate(st["item_id"], it["invfile"], alt_id)
			activated = True
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 502
	return jsonify({"ok": True, "alt_id": alt_id, "activated": bool(activated),
	                "note": ("activated -- Push to game to see it" if activated
	                         else "saved as alternate -- Activate when ready")})


@flask_app.get("/pairing")
def pairing_page():
	return send_from_directory(STATIC, "pairing.html")


@flask_app.post("/api/meshy/links/batch")
def api_meshy_link_batch():
	"""Link many task->item picks in one go (the pairing page's 'Link all picked').
	Body: {picks: [{task_id, item_id}, ...]}. Reports per-pick ok/error."""
	picks = (request.json or {}).get("picks") or []
	done, failed = [], []
	for p in picks:
		tid, item_id = p.get("task_id", ""), p.get("item_id", "")
		it = _item(item_id)
		if not it:
			failed.append({"task_id": tid, "error": "no such item"})
			continue
		try:
			t = meshy_web.get_task(tid)
		except Exception as e:  # noqa: BLE001
			failed.append({"task_id": tid, "error": str(e)[:120]})
			continue
		_remember(tid, item_id, meshy_links._task_image_id(t), t.get("phase") or "draft",
		          p.get("source") or "reviewed", name=t.get("name") or "",
		          invfile=(p.get("invfile") or it["invfile"]).lower())
		done.append({"task_id": tid, "item_id": item_id, "item_name": it["name"]})
	return jsonify({"ok": True, "linked": done, "failed": failed})


def _link_art_png(task_id):
	"""The single-hand source art for a linked generation, as bytes (background cut)."""
	l = _LINKS.get(task_id) or {}
	path = l.get("art_file")
	if not path or not os.path.exists(path):
		return None
	with open(path, "rb") as f:
		return f.read()


@flask_app.get("/api/pair/ghost/<invfile>.png")
def api_pair_ghost(invfile):
	"""The original DC6, upscaled nearest-neighbour, as the tuning ghost."""
	try:
		png = assets.dc6_to_png_bytes(assets.read_original_dc6(invfile))
		im = Image.open(io.BytesIO(png)).convert("RGBA")
		k = max(1, int(request.args.get("k", 6)))
		im = im.resize((im.width * k, im.height * k), Image.NEAREST)
		buf = io.BytesIO(); im.save(buf, "PNG")
	except Exception as e:  # noqa: BLE001
		return f"ghost error: {e}", 404
	return _processed_png(buf.getvalue(), key=f"ghost-{invfile}-{request.args.get('k', 6)}")


@flask_app.get("/api/pair/hand/<task_id>.png")
def api_pair_hand(task_id):
	"""A generation's single-hand source art with its flat background removed, so the
	template editor can position real shapes before any 3D render exists."""
	raw = _link_art_png(task_id)
	if raw is None:
		return "no source art for this generation", 404
	path = (_LINKS.get(task_id) or {}).get("art_file") or ""
	try:
		mtime = int(os.path.getmtime(path)) if path and os.path.exists(path) else 0
	except OSError:
		mtime = 0
	etag = f'W/"{IMAGE_PIPELINE_VERSION}-{task_id}-{mtime}"'
	if request.headers.get("If-None-Match") == etag:
		return Response(status=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
	try:
		im = glove_pairs.drop_flat_background(Image.open(io.BytesIO(raw)))
		bb = im.split()[-1].getbbox()
		if bb:
			im = im.crop(bb)
		im.thumbnail((512, 512), Image.LANCZOS)
		buf = io.BytesIO(); im.save(buf, "PNG")
	except Exception as e:  # noqa: BLE001
		return f"hand error: {e}", 500
	return Response(buf.getvalue(), mimetype="image/png",
	                headers={"ETag": etag, "Cache-Control": "no-cache"})


def _pair_art_paths(invfile, left_task=None, right_task=None):
	"""Source art paths for a glove's two slots, defaulting to whatever is linked."""
	paths = {"left": None, "right": None}
	for tid, l in _LINKS.items():
		if l.get("ignored") or (l.get("invfile") or "").lower() != (invfile or "").lower():
			continue
		hand = glove_pairs.hand_of(l.get("art_file") or l.get("source") or "")
		if not hand:
			continue
		if (hand == "left" and left_task and tid != left_task) or \
		   (hand == "right" and right_task and tid != right_task):
			continue
		if not paths[hand] and l.get("art_file") and os.path.exists(l["art_file"]):
			paths[hand] = l["art_file"]
	return paths


def _effective_template(invfile, item=None, left_task=None, right_task=None):
	"""The layout to use: a saved template wins; otherwise auto-fit, so a glove you
	never opened still builds correctly rather than at the plain neutral placement."""
	saved = glove_pairs.load_templates().get((invfile or "").lower())
	if saved:
		return glove_pairs.get_template(invfile), "saved", {}
	sz = glove_pairs.original_size(invfile)
	if not sz:
		it = item or _item_for_invfile(invfile) or {}
		sz = (it.get("invwidth", 2) * assets.CELL_PX, it.get("invheight", 2) * assets.CELL_PX)
	paths = _pair_art_paths(invfile, left_task, right_task)
	if not paths["left"] and not paths["right"]:
		return glove_pairs.get_template(invfile), "neutral", {}
	r = glove_pairs.autofit_template(paths["left"], paths["right"], sz[0], sz[1])
	return r["template"], "autofit", r["notes"]


@flask_app.post("/api/pair/autofit")
def api_pair_autofit():
	"""Compute (but do not save) the auto-fit layout for a glove: rotate each pinky
	edge parallel to its border, fill the height, snap to the side."""
	body = request.json or {}
	invfile = (body.get("invfile") or "").lower()
	it = _item_for_invfile(invfile)
	if not it:
		return jsonify({"ok": False, "error": "unknown art file"}), 404
	sz = glove_pairs.original_size(invfile) or (it["invwidth"] * assets.CELL_PX,
	                                            it["invheight"] * assets.CELL_PX)
	paths = _pair_art_paths(invfile, body.get("left_task"), body.get("right_task"))
	if not paths["left"] and not paths["right"]:
		return jsonify({"ok": False, "error": "no source art linked for this glove"}), 400
	r = glove_pairs.autofit_template(paths["left"], paths["right"], sz[0], sz[1])
	return jsonify({"ok": True, "invfile": invfile, "template": r["template"],
	                "notes": r["notes"]})


_LAST_TEX_RESOLVE = [0.0]  # throttle for the auto-resolve on pairs load


def _resolve_textures(force=False, only_missing=True):
	"""Point links at their TEXTURED descendant so the preview stops rendering grey clay.

	Links are made at DRAFT time (bare geometry, no materials); texturing is a SEPARATE Meshy
	task found via rootId/parent. `only_missing` scans just the links that still lack a
	texture_task (so an already-resolved workspace is cheap). Throttled unless `force`, because
	untextured drafts never resolve and would otherwise re-scan on every single page load.
	Returns (found_count, updated_count)."""
	import time as _t
	if not force and (_t.time() - _LAST_TEX_RESOLVE[0]) < 120:
		return (0, 0)
	_LAST_TEX_RESOLVE[0] = _t.time()
	ids = {t for t, l in _LINKS.items()
	       if not l.get("ignored") and (not only_missing or not l.get("texture_task"))}
	if not ids:
		return (0, 0)
	found = meshy_web.find_textured_children(ids)
	n = 0
	for draft, tex in found.items():
		if _LINKS.get(draft) is not None and (_LINKS[draft].get("texture_task") or "") != tex:
			_LINKS[draft]["texture_task"] = tex
			n += 1
			p = _pair_thumb_path(draft)   # cached render was made from untextured geometry
			if os.path.exists(p):
				os.remove(p)
	if n:
		meshy_links.save_links(_LINKS)
	return (len(found), n)


@flask_app.post("/api/meshy/use-textured")
def api_meshy_use_textured():
	"""Manual 'resolve textures': force a full scan and point every link at its textured child."""
	try:
		found, n = _resolve_textures(force=True, only_missing=False)
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)[:300]}), 502
	return jsonify({"ok": True, "linked": found, "updated": n,
	                "note": f"{n} generation(s) now use their textured model"})


@flask_app.post("/api/pair/dc6preview")
def api_pair_dc6preview():
	"""Round-trip a posed render through the REAL DC6 encoder and hand back a PNG.

	Body: {invfile, png}. Deliberately uses png_to_item_dc6 + dc6_to_png_bytes rather than
	approximating the palette in the browser: the point of this preview is to show the
	quantisation, so anything but the actual encoder could disagree in exactly the way you
	are looking for.
	"""
	body = request.json or {}
	invfile = (body.get("invfile") or "").lower()
	it = _item_for_invfile(invfile)
	if not it:
		return jsonify({"ok": False, "error": "unknown art file"}), 404
	data_url = body.get("png") or ""
	if "," not in data_url:
		return jsonify({"ok": False, "error": "no image"}), 400
	import base64
	try:
		raw = base64.b64decode(data_url.split(",", 1)[1])
		dc6_bytes = assets.png_to_item_dc6(raw, it["invwidth"], it["invheight"], fill=1.0)
		png = assets.dc6_to_png_bytes(dc6_bytes)
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)[:300]}), 500
	return jsonify({"ok": True, "png": "data:image/png;base64," + base64.b64encode(png).decode(),
	                "bytes": len(dc6_bytes),
	                "size": [it["invwidth"] * assets.CELL_PX, it["invheight"] * assets.CELL_PX]})


@flask_app.post("/api/pair/gap/<task_id>")
def api_pair_gap(task_id):
	"""Set this model's own pose values (separation and tilt).

	Body: {gap?, yaw?} to set, or {clear: true} to fall back to the row's shared values.
	"""
	body = request.json or {}
	if task_id not in _LINKS:
		return jsonify({"ok": False, "error": "unknown generation"}), 404
	if body.get("clear"):
		for f in meshy_links.PER_MODEL_POSE:
			meshy_links.set_pose_field(_LINKS, task_id, f, None)
		return jsonify({"ok": True, "task_id": task_id, "own": {}})
	own = {}
	for f in meshy_links.PER_MODEL_POSE:
		if body.get(f) is None:
			continue
		try:
			v = float(body[f])
		except (TypeError, ValueError):
			return jsonify({"ok": False, "error": f"{f} must be a number"}), 400
		l = meshy_links.set_pose_field(_LINKS, task_id, f, v)
		own[f] = l.get(f)
	p = _pair_thumb_path(task_id)
	if os.path.exists(p):
		os.remove(p)                       # the cached thumbnail used the old pose
	return jsonify({"ok": True, "task_id": task_id, "own": own})


@flask_app.post("/api/pair/square/<task_id>")
def api_pair_square(task_id):
	"""Bake the current camera angles into this model as its top-down zero.

	Body: {azim, elev} to bake, or {clear: true} to drop the squaring. Baking rotates the
	model by the inverse of the camera orbit, so the picture does not move -- but the
	model becomes genuinely square, which is what makes 'tilt apart' fan the hands out
	rather than curl them together.
	"""
	body = request.json or {}
	if task_id not in _LINKS:
		return jsonify({"ok": False, "error": "unknown generation"}), 404
	if body.get("clear"):
		meshy_links.set_squaring(_LINKS, task_id, None)
		return jsonify({"ok": True, "task_id": task_id, "squaring": None,
		                "note": "squaring cleared -- back to the raw model"})
	cur = list((_LINKS.get(task_id) or {}).get("squaring") or [])
	az, el = float(body.get("azim", 0)), float(body.get("elev", 0))
	if abs(az) < 1e-6 and abs(el) < 1e-6:
		return jsonify({"ok": False,
		                "error": "camera is already at 0/0 -- move it to the view you want first"}), 400
	cur.append([round(az, 4), round(el, 4)])
	sq = cur
	meshy_links.set_squaring(_LINKS, task_id, sq)
	return jsonify({"ok": True, "task_id": task_id, "squaring": sq,
	                "note": "top-down set -- camera back to 0, axes now square"})


@flask_app.get("/api/pair/outline/<task_id>")
def api_pair_outline(task_id):
	"""Silhouette outline + aspect for a generation's hand art, so the tuner can clamp
	the real shape to the output border instead of its bounding box."""
	l = _LINKS.get(task_id) or {}
	path = l.get("art_file")
	if not path or not os.path.exists(path):
		return jsonify({"ok": False, "error": "no source art"}), 404
	return jsonify({"ok": True, "points": glove_pairs.outline_for(path),
	                "aspect": glove_pairs.aspect_for(path)})


@flask_app.route("/api/pair/template/<invfile>", methods=["GET", "POST"])
def api_pair_template(invfile):
	if request.method == "GET":
		return jsonify({"ok": True, "invfile": invfile,
		                "template": glove_pairs.get_template(invfile)})
	tpl = glove_pairs.save_template(invfile, request.json or {})
	return jsonify({"ok": True, "invfile": invfile, "template": tpl})


@flask_app.post("/api/pair/preview")
def api_pair_preview():
	"""Live composite preview from the SOURCE ART (no Blender), for template tuning.
	Body: {invfile, left_task?, right_task?, template}."""
	body = request.json or {}
	invfile = (body.get("invfile") or "").lower()
	it = _item_for_invfile(invfile)
	if not it:
		return jsonify({"ok": False, "error": "unknown art file"}), 404
	tpl = body.get("template") or glove_pairs.get_template(invfile)
	lp = _link_art_png(body.get("left_task") or "")
	rp = _link_art_png(body.get("right_task") or "")
	ml = mr = False
	if lp is None and rp is not None:
		lp, ml = rp, True
	elif rp is None and lp is not None:
		rp, mr = lp, True
	if lp is None and rp is None:
		return jsonify({"ok": False, "error": "no source art"}), 400
	canvas = glove_pairs.composite(lp, rp, tpl, it["invwidth"], it["invheight"],
	                               mirror_left=ml, mirror_right=mr,
	                               size=glove_pairs.original_size(invfile))
	k = max(1, int(body.get("k", 6)))
	canvas = canvas.resize((canvas.width * k, canvas.height * k), Image.NEAREST)
	buf = io.BytesIO(); canvas.save(buf, "PNG")
	return Response(buf.getvalue(), mimetype="image/png")


def _item_for_invfile(invfile):
	for it in catalog()["items"]:
		if (it["invfile"] or "").lower() == (invfile or "").lower():
			return it
	return None


@flask_app.post("/api/pair/build")
def api_pair_build():
	"""Render both hands' 3D models in Blender, composite onto the saved template, and
	save+activate the paired DC6. A missing hand is mirrored so the output is always a
	pair. Body: {invfile, left_task?, right_task?, azim?, elev?}."""
	body = request.json or {}
	invfile = (body.get("invfile") or "").lower()
	it = _item_for_invfile(invfile)
	if not it:
		return jsonify({"ok": False, "error": "unknown art file"}), 404
	if not blender.available():
		return jsonify({"ok": False, "error": "Blender not found"}), 501
	tpl, tpl_src, _notes = _effective_template(invfile, it, body.get("left_task"),
	                                           body.get("right_task"))
	azim = float(body.get("azim", 25)); elev = float(body.get("elev", 15))

	def render(task_id):
		"""Meshy GLB -> transparent PNG at the inventory angle."""
		t = meshy_web.get_task(task_id)
		url = meshy_web.task_glb_url(t)
		if not url:
			raise ValueError(f"generation {task_id[:8]} has no 3D model yet")
		os.makedirs(MESHY_CACHE, exist_ok=True)
		glb = os.path.join(MESHY_CACHE, f"pair_{task_id}.glb")
		if not os.path.exists(glb):
			with open(glb, "wb") as f:
				f.write(meshy_web.download(url))
		out = os.path.join(MESHY_CACHE, f"pair_{task_id}_a{int(azim)}e{int(elev)}.png")
		paths = blender.render(glb, out, azim=azim, elev=elev, margin=1.02,
		                       res_x=512, res_y=512)
		with open(paths[0], "rb") as f:
			return f.read()

	lt, rt = body.get("left_task"), body.get("right_task")
	try:
		lp = render(lt) if lt else None
		rp = render(rt) if rt else None
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 502
	ml = mr = False
	if lp is None and rp is not None:
		lp, ml = rp, True
	elif rp is None and lp is not None:
		rp, mr = lp, True
	if lp is None and rp is None:
		return jsonify({"ok": False, "error": "pick at least one hand"}), 400

	try:
		canvas = glove_pairs.composite(lp, rp, tpl, it["invwidth"], it["invheight"],
		                               mirror_left=ml, mirror_right=mr,
		                               size=glove_pairs.original_size(invfile))
		dc6_bytes = glove_pairs.canvas_to_dc6(canvas)
		alt_id = f"pair-{invfile}"
		assets.save_alternate_dc6(it["id"], alt_id, dc6_bytes)
		buf = io.BytesIO(); canvas.save(buf, "PNG")
		assets.save_alt_provenance(it["id"], alt_id, render_png=buf.getvalue(),
		                           meta={"source": "glove-pair", "invfile": invfile,
		                                 "left_task": lt, "right_task": rt,
		                                 "mirrored_left": ml, "mirrored_right": mr,
		                                 "template": tpl, "azim": azim, "elev": elev})
		assets.activate(it["id"], it["invfile"], alt_id)
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 500
	return jsonify({"ok": True, "invfile": invfile, "item_id": it["id"], "alt_id": alt_id,
	                "mirrored_left": ml, "mirrored_right": mr, "template_source": tpl_src,
	                "note": "paired sprite saved + activated -- Push to game to see it"})


def _pair_glb_path(task_id):
	"""Cached GLB for a generation, downloading it once if needed.

	Prefers the TEXTURED descendant when the link knows of one: a link made at draft time
	points at bare geometry with no materials, which renders as grey clay.
	"""
	os.makedirs(MESHY_CACHE, exist_ok=True)
	task_id = (_LINKS.get(task_id) or {}).get("texture_task") or task_id
	path = os.path.join(MESHY_CACHE, f"pair_{task_id}.glb")
	if os.path.exists(path) and os.path.getsize(path) > 1024:
		return path
	t = meshy_web.get_task(task_id)
	url = meshy_web.task_glb_url(t)
	if not url:
		return None
	with open(path, "wb") as f:
		f.write(meshy_web.download(url))
	return path


@flask_app.get("/api/pair/model/<task_id>.glb")
def api_pair_model(task_id):
	"""Serve a generation's GLB to the browser preview (Meshy's own URLs are signed and
	short-lived, so they cannot be referenced directly from the page)."""
	try:
		path = _pair_glb_path(task_id)
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)[:200]}), 502
	if not path:
		return jsonify({"ok": False, "error": "generation has no 3D model yet"}), 404
	with open(path, "rb") as f:
		data = f.read()
	# Keyed to the RESOLVED file, and revalidated. A plain max-age pinned the browser to
	# the old untextured GLB for a day, so re-pointing a link at its textured model had
	# no visible effect in the page.
	etag = f'W/"{os.path.basename(path)}-{int(os.path.getmtime(path))}-{len(data)}"'
	if request.headers.get("If-None-Match") == etag:
		return Response(status=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
	return Response(data, mimetype="model/gltf-binary",
	                headers={"ETag": etag, "Cache-Control": "no-cache"})


PAIR_THUMB_DIR = os.path.join(MESHY_CACHE, "pair_thumbs")


def _pair_thumb_path(task_id):
	return os.path.join(PAIR_THUMB_DIR, f"{task_id}.png")


@flask_app.get("/api/pair/thumb/<task_id>.png")
def api_pair_thumb(task_id):
	"""Cached 3D pair thumbnail for a generation card.

	Rendered ONCE by the browser and uploaded here, so later visits load a small PNG
	instead of re-downloading a ~7MB GLB and re-running WebGL for every card.
	"""
	path = _pair_thumb_path(task_id)
	if not os.path.exists(path):
		return jsonify({"ok": False, "error": "not rendered yet"}), 404
	with open(path, "rb") as f:
		data = f.read()
	etag = f'W/"{IMAGE_PIPELINE_VERSION}-thumb-{task_id}-{int(os.path.getmtime(path))}"'
	if request.headers.get("If-None-Match") == etag:
		return Response(status=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
	return Response(data, mimetype="image/png",
	                headers={"ETag": etag, "Cache-Control": "no-cache"})


@flask_app.post("/api/pair/thumb/<task_id>")
def api_pair_thumb_put(task_id):
	"""Store a browser-rendered pair thumbnail. Body: {png: dataURL}."""
	data_url = (request.json or {}).get("png") or ""
	if "," not in data_url:
		return jsonify({"ok": False, "error": "no image"}), 400
	import base64
	try:
		raw = base64.b64decode(data_url.split(",", 1)[1])
		os.makedirs(PAIR_THUMB_DIR, exist_ok=True)
		with open(_pair_thumb_path(task_id), "wb") as f:
			f.write(raw)
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)[:200]}), 500
	return jsonify({"ok": True, "task_id": task_id, "bytes": len(raw)})


@flask_app.delete("/api/pair/thumb/<invfile>/all")
def api_pair_thumb_clear(invfile):
	"""Drop every cached thumbnail for an art file, so a pose change re-renders them."""
	n = 0
	for tid, l in _LINKS.items():
		if (l.get("invfile") or "").lower() != (invfile or "").lower():
			continue
		p2 = _pair_thumb_path(tid)
		if os.path.exists(p2):
			os.remove(p2)
			n += 1
	return jsonify({"ok": True, "cleared": n})


@flask_app.get("/api/pair/engines")
def api_pair_engines():
	"""Which final renderers are available. Blender is optional by design."""
	return jsonify({"ok": True, "browser": True, "blender": blender.available(),
	                "blender_note": None if blender.available()
	                else "Blender not found (set BLENDER_EXE to enable)"})


@flask_app.post("/api/pair/build3d")
def api_pair_build3d():
	"""Accept a browser-rendered pair sprite and save it as the item's DC6 alternate.

	A 3D pair render is a COMPLETE sprite, so it goes straight through the normal
	crop-to-content fit -- no 2D placement maths involved.
	Body: {invfile, png (data URL), pose?, engine?}
	"""
	body = request.json or {}
	invfile = (body.get("invfile") or "").lower()
	it = _item_for_invfile(invfile)
	if not it:
		return jsonify({"ok": False, "error": "unknown art file"}), 404
	data_url = body.get("png") or ""
	if "," not in data_url:
		return jsonify({"ok": False, "error": "no image supplied"}), 400
	import base64
	try:
		png = base64.b64decode(data_url.split(",", 1)[1])
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": f"bad image: {e}"}), 400
	try:
		dc6_bytes = assets.png_to_item_dc6(png, it["invwidth"], it["invheight"], fill=1.0)
		alt_id = f"pair3d-{invfile}"
		assets.save_alternate_dc6(it["id"], alt_id, dc6_bytes)
		assets.save_alt_provenance(it["id"], alt_id, render_png=png,
		                           meta={"source": "glove-pair-3d", "invfile": invfile,
		                                 "engine": body.get("engine") or "browser",
		                                 "pose": body.get("pose")})
		activated = body.get("activate", True)  # workflow panel passes False (manual Activate)
		if activated or assets.active_choice(it["id"]) == alt_id:  # refresh already-active art
			assets.activate(it["id"], it["invfile"], alt_id)
			activated = True
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 500
	return jsonify({"ok": True, "invfile": invfile, "item_id": it["id"], "alt_id": alt_id,
	                "engine": body.get("engine") or "browser", "activated": bool(activated),
	                "note": ("3D pair sprite saved + activated -- Push to game to see it"
	                         if activated else "3D pair sprite saved -- Activate when ready")})


@flask_app.post("/api/pair/build3d/blender")
def api_pair_build3d_blender():
	"""Same output, rendered by Blender: one model, mirrored into a pair in one scene so
	the cast shadow between the hands is real geometry."""
	body = request.json or {}
	invfile = (body.get("invfile") or "").lower()
	it = _item_for_invfile(invfile)
	if not it:
		return jsonify({"ok": False, "error": "unknown art file"}), 404
	if not blender.available():
		return jsonify({"ok": False, "error": "Blender not installed"}), 501
	task_id = body.get("task_id")
	if not task_id:
		return jsonify({"ok": False, "error": "no generation chosen"}), 400
	pose = dict(glove_pairs.get_template(invfile).get("pose3d") or {})
	pose.update(body.get("pose") or {})
	# separation and tilt are stored per model and win over the row's shared values
	link = _LINKS.get(task_id) or {}
	for f in meshy_links.PER_MODEL_POSE:
		if link.get(f) is not None:
			pose[f] = float(link[f])
	try:
		glb = _pair_glb_path(task_id)
		if not glb:
			return jsonify({"ok": False, "error": "generation has no 3D model yet"}), 400
		K = 8                                    # supersample, then fit down
		out = os.path.join(MESHY_CACHE, f"pair3d_{invfile}.png")
		paths = blender.render(
			glb, out, azim=float(pose.get("azim", 0)), elev=float(pose.get("elev", 0)),
			res_x=it["invwidth"] * assets.CELL_PX * K // 2,
			res_y=it["invheight"] * assets.CELL_PX * K // 2,
			margin=float(pose.get("margin", 1.06)), pair=True,
			pair_yaw=float(pose.get("yaw", 0)), pair_gap=float(pose.get("gap", 0.55)),
			pair_depth=float(pose.get("depth", 0)), samples=int(pose.get("samples", 48)),
			# the model's own top-down squaring, as the camera angles that were baked in;
			# Blender re-derives the rotation with its own camera formula
			orient=tuple((_LINKS.get(task_id) or {}).get("squaring") or ()))
		with open(paths[0], "rb") as f:
			png = f.read()
		dc6_bytes = assets.png_to_item_dc6(png, it["invwidth"], it["invheight"], fill=1.0)
		alt_id = f"pair3d-{invfile}"
		assets.save_alternate_dc6(it["id"], alt_id, dc6_bytes)
		assets.save_alt_provenance(it["id"], alt_id, render_png=png,
		                           meta={"source": "glove-pair-3d", "invfile": invfile,
		                                 "engine": "blender", "pose": pose,
		                                 "task_id": task_id})
		activated = body.get("activate", True)  # workflow panel passes False (manual Activate)
		if activated or assets.active_choice(it["id"]) == alt_id:  # refresh already-active art
			assets.activate(it["id"], it["invfile"], alt_id)
			activated = True
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)[:300]}), 502
	return jsonify({"ok": True, "invfile": invfile, "item_id": it["id"], "alt_id": alt_id,
	                "engine": "blender", "activated": bool(activated),
	                "note": ("3D pair sprite rendered in Blender + activated" if activated
	                         else "3D pair sprite rendered in Blender -- Activate when ready")})


@flask_app.post("/api/pair/build-single")
def api_pair_build_single():
	"""Build a NON-pair (single-model) generation into its item's DC6 and activate it, at a
	default inventory angle. For the pairing page's 'Accept all' over single-model items (weapons,
	armour, amulets...). Uses the TEXTURED model via _pair_glb_path -- so it never bakes grey clay,
	the same fix the pair path got.

	Body: {task_id, azim?, elev?, fill?, png?}. When `png` (a browser render) is supplied it is
	used directly -- the same Browser-vs-Blender choice the pair path offers. Otherwise Blender
	renders it server-side (needs Blender installed)."""
	body = request.json or {}
	tid = body.get("task_id", "")
	st = _STUDIO.get(tid)
	if not st:
		return jsonify({"ok": False, "error": "unknown task"}), 400
	it = _item(st["item_id"])
	if not it:
		return jsonify({"ok": False, "error": "item gone"}), 404
	azim = float(body.get("azim", 25)); elev = float(body.get("elev", 15)); fill = float(body.get("fill", 0.94))
	iw, ih = it["invwidth"], it["invheight"]
	image_url = body.get("image_url")     # the 2D card image (redraw fed to Meshy) -- use it AS the sprite
	browser_png = body.get("png")
	if not image_url and not browser_png and not blender.available():
		return jsonify({"ok": False, "error": "Blender not found (or render in the browser)"}), 501
	try:
		if image_url:                     # WYSIWYG: the DC6 IS the 2D image shown on the card, no render
			raw = meshy_web.download(image_url)
			im = glove_pairs.drop_flat_background(Image.open(io.BytesIO(raw)))  # cut any flat backdrop
			buf = io.BytesIO(); im.convert("RGBA").save(buf, format="PNG"); png = buf.getvalue()
			engine = "card-image"
		elif browser_png:                 # browser engine: caller already rendered it
			png = _decode_data_url(browser_png)
			engine = "browser"
		else:                             # blender engine: render the textured model server-side
			glb = _pair_glb_path(tid)     # textured + cached (resolves texture_task)
			if not glb:
				return jsonify({"ok": False, "error": "generation has no 3D model yet"}), 400
			out = os.path.join(MESHY_CACHE, f"single_{tid}_a{int(azim)}e{int(elev)}.png")
			K = 64
			paths = blender.render(glb, out, azim=azim, elev=elev, margin=1.06, res_x=iw * K, res_y=ih * K)
			with open(paths[0], "rb") as f:
				png = f.read()
			engine = "blender"
		dc6_bytes = assets.png_to_item_dc6(png, iw, ih, fill=fill)
		alt_id = f"pair-single-{tid[:8]}"
		assets.save_alternate_dc6(st["item_id"], alt_id, dc6_bytes)
		assets.save_alt_provenance(st["item_id"], alt_id, render_png=png,
		                           meta={"source": "pairing-single", "task_id": tid,
		                                 "engine": engine,
		                                 "azim": azim, "elev": elev, "fill": fill})
		if body.get("activate", True) or assets.active_choice(st["item_id"]) == alt_id:
			assets.activate(st["item_id"], it["invfile"], alt_id)  # refresh already-active art
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)[:300]}), 502
	return jsonify({"ok": True, "item_id": it["id"], "alt_id": alt_id, "invfile": it["invfile"]})


@flask_app.post("/api/meshy/none")
def api_meshy_none():
	"""'None' for an art file: unlink every generation currently paired to it. The
	generations return to Unplaced so a wrong auto-match frees them to be reassigned
	rather than being stuck on the wrong DC6."""
	invfile = ((request.json or {}).get("invfile") or "").lower()
	freed = [tid for tid, l in _LINKS.items() if (l.get("invfile") or "").lower() == invfile]
	for tid in freed:
		_STUDIO.pop(tid, None)
		meshy_links.forget(_LINKS, tid)
	return jsonify({"ok": True, "invfile": invfile, "freed": freed})


@flask_app.post("/api/meshy/ignore")
def api_meshy_ignore():
	"""'None' for a generation: never pair this one (non-game art, experiments).
	Persisted so a rescan cannot re-link it; reversible."""
	body = request.json or {}
	tid = body.get("task_id", "")
	ignored = body.get("ignored", True)
	if not tid:
		return jsonify({"ok": False, "error": "task_id required"}), 400
	_STUDIO.pop(tid, None)
	meshy_links.set_ignored(_LINKS, tid, bool(ignored))
	return jsonify({"ok": True, "task_id": tid, "ignored": bool(ignored)})


@flask_app.get("/api/dc6/<name>.png")
def api_dc6_png(name):
	"""Render a DC6 by FILE NAME. The pairing page is anchored on art files, some of
	which (PD2 customs like invch1) no catalog item references."""
	try:
		png = assets.dc6_to_png_bytes(assets.read_original_dc6(name))
	except Exception as e:  # noqa: BLE001
		return f"render error: {e}", 404
	return Response(png, mimetype="image/png")


@flask_app.post("/api/meshy/primary")
def api_meshy_primary():
	"""Choose WHICH generation is the one to use for a DC6 (several can target one
	file -- e.g. four re-imagined variants of invtgl). Exclusive per invfile."""
	body = request.json or {}
	tid, invfile = body.get("task_id", ""), (body.get("invfile") or "").lower()
	if tid not in _LINKS:
		return jsonify({"ok": False, "error": "task not linked"}), 404
	for k, l in _LINKS.items():
		if (l.get("invfile") or "").lower() == invfile:
			l["primary"] = (k == tid)
	meshy_links.save_links(_LINKS)
	return jsonify({"ok": True, "invfile": invfile, "task_id": tid})


# ---- re-imagined art library index (all variants, generated or not) -------
# A glove was re-drawn as many numbered variants (invtgl-l1..l8, lj1..lj4 + the r* hands),
# but only a few were ever generated in Meshy. The pairing row shows every LIBRARY variant as
# a slot so the ungenerated ones are visible and fillable, not just the handful already made.
_LIB_INDEX = {"by_file": None, "name2path": None}


def _library_art_index():
	"""{invfile: {variant: {'variant','left','right'}}} over the re-imagined library, plus a
	basename->abspath map for serving. Cached (the library is static within a run)."""
	if _LIB_INDEX["by_file"] is not None:
		return _LIB_INDEX["by_file"]
	root = meshy_links.REIMAGINED_ROOT
	known = {(it["invfile"] or "").lower() for it in catalog()["items"]}
	by_file, name2path = {}, {}
	if os.path.isdir(root):
		for dp, _d, fns in os.walk(root):
			for fn in fns:
				if not fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
					continue
				path = os.path.join(dp, fn)
				dc6 = meshy_links.dc6_name_from_art_path(path, known)
				hand = glove_pairs.hand_of(fn)
				if not dc6 or not hand:      # only split (paired) art files are slots
					continue
				name2path[fn.lower()] = path
				var = glove_pairs.variant_of(fn) or ""
				v = by_file.setdefault(dc6, {}).setdefault(
					var, {"variant": var, "label": glove_pairs.variant_label(var or None),
					      "left": None, "right": None})
				v[hand] = fn
	_LIB_INDEX["by_file"], _LIB_INDEX["name2path"] = by_file, name2path
	return by_file


def _variant_sort_key(v: str):
	# numbered variants first (1..8) then the j-series (j1..j4); stable and human-ordered
	s = v or ""
	series = 1 if s.startswith("j") else 0
	num = s[1:] if series else s
	return (series, int(num) if num.isdigit() else 99, s)


_REIMG_THUMB = {}  # basename -> downscaled PNG bytes (redraws are ~1.3MB each; the slots are tiny)


@flask_app.get("/api/reimagined/<path:name>")
def api_reimagined_art(name):
	"""Serve a re-imagined redraw by basename (e.g. invtgl-l4.png), for the variant slots on the
	pairing page. Downscaled to a thumbnail + cached, since the source PNGs are ~1.3MB each and a
	row shows a dozen. `?full=1` serves the original."""
	_library_art_index()
	key = os.path.basename(name).lower()
	path = (_LIB_INDEX["name2path"] or {}).get(key)
	if not path or not os.path.exists(path):
		return ("no such re-imagined art", 404)
	if request.args.get("full"):
		with open(path, "rb") as f:
			return Response(f.read(), mimetype="image/png")
	if key not in _REIMG_THUMB:
		im = Image.open(path).convert("RGBA")
		im.thumbnail((192, 192), Image.LANCZOS)
		buf = io.BytesIO(); im.save(buf, format="PNG")
		_REIMG_THUMB[key] = buf.getvalue()
	return Response(_REIMG_THUMB[key], mimetype="image/png",
	                headers={"Cache-Control": "max-age=86400"})


@flask_app.post("/api/studio/generate-from-art")
def api_studio_generate_from_art():
	"""Generate a variant straight from its re-imagined redraw (fills an empty pairing slot).
	Body: {invfile, art_file (basename), opts}. Links the new draft to the art file so it
	lands back on the row with its hand + variant."""
	body = request.json or {}
	invfile = (body.get("invfile") or "").lower()
	it = _item_for_invfile(invfile)
	if not it:
		return jsonify({"ok": False, "error": "unknown art file"}), 404
	_library_art_index()
	art = body.get("art_file") or ""
	path = (_LIB_INDEX["name2path"] or {}).get(os.path.basename(art).lower())
	if not path or not os.path.exists(path):
		return jsonify({"ok": False, "error": f"no re-imagined art '{art}'"}), 404
	opts = body.get("opts") or {}
	try:
		with open(path, "rb") as f:
			sprite = assets.prep_image_for_meshy(f.read())
		image_id = meshy_web.register_image(sprite, filename=os.path.basename(path))
		tid = meshy_web.create_draft(image_id, ai_model=opts.get("aiModel", "avocado"),
		                             model_type=opts.get("modelType", "standard"),
		                             topology=opts.get("topology", "triangle"),
		                             symmetry=int(opts.get("symmetry", 0)),
		                             seed=int(opts.get("seed", 0)))
		_remember(tid, it["id"], image_id, "draft", f"studio-variant ({os.path.basename(path)})",
		          name=it["name"], invfile=invfile, art_file=path)
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 502
	return jsonify({"ok": True, "task_id": tid, "phase": "draft", "invfile": invfile,
	                "art_file": os.path.basename(path)})


@flask_app.get("/api/meshy/pairs")
def api_meshy_pairs():
	"""The pairing view, anchored on DC6 ART FILES: every file that has at least one
	matched Meshy generation, plus the generations still needing a home."""
	# Page through ALL tasks, not just the first two pages: a glove can have more linked
	# generations than fit in one window (invtgl has 5), and any beyond the window rendered
	# as empty cards (no preview/thumb) while unplaced ones never surfaced at all. Stop at the
	# first short/empty page; cap the loop so a full-page-every-time API can't spin forever.
	tasks = {}
	PAGE = 30
	try:
		for pg in range(1, 41):  # up to ~1200 tasks
			batch = meshy_web.list_tasks(page_num=pg, page_size=PAGE)
			for t in batch:
				tasks[t["id"]] = t
			if len(batch) < PAGE:
				break
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 502
	try:
		_resolve_textures()  # auto-heal grey-clay links -> their textured children (throttled)
	except Exception:  # noqa: BLE001 -- never let texture resolution break the page
		pass
	c = catalog()
	byfile = {}
	for it in c["items"]:
		byfile.setdefault((it["invfile"] or "").lower(), []).append(it)

	rows = {}
	for tid, l in _LINKS.items():
		if l.get("ignored"):
			continue
		f = (l.get("invfile") or "").lower()
		if not f:
			continue
		t = tasks.get(tid) or {}
		grp = byfile.get(f, [])
		row = rows.setdefault(f, {
			"invfile": f,
			"item_id": l.get("item_id"),
			"items": [i["name"] for i in grp],
			"item_count": len(grp),
			"generations": [],
		})
		row["generations"].append({
			"task_id": tid,
			"hand": glove_pairs.hand_of(l.get("art_file") or l.get("source") or ""),
			"variant": glove_pairs.variant_of(l.get("art_file") or l.get("source") or ""),
			"art_file": os.path.basename(l.get("art_file") or "") or None,
			"name": l.get("name") or "",
			"prompt": ((t.get("args") or {}).get("draft") or {}).get("prompt", ""),
			"input_image": meshy_links._task_input_url(t) or "",
			"preview": ((t.get("result") or {}).get("previewUrl") or ""),
			"status": t.get("status"), "phase": t.get("phase"),
			"retries_left": 8 - (t.get("retryCount") or 0),
			"has_model": bool((t.get("result") or {}).get("generate") or
			                  (t.get("result") or {}).get("modelUrl") or t.get("status") == "SUCCEEDED"),
			# The pair THUMBNAIL renders from the GLB, which _pair_glb_path resolves through the
			# textured child -- so a generation is renderable whenever it has a model OR a textured
			# task, even if the DRAFT task 404'd (aged out after texturing) and thus has no preview
			# URL. Gating the thumbnail on `preview` alone left such cards permanently blank.
			"renderable": bool((t.get("result") or {}).get("generate") or
			                   (t.get("result") or {}).get("modelUrl")
			                   or t.get("status") == "SUCCEEDED" or l.get("texture_task")),
			"source": l.get("source") or "",
			"has_thumb": os.path.exists(os.path.join(MESHY_CACHE, "pair_thumbs", f"{tid}.png")),
			"squaring": l.get("squaring"),          # per-model top-down correction
			# Cache-busting id for the GLB URL. Re-pointing a link at its textured model
			# changes this, so the browser cannot serve the untextured copy it already
			# stored under the old long-lived Cache-Control header.
			"model_rev": l.get("texture_task") or tid,
			# separation and tilt are per-model: both scale by that model's own
			# proportions, so a shared value lands differently on each generation
			"gap": l.get("gap"),
			"yaw": l.get("yaw"),
			"depth": l.get("depth"),
			"primary": bool(l.get("primary")),
			"alive": tid in tasks,
		})
	for r in rows.values():
		# an art file is "pairable" when its library art is split into hands
		r["pairable"] = any(g["hand"] for g in r["generations"])
		# Group hands into VARIANT sets: -l4 and -r4 are one redraw's two hands and
		# must be built together; mixing variants would pair two different designs.
		vsets = {}
		for g in r["generations"]:
			if not g["hand"]:
				continue
			v = vsets.setdefault(g["variant"] or "", {"variant": g["variant"] or "",
			                                          "label": glove_pairs.variant_label(g["variant"]),
			                                          "left": None, "right": None})
			if not v[g["hand"]]:
				v[g["hand"]] = g["task_id"]
		for v in vsets.values():
			v["complete"] = bool(v["left"] and v["right"])
		# complete sets first -- they need no mirroring
		r["variants"] = sorted(vsets.values(),
		                       key=lambda v: (not v["complete"], v["variant"]))
		# ALL re-imagined variants as slots: every variant the library has for this glove,
		# with the linked generation task where one exists and None where it's still to make.
		lib = _library_art_index().get(r["invfile"], {})
		gen_by = {(g["variant"] or "", g["hand"]): g["task_id"]
		          for g in r["generations"] if g["hand"]}
		all_vars = []
		for var in sorted(set(lib) | {g["variant"] or "" for g in r["generations"] if g["hand"]},
		                  key=_variant_sort_key):
			libv = lib.get(var, {})
			slot = {"variant": var, "label": glove_pairs.variant_label(var or None)}
			for hand in ("left", "right"):
				slot[hand] = {"art_file": libv.get(hand),          # redraw basename or None
				              "task_id": gen_by.get((var, hand))}   # generation or None
			slot["generated"] = bool(slot["left"]["task_id"] or slot["right"]["task_id"])
			all_vars.append(slot)
		r["all_variants"] = all_vars
		if r["pairable"]:
			# Fit the SAME variant the UI defaults to (variants are sorted complete-first).
			# Using link order instead computed the layout from one variant's art while the
			# dropdown displayed another's -- different silhouette, so the glove sat off the
			# border with padding that read as a positioning bug.
			dv = (r["variants"] or [{}])[0]
			tpl, src, notes = _effective_template(r["invfile"], _item(r["item_id"]),
			                                      left_task=dv.get("left"),
			                                      right_task=dv.get("right"))
			r["template"] = tpl
			r["template_source"] = src        # "saved" | "autofit" | "neutral"
			r["autofit_notes"] = notes
		else:
			r["template"] = None
		if r["pairable"]:
			# the tuner draws the OUTPUT bounds from this -- anything outside is clipped
			it0 = _item(r["item_id"]) or {}
			sz = glove_pairs.original_size(r["invfile"])
			r["out_size"] = list(sz) if sz else [it0.get("invwidth", 2) * assets.CELL_PX,
			                                     it0.get("invheight", 2) * assets.CELL_PX]
			r["cells"] = [it0.get("invwidth", 2), it0.get("invheight", 2)]
		r["has_left"] = any(g["hand"] == "left" for g in r["generations"])
		r["has_right"] = any(g["hand"] == "right" for g in r["generations"])
		gens = r["generations"]
		if gens and not any(g["primary"] for g in gens):
			gens[0]["primary"] = True   # default: first one wins until you choose
		gens.sort(key=lambda g: (not g["primary"], g["name"] or g["prompt"]))

	unpaired = [{
		"task_id": t["id"],
		"name": t.get("name") or "",
		"prompt": ((t.get("args") or {}).get("draft") or {}).get("prompt", ""),
		"input_image": meshy_links._task_input_url(t) or "",
		"preview": ((t.get("result") or {}).get("previewUrl") or ""),
	} for t in tasks.values()
		if t["id"] not in _LINKS and t.get("phase") in ("generate", "draft")
		and t.get("status") == "SUCCEEDED"]

	ignored = [{
		"task_id": tid,
		"name": (_LINKS[tid].get("name") or ""),
		"prompt": ((tasks.get(tid, {}).get("args") or {}).get("draft") or {}).get("prompt", ""),
		"input_image": meshy_links._task_input_url(tasks.get(tid, {})) or "",
	} for tid in _LINKS if _LINKS[tid].get("ignored")]

	return jsonify({"ok": True,
	                "pairs": sorted(rows.values(), key=lambda r: r["invfile"]),
	                "unpaired": unpaired, "ignored": ignored})


@flask_app.get("/api/meshy/tasks")
def api_meshy_tasks():
	"""Recent Meshy workspace tasks (slim), with any linked item, for the pairing UI."""
	page = int(request.args.get("page", 1))
	try:
		tasks = meshy_web.list_tasks(page_num=page, page_size=30)
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 502
	out = []
	for t in tasks:
		tid = t.get("id")
		link = _LINKS.get(tid)
		it = _item(link["item_id"]) if link else None
		out.append({
			"id": tid, "name": t.get("name") or "", "phase": t.get("phase"),
			"status": t.get("status"), "retryCount": t.get("retryCount") or 0,
			"createdAt": t.get("createdAt"),
			"preview": ((t.get("result") or {}).get("previewUrl") or ""),
			"linked_item": link["item_id"] if link else None,
			"linked_item_name": it["name"] if it else None,
		})
	return jsonify({"ok": True, "page": page, "tasks": out})


@flask_app.get("/api/meshy/links")
def api_meshy_links():
	out = []
	for tid, l in sorted(_LINKS.items(), key=lambda kv: kv[1].get("linked_at", ""), reverse=True):
		it = _item(l["item_id"])
		out.append(dict(l, task_id=tid, item_name=(it["name"] if it else "?")))
	return jsonify({"ok": True, "links": out})


@flask_app.post("/api/meshy/links")
def api_meshy_link():
	"""Manually pair a Meshy task with a catalog item (or confirm a scan suggestion).
	Recovers the task's registered image_id so texture/re-roll work on the pairing."""
	body = request.json or {}
	tid = body.get("task_id", ""); item_id = body.get("item_id", "")
	it = _item(item_id)
	if not it:
		return jsonify({"ok": False, "error": "no such item"}), 404
	try:
		t = meshy_web.get_task(tid)
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": f"no such task: {e}"}), 404
	_remember(tid, item_id, meshy_links._task_image_id(t), t.get("phase") or "draft",
	          body.get("source") or "manual", name=t.get("name") or "",
	          invfile=(body.get("invfile") or it["invfile"]).lower())
	return jsonify({"ok": True, "task_id": tid, "item_id": item_id, "item_name": it["name"],
	                "invfile": (body.get("invfile") or it["invfile"]).lower()})


@flask_app.delete("/api/meshy/links/<tid>")
def api_meshy_unlink(tid):
	_STUDIO.pop(tid, None)
	return jsonify({"ok": meshy_links.forget(_LINKS, tid)})


@flask_app.post("/api/meshy/links/scan")
def api_meshy_scan():
	"""Auto-pair unlinked drafts: image-hash matches link immediately; name matches
	come back as suggestions for one-click confirm. Body: {pages?: 2}."""
	pages = min(10, int((request.json or {}).get("pages", 2)))
	tasks = []
	try:
		for p in range(1, pages + 1):
			batch = meshy_web.list_tasks(page_num=p, page_size=30)
			if not batch:
				break
			tasks.extend(batch)
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 502
	res = meshy_links.auto_pair(tasks, catalog()["items"], _LINKS)
	for a in res["auto"]:  # mirror fresh links into the live studio map
		l = _LINKS[a["task_id"]]
		_STUDIO[a["task_id"]] = {"item_id": l["item_id"], "image_id": l.get("image_id"),
		                         "phase": l.get("phase", "draft")}
	return jsonify({"ok": True, "scanned": len(tasks), **res})


@flask_app.get("/api/game/status")
def api_game_status():
	res, err = _dbg("GET", "/asset/status", timeout=4)
	if err:
		return jsonify({"ok": False, "reachable": False, "error": err})
	return jsonify({"ok": True, "reachable": True, "asset": res})


@flask_app.post("/api/game/launch")
def api_game_launch():
	"""Start PD2 with the debugger (conformance build + -direct, D2Debugger on :8790) via the
	elevated -direct launcher (1 UAC). Fire-and-forget: returns immediately; the UI polls
	/api/game/status until the fresh process connects. Used by the 'game: not running' link."""
	try:
		subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
		                  "-File", os.path.abspath(RELAUNCH_SCRIPT)])
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 500
	return jsonify({"ok": True, "note": "launching — accept the UAC prompt; the game will connect shortly"})


# ==========================================================================
# AI upscale -> 3D workflow panel (Phase B: source + upscale stage)
# See UPSCALE_3D_PANEL_DESIGN.md. Endpoints are synchronous like the rest of the app; the
# ~11s SDXL generation blocks its request and the browser shows a spinner. Captioning is slow
# (CPU Ollama) so it runs in a background thread with a polled status.
# ==========================================================================
import threading as _threading  # noqa: E402
import time as _time  # noqa: E402

import app.boot_split as boot_split  # noqa: E402
import app.comfy as comfy  # noqa: E402
import app.enhance_recipes as enhance_recipes  # noqa: E402
import app.gen_prompts as gen_prompts  # noqa: E402
import app.gen_settings as gen_settings  # noqa: E402
import app.describe as describe  # noqa: E402
import app.upscale_store as upscale_store  # noqa: E402

_CAPTION_JOBS: dict[str, str] = {}   # item_id -> "running" | "error:<msg>"
_CAPTION_LOCK = _threading.Lock()


def _original_png_for(it: dict, *, tinted: bool = True) -> bytes:
	"""The same art the gallery shows as `original.png` (tinted uniques included).

	`tinted=False` is the tintless stock art -- what the generator and captioner are fed, so the
	art they produce carries no tint; each item sharing the DC6 applies its own at display time."""
	pal = _inv_tint_palette(it) if tinted else None
	return assets.dc6_to_png_bytes(
		assets.read_original_dc6(it.get("stock_invfile") or it["invfile"]), palette=pal)


_GLOVE_TYPES = {"glov", "tglv", "hglv", "mglv", "vglv"}
_BOOT_TYPES = {"boot", "tbot", "hbot", "mbot", "vbot"}


def _fallback_identity(it: dict) -> str:
	"""Prompt identity for an item with no saved caption.

	A base item's own name is already the noun the model needs ("Ancient Armor"), but a unique or
	set name usually is NOT -- "Occultist" names no object, and sending it bare left the model with
	nothing to draw (2026-07-29: it returned a plain silhouette). Name the base item alongside it
	so the prompt always contains a real noun.
	"""
	name = it.get("name") or "item"
	base = None
	if it.get("category") in ("unique", "set") and it.get("family"):
		base = (_item_by_code(it["family"]) or {}).get("name")
	# "Occultist (Light Gauntlets)" rather than "Occultist, a Light Gauntlets" -- base names are
	# often plural ("Gauntlets", "Boots"), so an article would read wrong half the time.
	what = f"{name} ({base})" if base and base.lower() != name.lower() else name
	return f"{what}, a Diablo II inventory item"

# Thin/elongated weapons: the Enhance picker's m7 recipe (margin pad + gem/glow category style)
# over-loosens a thin diagonal blade's silhouette (IoU ~0.85 -> ~0.70, measured in the
# fidelity-lab investigation over 57 items). These itemtypes pre-select m3 (identity + colour-
# lock, no pad/style) instead of m7 on FIRST open only -- never overrides a user's own
# last-used choice for that item (see is_thin_weapon below + gen_prompts.last_method).
_THIN_WEAPON_TYPES = {"swor", "2hcs", "knif", "staf", "spea", "jave", "wand", "pole", "sc9"}

# cat classification for enhance_recipes.run()'s category-style routing (gem/glow style vs.
# the plain house style). Mirrors _STACK_TYPES (defined later, near derive_stack_alternate)
# minus "rune" -- gems get the vivid/glossy style, runes don't.
_GEM_TYPES = {"gema", "gemd", "geme", "gemr", "gems", "gemz", "gemt",
              "jewl", "jewf"}          # jewels are faceted crystal too -- same treatment as gems
# Items that EMIT light. The plain house style ends in "no glow halo, no outline halo" (aimed at
# the background-cutter's edge fringe), and on emitted glow that measurably washes the art out --
# proven 2026-07-29 on Lightsabre, where the same caption+seed gave a pale ghost-ringed blade
# under the house style and a bold luminous one under GLOW_STYLE. Route anything whose whole
# identity is "it glows" here: runes, potions, charms, essences, torches.
_GLOW_TYPES = {"ubr",                                          # uber organs / essences
               "rune", "runs",                                 # runes + rune stacks
               "hpot", "mpot", "rpot", "apot", "spot", "wpot", "elix",   # potions / elixirs
               "lcha", "mcha", "scha", "lchp", "mchp", "schp", # charms (incl. PVP variants)
               "torc", "cm2f",                                 # torch + torch fragment
               "corr"}                                         # corrupted worldstone shard


def _is_thin(it) -> bool:
	return (it.get("type") or "").lower() in _THIN_WEAPON_TYPES


def _footprint_for(it) -> dict:
	"""(fill, dx, dy) reproducing the ORIGINAL art's size/position in its cell (assets."""
	f, dx, dy = assets.original_footprint(it.get("stock_invfile") or it["invfile"],
	                                      it["invwidth"], it["invheight"])
	return {"fill": f, "dx": dx, "dy": dy}


def _resolve_fit(it, fill, dx, dy):
	"""Resolve a request's fill/dx/dy. fill in (None, '', 'auto') -> the original-footprint numbers
	(and dx/dy default to the footprint's too); an explicit numeric fill always wins, with dx/dy
	defaulting to 0. Returns (fill, dx, dy, was_auto)."""
	auto = fill in (None, "", "auto")
	fp = _footprint_for(it)
	if auto:
		return (fp["fill"],
		        float(dx) if dx not in (None, "") else fp["dx"],
		        float(dy) if dy not in (None, "") else fp["dy"], True)
	return (float(fill),
	        float(dx) if dx not in (None, "") else 0.0,
	        float(dy) if dy not in (None, "") else 0.0, False)


@flask_app.get("/api/upscale/<path:item_id>/state")
def api_upscale_state(item_id):
	"""Panel bootstrap: description record + variant list + selection + Meshy chain state."""
	it = _item(item_id)
	if not it:
		return jsonify({"ok": False, "error": "no such item"}), 404
	with _CAPTION_LOCK:
		cap_status = _CAPTION_JOBS.get(item_id)
	is_glove = (it.get("type") or "").lower() in _GLOVE_TYPES
	is_boot = (it.get("type") or "").lower() in _BOOT_TYPES
	is_thin_weapon = (it.get("type") or "").lower() in _THIN_WEAPON_TYPES
	# Enhances are generated once per invfile; a deduped-out item shares a sibling's variants.
	owner_id, idx, inherited_from = _variant_owner(it)
	return jsonify({"ok": True,
	                "item": {"id": it["id"], "name": it["name"], "code": it["code"],
	                         "cells": [it["invwidth"], it["invheight"]], "type": it.get("type"),
	                         "invfile": it["invfile"].lower(),
	                         "shared_by": len(_shared_group().get((it["invfile"] or "").lower(), [])),
	                         "inherited_from": inherited_from,
	                         "is_glove": is_glove, "is_boot": is_boot,
	                         "is_thin_weapon": is_thin_weapon,
	                         "footprint": _footprint_for(it),
	                         "has_mask": is_glove and os.path.exists(_mask_path(it["invfile"])),
	                         "alts": assets.list_alternates(it["id"]),
	                         "active": assets.active_choice(it["id"])},
	                "description": describe.get(item_id),
	                "gen_prompts": gen_prompts.get(item_id),
	                "caption_status": cap_status,
	                "upscale": idx,
	                "meshy": idx.get("meshy") or {},
	                "boots": idx.get("boots") or {},
	                "blender": blender.available()})


def _boot_side_path(item_id: str, side: str) -> str:
	return os.path.join(upscale_store._dir(item_id), f"boots-{side}.png")


@flask_app.post("/api/boots/split/<path:item_id>")
def api_boots_split(item_id):
	"""Auto-split the selected upscale variant's master (fallback: original art) into left/right
	boots. Caches boots-left/right.png next to the variants and records method+confidence."""
	it = _item(item_id)
	if not it:
		return jsonify({"ok": False, "error": "no such item"}), 404
	store = _store_id(item_id)
	idx = upscale_store.load(store)
	vid = (request.json or {}).get("vid") or idx.get("selected")
	src = upscale_store.variant_png(store, vid, "master") if vid else None
	source = f"variant {vid}" if src else "original"
	if src is None:
		src = _original_png_for(it, tinted=False)
	r = boot_split.split(src)
	if not r["ok"]:
		return jsonify({"ok": False, "error": r["error"]}), 422
	os.makedirs(upscale_store._dir(store), exist_ok=True)
	with open(_boot_side_path(store, "left"), "wb") as f:
		f.write(r["left"])
	with open(_boot_side_path(store, "right"), "wb") as f:
		f.write(r["right"])
	idx["boots"] = {"method": r["method"], "confidence": r["confidence"], "source": source}
	upscale_store._write(store, idx)
	return jsonify({"ok": True, "boots": idx["boots"]})


@flask_app.get("/api/upscale/<path:item_id>/boot/<side>.png")
def api_boot_side_png(item_id, side):
	if side not in ("left", "right"):
		return "bad side", 400
	p = _boot_side_path(_store_id(item_id), side)
	if not os.path.exists(p):
		return "not split yet", 404
	with open(p, "rb") as f:
		return _processed_png(f.read(), key=f"boot-{item_id}-{side}")


@flask_app.put("/api/upscale/<path:item_id>/meshy")
def api_upscale_meshy_save(item_id):
	"""Persist the panel's Meshy chain state (draft/texture task ids, phase) into the item's
	upscale index so the panel survives reloads and server restarts."""
	store = _store_id(item_id)
	idx = upscale_store.load(store)
	cur = idx.get("meshy") or {}
	cur.update({k: v for k, v in (request.json or {}).items()
	            if k in ("draft_tid", "texture_tid", "phase", "source_vid", "alt_id",
	                     "alt_item", "boots_mode", "engine")})
	idx["meshy"] = cur
	upscale_store._write(store, idx)
	return jsonify({"ok": True, "meshy": cur})


@flask_app.put("/api/upscale/<path:item_id>/prompts")
def api_upscale_prompts_save(item_id):
	"""Persist the Enhance picker's per-item text (restyle/nudge/negative) + last-used method,
	so switching items or reloading the page never loses or bleeds this text (see gen_prompts.py)."""
	it = _item(item_id)
	if not it:
		return jsonify({"ok": False, "error": "no such item"}), 404
	rec = gen_prompts.update(item_id, request.json or {})
	return jsonify({"ok": True, "gen_prompts": rec})


@flask_app.post("/api/studio/generate-upscale")
def api_studio_generate_upscale():
	"""Image-to-3D draft fed from a workflow-panel upscale variant's hi-res MASTER (the
	1024px BiRefNet-cut RGBA). Body: {item_id, vid (optional; default = selected), opts,
	boots_mode: "multi" | "single" (boots only)}.

	Boots (decision #10): the sprite holds BOTH boots, so the draft is fed the auto-split
	sides — "multi" sends left+right as a 2-image draft (both views inform one model),
	"single" sends just the left boot (the pair render mirrors it later)."""
	body = request.json or {}
	item_id = body.get("item_id", "")
	it = _item(item_id)
	if not it:
		return jsonify({"ok": False, "error": "no such item"}), 404
	store = _store_id(item_id)
	idx = upscale_store.load(store)
	vid = body.get("vid") or idx.get("selected")
	if not vid:
		return jsonify({"ok": False, "error": "no upscale variant selected (generate one first)"}), 400
	master = upscale_store.variant_png(store, vid, "master")
	if master is None:
		return jsonify({"ok": False, "error": f"variant {vid} has no master image"}), 404
	boots_mode = body.get("boots_mode")
	try:
		if boots_mode in ("multi", "single"):
			# ensure a split exists for this variant (re-split when the source changed)
			cur = idx.get("boots") or {}
			if cur.get("source") != f"variant {vid}" or \
			   not os.path.exists(_boot_side_path(store, "left")):
				r = boot_split.split(master)
				if not r["ok"]:
					return jsonify({"ok": False, "error": f"boot split failed: {r['error']}"}), 422
				os.makedirs(upscale_store._dir(store), exist_ok=True)
				with open(_boot_side_path(store, "left"), "wb") as f:
					f.write(r["left"])
				with open(_boot_side_path(store, "right"), "wb") as f:
					f.write(r["right"])
				idx["boots"] = {"method": r["method"], "confidence": r["confidence"],
				                "source": f"variant {vid}"}
			with open(_boot_side_path(store, "left"), "rb") as f:
				left = f.read()
			if boots_mode == "multi":
				with open(_boot_side_path(store, "right"), "rb") as f:
					right = f.read()
				sprite = [assets.prep_image_for_meshy(left), assets.prep_image_for_meshy(right)]
			else:
				sprite = assets.prep_image_for_meshy(left)
			source = f"workflow-boots-{boots_mode}-{vid}"
		else:
			sprite = assets.prep_image_for_meshy(master)
			source = f"workflow-upscale-{vid}"
		tid = _draft_from_sprite(it, sprite, body.get("opts") or {}, source)
		idx["meshy"] = {"draft_tid": tid, "phase": "draft", "source_vid": vid,
		                **({"boots_mode": boots_mode} if boots_mode else {})}
		upscale_store._write(store, idx)
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 502
	return jsonify({"ok": True, "task_id": tid, "phase": "draft", "source_vid": vid,
	                "boots": idx.get("boots")})


def _sanitize_item_prompt(text: str, it: dict) -> str:
	"""Drop the tokens that make a general image model hallucinate: the item's own name (e.g.
	'Skull Cap' -> a literal skull), the 'Diablo II' branding, the type parenthetical, and the
	'inventory item' boilerplate. Leaves the physical description intact."""
	import re
	out = text or ""
	name = (it.get("name") or "").strip()
	if name:
		out = re.sub(re.escape(name), "", out, flags=re.IGNORECASE)
	out = re.sub(r"\bdiablo\s*(?:ii|2)?\b", "", out, flags=re.IGNORECASE)
	out = re.sub(r"\((?:helm|armor|weapon|shield|gloves|boots|belt|amulet|ring)\)", "", out, flags=re.IGNORECASE)
	out = re.sub(r"\binventory item\b", "", out, flags=re.IGNORECASE)
	out = re.sub(r"\s{2,}", " ", out).strip(" —-–,.;:")
	return out


@flask_app.post("/api/upscale/<path:item_id>/generate")
def api_upscale_generate(item_id):
	"""Run one Enhance-picker generation and store it as a new history-strip variant.

	Body: {method, nudge?, restyle?, negative?, seed?, shape_strength?}. `method` must be a key
	of enhance_recipes.METHOD_LABELS (m7 = "Best quality" is the default, proven best across the
	fidelity-lab investigation's 57-item sweep; m3 wins on thin weapons -- see
	_THIN_WEAPON_TYPES). Every result is scored against the original (enhance_recipes.score) and
	rejected before it reaches the strip if it's empty or badly off-target (auto-QA gate).
	"""
	it = _item(item_id)
	if not it:
		return jsonify({"ok": False, "error": "no such item"}), 404
	b = request.json or {}
	method = b.get("method", enhance_recipes.DEFAULT_METHOD)
	if method not in enhance_recipes.METHOD_LABELS:
		return jsonify({"ok": False, "error": f"unknown method {method!r}"}), 400
	nudge = (b.get("nudge") or "").strip()
	restyle = (b.get("restyle") or "").strip()
	negative = (b.get("negative") or "").strip()
	seed = int(b.get("seed") or 0)
	shape_strength = float(b.get("shape_strength") or 0.65)
	if method in ("sdxl_lock", "flux_lock") and not restyle:
		return jsonify({"ok": False, "error": "describe the new look first"}), 400
	try:
		orig = _original_png_for(it, tinted=False)   # tintless in -> tintless out (see docstring)
		t = (it.get("type") or "").lower()
		cat = "gem" if t in _GEM_TYPES else "glow" if t in _GLOW_TYPES else "control"
		identity = (describe.get(item_id) or {}).get("text") or _fallback_identity(it)
		master, meta = enhance_recipes.run(method, sprite_png=orig, identity=identity, cat=cat,
		                                   nudge=nudge, restyle=restyle, negative=negative,
		                                   seed=seed, shape_strength=shape_strength)
		scores = enhance_recipes.score(orig, master)
		if scores.get("iou", 0) == 0.0:
			return jsonify({"ok": False, "error": "generation produced an empty/undetectable "
			                "image — try again or a different method"}), 422
		# Structure floor, checked BEFORE the composite. On a colour-transfer recipe (m3/m7) the
		# composite is 70% iou+colour, and both are pinned high by the pipeline itself -- iou by the
		# original's alpha guide, colour by the transfer -- so a bare silhouette still scored ~0.78
		# and sailed through (2026-07-29, unique/Occultist). Gradient energy is the term nothing
		# downstream can fake, so it is what decides "is there actually an item in here".
		if scores.get("detail_ratio", 1.0) < 0.30:
			return jsonify({"ok": False, "score": scores,
			                "error": f"no internal detail ({scores['detail_ratio']:.2f}x the "
			                "original) — the model returned a flat shape, not the item; try "
			                "again or a different method"}), 422
		if scores["score"] < 0.35:
			return jsonify({"ok": False, "score": scores,
			                "error": f"low fidelity ({scores['score']:.2f}) — try again or a "
			                "different method"}), 422
		w, h = meta.pop("size")
		canonical = comfy.to_canonical_2x(master, (it["invwidth"] * assets.CELL_PX,
		                                            it["invheight"] * assets.CELL_PX))
		store = _store_id(item_id)
		rec = upscale_store.add_variant(store, master_png=master, canonical_png=canonical,
		                                meta={**meta, "method": method, "score": scores,
		                                      "size": [w, h], "ts": _time.time()})
		gen_prompts.update(item_id, {"nudge": nudge, "restyle": restyle, "negative": negative,
		                             "last_method": method})
	except enhance_recipes.RecipeError as e:
		return jsonify({"ok": False, "error": str(e)}), 400
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 500
	return jsonify({"ok": True, "variant": rec, "upscale": upscale_store.load(store)})


@flask_app.get("/api/upscale/<path:item_id>/variant/<vid>.png")
def api_upscale_variant_png(item_id, vid):
	which = "master" if request.args.get("master") else "canonical"
	png = upscale_store.variant_png(item_id, vid, which)
	if png is None:
		# shared-invfile art: fall back to the representative sibling that owns the variants
		it = _item(item_id)
		if it:
			owner_id, _idx, inherited_from = _variant_owner(it)
			if inherited_from:
				png = upscale_store.variant_png(owner_id, vid, which)
	if png is None:
		return "no such variant", 404
	return _processed_png(png, key=f"up-{item_id}-{vid}-{which}")


@flask_app.post("/api/upscale/<path:item_id>/select")
def api_upscale_select(item_id):
	vid = (request.json or {}).get("vid")
	return jsonify({"ok": True, "upscale": upscale_store.select_variant(_store_id(item_id), vid)})


@flask_app.delete("/api/upscale/<path:item_id>/variant/<vid>")
def api_upscale_variant_delete(item_id, vid):
	return jsonify({"ok": True, "upscale": upscale_store.delete_variant(_store_id(item_id), vid)})


def _accept2d_source(item_id, vid):
	"""(item, vid, master_png) for a §2 accept-as-art, or (None, err) on failure. The master is
	the ~1024px BiRefNet-cut RGBA — already background-free — so fitting it to the cell is all
	that's left. Falls back to the canonical (2x) PNG if no master is on disk. Shared-invfile
	items read the variants of the sibling that owns them (same as the variant PNG route)."""
	it = _item(item_id)
	if not it:
		return None, "no such item"
	owner_id, idx, _inherited = _variant_owner(it)
	vid = vid or idx.get("selected")
	if not vid:
		return None, "no variant selected (generate or pick one in §2 first)"
	png = upscale_store.variant_png(owner_id, vid, "master") or upscale_store.variant_png(owner_id, vid, "canonical")
	if png is None:
		return None, f"variant {vid} has no image on disk"
	return it, vid, png


@flask_app.get("/api/upscale/<path:item_id>/accept-2d/preview.png")
def api_upscale_accept2d_preview(item_id):
	"""What the DC6 will actually look like if you accept the selected §2 image as art: the
	variant master fitted into the item's cell grid and round-tripped through the exact same
	png -> DC6 -> png path shipping uses, so the confirm shows true palette + framing (no save)."""
	res = _accept2d_source(item_id, request.args.get("vid"))
	if res[0] is None:
		return res[1], 404
	it, _vid, png = res
	fill, dx, dy, _auto = _resolve_fit(it, request.args.get("fill", "auto"),
	                                   request.args.get("dx"), request.args.get("dy"))
	rot = float(request.args.get("rot") or 0.0)
	grade = {k: request.args.get(k) for k in ("brightness", "warmth", "saturation", "contrast", "hue")
	         if request.args.get(k) is not None} or None
	even = request.args.get("even_border") in ("1", "true")
	outline = request.args.get("outline", "1") not in ("0", "false")
	try:
		dc6_bytes = assets.png_to_item_dc6(png, it["invwidth"], it["invheight"], fill=fill,
		                                   dx=dx, dy=dy, rot=rot, grade=grade,
		                                   thin=_is_thin(it), even_border=even, outline=outline)
		out = assets.dc6_to_png_bytes(dc6_bytes, palette=_inv_tint_palette(it))
	except Exception as e:  # noqa: BLE001
		return f"preview error: {e}", 500
	return Response(out, mimetype="image/png")


@flask_app.post("/api/upscale/<path:item_id>/accept-2d")
def api_upscale_accept2d(item_id):
	"""Ship a §2 generated image straight to the item's art -- no 3D model / texture step. The
	variant master (background already cut) is fitted to the cell and saved as a new DC6 alternate.
	Like the workflow's 3D ship, it does NOT auto-activate (manual Activate keeps the current art
	until the user commits). Body: {vid?, fill|"auto"?, dx?, dy?, grade?}."""
	body = request.json or {}
	res = _accept2d_source(item_id, body.get("vid"))
	if res[0] is None:
		return jsonify({"ok": False, "error": res[1]}), 400
	it, vid, png = res
	fill, dx, dy, was_auto = _resolve_fit(it, body.get("fill", "auto"), body.get("dx"), body.get("dy"))
	rot = float(body.get("rot") or 0.0)
	even = bool(body.get("even_border"))
	outline = bool(body.get("outline", True))
	rec = next((v for v in _variant_owner(it)[1].get("variants", []) if v["id"] == vid), {})
	# explicit UI grade wins over the global accept-brightness default; hue passes straight through
	grade = {**(_accept_grade() or {}), **(body.get("grade") or {})} or None
	try:
		dc6_bytes = assets.png_to_item_dc6(png, it["invwidth"], it["invheight"], fill=fill,
		                                   dx=dx, dy=dy, rot=rot, grade=grade,
		                                   thin=_is_thin(it), even_border=even, outline=outline)
		alt_id = f"img-{vid}"
		assets.save_alternate_dc6(it["id"], alt_id, dc6_bytes)
		# provenance: the variant's full record (method/prompt/score/…) + the resolved fit/grade,
		# so the gallery inspector can show what made this and offer "use these settings".
		meta = {"source": "upscale-2d", "vid": vid, "mode": rec.get("mode"),
		        "fill": fill, "dx": dx, "dy": dy, "rot": rot, "fit_auto": was_auto, "grade": grade,
		        "even_border": even, "outline": outline, "ts": _time.time()}
		for k in ("engine", "seed", "method", "method_label", "instruction", "positive",
		          "negative", "restyle", "nudge", "score", "model", "protect_silhouette"):
			if rec.get(k) is not None:
				meta[k] = rec.get(k)
		assets.save_alt_provenance(it["id"], alt_id, render_png=png, meta=meta)
		# refresh the sprite in place if this alt happens to already be the active choice
		activated = assets.active_choice(it["id"]) == alt_id
		if activated:
			assets.activate(it["id"], it["invfile"], alt_id)
		stacked = derive_stack_alternate(it, png, alt_id, fill)  # auto-derive rune/gem stack
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 500
	return jsonify({"ok": True, "item_id": it["id"], "alt_id": alt_id, "activated": activated,
	                "stacked": stacked, "alts": assets.list_alternates(it["id"]),
	                "note": ("art updated" if activated
	                         else f"saved as alternate '{alt_id}' -- Activate it to ship")})


# ---- stacked-variant derivation (runes/gems) --------------------------------------------------
# A stacked rune/gem sprite IS the base art plus a fixed gold '+' badge (top-right). We never
# enhance stacks separately (that would let them drift from their base); instead we derive each
# stack DC6 from the base's enhanced master + the badge, so it always matches whatever variant is
# active on the base. Auto-runs at accept time; also exposed as a manual regen endpoint.
_STACK_TYPES = {"rune", "gema", "gemd", "geme", "gemr", "gems", "gemz", "gemt"}


def _accept_grade():
	"""Optional exposure lift applied when a master becomes a DC6, to recover brightness lost in
	generation. Driven by the user-editable accept_brightness setting; None when it's 1.0 (off)."""
	gb = gen_settings.get().get("accept_brightness", 1.0)
	return {"brightness": gb} if gb and abs(gb - 1.0) > 1e-3 else None


def _stack_item_for(it: dict):
	"""The STACK catalog item for a base rune/gem (code + 's'), or None."""
	if (it.get("type") or "").strip() not in _STACK_TYPES:
		return None
	return catalog().get("by_code", {}).get((it.get("code") or "") + "s")


def derive_stack_alternate(base_it: dict, master_png: bytes, alt_id: str, fill: float):
	"""If `base_it` is a stackable rune/gem, render its stack DC6 (base art + '+' badge) from the
	same master and save it as the stack item's alternate under the same alt_id. Returns the stack
	item id on success, else None."""
	st = _stack_item_for(base_it)
	if not st:
		return None
	dc6 = assets.png_to_item_dc6(master_png, st["invwidth"], st["invheight"], fill=fill, stack=True,
	                             grade=_accept_grade())
	assets.save_alternate_dc6(st["id"], alt_id, dc6)
	assets.save_alt_provenance(st["id"], alt_id, render_png=master_png,
	                           meta={"source": "stack-derived", "from": base_it["id"], "fill": fill})
	if assets.active_choice(st["id"]) == alt_id:      # keep an already-active stack in sync
		assets.activate(st["id"], st["invfile"], alt_id)
	return st["id"]


@flask_app.post("/api/upscale/<path:item_id>/stack")
def api_upscale_stack(item_id):
	"""Manually (re)derive the stacked variant from the item's currently selected enhanced art."""
	body = request.json or {}
	res = _accept2d_source(item_id, body.get("vid"))
	if res[0] is None:
		return jsonify({"ok": False, "error": res[1]}), 400
	it, vid, png = res
	if not _stack_item_for(it):
		return jsonify({"ok": False, "error": "this item has no stacked variant"}), 400
	try:
		sid = derive_stack_alternate(it, png, f"img-{vid}", float(body.get("fill", 0.94)))
	except Exception as e:  # noqa: BLE001
		return jsonify({"ok": False, "error": str(e)}), 500
	return jsonify({"ok": True, "stack_item": sid, "alts": assets.list_alternates(sid)})


@flask_app.get("/api/gen/settings")
def api_gen_settings_get():
	"""The live, user-editable generation settings + the exact assembled instructions they produce,
	plus the fixed DEFAULTS so the UI can show a 'reset' target. Full transparency: every parameter
	that goes into an image is here."""
	s = gen_settings.get()
	return jsonify({"ok": True, "settings": s, "defaults": gen_settings.DEFAULTS,
	                "preview": {"enhance": gen_settings.enhance_instruction(),
	                            "restyle": gen_settings.restyle_instruction("<your restyle text>")}})


@flask_app.put("/api/gen/settings")
def api_gen_settings_put():
	"""Patch one or more generation settings (style, negative, steps, gan, px, model,
	accept_brightness). Takes effect on the next image; no restart."""
	s = gen_settings.update(request.json or {})
	return jsonify({"ok": True, "settings": s,
	                "preview": {"enhance": gen_settings.enhance_instruction(),
	                            "restyle": gen_settings.restyle_instruction("<your restyle text>")}})


@flask_app.post("/api/gen/settings/reset")
def api_gen_settings_reset():
	return jsonify({"ok": True, "settings": gen_settings.reset()})


@flask_app.post("/api/describe/<path:item_id>")
def api_describe_generate(item_id):
	"""Kick off a caption in the background (CPU Ollama is slow). Poll /state for status/result."""
	it = _item(item_id)
	if not it:
		return jsonify({"ok": False, "error": "no such item"}), 404
	force = bool(request.args.get("force"))  # capture before the thread (request is thread-local)
	with _CAPTION_LOCK:
		if _CAPTION_JOBS.get(item_id) == "running":
			return jsonify({"ok": True, "status": "running"})
		_CAPTION_JOBS[item_id] = "running"

	def _job():
		try:
			png = _original_png_for(it, tinted=False)   # the caption drives the generation prompt
			describe.caption_and_store(item_id, png, ts=_time.time(), overwrite_user=force,
			                           item_name=it.get("name"), item_kind=it.get("type"))
			with _CAPTION_LOCK:
				_CAPTION_JOBS.pop(item_id, None)
		except Exception as e:  # noqa: BLE001
			with _CAPTION_LOCK:
				_CAPTION_JOBS[item_id] = f"error:{e}"

	_threading.Thread(target=_job, daemon=True).start()
	return jsonify({"ok": True, "status": "running"})


@flask_app.put("/api/describe/<path:item_id>")
def api_describe_save(item_id):
	text = (request.json or {}).get("text", "")
	rec = describe.set_text(item_id, text, source="user", ts=_time.time())
	return jsonify({"ok": True, "description": rec})


def _warm_char_archives():
	"""Open the base MPQs (esp. d2char.mpq, the last in the search order) once in the background,
	so the first Equipped preview doesn't pay the ~3s archive-open cost on the request thread."""
	try:
		assets.try_read_effective(r"data\global\chars\BA\TR\BATRLITNUHTH.dcc")
	except Exception:  # noqa: BLE001
		pass


if __name__ == "__main__":
	_port = int(os.environ.get("ASSET_STUDIO_PORT", "5001"))
	print(f"PD2 Asset Studio -> http://127.0.0.1:{_port}")
	import threading as _threading
	_threading.Thread(target=_warm_char_archives, daemon=True).start()
	flask_app.run(host="127.0.0.1", port=_port, debug=False, threaded=True)
