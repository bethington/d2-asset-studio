"""Tint (invtransform / chrtransform) editing for uniques and sets.

The tint cells are single signed bytes in the compiled .bin records (-1 = no tint, 0..20 = a
colors.txt row).  Reads the live MPQ chain; all writes go to a throw-away workspace.
Usage: python tests/test_tint_edit.py
"""

import os
import struct
import sys
import tempfile
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_TMP = tempfile.mkdtemp(prefix="asset_studio_test_")
os.environ["ASSET_STUDIO_WS"] = _TMP  # must be set before app.assets import

import app.excel as excel  # noqa: E402

PASS = 0
U, S = "uniqueitems", "setitems"


def ok(label):
	global PASS
	PASS += 1
	print(f"OK  {label}")


def _overlay_bytes(table):
	with open(excel._overlay_bin_path(excel.TABLES[table]), "rb") as f:
		return f.read()


def _diff_offsets(a, b):
	return [i for i in range(len(a)) if a[i] != b[i]]


def _reset_all():
	for table, name in ((U, "Darksight Helm"), (U, "Duskdeep"), (S, "Isenhart's Horns"),
	                    (S, "Tancred's Spine")):
		excel.set_tint(table, name, inv="stock", chr="stock")


def test_stock_tints_read_back():
	dark = excel.get_tint(U, "Darksight Helm")
	assert (dark["inv"], dark["chr"], dark["linked"]) == ("blac", "blac", True), dark
	dusk = excel.get_tint(U, "Duskdeep")   # on-body lgry but NO inventory tint -> not linked
	assert (dusk["inv"], dusk["chr"], dusk["linked"]) == ("", "lgry", False), dusk
	isen = excel.get_tint(S, "Isenhart's Horns")
	assert (isen["inv"], isen["chr"]) == ("lgld", "lgld"), isen
	sazabi = excel.get_tint(S, "Sazabi's Mental Sheath")
	assert (sazabi["inv"], sazabi["chr"]) == ("dblu", "dblu"), sazabi
	assert not dark["edited"]
	ok("stock tint bytes decode to colour codes ('' = none) with a linked flag")


def test_unique_edit_touches_only_the_two_tint_bytes():
	stock = excel.stock_bin()
	r = excel.set_tint(U, "Darksight Helm", inv="dgld", chr="dgld")
	assert (r["inv"], r["chr"]) == ("dgld", "dgld") and r["edited"]
	base = 4 + r["row"] * excel.REC_SIZE
	diffs = _diff_offsets(stock, _overlay_bytes(U))
	assert diffs == [base + 0x38, base + 0x39], [hex(d - base) for d in diffs]
	assert excel.get_unique("Darksight Helm")["invfile"] == "invfhlu", "invfile cell must be untouched"
	_reset_all()
	ok("unique tint edit changes exactly the chr/inv bytes")


def test_none_writes_minus_one():
	r = excel.set_tint(U, "Darksight Helm", inv="", chr=None)
	assert r["inv"] == "" and r["chr"] == "blac", "chr=None must leave the on-body tint alone"
	base = 4 + r["row"] * excel.REC_SIZE
	assert struct.unpack_from("<b", _overlay_bytes(U), base + 0x39)[0] == -1
	assert excel.tint_overrides(U)["Darksight Helm"] == {"invtransform": "none"}
	_reset_all()
	ok("None writes -1 (only the side asked for) and is recorded as 'none'")


def test_stock_reverts_and_drops_overlay():
	excel.set_tint(U, "Darksight Helm", inv="cred", chr="cred")
	excel.set_tint(U, "Duskdeep", chr="dgld")
	assert excel.get_tint(U, "Duskdeep")["chr"] == "dgld", "edits stack across rows"
	excel.set_tint(U, "Darksight Helm", inv="stock", chr="stock")
	assert excel.get_tint(U, "Darksight Helm")["edited"] is False
	assert excel.tint_overrides(U) == {"Duskdeep": {"chrtransform": "dgld"}}
	excel.set_tint(U, "Duskdeep", chr="stock")
	assert not os.path.exists(excel._overlay_bin_path(excel.TABLES[U])), "overlay must be removed"
	assert excel.tint_overrides(U) == {}
	ok("'stock' restores the bytes; a fully reverted table drops its overlay + manifest entries")


