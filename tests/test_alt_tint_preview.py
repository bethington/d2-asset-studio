"""A tinted unique/set (invtransform) shows its tint on every piece of item art, while the art
the generator makes and is fed stays tintless.

The game tints the 8-bit inventory sprite by palette-index remap (invtransform colour code +
the base's InvTrans), so generated base art must be tintless -- each item sharing the DC6 applies
its own tint at display time / in-game. Previews of an alternate therefore have to apply the item's
tint themselves (the gallery tile already did; the alternate previews did not).

Runs against the live MPQ chain read-only; all writes go to a throw-away workspace.
Usage: python tests/test_alt_tint_preview.py
"""

import io
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_TMP = tempfile.mkdtemp(prefix="asset_studio_test_")
os.environ["ASSET_STUDIO_WS"] = _TMP  # must be set before app.assets import

from PIL import Image, ImageDraw  # noqa: E402

import app.assets as assets  # noqa: E402
import app.comfy as comfy  # noqa: E402
import app.describe as describe  # noqa: E402
import app.enhance_recipes as enhance_recipes  # noqa: E402
import app.gen_prompts as gen_prompts  # noqa: E402
import app.server as server  # noqa: E402
import app.upscale_store as upscale_store  # noqa: E402

PASS = 0
TINTED = "unique/Vampiregaze"   # invtransform cgrn on a Bone Helm (InvTrans 8)
ALT = "tt1"


def ok(label):
	global PASS
	PASS += 1
	print(f"OK  {label}")


