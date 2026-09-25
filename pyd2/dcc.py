"""DCC decoder — the character-sprite format. Palette-indexed, bit-stream compressed, two-pass
cell-based. Implemented from pyd2/DCC_FORMAT.md + the OpenDiablo2 reference read order.

Public API:
    d = decode(bytes)                       -> Dcc
    frame = d.directions[dir].frames[f]      -> DccFrame(width,height,x_offset,y_offset,pixels)
        pixels: 2D list [h][w] of palette indices; 0 == transparent.
    rgba = frame_to_rgba(frame, palette)     -> (w, h, bytes)   for rendering

Validate visually before trusting it: decode a real char file and render dir 0 / frame 0.
"""
from __future__ import annotations

from dataclasses import dataclass

WIDTH_TABLE = [0, 1, 2, 4, 6, 8, 10, 12, 14, 16, 20, 24, 26, 28, 30, 32]


class _Bits:
    """LSB-first bit reader over a byte buffer, positioned by absolute bit index."""
    __slots__ = ("d", "pos")

    def __init__(self, data: bytes, bit_pos: int = 0):
        self.d = data
        self.pos = bit_pos

    def bit(self) -> int:
        p = self.pos
        self.pos = p + 1
        return (self.d[p >> 3] >> (p & 7)) & 1

    def bits(self, n: int) -> int:
        # read up to a byte at a time (LSB-first) instead of bit-by-bit
        v = 0
        got = 0
        d = self.d
        pos = self.pos
        while got < n:
            bit_i = pos & 7
            take = 8 - bit_i
            if take > n - got:
                take = n - got
            v |= ((d[pos >> 3] >> bit_i) & ((1 << take) - 1)) << got
            got += take
            pos += take
        self.pos = pos
        return v

    def signed(self, n: int) -> int:
        v = self.bits(n)
        if n and (v & (1 << (n - 1))):
            v -= (1 << n)
        return v


@dataclass
class DccFrame:
    width: int
    height: int
    x_offset: int          # box left, relative to the character anchor
    y_offset: int          # box top,  relative to the character anchor
    pixels: list           # [height][width] palette indices; 0 = transparent


@dataclass
class DccDirection:
    frames: list           # list[DccFrame]


class Dcc:
    """Directions are decoded LAZILY (and cached) — a paper-doll preview shows one direction at a
    time, so eagerly decoding all 16 was 16x wasted work."""
    def __init__(self, data: bytes, dir_offsets: list, frames_per_dir: int):
        self._data = data
        self._offsets = dir_offsets
        self.frames_per_dir = frames_per_dir
        self._cache = {}

    @property
    def num_directions(self) -> int:
        return len(self._offsets)

    def direction(self, i: int) -> DccDirection:
        d = self._cache.get(i)
        if d is None:
            d = _decode_direction(self._data, self._offsets[i], self.frames_per_dir)
            self._cache[i] = d
        return d


class _Cell:
    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


def _dir_cells(size):
    """Direction grid: 4-px cells, last is the remainder."""
    if size <= 1:
        return [size] if size else []
    n = 1 + (size - 1) // 4
    return [4] * (n - 1) + [size - 4 * (n - 1)]


def _frame_cells(length, box_start, dir_start):
    """Frame cell widths (or heights) aligned to the direction 4-px grid.
    `w = 4 - ((box_start - dir_start) % 4)` is the first cell; middle cells are 4."""
    w0 = 4 - ((box_start - dir_start) % 4)
    if (length - w0) <= 1:
        return [length]
    tmp = length - w0 - 1
    n = 2 + tmp // 4
    if tmp % 4 == 0:
        n -= 1
    widths = [w0] + [4] * (n - 2) + [length - w0 - 4 * (n - 2)]
    return widths


def decode(data: bytes) -> Dcc:
    assert data[0] == 0x74, "not a DCC (bad signature)"
    n_dirs = data[2]
    n_frames = int.from_bytes(data[3:7], "little")
    # dir offsets start at 15 (after sig,ver,dirs,nframes(4),one(4),totalsize(4))
    dir_offsets = [int.from_bytes(data[15 + i * 4:19 + i * 4], "little") for i in range(n_dirs)]
    return Dcc(data, dir_offsets, n_frames)


