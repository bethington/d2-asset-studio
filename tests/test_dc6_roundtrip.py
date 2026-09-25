"""DC6 codec round-trip check against real extracted files.

Usage: python tests/test_dc6_roundtrip.py file1.dc6 [file2.dc6 ...]
For each file asserts decode(encode(decode(f))) is pixel-identical to
decode(f) and that frame geometry survives.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyd2 import dc6  # noqa: E402


def roundtrip(path):
	with open(path, "rb") as f:
		original_bytes = f.read()
	a = dc6.decode(original_bytes)
	reencoded = dc6.encode(a)
	b = dc6.decode(reencoded)
	assert a.directions == b.directions and a.frames_per_direction == b.frames_per_direction
	for fa, fb in zip(a.frames, b.frames):
		assert (fa.width, fa.height, fa.offset_x, fa.offset_y, fa.flip) == \
		       (fb.width, fb.height, fb.offset_x, fb.offset_y, fb.flip), "frame header drift"
		assert fa.pixels == fb.pixels, "pixel drift"
	frames = len(a.frames)
	print(f"OK  {os.path.basename(path):20} dirs={a.directions} fpd={a.frames_per_direction} "
	      f"frames={frames} first={a.frames[0].width}x{a.frames[0].height} "
	      f"orig={len(original_bytes)}B reenc={len(reencoded)}B")


if __name__ == "__main__":
	failures = 0
	for p in sys.argv[1:]:
		try:
			roundtrip(p)
		except Exception as e:  # noqa: BLE001
			failures += 1
			print(f"FAIL {p}: {e}")
	sys.exit(1 if failures else 0)