def test_setting_the_stock_colour_is_not_an_edit():
	excel.set_tint(U, "Darksight Helm", inv="blac", chr="blac")
	assert not os.path.exists(excel._overlay_bin_path(excel.TABLES[U]))
	assert excel.tint_overrides(U) == {}
	ok("writing a cell's own stock value leaves no overlay behind")


def test_set_items_use_their_own_bin():
	stock = excel.stock_bin(excel.TABLES[S])
	r = excel.set_tint(S, "Isenhart's Horns", inv="cred", chr="cred")
	assert (r["inv"], r["chr"]) == ("cred", "cred")
	assert not os.path.exists(excel._overlay_bin_path(excel.TABLES[U])), "uniqueitems.bin untouched"
	base = 4 + r["row"] * excel.TABLES[S].rec_size
	diffs = _diff_offsets(stock, _overlay_bytes(S))
	assert diffs == [base + 0x40, base + 0x41], [hex(d - base) for d in diffs]
	assert excel.tint_overrides(S) == {"Isenhart's Horns": {"invtransform": "cred", "chrtransform": "cred"}}
	_reset_all()
	assert not os.path.exists(excel._overlay_bin_path(excel.TABLES[S]))
	ok("set tint edits patch setitems.bin at 0x40/0x41 and revert cleanly")


def test_validation():
	for bad_call in (
		lambda: excel.set_tint(U, "Darksight Helm", inv="zzzz"),
		lambda: excel.set_tint(U, "Darksight Helm", inv="12"),
		lambda: excel.set_tint("armor", "Darksight Helm", inv="cred"),
	):
		try:
			bad_call()
			raise AssertionError("accepted a bad call")
		except ValueError:
			pass
	try:
		excel.set_tint(U, "No Such Unique XYZ", inv="cred")
		raise AssertionError("accepted unknown unique")
	except KeyError:
		pass
	assert not os.path.exists(excel._overlay_bin_path(excel.TABLES[U]))
	ok("validation rejects unknown colour codes / tables / names without writing")


def test_invfile_edits_still_work_and_coexist():
	excel.set_unique_field("Darksight Helm", "invfile", "invtintTest")
	excel.set_tint(U, "Darksight Helm", inv="cred", chr="cred")
	assert excel.unique_overrides()["Darksight Helm"] == {
		"invfile": "invtintTest", "invtransform": "cred", "chrtransform": "cred"}
	excel.set_unique_field("Darksight Helm", "invfile", "")
	assert excel.unique_overrides()["Darksight Helm"] == {"invtransform": "cred", "chrtransform": "cred"}
	excel.set_tint(U, "Darksight Helm", inv="stock", chr="stock")
	assert excel.unique_overrides() == {}
	ok("invfile edits and tint edits share a row's manifest entry independently")


# ---- server layer: catalog merge, /txt tint API, swatch previews, Equipped tab ----------------

def _url(item_id, tail):
	return "/api/item/" + urllib.parse.quote(item_id, safe="") + tail


ISEN, DARK = "set/Isenhart's Horns", "unique/Darksight Helm"
TANC = "set/Tancred's Spine"   # base Full Plate Mail: InvTrans 8 AND Transform 8 (fhl/xhl have Transform 0)


def _server():
	import app.server as server
	return server


