r"""
Pixel-exact port of Diablo II's item colour-transform ("colormap") system.

Reverse-engineered from D2CMP.dll (Project Diablo 2 build, image base 0x6fe10000)
and cross-checked against D2Common ITEMS_GetColor (source/D2Common/src/Items/Items.cpp).

=============================================================================
1.  HOW WORN-ITEM RECOLOURING ACTUALLY WORKS  (the important finding)
=============================================================================
D2Common ITEMS_GetColor (#10829, Items.cpp:3694) returns, for a unique item:

        D2CMP_MixPalette(pItemsTxt->nTransform, pUniqueItemsTxt->nChrTransform)

    guard (Items.cpp:3832): valid only when  0 < nTransform < 9  and  0 <= nColor < 21.

MixPalette lives in D2CMP.  Contrary to the assumption that the colormap is
generated at runtime from the HSL pipeline, the 8 item-transform tables are
*pre-baked files* that the game simply loads verbatim:

    ITEMS_LoadAllPaletteTransforms (D2CMP 0x6fe250d0) loads 8 files via
    LoadItemPaletteFile("<name>", <type>):
        grey=1  grey2=2  gold=3  brown=4  greybrown=5
        invgrey=6  invgrey2=7  invgreybrown=8
    path = "Data\\Global\\Items\\Palette\\<name>.dat"

    Each .dat is 5376 bytes = 21 tables * 256 bytes  (one 256-entry index->index
    remap per colour code).  LoadItemPaletteFile reads the file straight into the
    static global g_pPaletteBrightnessTables (D2CMP 0x6fe34aa8) at slot
    <type>*0x6900.

The accessor (the D2CMP function ITEMS_GetColor ultimately reaches),
D2CMP @ 0x6fe24ec0  (Ghidra-labelled "GetPaletteBrightnessTable", export #10091):

        byte* select(int nTrans, int nColor):
            if !(1 <= nTrans <= 8) : return NULL
            if !(0 <= nColor <= 20): return NULL
            if nTrans in (3,4)     : return NULL          # <-- see NOTE below
            return 0x6fe34aa8 + ((nTrans*0x69 + nColor) << 8)   # *256

    i.e. byte offset = (nTrans*105 + nColor) * 256   into the loaded block.
    (105 = 0x69: each transform slot reserves 105 tables; only the first 21 are
     filled from the .dat file.)

So the colormap for (nTransform, nColor) is exactly:

        <transform-file>.dat[ nColor*256 : nColor*256 + 256 ]

a 256-entry table T where new_palette_index = T[old_palette_index].
`mix_palette()` below reproduces this by reading the .dat files -- this is
pixel-exact because it is literally the data the game uses.

NOTE (uncertainty): the 0x6fe24ec0 accessor rejects nTransform 3 (gold) and 4
(brown); it is the only reader of g_pPaletteBrightnessTables found in the DLL.
The D2Common-side guard (Items.cpp) allows 1..8.  gold.dat / brown.dat DO exist
and ARE loaded.  For maximum data-fidelity `mix_palette()` allows all 1..8 and
returns the real table for gold/brown; the exact-binary selection (incl. the
3/4 rejection) is available separately as `select_colormap_offset()`.

=============================================================================
2.  THE HSL PIPELINE  (how those .dat files were baked offline)
=============================================================================
CMP_GenerateColorTransformTables (D2CMP 0x6fe1ab60), driven by
CMP_BuildPaletteTransformTables (0x6fe1b930), builds the *general* per-ACT
transform buffer (0x6C327 bytes -- note: data\global\palette\ACTx\pal.pl2 IS a
serialised copy of this buffer; its first 1024 bytes are the RGBA palette).
It is NOT what worn items index at runtime, but it is the algorithm that
produced the item .dat colormaps, and it is faithfully ported here
(`generate_transform_tables`) for documentation / verification / regeneration.

Base palette fed to the pipeline: the 256-entry RGBA palette =
pal.pl2[0:1024]  (byte order R,G,B,A).  This equals pal.dat (768 bytes, byte
order B,G,R) converted to RGB.  The nearest-colour search uses this same RGBA
palette (channels at offsets 0,1,2 = R,G,B).

Exact maths (all quoted from the decompilation):

  RGB->HSL  (D2CMP_ConvertRGBPaletteToHSL 0x6fe1a830):
      r,g,b = channel/255.0            (scale const = 0.00392156862745098 = 1/255)
      mx=max(r,g,b); mn=min(r,g,b)
      L=(mx+mn)/2
      if mx==mn: H=0, S=0
      else:
          d=mx-mn
          S = d/(mx+mn)          if L<=0.5
          S = d/(2.0-(mx+mn))    if L> 0.5
          if   mx==r: H=(g-b)/d
          elif mx==g: H=(b-r)/d + 2.0
          else:       H=(r-g)/d + 4.0
          H *= 60.0                     ;  if H<0: H+=360.0        (H in DEGREES 0..360)

  HSL->RGB  (CMP_ConvertHSLToRGBPalette 0x6fe1a710 + CMP_InterpolateColorComponent 0x6fe1a1b0):
      if S==0: R=G=B=round(L*255)
      else:
          q = L*(1.0+S)          if L<=0.5
          q = (L+S) - L*S        if L> 0.5
          p = 2.0*L - q
          R = round(255 * hue2rgb(p,q, H+120.0))
          G = round(255 * hue2rgb(p,q, H     ))
          B = round(255 * hue2rgb(p,q, H-120.0))
      hue2rgb(p,q,t):                    # t in DEGREES
          if t>360: t-=360
          if t<0  : t+=360
          if t< 60 : return p + (q-p)*t*(1/60)
          if t<180 : return q
          if t<240 : return p + (q-p)*(240-t)*(1/60)
          return p
      byte scale const = 255.0 ; hue offset const = 120.0 ; boundaries 60/180/240.

  Nearest palette colour  (FindNearestPaletteColor 0x6fe19d30):
      argmin_i  (pal_r[i]-r)^2 + (pal_g[i]-g)^2 + (pal_b[i]-b)^2
      plain squared Euclidean, unweighted; strict '<' so lowest index wins ties.

Transform blocks written by CMP_GenerateColorTransformTables (each = 256-byte
remap found via nearest-colour on the HSL-shifted-then-back-to-RGB palette):
  block1 @+0x53500 (24): H += k*15deg
  block2 @+0x54D00 (24): H += k*15deg, S=0.5, L-=0.1 (clamp>=0)
  block3 @+0x56500 (24): H += k*15deg, S=0.5, L+=0.2 (clamp<=1)
  block4 @+0x57D00 ( 1): S=0, L*=0.5
  block5 @+0x57E00 ( 1): S=0, L=L*0.8333333+0.1666667
  block6 @+0x57F00 (24): H += k*15deg but ONLY for entries with 45<H<315
  block7 @+0x59800 (12): H = k*30deg, S=1, L=1
  block8 @+0x5A500     : per-gray-level R-only/G-only/B-only nearest lookups

=============================================================================
3.  COLOUR-CODE MAPPING (colors.txt)
=============================================================================
data\global\excel\colors.txt has a header row then 21 data rows; the 0-based
row index IS the value stored in uniqueitems/setitems `nChrTransform`,
magicaffix `nTransformColor`, gems `nTransForm`, i.e. the `nColor` argument:

    0 whit  1 lgry  2 dgry  3 blac  4 lblu  5 dblu  6 cblu
    7 lred  8 dred  9 cred 10 ...   (see COLOR_CODES below)
   ...     19 dpur 20 oran            <-- NB: row order is the authority
Confirmed against the live colors.txt (21 codes) and the nColor<21 guard.
"""

