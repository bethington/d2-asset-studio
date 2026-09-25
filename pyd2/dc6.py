"""DC6 sprite codec.

DC6 layout:
  header (24 bytes): version=6, flags=1, encoding=0, termination[4],
                     directions, frames_per_direction
  frame pointer table: directions*frames_per_direction uint32 offsets
  per frame (32-byte header): flip, width, height, offset_x, offset_y,
                              alloc_size, next_block, length
  then `length` bytes of RLE data, then a 3-byte terminator.

RLE (per scanline, bottom-up when flip == 0):
  0x80        -> end of line
  b > 0x80    -> (b & 0x7F) transparent pixels
  b < 0x80    -> b literal palette-index bytes follow
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

TRANSPARENT = -1

_HEADER = struct.Struct("<iii4sii")
_FRAME_HEADER = struct.Struct("<8i")
_FRAME_TERMINATOR = b"\xee\xee\xee"


@dataclass
class Dc6Frame:
	flip: int
	width: int
	height: int
	offset_x: int
	offset_y: int
	# rows top-down; values are palette indices 0..255 or TRANSPARENT
	pixels: list = field(repr=False, default=None)


@dataclass
class Dc6File:
	directions: int
	frames_per_direction: int
	termination: bytes
	frames: list  # flat list, index = direction * frames_per_direction + frame


def decode(data: bytes) -> Dc6File:
	version, flags, encoding, termination, directions, fpd = _HEADER.unpack_from(data, 0)
	if version != 6:
		raise ValueError(f"not a DC6 (version={version})")
	count = directions * fpd
	ptrs = struct.unpack_from(f"<{count}I", data, _HEADER.size)
	frames = []
	for ptr in ptrs:
		flip, width, height, ox, oy, _alloc, _next_block, length = _FRAME_HEADER.unpack_from(data, ptr)
		rle = data[ptr + _FRAME_HEADER.size : ptr + _FRAME_HEADER.size + length]
		rows = [[TRANSPARENT] * width for _ in range(height)]
		x = 0
		y = 0 if flip else height - 1
		i = 0
		while i < length:
			b = rle[i]
			i += 1
			if b == 0x80:
				x = 0
				y += 1 if flip else -1
			elif b & 0x80:
				x += b & 0x7F
			else:
				for _ in range(b):
					if 0 <= y < height and x < width:
						rows[y][x] = rle[i]
					i += 1
					x += 1
		frames.append(Dc6Frame(flip, width, height, ox, oy, rows))
	return Dc6File(directions, fpd, termination, frames)


def _encode_frame_rle(frame: Dc6Frame) -> bytes:
	out = bytearray()
	order = range(frame.height) if frame.flip else range(frame.height - 1, -1, -1)
	for y in order:
		row = frame.pixels[y]
		last = -1
		for x in range(frame.width - 1, -1, -1):
			if row[x] != TRANSPARENT:
				last = x
				break
		x = 0
		while x <= last:
			if row[x] == TRANSPARENT:
				run = 0
				while x <= last and row[x] == TRANSPARENT and run < 0x7F:
					run += 1
					x += 1
				out.append(0x80 | run)
			else:
				start = x
				while x <= last and row[x] != TRANSPARENT and (x - start) < 0x7F:
					x += 1
				out.append(x - start)
				out.extend(p & 0xFF for p in row[start:x])
		out.append(0x80)  # end of line
	return bytes(out)


def encode(dc6: Dc6File) -> bytes:
	count = len(dc6.frames)
	assert count == dc6.directions * dc6.frames_per_direction
	header = _HEADER.pack(6, 1, 0, dc6.termination or b"\xee\xee\xee\xee",
	                      dc6.directions, dc6.frames_per_direction)
	ptr_table_at = len(header)
	body_at = ptr_table_at + 4 * count
	ptrs = []
	body = bytearray()
	for frame in dc6.frames:
		rle = _encode_frame_rle(frame)
		ptr = body_at + len(body)
		ptrs.append(ptr)
		next_block = ptr + _FRAME_HEADER.size + len(rle) + len(_FRAME_TERMINATOR)
		body += _FRAME_HEADER.pack(frame.flip, frame.width, frame.height,
		                           frame.offset_x, frame.offset_y,
		                           0, next_block, len(rle))
		body += rle
		body += _FRAME_TERMINATOR
	return header + struct.pack(f"<{count}I", *ptrs) + bytes(body)