def test_catalog_reflects_tint_edits():
	server = _server()
	server.invalidate_catalog()
	assert server._item(ISEN)["invtransform"] == "lgld"
	excel.set_tint(S, "Isenhart's Horns", inv="cred", chr="cred")
	excel.set_tint(U, "Darksight Helm", inv="none")
	server.invalidate_catalog()
	assert server._item(ISEN)["invtransform"] == "cred"
	assert server._item(DARK)["invtransform"] == "", "None must clear the catalog's tint"
	c = server.flask_app.test_client()
	listed = {i["id"]: i["invtransform"]
	          for q in ("isenhart", "darksight") for i in c.get(f"/api/items?q={q}").get_json()["items"]}
	assert listed[ISEN] == "cred" and listed[DARK] == "", "the gallery list must carry the edit"
	_reset_all()
	server.invalidate_catalog()
	assert server._item(ISEN)["invtransform"] == "lgld" and server._item(DARK)["invtransform"] == "blac"
	ok("catalog + /api/items carry tint edits (and reverts) for sets and uniques")


def test_txt_get_reports_tint_and_gating():
	server = _server()
	c = server.flask_app.test_client()
	r = c.get(_url(ISEN, "/txt"))
	assert r.status_code == 200, r.get_data(as_text=True)
	t = r.get_json()["tint"]
	assert (t["inv"], t["chr"], t["linked"], t["edited"]) == ("lgld", "lgld", True, False), t
	# Full Helm has Transform=0: its worn art is never recoloured, so only the inventory side can render
	assert t["can_inv"] is True and t["can_chr"] is False, t
	assert t["colors"][:3] == ["whit", "lgry", "dgry"] and len(t["colors"]) == 21, "colour list for the picker"
	tanc = c.get(_url(TANC, "/txt")).get_json()["tint"]
	assert tanc["can_inv"] is True and tanc["can_chr"] is True, "Full Plate Mail has Transform=8"
	u = c.get(_url(DARK, "/txt")).get_json()
	assert u["invfile"] == "invfhlu" or "invfile" in u, "unique GET keeps its invfile fields"
	assert u["tint"]["inv"] == "blac"
	assert c.get(_url("base/armor/fhl", "/txt")).status_code == 400
	ok("GET /txt returns the tint state (+ gating) for sets and uniques, 400 for bases")


def test_txt_post_tint_roundtrip():
	server = _server()
	c = server.flask_app.test_client()
	r = c.post(_url(ISEN, "/txt"), json={"field": "tint", "inv": "cred", "chr": "cred"})
	assert r.status_code == 200, r.get_data(as_text=True)
	j = r.get_json()
	assert j["ok"] and j["tint"]["inv"] == "cred" and j["tint"]["edited"], j
	assert server._item(ISEN)["invtransform"] == "cred", "the POST must invalidate the catalog"
	# split edit: only the on-body side
	j = c.post(_url(ISEN, "/txt"), json={"field": "tint", "chr": "none"}).get_json()
	assert (j["tint"]["inv"], j["tint"]["chr"], j["tint"]["linked"]) == ("cred", "", False), j
	r = c.post(_url(ISEN, "/txt"), json={"field": "tint", "inv": "stock", "chr": "stock"})
	assert r.get_json()["tint"]["edited"] is False
	bad = c.post(_url(ISEN, "/txt"), json={"field": "tint", "inv": "zzzz"})
	assert bad.status_code == 400 and bad.get_json()["ok"] is False
	own = c.post(_url(ISEN, "/txt"), json={"field": "invfile", "value": "invx"})
	assert own.status_code == 400, "own-invfile edits stay unique-only"
	assert not os.path.exists(excel._overlay_bin_path(excel.TABLES[S]))
	ok("POST /txt field=tint: set/none/stock, split sides, validation, sets stay tint-only")


def _first_with_inv_trans(zero):
	server = _server()
	for it in server.catalog()["items"]:
		if it["category"] in ("unique", "set") and (it.get("inv_trans", 0) == 0) == zero:
			return it
	return None