from __future__ import annotations

import math
import os
import tempfile

# --------------------------------------------------------------------------
# Constants (all values read out of the D2CMP.dll constant pool @ 0x6fe300b8)
# --------------------------------------------------------------------------
RGB_TO_FLOAT = 1.0 / 255.0          # 0x6fe30118  0.00392156862745098
BYTE_SCALE = 255.0                  # 0x6fe30100
HUE_OFFSET_DEG = 120.0              # 0x6fe300e8
HUE_WRAP_DEG = 360.0                # 0x6fe300e0
SLOPE = 1.0 / 60.0                  # 0x6fe300c8  0.016666666666666666
SEG1_DEG = 60.0                     # 0x6fe300d0
SEG2_DEG = 180.0                    # 0x6fe300c0
SEG3_DEG = 240.0                    # 0x6fe300b8
HUE_STEP_DEG = 15.0                 # block hue step (float 15.0)

# D2CMP g_pPaletteBrightnessTables base + layout (for select_colormap_offset)
TABLE_BYTES = 256
TABLES_PER_TRANSFORM = 0x69         # 105  (only first 21 come from the .dat file)

# colors.txt order == nColor index == uniqueitems.nChrTransform, etc.
COLOR_CODES = [
    "whit", "lgry", "dgry", "blac", "lblu", "dblu", "cblu",
    "lred", "dred", "cred", "lgrn", "dgrn", "cgrn", "lyel",
    "dyel", "lgld", "dgld", "lpur", "dpur", "oran", "bwht",
]  # index 0..20
COLOR_INDEX = {c: i for i, c in enumerate(COLOR_CODES)}

