"""D2 palette (pal.dat) loading and DC6 -> RGBA rendering.

pal.dat = 256 entries x 3 bytes, stored B,G,R on disk — verified
empirically in Phase 0 (healing potion red / mana potion blue only under
BGR; see AssetStudioPlan.md).
"""

from __future__ import annotations

from .dc6 import Dc6Frame, TRANSPARENT


def load_pal_dat(data: bytes, order: str = "bgr"):
	if len(data) < 768:
		raise ValueError(f"pal.dat too short: {len(data)}")
	entries = []
	for i in range(256):
		a, b, c = data[i * 3], data[i * 3 + 1], data[i * 3 + 2]
		entries.append((a, b, c) if order == "rgb" else (c, b, a))
	return entries


def frame_to_rgba(frame: Dc6Frame, palette):
	"""Return (width, height, bytes) RGBA for a DC6 frame."""
	out = bytearray(frame.width * frame.height * 4)
	i = 0
	for y in range(frame.height):
		row = frame.pixels[y]
		for x in range(frame.width):
			p = row[x]
			if p != TRANSPARENT:
				r, g, b = palette[p]
				out[i : i + 4] = bytes((r, g, b, 255))
			i += 4
	return frame.width, frame.height, bytes(out)


def save_frame_png(frame: Dc6Frame, palette, path: str) -> None:
	from PIL import Image

	w, h, rgba = frame_to_rgba(frame, palette)
	Image.frombytes("RGBA", (w, h), rgba).save(path)