def test_tint_preview_png():
	from app import assets
	server = _server()
	c = server.flask_app.test_client()
	it = server._item(ISEN)
	dc6 = assets.read_original_dc6(it["invfile"])
	for code in ("cred", "dblu"):
		got = c.get(_url(ISEN, f"/tint/{code}.png"))
		assert got.status_code == 200 and got.mimetype == "image/png"
		want = assets.dc6_to_png_bytes(dc6, palette=assets.item_transform_palette(it["inv_trans"], code))
		assert got.data == want, f"{code} preview must equal the game's remap of the art"
	none = c.get(_url(ISEN, "/tint/none.png"))
	assert none.data == assets.dc6_to_png_bytes(dc6), "'none' previews the untinted art"
	assert c.get(_url(ISEN, "/tint/zzzz.png")).status_code == 400
	untintable = _first_with_inv_trans(zero=True)
	assert untintable is not None, "expected at least one unique/set on a base with InvTrans=0"
	assert c.get(_url(untintable["id"], "/tint/cred.png")).status_code == 409
	ok("tint preview = the item's art through the game's remap; bad code 400, InvTrans=0 base 409")


def test_tint_preview_uses_the_active_alternate():
	from app import assets
	server = _server()
	c = server.flask_app.test_client()
	it = server._item(ISEN)
	alt = assets.read_original_dc6("invcap")   # any other art, so it differs from the original
	assets.save_alternate_dc6(ISEN, "t1", alt)
	assets.activate(ISEN, it["invfile"], "t1")
	got = c.get(_url(ISEN, "/tint/cred.png")).data
	want = assets.dc6_to_png_bytes(alt, palette=assets.item_transform_palette(it["inv_trans"], "cred"))
	assert got == want, "swatches must preview the art that is active in-game, not the original"
	assets.activate(ISEN, it["invfile"], "original")
	ok("swatch previews follow the active alternate")


def test_worn_colormap_honours_chr_override():
	from app import assets
	from pyd2 import chars, colortransform
	name = "Tancred's Spine"
	stock = chars.worn_colormap("ful", "set", name)
	assert stock is not None, "stock on-body tint should produce a colormap"
	excel.set_tint(S, name, chr="none")
	assert chars.worn_colormap("ful", "set", name) is None
	excel.set_tint(S, name, chr="cred")
	got = chars.worn_colormap("ful", "set", name)
	want = colortransform.mix_palette(chars.base_transform("ful"), colortransform.COLOR_INDEX["cred"],
	                                  reader=assets.try_read_effective)
	assert got == want and got != stock
	excel.set_tint(S, name, chr="stock")
	assert chars.worn_colormap("ful", "set", name) == stock
	assert chars.worn_colormap("fhl", "set", "Isenhart's Horns") is None, "Transform=0 base never recolours"
	ok("Equipped worn colormap follows the on-body tint override (none / recolour / stock)")


def test_equipped_gif_not_stale_after_tint_edit():
	server = _server()
	c = server.flask_app.test_client()
	first = c.get(_url(TANC, "/equipped.gif?cls=barbarian&mode=NU&dir=0&body=0"))
	if first.status_code != 200:
		print(f"SKIP equipped cache test (no equipped art here: {first.status_code})")
		return
	excel.set_tint(S, "Tancred's Spine", chr="cred")
	after = c.get(_url(TANC, "/equipped.gif?cls=barbarian&mode=NU&dir=0&body=0"))
	assert after.status_code == 200 and after.data != first.data, "stale cached GIF after a tint change"
	excel.set_tint(S, "Tancred's Spine", chr="stock")
	ok("Equipped GIF cache does not serve a stale tint")


if __name__ == "__main__":
	test_stock_tints_read_back()
	test_unique_edit_touches_only_the_two_tint_bytes()
	test_none_writes_minus_one()
	test_stock_reverts_and_drops_overlay()
	test_setting_the_stock_colour_is_not_an_edit()
	test_set_items_use_their_own_bin()
	test_validation()
	test_invfile_edits_still_work_and_coexist()
	test_catalog_reflects_tint_edits()
	test_txt_get_reports_tint_and_gating()
	test_txt_post_tint_roundtrip()
	test_tint_preview_png()
	test_tint_preview_uses_the_active_alternate()
	test_worn_colormap_honours_chr_override()
	test_equipped_gif_not_stale_after_tint_edit()
	print(f"\nall {PASS} checks passed (workspace: {_TMP})")