# nTransform (1..8) -> item-palette .dat filename (ITEMS_LoadAllPaletteTransforms)
TRANSFORM_FILES = {
    1: "grey", 2: "grey2", 3: "gold", 4: "brown", 5: "greybrown",
    6: "invgrey", 7: "invgrey2", 8: "invgreybrown",
}

ITEM_PALETTE_DIR = r"data\global\items\palette"


# --------------------------------------------------------------------------
# byte rounding: D2CMP ConvertFloatingPointTo64BitInteger (0x6fe27c10) rounds
# to nearest (x87 FIST + a >0.5 correction => round-half-away-from-zero for the
# non-negative values used here). round-half-up + clamp reproduces that.
# --------------------------------------------------------------------------
def _to_byte(x: float) -> int:
    v = int(math.floor(x + 0.5))
    return 0 if v < 0 else (255 if v > 255 else v)


# ==========================================================================
# 2. HSL pipeline (reference / regeneration)
# ==========================================================================
def rgb_to_hsl(r: int, g: int, b: int):
    """(0..255 ints) -> (H degrees 0..360, S 0..1, L 0..1). Exact D2CMP maths."""
    rf = r * RGB_TO_FLOAT
    gf = g * RGB_TO_FLOAT
    bf = b * RGB_TO_FLOAT
    mx = max(rf, gf, bf)
    mn = min(rf, gf, bf)
    L = (mx + mn) * 0.5
    if mx == mn:
        return 0.0, 0.0, L
    d = mx - mn
    if L > 0.5:
        S = d / (2.0 - (mx + mn))
    else:
        S = d / (mx + mn)
    if mx == rf:
        H = (gf - bf) / d
    elif mx == gf:
        H = (bf - rf) / d + 2.0
    else:
        H = (rf - gf) / d + 4.0
    H *= 60.0
    if H < 0.0:
        H += 360.0
    return H, S, L


def _hue2rgb(p: float, q: float, t_deg: float) -> float:
    """CMP_InterpolateColorComponent (0x6fe1a1b0). t in degrees; returns 0..1."""
    t = t_deg
    if t > HUE_WRAP_DEG:
        t -= HUE_WRAP_DEG
    if t < 0.0:
        t += HUE_WRAP_DEG
    if t < SEG1_DEG:                       # 0..60 ascending
        return p + (q - p) * t * SLOPE
    if t < SEG2_DEG:                       # 60..180 flat top
        return q
    if t < SEG3_DEG:                       # 180..240 descending
        return p + (q - p) * (SEG3_DEG - t) * SLOPE
    return p                               # 240..360 flat bottom


def hsl_to_rgb(h_deg: float, s: float, l: float):
    """(H degrees, S, L) -> (r,g,b) ints 0..255. Exact D2CMP maths."""
    if s == 0.0:
        v = _to_byte(l * BYTE_SCALE)
        return v, v, v
    if l > 0.5:
        q = (l + s) - l * s
    else:
        q = l * (1.0 + s)
    p = 2.0 * l - q
    r = _to_byte(BYTE_SCALE * _hue2rgb(p, q, h_deg + HUE_OFFSET_DEG))
    g = _to_byte(BYTE_SCALE * _hue2rgb(p, q, h_deg))
    b = _to_byte(BYTE_SCALE * _hue2rgb(p, q, h_deg - HUE_OFFSET_DEG))
    return r, g, b


