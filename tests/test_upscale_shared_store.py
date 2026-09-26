"""Items that share an invfile share one Enhance history strip.

Enhances are generated once per invfile; an item that resolves to the same DC6 as a sibling reads
that sibling's variants (server._variant_owner). Every write route must therefore target the same
store the read side resolves to -- otherwise the first generation on the inheriting item forks a
fresh store and the sibling's history vanishes from view (the new "v1" replaces the old "v1").

Generator, catalog and workspace are all faked; nothing here touches MPQs, ComfyUI or Meshy.
Usage: python tests/test_upscale_shared_store.py
"""

import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_TMP = tempfile.mkdtemp(prefix="asset_studio_test_")
os.environ["ASSET_STUDIO_WS"] = _TMP  # must be set before app.assets import

from PIL import Image  # noqa: E402

import app.comfy as comfy  # noqa: E402
import app.enhance_recipes as enhance_recipes  # noqa: E402
import app.gen_prompts as gen_prompts  # noqa: E402
import app.server as server  # noqa: E402
import app.upscale_store as upscale_store  # noqa: E402

PASS = 0


def ok(label):
	global PASS
	PASS += 1
	print(f"OK  {label}")


def _png(color=(255, 0, 0, 255)):
	b = io.BytesIO()
	Image.new("RGBA", (64, 64), color).save(b, "PNG")
	return b.getvalue()


def _item(item_id, invfile):
	return {"id": item_id, "name": item_id.split("/")[-1], "code": "xxx", "category": "unique",
	        "type": "helm", "invfile": invfile, "flippyfile": "", "invwidth": 2, "invheight": 2}


A, B, C = "unique/Alpha", "unique/Bravo", "unique/Charlie"   # A and B share invfile "invshared"
LONER = "unique/Loner"                                        # own invfile


def _setup():
	"""Fresh store + fake catalog + mocked generator for one test."""
	upscale_store.ROOT = os.path.join(tempfile.mkdtemp(dir=_TMP), "upscales")
	items = [_item(A, "invshared"), _item(B, "InvShared"), _item(C, "invshared"), _item(LONER, "invloner")]
	server._CATALOG["items"] = items
	server._CATALOG["by_id"] = {it["id"]: it for it in items}
	enhance_recipes.run = lambda *a, **k: (_png(), {"size": (64, 64), "seed": 1})
	enhance_recipes.score = lambda o, m: {"score": 0.9, "iou": 0.9, "detail_ratio": 1.0,
	                                      "color": 0.9, "ssim": 0.9}
	comfy.to_canonical_2x = lambda m, size: m
	gen_prompts.update = lambda *a, **k: {}
	server._original_png_for = lambda it, **k: _png((0, 0, 255, 255))
	return server.flask_app.test_client()


def _gen(c, item_id):
	r = c.post(f"/api/upscale/{item_id}/generate", json={"method": "m7"}).get_json()
	assert r["ok"], r
	return r


def _ids(up):
	return [v["id"] for v in up["variants"]]


def _state(c, item_id):
	return c.get(f"/api/upscale/{item_id}/state").get_json()


def test_generate_on_inheriting_item_appends_to_shared_history():
	c = _setup()
	_gen(c, A)
	r = _gen(c, B)  # B inherits A's v1; generating must append, not fork
	assert _ids(r["upscale"]) == ["v1", "v2"], f"B's generate response lost history: {_ids(r['upscale'])}"
	assert _ids(_state(c, B)["upscale"]) == ["v1", "v2"]
	assert _ids(_state(c, A)["upscale"]) == ["v1", "v2"], "owner must see the sibling's new image too"
	assert _ids(_state(c, C)["upscale"]) == ["v1", "v2"]
	assert upscale_store.load(B)["variants"] == [], "inheriting item must not grow a store of its own"
	ok("generate on an inheriting item appends to the shared history (no fork)")


def test_first_generation_in_a_group_still_works():
	c = _setup()
	r = _gen(c, B)  # nobody in the group has variants yet: B becomes the owner
	assert _ids(r["upscale"]) == ["v1"]
	assert _ids(_state(c, A)["upscale"]) == ["v1"]
	assert _state(c, A)["item"]["inherited_from"] == B
	ok("first generation in a group creates the shared history")


def test_select_on_inheriting_item():
	c = _setup()
	_gen(c, A)
	_gen(c, A)
	r = c.post(f"/api/upscale/{B}/select", json={"vid": "v1"}).get_json()
	assert _ids(r["upscale"]) == ["v1", "v2"] and r["upscale"]["selected"] == "v1", r
	assert _state(c, A)["upscale"]["selected"] == "v1"
	ok("select on an inheriting item changes the shared selection")


def test_delete_on_inheriting_item():
	c = _setup()
	_gen(c, A)
	_gen(c, A)
	r = c.delete(f"/api/upscale/{B}/variant/v1").get_json()
	assert _ids(r["upscale"]) == ["v2"], r
	assert _ids(_state(c, A)["upscale"]) == ["v2"]
	assert upscale_store.variant_png(A, "v1") is None, "file must be removed from the owner's store"
	ok("delete on an inheriting item removes the shared variant")


def test_meshy_state_follows_the_shared_store():
	c = _setup()
	_gen(c, A)
	r = c.put(f"/api/upscale/{B}/meshy", json={"draft_tid": "task-1", "phase": "draft"}).get_json()
	assert r["ok"]
	assert _state(c, B)["meshy"].get("draft_tid") == "task-1", "state reads what meshy-save wrote"
	assert _state(c, A)["meshy"].get("draft_tid") == "task-1"
	ok("meshy chain state saved via an inheriting item is read back")


def test_unrelated_item_keeps_its_own_history():
	c = _setup()
	_gen(c, LONER)
	_gen(c, A)
	_gen(c, B)
	assert _ids(_state(c, LONER)["upscale"]) == ["v1"]
	assert _ids(_state(c, A)["upscale"]) == ["v1", "v2"]
	ok("items outside the shared group are unaffected")


if __name__ == "__main__":
	test_generate_on_inheriting_item_appends_to_shared_history()
	test_first_generation_in_a_group_still_works()
	test_select_on_inheriting_item()
	test_delete_on_inheriting_item()
	test_meshy_state_follows_the_shared_store()
	test_unrelated_item_keeps_its_own_history()
	print(f"\nall {PASS} checks passed (workspace: {_TMP})")
