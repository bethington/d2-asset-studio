"""COF (Component Object Format) reader — the character-composite layout.

A COF (`<class><mode><wclass>.cof`) says, for one (char class, animation mode, weapon class):
which of the 16 components are drawn, each component's own weapon-class token (used to build its
DCC filename), per-layer flags (shadow / transparency / draw-effect), and the DRAW ORDER per
(direction, frame).

Layout (verified against the PD2 MPQs, e.g. AMNUHTH.cof):
  0x00  u8  layers          (nLayers)
  0x01  u8  frames          (frames per direction)
  0x02  u8  directions
  0x03  u8  version         (20)
  0x04  6×i32               unknown + bounding box + anim rate (not needed here)
  0x1C  nLayers × 9 bytes   layer records
  ...   nFrames bytes       keyframe/action flags (one per frame)
  ...   nDirs*nFrames*nLayers bytes   per-(dir,frame) draw order (component ids, back→front)

Each 9-byte layer record: component(u8), castShadow(u8), selectable(u8), transparent(u8),
drawEffect(u8), weaponClass(3 chars, space/NUL-trimmed).
"""
from __future__ import annotations

from dataclasses import dataclass

# The 16 composite components, index = value in the COF (mirrors D2Composit.h `enum Composits`).
COMPONENTS = ["HD", "TR", "LG", "RA", "LA", "RH", "LH", "SH",
              "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]


@dataclass
class CofLayer:
    component: int          # index into COMPONENTS
    shadow: int
    selectable: int
    transparent: int
    draw_effect: int
    weapon_class: str       # 3-char token, e.g. "1ht", "hth" — used to build the DCC filename

    @property
    def token(self) -> str:
        return COMPONENTS[self.component]


@dataclass
class Cof:
    layers: list             # list[CofLayer]
    frames: int              # frames per direction
    directions: int
    keyframes: list          # nFrames action flags
    # order[dir][frame] = list of component ids in draw order (back→front)
    order: list

    def layer_for(self, component_id: int):
        for l in self.layers:
            if l.component == component_id:
                return l
        return None

    def draw_order(self, direction: int, frame: int) -> list:
        """Component ids to draw, back→front, for a given direction+frame (falls back to the
        layer-record order if the priority block is absent/short)."""
        try:
            return self.order[direction][frame]
        except (IndexError, TypeError):
            return [l.component for l in self.layers]


def decode(data: bytes) -> Cof:
    n_layers = data[0]
    n_frames = data[1]
    n_dirs = data[2]
    off = 28
    layers = []
    for i in range(n_layers):
        r = data[off + i * 9: off + i * 9 + 9]
        layers.append(CofLayer(
            component=r[0], shadow=r[1], selectable=r[2], transparent=r[3], draw_effect=r[4],
            weapon_class=r[5:8].decode("latin-1").replace("\x00", "").strip().lower()))
    off += n_layers * 9

    keyframes = list(data[off: off + n_frames])
    off += n_frames

    # per-(direction, frame) draw order: nDirs * nFrames records of nLayers component ids
    order = []
    need = n_dirs * n_frames * n_layers
    if len(data) - off >= need:
        for d in range(n_dirs):
            dir_frames = []
            for f in range(n_frames):
                rec = list(data[off: off + n_layers])
                off += n_layers
                dir_frames.append(rec)
            order.append(dir_frames)
    return Cof(layers=layers, frames=n_frames, directions=n_dirs,
               keyframes=keyframes, order=order)