def nearest_palette_color(pal_rgb, r: int, g: int, b: int) -> int:
    """FindNearestPaletteColor (0x6fe19d30): unweighted squared-Euclidean,
    strict '<' (lowest index wins ties). `pal_rgb` = list of 256 (r,g,b)."""
    best = 0
    best_d = 0xFFFFFFFF
    for i, (pr, pg, pb) in enumerate(pal_rgb):
        dr = pr - r
        dg = pg - g
        db = pb - b
        d = dr * dr + dg * dg + db * db
        if d < best_d:
            best_d = d
            best = i
    return best


def _hsl_palette(pal_rgb):
    return [rgb_to_hsl(r, g, b) for (r, g, b) in pal_rgb]


def _bake_block(pal_rgb, hsl, mutate):
    """Apply `mutate(h,s,l,index)->(h,s,l)` to every palette entry, convert back
    to RGB, and resolve each result to a nearest palette index -> 256-byte table."""
    out = bytearray(256)
    for i in range(256):
        h, s, l = mutate(hsl[i][0], hsl[i][1], hsl[i][2], i)
        r, g, b = hsl_to_rgb(h, s, l)
        out[i] = nearest_palette_color(pal_rgb, r, g, b)
    return bytes(out)


def generate_transform_tables(rgba_palette) -> dict:
    """Reference re-implementation of CMP_GenerateColorTransformTables.

    `rgba_palette` : bytes/list of 1024 bytes = 256 * (R,G,B,A)  (pal.pl2[0:1024]).
    Returns dict of region-name -> list of 256-byte remap tables (bytes), matching
    the blocks the DLL writes.  This is the OFFLINE bake algorithm; worn items use
    the pre-baked .dat files at runtime (see mix_palette).
    """
    pal_rgb = [(rgba_palette[i * 4], rgba_palette[i * 4 + 1], rgba_palette[i * 4 + 2])
               for i in range(256)]
    hsl = _hsl_palette(pal_rgb)

    def wrap(h):
        return h - 360.0 if h > 360.0 else h

    regions = {}
    # block1 @+0x53500 : 24 tables, H += k*15
    regions["hue"] = [
        _bake_block(pal_rgb, hsl, lambda h, s, l, i, k=k: (wrap(h + k * HUE_STEP_DEG), s, l))
        for k in range(24)]
    # block2 @+0x54D00 : 24 tables, H += k*15, S=0.5, L-=0.1 (clamp>=0)
    regions["hue_dark"] = [
        _bake_block(pal_rgb, hsl,
                    lambda h, s, l, i, k=k: (wrap(h + k * HUE_STEP_DEG), 0.5, max(0.0, l - 0.1)))
        for k in range(24)]
    # block3 @+0x56500 : 24 tables, H += k*15, S=0.5, L+=0.2 (clamp<=1)
    regions["hue_light"] = [
        _bake_block(pal_rgb, hsl,
                    lambda h, s, l, i, k=k: (wrap(h + k * HUE_STEP_DEG), 0.5, min(1.0, l + 0.2)))
        for k in range(24)]
    # block4 @+0x57D00 : 1 table, S=0, L*=0.5
    regions["gray_half"] = [
        _bake_block(pal_rgb, hsl, lambda h, s, l, i: (h, 0.0, l * 0.5))]
    # block5 @+0x57E00 : 1 table, S=0, L=L*0.8333333+0.1666667
    regions["gray_bright"] = [
        _bake_block(pal_rgb, hsl,
                    lambda h, s, l, i: (h, 0.0, l * 0.8333333002196431 + 0.1666666625274554))]
    # block6 @+0x57F00 : 24 tables, H += k*15 ONLY where 45 < H < 315
    def _cond(h, s, l, i, k):
        return (wrap(h + k * HUE_STEP_DEG), s, l) if 45.0 < h < 315.0 else (h, s, l)
    regions["hue_cond"] = [
        _bake_block(pal_rgb, hsl, lambda h, s, l, i, k=k: _cond(h, s, l, i, k))
        for k in range(24)]
    # block7 @+0x59800 : 12 tables, H=k*30, S=1, L=1
    regions["pure_hue"] = [
        _bake_block(pal_rgb, hsl, lambda h, s, l, i, k=k: (k * 30.0, 1.0, 1.0))
        for k in range(12)]
    return regions