def _art_png():
	"""Grey-ramp blob on transparent: the kind of art an index remap visibly recolours."""
	im = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
	d = ImageDraw.Draw(im)
	for i in range(100):
		g = 40 + int(i * 2)
		d.ellipse([28 + i // 2, 28 + i // 2, 228 - i // 2, 228 - i // 2], fill=(g, g, g, 255))
	b = io.BytesIO()
	im.save(b, "PNG")
	return b.getvalue()


def _client():
	return server.flask_app.test_client()


def _item(item_id=TINTED):
	return server._item(item_id)


def _import_alt(c, item_id, raw):
	r = c.post(f"/api/item/{item_id}/import", data={"alt_id": ALT, "file": (io.BytesIO(raw), "a.png")},
	           content_type="multipart/form-data")
	assert r.get_json()["ok"], r.get_data(as_text=True)


def _pixels(png_bytes):
	im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
	return im.size, {p[:3] for p in im.getdata() if p[3] > 0}


def _untinted(monkeypatch_fn):
	"""Call monkeypatch_fn() with tinting switched off (server._inv_tint_palette -> None)."""
	real = server._inv_tint_palette
	server._inv_tint_palette = lambda it: None
	try:
		return monkeypatch_fn()
	finally:
		server._inv_tint_palette = real


def test_fixture_item_really_is_tinted():
	it = _item()
	assert it and server._inv_tint_palette(it) is not None, "Vampiregaze should carry a tint"
	stock = assets.dc6_to_png_bytes(assets.read_original_dc6(it["invfile"]))
	assert server._original_png_for(it) != stock, "display 'original' must stay tinted"
	ok("fixture: Vampiregaze has a tint that changes its art")


def test_alt_png_is_tinted():
	c = _client()
	it = _item()
	_import_alt(c, TINTED, _art_png())
	pal = server._inv_tint_palette(it)
	dc6 = assets.alt_dc6_bytes(TINTED, ALT)
	got = c.get(f"/api/item/{TINTED}/alt/{ALT}.png").data
	assert got == assets.dc6_to_png_bytes(dc6, palette=pal), "alt thumbnail must apply the item's tint"
	assert got != assets.dc6_to_png_bytes(dc6), "sanity: the tint changes this art"
	ok("alternate thumbnail (alt/<id>.png) applies the item's tint")


def test_untinted_item_alt_png_unchanged():
	c = _client()
	base = next(i for i in server.catalog()["items"] if i["category"] == "base")
	assert server._inv_tint_palette(base) is None
	_import_alt(c, base["id"], _art_png())
	got = c.get(f"/api/item/{base['id']}/alt/{ALT}.png").data
	assert got == assets.dc6_to_png_bytes(assets.alt_dc6_bytes(base["id"], ALT))
	ok("items without a tint render alternates exactly as before")


def test_accept2d_preview_is_tinted():
	c = _client()
	it = _item()
	pal = {tuple(p) for p in server._inv_tint_palette(it)}
	upscale_store.add_variant(TINTED, master_png=_art_png(), canonical_png=_art_png(), meta={"ts": 1})
	url = f"/api/upscale/{TINTED}/accept-2d/preview.png?vid=v1"
	tinted = c.get(url).data
	plain = _untinted(lambda: c.get(url).data)
	assert tinted != plain
	size, colors = _pixels(tinted)
	assert colors <= pal, "every sprite pixel must come from the tinted palette"
	assert _pixels(plain)[0] == size
	ok("accept-as-art preview applies the item's tint")


def test_alt_cell_preview_is_tinted():
	c = _client()
	it = _item()
	_import_alt(c, TINTED, _art_png())
	pal = {tuple(p) for p in server._inv_tint_palette(it)}
	url = f"/api/item/{TINTED}/alt/{ALT}/cell.png?fill=0.9&dx=0&dy=0"
	tinted = c.get(url).data
	plain = _untinted(lambda: c.get(url).data)
	assert tinted != plain
	size, colors = _pixels(tinted)
	assert size == _pixels(plain)[0], "tinted framing preview keeps the cell-grid layout"
	backdrop = {(46, 20, 20), (90, 60, 60)}   # reddish cell bg + grid lines
	assert colors <= pal | backdrop, "sprite pixels must come from the tinted palette"
	ok("framing/adjust preview (alt cell.png) applies the item's tint")


def test_items_api_flags_tinted_items():
	c = _client()
	rows = {r["id"]: r for r in c.get("/api/items?q=vampiregaze").get_json()["items"]}
	assert rows[TINTED]["tinted"] is True
	base = next(r for r in c.get("/api/items?category=base").get_json()["items"])
	assert base["tinted"] is False
	ok("/api/items flags items that carry an active tint (client shows the sprite, not hi-res)")


def _stub_generator():
	seen = {}

	def run(method, *, sprite_png, **k):
		seen["sprite"] = sprite_png
		return _art_png(), {"size": (256, 256), "seed": 1}
	enhance_recipes.run = run
	enhance_recipes.score = lambda o, m: {"score": 0.9, "iou": 0.9, "detail_ratio": 1.0,
	                                      "color": 0.9, "ssim": 0.9}
	comfy.to_canonical_2x = lambda m, size: m
	gen_prompts.update = lambda *a, **k: {}
	return seen


def test_generation_source_is_untinted():
	c = _client()
	it = _item()
	seen = _stub_generator()
	r = c.post(f"/api/upscale/{TINTED}/generate", json={"method": "m7"}).get_json()
	assert r["ok"], r
	stock = assets.dc6_to_png_bytes(assets.read_original_dc6(it["invfile"]))
	assert seen["sprite"] == stock, "generator must be fed the untinted stock art"
	assert seen["sprite"] != server._original_png_for(it)
	ok("Enhance generation is fed the untinted stock art")


def test_caption_source_is_untinted():
	c = _client()
	it = _item()
	got = {}
	describe.caption_and_store = lambda item_id, png, **k: got.setdefault("png", png)
	assert c.post(f"/api/describe/{TINTED}?force=1").get_json()["ok"]
	for _ in range(100):
		if "png" in got:
			break
		time.sleep(0.05)
	stock = assets.dc6_to_png_bytes(assets.read_original_dc6(it["invfile"]))
	assert got.get("png") == stock, "caption (which drives the prompt) must describe the untinted art"
	ok("captioning describes the untinted stock art")


if __name__ == "__main__":
	test_fixture_item_really_is_tinted()
	test_alt_png_is_tinted()
	test_untinted_item_alt_png_unchanged()
	test_accept2d_preview_is_tinted()
	test_alt_cell_preview_is_tinted()
	test_items_api_flags_tinted_items()
	test_generation_source_is_untinted()
	test_caption_source_is_untinted()
	print(f"\nall {PASS} checks passed (workspace: {_TMP})")