def _decode_direction(data: bytes, byte_off: int, n_frames: int) -> DccDirection:
    b = _Bits(data, byte_off * 8)
    b.bits(32)                                    # OutSizeCoded (ignored)
    flags = b.bits(2)
    has_equal = bool(flags & 0x02)
    has_encoding = bool(flags & 0x01)
    v0b = WIDTH_TABLE[b.bits(4)]
    wb = WIDTH_TABLE[b.bits(4)]
    hb = WIDTH_TABLE[b.bits(4)]
    xb = WIDTH_TABLE[b.bits(4)]
    yb = WIDTH_TABLE[b.bits(4)]
    ob = WIDTH_TABLE[b.bits(4)]
    cb = WIDTH_TABLE[b.bits(4)]

    # per-frame headers
    fh = []
    for _ in range(n_frames):
        b.bits(v0b)                               # variable0 (unused)
        w = b.bits(wb)
        h = b.bits(hb)
        xo = b.signed(xb)
        yo = b.signed(yb)
        nopt = b.bits(ob)
        b.bits(cb)                                # nCodedBytes (unused here)
        bottom_up = b.bit()
        if bottom_up:
            top = yo
        else:
            top = yo - h + 1
        fh.append({"w": w, "h": h, "x": xo, "top": top, "bottom_up": bottom_up, "nopt": nopt})

    if any(f["nopt"] for f in fh):
        raise NotImplementedError("DCC optional-bytes not supported (unused by char sprites)")

    # direction bounding box (union of frame boxes)
    minx = min(f["x"] for f in fh)
    maxx = max(f["x"] + f["w"] - 1 for f in fh)
    miny = min(f["top"] for f in fh)
    maxy = max(f["top"] + f["h"] - 1 for f in fh)
    dir_w = maxx - minx + 1
    dir_h = maxy - miny + 1

    # bitstream sizes (20 bits each, gated) — NO byte alignment
    eq_size = b.bits(20) if has_equal else 0
    pm_size = b.bits(20)
    et_size = b.bits(20) if has_encoding else 0
    rp_size = b.bits(20) if has_encoding else 0

    # PixelValuesKey: 256 bits, set bit N => palette index N used
    key = [i for i in range(256) if b.bit()]

    # sub-bitstreams follow sequentially; pcd = the rest
    p = b.pos
    ec = _Bits(data, p); p += eq_size
    pm = _Bits(data, p); p += pm_size
    et = _Bits(data, p); p += et_size
    rp = _Bits(data, p); p += rp_size
    pcd = _Bits(data, p)

    # per-frame cells (aligned to the direction grid)
    frames_cells = []
    for f in fh:
        cw = _frame_cells(f["w"], f["x"], minx)
        ch = _frame_cells(f["h"], f["top"], miny)
        cells = []
        oy = f["top"] - miny
        for ci_y, hh in enumerate(ch):
            ox = f["x"] - minx
            row = []
            for ci_x, ww in enumerate(cw):
                row.append(_Cell(ox, oy, ww, hh))
                ox += ww
            cells.append(row)
            oy += hh
        frames_cells.append(cells)

    dir_cols = _dir_cells(dir_w)
    dir_rows = _dir_cells(dir_h)
    n_gx, n_gy = len(dir_cols), len(dir_rows)

    # ---- Pass 1: fill the pixel buffer -------------------------------------
    # lookup[gy][gx] = current 4-value entry (as CODES) for that direction cell
    lookup = [[None] * n_gx for _ in range(n_gy)]
    cell_entry = []  # per (frame, cy, cx) -> resolved 4-value entry (list) or None(equal->reuse)
    pixel_entries = []   # all appended entries (for later code->palette mapping)

    POP = [bin(m).count("1") for m in range(16)]

    for fi, cells in enumerate(frames_cells):
        fe = []
        for cy, row in enumerate(cells):
            fer = []
            for cx, cell in enumerate(row):
                gx = cell.x // 4
                gy = cell.y // 4
                prev = lookup[gy][gx]
                entry = None
                if prev is not None:
                    if has_equal and ec.bit():
                        fer.append(prev)          # equal cell: reuse prior codes
                        continue
                    pixel_mask = pm.bits(4)
                else:
                    pixel_mask = 0x0F
                # decode up to popcount(pixel_mask) pixels
                nbits = POP[pixel_mask]
                enc = et.bit() if (nbits and et_size) else 0
                stack = [0, 0, 0, 0]
                last = 0
                decoded = 0
                for i in range(nbits):
                    if enc:
                        stack[i] = rp.bits(8)
                    else:
                        val = last
                        disp = pcd.bits(4)
                        val += disp
                        while disp == 15:
                            disp = pcd.bits(4)
                            val += disp
                        stack[i] = val
                    if stack[i] == last:
                        stack[i] = 0
                        break
                    last = stack[i]
                    decoded += 1
                entry = [0, 0, 0, 0]
                di = decoded - 1
                for bit in range(4):
                    if pixel_mask & (1 << bit):
                        if di >= 0:
                            entry[bit] = stack[di]
                            di -= 1
                        else:
                            entry[bit] = 0
                    else:
                        entry[bit] = prev[bit] if prev is not None else 0
                pixel_entries.append(entry)
                lookup[gy][gx] = entry
                fer.append(entry)
            fe.append(fer)
        cell_entry.append(fe)

    # map codes -> real palette indices
    for e in pixel_entries:
        for i in range(4):
            e[i] = key[e[i]] if e[i] < len(key) else 0

    # ---- Pass 2: build frames into a persistent direction buffer -----------
    # EqualCells is re-read from its start; pcd CONTINUES from where pass 1 left it. A direction
    # cell is gated by EqualCells only once it has been decoded by an earlier frame ('seen').
    ec2 = _Bits(data, b.pos)      # b.pos == start of the ec sub-stream
    seen = [[False] * n_gx for _ in range(n_gy)]
    dir_buf = [[0] * dir_w for _ in range(dir_h)]
    out_frames = []
    for fi, cells in enumerate(frames_cells):
        f = fh[fi]
        for cy, row in enumerate(cells):
            for cx, cell in enumerate(row):
                gx = cell.x // 4
                gy = cell.y // 4
                if seen[gy][gx]:
                    if has_equal and ec2.bit():
                        continue                  # equal cell: keep prior pixels in dir_buf
                else:
                    seen[gy][gx] = True
                v = cell_entry[fi][cy][cx]
                if v[0] == v[1]:                  # solid cell: fill with Value[0], no pcd reads
                    fill = v[0]
                    for yy in range(cell.h):
                        base = dir_buf[cell.y + yy]
                        for xx in range(cell.w):
                            base[cell.x + xx] = fill
                else:                             # 2 (or 4) distinct: 1 or 2 index bits per pixel
                    bits = 1 if v[1] == v[2] else 2
                    for yy in range(cell.h):
                        base = dir_buf[cell.y + yy]
                        for xx in range(cell.w):
                            base[cell.x + xx] = v[pcd.bits(bits)]
        fx = f["x"] - minx
        fy = f["top"] - miny
        out_frames.append(DccFrame(
            width=f["w"], height=f["h"], x_offset=f["x"], y_offset=f["top"],
            pixels=[list(dir_buf[fy + r][fx:fx + f["w"]]) for r in range(f["h"])]))
    return DccDirection(frames=out_frames)


def frame_to_rgba(frame: DccFrame, palette) -> tuple:
    """(w, h, rgba_bytes). palette[i] = (r,g,b). index 0 = transparent."""
    w, h = frame.width, frame.height
    out = bytearray(w * h * 4)
    for y in range(h):
        row = frame.pixels[y]
        for x in range(w):
            idx = row[x]
            o = (y * w + x) * 4
            if idx:
                r, g, bl = palette[idx]
                out[o] = r; out[o + 1] = g; out[o + 2] = bl; out[o + 3] = 255
    return w, h, bytes(out)
