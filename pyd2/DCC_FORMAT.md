# DCC decode spec (for `pyd2/dcc.py`)

Captured algorithm for the character-sprite format. DCC is palette-indexed, bit-stream compressed,
two-pass cell-based — much more involved than DC6. Sources at the bottom. Implement against this,
then **validate visually** by decoding a real file (e.g. `BATRLITNUHTH.dcc`) and rendering
direction 0 / frame 0 — it should read as a recognizable body part, not noise.

## File header
```
u8  signature (0x74)
u8  version   (6)
u8  nDirections
u32 nFrames            (frames per direction)
u32 one               (=1, ignore)
u32 totalSizeCoded
u32 dirOffset[nDirections]   (byte offset of each direction from file start)
```

## Bit reader
LSB-first within each byte. `read(n)` → unsigned. `read_signed(n)` → read n, sign-extend from
bit n-1. Each direction is its own bitstream starting at `dirOffset[d]`.

## Direction header (bits)
```
OutSizeCoded        : 32
CompressionFlags    : 2      bit0(0x01)=EncodingType+RawPixelCodes present; bit1(0x02)=EqualCells present
Variable0Bits       : 4  -> WIDTH_TABLE
WidthBits           : 4  -> WIDTH_TABLE
HeightBits          : 4  -> WIDTH_TABLE
XOffsetBits         : 4  -> WIDTH_TABLE
YOffsetBits         : 4  -> WIDTH_TABLE
OptionalDataBits    : 4  -> WIDTH_TABLE
CodedBytesBits      : 4  -> WIDTH_TABLE
```
`WIDTH_TABLE = [0,1,2,4,6,8,10,12,14,16,20,24,26,28,30,32]` (index by the 4-bit code).

## Per-frame headers (nFrames of them, bits)
```
Variable0     : Variable0Bits      (unused)
Width         : WidthBits
Height        : HeightBits
XOffset       : XOffsetBits         (SIGNED)
YOffset       : YOffsetBits         (SIGNED)
nOptionalBytes: OptionalDataBits
nCodedBytes   : CodedBytesBits
BottomUp      : 1
```
Frame box: `x0=XOffset; x1=XOffset+Width-1; if BottomUp: y0=YOffset,y1=YOffset+Height-1 else
y1=YOffset,y0=YOffset-Height+1`. Direction FrameBuffer box = union over frames; width/height from
min/max.

## After frame headers
- If ANY frame has `nOptionalBytes>0`: byte-align the reader.
- Bitstream sizes (20 bits each, gated):
  `if flags&0x02: equalCellsSize`; `pixelMaskSize`; `if flags&0x01: encodingTypeSize, rawPixelCodesSize`.
- Read the optional frame data bytes (sum of nOptionalBytes) — byte aligned.
- The four sub-bitstreams follow in order (each `size` bits): EqualCells, PixelMask, EncodingType,
  RawPixelCodes. The remaining bits are the **PixelCodeAndDisplacement** bitstream (used in BOTH passes).
- **PixelValuesKey**: read 256 bits; set bit N ⇒ palette index N is used. Enumerate set bits in
  order → `key[code]=index` (pixel codes 0,1,2… map to those indices).

## Cell grid
FrameBuffer divided into 4×4 cells, left→right, top→bottom:
```
gridW = (fbW + 3)//4 ; gridH = (fbH + 3)//4
cell (gx,gy): x0=gx*4, y0=gy*4; w=min(4, fbW-x0); h=min(4, fbH-y0)
```
"No width/height 1" rule: if the rightmost cell-column has base width 1, widen the previous column
cell to 5 (and drop the 1-wide cell); same for a bottom row of height 1. (Frame cells then align to
this grid; a frame's cell covers only the frame's pixels within that grid cell.)

## Pass 1 — fill the direction PixelBuffer (per frame, cells in grid order)
```
if cell already has a PixelBuffer entry (from a previous frame):
    if EqualCells present: eq = equalCells.read(1) ; if eq: continue (reuse)
    pixelMask = pixelMask.read(4)
else:
    pixelMask = 0xF
decoded = []; last = 0
for each SET bit of pixelMask (LSB first):
    enc = encodingType.read(1) if EncodingType present else 0
    if enc: code = rawPixelCodes.read(8)
    else:
        code = last; d = pcd.read(4)
        while d==15: d += pcd.read(4); (if the last read was <15 stop)
        code += d
    if code==last: break
    last = code; decoded.append(code)
entry=[0,0,0,0]; di=len(decoded)-1
for bit in 0..3:
    if pixelMask & (1<<bit): entry[bit]=decoded[di] if di>=0 else 0; di-=1
    else: entry[bit]= prev_entry[bit] if prev else 0
entry = [key[c] for c in entry]   # code -> palette index
append entry to PixelBuffer; cell.ref = that entry
```

## Pass 2 — build frames (reset EqualCells to start; per frame, cells in grid order)
```
if cell reuses a previous frame's entry:
    if EqualCells present and equalCells.read(1)==1:
        if same w/h: copy previous frame cell pixels ; else fill 0 (transparent)
        continue
entry = PixelBuffer[cell.ref]
nvals = number of distinct-run values in entry (1..4)
bits = 0 if nvals==1 else ceil(log2(nvals))
for each pixel in cell (row-major, cell.w*cell.h):
    idx = pcd.read(bits) if bits>0 else 0
    pixel = entry[idx]     # palette index; index 0 (=key[0]) is transparent
```
Assemble cells into the frame bitmap (respecting BottomUp), offset by (XOffset - fbMinX,
YOffset - fbMinY). Output per frame: width, height, offsetX/Y, and a 2D array of palette indices
(with 0/transparent flagged). Convert to RGBA with the act/char palette + the layer's colour shift.

## Notes / gotchas
- The PixelCodeAndDisplacement stream is shared across both passes and both purposes (pass-1 deltas
  and pass-2 index selectors) — read positions must be sequential/consistent.
- `key[0]` is the transparent index; a decoded palette index equal to it renders transparent.
- The "no width-1" cell rule and frame-cell→grid alignment are the fiddliest parts; if the first
  decode looks sheared/offset, that's where to look. Cross-check with OpenDiablo2's `d2dcc` source.

## Sources
- The Dcc File Format Description (d2imdev): https://github.com/gitustc/d2imdev/blob/master/dcc/dcc_doc/The%20Dcc%20File%20Format%20Description.htm
- OpenDiablo2 DCC transcoder (reference impl): https://github.com/OpenDiablo2/dcc
- Phrozen Keep DCC format thread: https://d2mods.info/forum/viewtopic.php?t=5480
