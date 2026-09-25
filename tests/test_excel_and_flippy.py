"""Offline verification for the txt sliver (uniqueitems.bin cell edits) and
flippy authoring (multi-frame DC6 encode matched to the original's geometry).

Runs against the live MPQ chain read-only; all writes go to a throw-away
workspace.  Usage: python tests/test_excel_and_flippy.py
"""

import io
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_TMP = tempfile.mkdtemp(prefix="asset_studio_test_")
os.environ["ASSET_STUDIO_WS"] = _TMP  # must be set before app.assets import

import app.assets as assets  # noqa: E402
import app.excel as excel  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from pyd2 import dc6  # noqa: E402

PASS = 0


def ok(label):
	global PASS
	PASS += 1
	print(f"OK  {label}")


def test_bin_integrity():
	data = excel.stock_bin()
	n = struct.unpack_from("<i", data, 0)[0]
	assert len(data) == 4 + n * excel.REC_SIZE, "record size drift vs UniqueItemsTxt"
	row = excel.find_row(data, "Harlequin Crest")
	assert row >= 0, "Harlequin Crest not found"
	u = excel.get_unique("Harlequin Crest")
	assert u["stock_invfile"] == "", "expected blank (inherited) stock invfile"
	ok(f"bin integrity: {n} records x 0x{excel.REC_SIZE:X}, Harlequin Crest @ row {row}")


def test_edit_and_revert():
	stock = excel.stock_bin()
	u = excel.set_unique_field("Harlequin Crest", "invfile", "invshakoU")
	assert u["invfile"] == "invshakoU"
	p = excel._overlay_bin_path()
	assert os.path.exists(p), "overlay bin not written"
	with open(p, "rb") as f:
		edited = f.read()
	assert len(edited) == len(stock), "size changed"
	row = u["row"]
	cell = 4 + row * excel.REC_SIZE + excel.OFF_INVFILE
	# only the 32-byte cell differs from stock
	diffs = [i for i in range(len(stock)) if stock[i] != edited[i]]
	assert diffs and all(cell <= i < cell + excel.STR_LEN for i in diffs), \
		f"unexpected bytes changed outside the invfile cell: {diffs[:5]}"
	assert edited[cell:cell + 10] == b"invshakoU\x00"
	ok("invfile edit touches exactly the one cell")

	# stack a second edit on another row, then revert both
	excel.set_unique_field("Windforce", "flippyfile", "flpTest")
	assert excel.get_unique("Windforce")["flippyfile"] == "flpTest"
	assert excel.get_unique("Harlequin Crest")["invfile"] == "invshakoU", "edits must stack"
	ok("edits stack across rows")

	excel.set_unique_field("Windforce", "flippyfile", "")
	excel.set_unique_field("Harlequin Crest", "invfile", "")
	assert not os.path.exists(p), "overlay bin should be removed after full revert"
	assert excel.unique_overrides() == {}, "manifest edits should be empty"
	ok("full revert removes the overlay bin + manifest entries")


def test_edit_validation():
	for bad in ("way_too_long_for_a_dc6_basename_x", "spa ce", "semi;colon"):
		try:
			excel.set_unique_field("Harlequin Crest", "invfile", bad)
			raise AssertionError(f"accepted bad value {bad!r}")
		except ValueError:
			pass
	try:
		excel.set_unique_field("No Such Unique XYZ", "invfile", "x")
		raise AssertionError("accepted unknown unique")
	except KeyError:
		pass
	ok("validation rejects bad values and unknown uniques")


def _dummy_frame_png(i, size=96):
	img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
	d = ImageDraw.Draw(img)
	d.ellipse([8, 8, size - 8, size - 8], fill=(200, 60 + i * 8 % 180, 40, 255))
	buf = io.BytesIO()
	img.save(buf, format="PNG")
	return buf.getvalue()


def test_flippy_encode():
	ref_bytes = assets.read_original_dc6("flpcap")
	ref = dc6.decode(ref_bytes)
	pngs = [_dummy_frame_png(i) for i in range(len(ref.frames))]
	out = assets.pngs_to_flippy_dc6(pngs, ref_bytes)
	got = dc6.decode(out)
	assert got.directions == ref.directions
	assert got.frames_per_direction == ref.frames_per_direction
	box = max(max(f.width, f.height) for f in ref.frames) + 4
	for gf, rf in zip(got.frames, ref.frames):
		assert gf.width == box and gf.height == box
		assert gf.offset_y == rf.offset_y, "fall-arc anchor must be preserved"
		assert gf.offset_x == rf.offset_x - (box - rf.width) // 2, "center correction"
	# frame-count mismatch must be rejected
	try:
		assets.pngs_to_flippy_dc6(pngs[:-1], ref_bytes)
		raise AssertionError("accepted wrong frame count")
	except ValueError:
		pass
	# and the result round-trips through the codec
	again = dc6.decode(dc6.encode(got))
	assert [f.pixels for f in again.frames] == [f.pixels for f in got.frames]
	ok(f"flippy encode: {len(got.frames)} frames @ {box}x{box}, arc offsets preserved, round-trips")


def test_flippy_gif_preview():
	gif = assets.dc6_to_gif_bytes(assets.read_original_dc6("flpcap"))
	assert gif[:6] in (b"GIF87a", b"GIF89a") and len(gif) > 500
	ok(f"animated GIF preview renders ({len(gif)}B)")


def test_activate_flippy_roundtrip():
	ref_bytes = assets.read_original_dc6("flpcap")
	ref = dc6.decode(ref_bytes)
	pngs = [_dummy_frame_png(i) for i in range(len(ref.frames))]
	out = assets.pngs_to_flippy_dc6(pngs, ref_bytes)
	assets.save_flippy_alternate_dc6("unique/Test Item", "tumble-1", out)
	assert assets.list_flippy_alternates("unique/Test Item") == ["tumble-1"]
	assets.activate_flippy("unique/Test Item", "flptest", "tumble-1")
	dest = os.path.join(assets.OVERLAY, "data", "global", "items", "flptest.dc6")
	assert os.path.exists(dest)
	assert assets.active_flippy_choice("unique/Test Item") == "tumble-1"
	assets.activate_flippy("unique/Test Item", "flptest", "original")
	assert not os.path.exists(dest)
	assert assets.active_flippy_choice("unique/Test Item") == "original"
	ok("flippy alternate save/activate/revert against the overlay + manifest")


if __name__ == "__main__":
	test_bin_integrity()
	test_edit_and_revert()
	test_edit_validation()
	test_flippy_encode()
	test_flippy_gif_preview()
	test_activate_flippy_roundtrip()
	print(f"\nall {PASS} checks passed (workspace: {_TMP})")