# ==========================================================================
# 1. Item colormap lookup -- the exact runtime path (reads pre-baked .dat)
# ==========================================================================
def select_colormap_offset(n_trans: int, n_color: int):
    """Exact port of the D2CMP accessor @ 0x6fe24ec0 (returns a *byte offset*
    into g_pPaletteBrightnessTables, or None when the guard rejects the args).
    NB: this rejects nTransform 3 and 4, exactly like the binary."""
    if not (0 < n_trans < 9):
        return None
    if not (0 <= n_color < 21):
        return None
    if 3 <= n_trans <= 4:
        return None
    return (n_trans * TABLES_PER_TRANSFORM + n_color) * TABLE_BYTES


def load_transform_dat(n_trans: int, reader) -> bytes:
    """Read the 5376-byte item-palette .dat for nTransform via `reader(rel_path)`
    (e.g. app.assets.try_read_effective).  Returns the raw 21*256 bytes."""
    name = TRANSFORM_FILES.get(int(n_trans))
    if name is None:
        raise ValueError(f"invalid nTransform {n_trans} (expected 1..8)")
    rel = os.path.join(ITEM_PALETTE_DIR, name + ".dat")
    data = reader(rel)
    if not data:
        raise FileNotFoundError(rel)
    if len(data) < 21 * 256:
        raise ValueError(f"{rel}: expected >=5376 bytes, got {len(data)}")
    return data


def mix_palette(transform: int, color: int, reader=None, dat_bytes: bytes = None):
    """D2CMP_MixPalette(nTrans, nColor) -> 256-entry index->index remap (list[int]).

    Pixel-exact: returns the colour code `color`'s 256-byte table straight out of
    the transform's pre-baked .dat file (exactly what the game loads & indexes).

    Guard mirrors ITEMS_GetColor (Items.cpp): 0 < transform < 9 and 0 <= color < 21.
    Supply either `reader` (a callable rel_path->bytes) or `dat_bytes` (the raw
    5376-byte file for this transform).
    """
    if not (0 < int(transform) < 9):
        raise ValueError(f"nTransform {transform} out of range (1..8)")
    if not (0 <= int(color) < 21):
        raise ValueError(f"nColor {color} out of range (0..20)")
    if dat_bytes is None:
        if reader is None:
            raise ValueError("provide reader= or dat_bytes=")
        dat_bytes = load_transform_dat(transform, reader)
    off = color * TABLE_BYTES
    return list(dat_bytes[off:off + TABLE_BYTES])


# ==========================================================================
# helpers for palettes
# ==========================================================================
def rgba_from_pl2(pl2_bytes: bytes):
    """pal.pl2[0:1024] -> list of 1024 RGBA bytes (already R,G,B,A)."""
    return list(pl2_bytes[:1024])


def rgba_from_pal_dat(pal_dat_bytes: bytes):
    """pal.dat (768 bytes, B,G,R order) -> 1024 RGBA bytes (R,G,B,0)."""
    out = bytearray(1024)
    for i in range(256):
        b = pal_dat_bytes[i * 3 + 0]
        g = pal_dat_bytes[i * 3 + 1]
        r = pal_dat_bytes[i * 3 + 2]
        out[i * 4 + 0] = r
        out[i * 4 + 1] = g
        out[i * 4 + 2] = b
        out[i * 4 + 3] = 0
    return list(out)


def pal_rgb_list(rgba_palette):
    """1024 RGBA bytes -> list of 256 (r,g,b) tuples (for display / nearest)."""
    return [(rgba_palette[i * 4], rgba_palette[i * 4 + 1], rgba_palette[i * 4 + 2])
            for i in range(256)]


# ==========================================================================
# self-test
# ==========================================================================
if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.environ.setdefault("ASSET_STUDIO_WS", "C:/Diablo2/AssetStudio")
    import app.assets as A  # noqa: E402
    from PIL import Image  # noqa: E402

    reader = A.try_read_effective

    # --- base palette: prefer pal.pl2[0:1024] (the pipeline's RGBA input) -----
    pl2 = reader(r"data\global\palette\ACT1\pal.pl2")
    pdat = reader(r"data\global\palette\ACT1\pal.dat")
    if pl2:
        rgba = rgba_from_pl2(pl2)
        print(f"base palette: pal.pl2[0:1024]  (len(pl2)={len(pl2)} == 0x6C327? "
              f"{len(pl2) == 0x6C327})")
    else:
        rgba = rgba_from_pal_dat(pdat)
        print("base palette: derived from pal.dat (BGR->RGB)")
    pal = pal_rgb_list(rgba)
    # sanity: pl2 RGBA must equal pal.dat BGR->RGB
    if pl2 and pdat:
        alt = pal_rgb_list(rgba_from_pal_dat(pdat))
        print("  pl2-rgba == pal.dat(BGR->RGB)? ", pal[1:8] == alt[1:8], " e.g. idx1:", pal[1])

    # --- HSL round-trip sanity ------------------------------------------------
    print("\nHSL round-trip (rgb -> hsl -> rgb) on a few palette colours:")
    for i in (1, 10, 47, 100, 200):
        r, g, b = pal[i]
        h, s, l = rgb_to_hsl(r, g, b)
        rr, gg, bb = hsl_to_rgb(h, s, l)
        print(f"  idx {i:3d}: rgb({r:3d},{g:3d},{b:3d}) -> "
              f"H={h:6.1f} S={s:4.2f} L={l:4.2f} -> rgb({rr:3d},{gg:3d},{bb:3d})")

    # --- mix_palette on representative codes ----------------------------------
    TRANS = 1  # "grey" transform
    print(f"\nmix_palette(transform={TRANS} '{TRANSFORM_FILES[TRANS]}') "
          f"for a few colour codes:")
    checks = ["cred", "dgrn", "dblu", "oran", "whit"]
    tables = {}
    for code in checks:
        ci = COLOR_INDEX[code]
        tbl = mix_palette(TRANS, ci, reader=reader)
        tables[code] = tbl
        remapped = sum(1 for i in range(256) if tbl[i] != i)
        # where remapped slots point, average hue, to sanity-check the tint
        hues = []
        for i in range(256):
            if tbl[i] != i:
                rr, gg, bb = pal[tbl[i]]
                hh, ss, ll = rgb_to_hsl(rr, gg, bb)
                if ss > 0.15:
                    hues.append(hh)
        avg = (sum(hues) / len(hues)) if hues else float("nan")
        print(f"  {code:4s} (nColor={ci:2d}): {remapped:3d}/256 slots remapped, "
              f"mean tint hue of remapped ~= {avg:5.1f} deg")

    # expected-hue sanity (deg): red~0/360, green~120, blue~240, orange~30
    print("\n  hue expectation: cred~0, dgrn~120, dblu~240, oran~30  "
          "(grey ramp slots should tint toward these)")

    # --- PNG strip: base palette vs transformed palettes ----------------------
    # each palette = one horizontal strip of 256 full-height cells
    CELL_W, ROW_H, GAP = 3, 20, 2
    codes_to_show = ["whit", "lgry", "cred", "dred", "dgrn", "cgrn",
                     "dblu", "cblu", "oran", "dpur", "dyel", "bwht"]
    labels = ["BASE"] + codes_to_show
    remaps = [None] + [mix_palette(TRANS, COLOR_INDEX[c], reader=reader) for c in codes_to_show]
    W = 256 * CELL_W
    H = len(labels) * (ROW_H + GAP)
    strip = Image.new("RGB", (W, H), (0, 0, 0))
    px = strip.load()

    def draw_palette(row, remap):
        y0 = row * (ROW_H + GAP)
        for idx in range(256):
            src = remap[idx] if remap is not None else idx
            r, g, b = pal[src]
            for xx in range(CELL_W):
                for yy in range(ROW_H):
                    px[idx * CELL_W + xx, y0 + yy] = (r, g, b)

    for ri, rm in enumerate(remaps):
        draw_palette(ri, rm)
    print("  strip rows (top->bottom): " + ", ".join(labels))

    out_png = os.path.join(tempfile.gettempdir(), "colortransform_selftest.png")
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    strip.save(out_png)
    print(f"\nsaved palette strip -> {out_png}")
    print("  row 0 = base ACT1 palette; rows below = grey-transform recolours "
          "(" + ", ".join(codes_to_show) + ")")
