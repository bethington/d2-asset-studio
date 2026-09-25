"""Character equipped-appearance resolver + compositor.

Maps an ITEM to the layered character graphics that render it worn on the player, then composites
them into a paper-doll image. Mirrors the D2 data model:

- Every worn item repaints some of the 16 components (HD/TR/LG/RA/LA/RH/LH/SH/S1..S8).
- Body armor (armor.txt component==1) sets the six armor components from `nArmorComp[6]`
  (rArm,lArm,Torso,Legs,rSPad,lSPad), each a code 0/1/2 -> lit/med/hvy.
- A helm repaints HD, a weapon repaints RH (right hand) + drives the weapon-class token, a shield
  repaints SH. For these three the graphic token is the item's own `alternateGfx || code`
  (D2Inventory.cpp INVENTORY_InitializeComponentArray builds one entry per weapon/helm/shield keyed
  by its code) — NOT a lit/med/hvy tier. Helm vs shield is decided by which component's DCC exists.
- The rest of the body stays at the NAKED default ("lit").

A component's DCC file is `<class><comp><graphic><mode><wclass>.dcc`; the COF
(`<class><mode><wclass>.cof`) gives the per-(dir,frame) draw order and each component's own wclass
token. Gloves/boots/belts are not drawn on the character in D2, so they resolve to the base body.
"""
from __future__ import annotations

import io
import os

from PIL import Image

import app.assets as assets
from pyd2 import cof, colortransform, dcc

CLASS_TOKENS = {
    "amazon": "AM", "sorceress": "SO", "necromancer": "NE", "paladin": "PA",
    "barbarian": "BA",
    # D2 char-graphic folder tokens for the two expansion classes are NOT their class codes:
    # the Druid's art lives under DZ and the Assassin's under AI (AS/DR are used only in data tables).
    "druid": "DZ", "assassin": "AI",
}
ARMOR_GRAPHIC = {0: "lit", 1: "med", 2: "hvy"}          # nArmorComp code -> graphic token
# armor.txt nArmorComp column order -> component tokens
ARMOR_COMP_ORDER = [("rArm", "RA"), ("lArm", "LA"), ("Torso", "TR"),
                    ("Legs", "LG"), ("rSPad", "S1"), ("lSPad", "S2")]
# a naked body draws these components with the "lit" graphic
NAKED = {"HD": "lit", "TR": "lit", "LG": "lit", "RA": "lit", "LA": "lit"}

_TXT_CACHE = {}


def _txt_by_code(name: str) -> dict:
    if name not in _TXT_CACHE:
        data = assets.try_read_effective(f"data\\global\\excel\\{name}.txt")
        rows = {}
        if data:
            lines = data.decode("latin-1", "replace").replace("\r\n", "\n").replace("\r", "\n").split("\n")
            hdr = lines[0].split("\t")
            ci = {c: i for i, c in enumerate(hdr)}
            for ln in lines[1:]:
                if not ln.strip():
                    continue
                cells = ln.split("\t")
                code = cells[ci["code"]] if "code" in ci and ci["code"] < len(cells) else ""
                if code:
                    rows[code] = {c: (cells[i] if i < len(cells) else "") for c, i in ci.items()}
        _TXT_CACHE[name] = rows
    return _TXT_CACHE[name]


def _txt_by_col(name: str, key_col: str) -> dict:
    """Parse an excel .txt keyed by an arbitrary column (e.g. uniqueitems/setitems by 'index')."""
    ck = f"{name}::{key_col}"
    if ck not in _TXT_CACHE:
        data = assets.try_read_effective(f"data\\global\\excel\\{name}.txt")
        rows = {}
        if data:
            lines = data.decode("latin-1", "replace").replace("\r\n", "\n").replace("\r", "\n").split("\n")
            hdr = lines[0].split("\t")
            ci = {c: i for i, c in enumerate(hdr)}
            ki = ci.get(key_col, -1)
            for ln in lines[1:]:
                if not ln.strip():
                    continue
                cells = ln.split("\t")
                key = cells[ki] if 0 <= ki < len(cells) else ""
                if key:
                    rows[key] = {c: (cells[i] if i < len(cells) else "") for c, i in ci.items()}
        _TXT_CACHE[ck] = rows
    return _TXT_CACHE[ck]


def worn_colormap(code: str, category: str = "", name: str = "") -> list | None:
    """The 256-entry palette index remap D2 applies to an item's worn art, or None for no transform.

    Mirrors ITEMS_GetColor (D2Common Items.cpp): the colormap = D2CMP MixPalette(nTransform, nColor)
    where nTransform is the base item's `Transform` (armor/weapons.txt, 1..8) and nColor is the
    unique's/set's `chrtransform` colour code (colors.txt row, 0..20). Normal/magic/rare items get no
    worn recolour here (magic-affix colours need affix RE; uniques & sets are the visible cases)."""
    base = _txt_by_code("armor").get(code) or _txt_by_code("weapons").get(code)
    if not base:
        return None
    try:
        transform = int((base.get("Transform") or "0").strip() or "0")
    except ValueError:
        transform = 0
    if not (0 < transform < 9):
        return None
    ct = ""
    if category == "unique" and name:
        row = _txt_by_col("uniqueitems", "index").get(name)
        ct = (row.get("chrtransform") or "").strip().lower() if row else ""
    elif category == "set" and name:
        row = _txt_by_col("setitems", "index").get(name)
        ct = (row.get("chrtransform") or "").strip().lower() if row else ""
    color = colortransform.COLOR_INDEX.get(ct)
    if color is None:
        return None
    try:
        return colortransform.mix_palette(transform, color, reader=assets.try_read_effective)
    except (ValueError, FileNotFoundError):
        return None


def cof_path(cc: str, mode: str, wclass: str) -> str:
    return f"data\\global\\chars\\{cc}\\cof\\{cc}{mode}{wclass}.cof".upper()


def dcc_path(cc: str, comp: str, graphic: str, mode: str, wclass: str) -> str:
    return f"data\\global\\chars\\{cc}\\{comp}\\{cc}{comp}{graphic}{mode}{wclass}.dcc".upper()


def resolve(code: str, char_class: str = "barbarian", mode: str = "NU") -> dict | None:
    """Resolve an item to its equipped composite spec. Returns:
       {class, mode, wclass, cof (Cof), components: {comp_token: {graphic, dcc_rel}}}."""
    cc = CLASS_TOKENS.get(char_class.lower(), "BA")
    armor = _txt_by_code("armor").get(code)
    weapon = _txt_by_code("weapons").get(code)

    def _gfx(row):
        # worn graphic token = alternateGfx (any case) else the item code (D2Inventory.cpp:2788)
        return ((row.get("alternateGfx") or row.get("alternategfx") or "").strip().lower() or code)

    graphics = dict(NAKED)
    wclass = "hth"
    kind = "base"
    worn = []                                                     # the item's own component token(s)
    if armor and str(armor.get("component") or "").strip() == "1":  # body armor -> 6 components
        kind = "body-armor"
        for col, comp_tok in ARMOR_COMP_ORDER:
            v = (armor.get(col) or "").strip()
            if v != "" and v.isdigit():
                graphics[comp_tok] = ARMOR_GRAPHIC.get(int(v), "lit")
                worn.append(comp_tok)
    elif weapon:                                                  # weapon -> held hand; its wclass
        kind = "weapon"
        wclass = (weapon.get("wclass") or "hth").strip().lower() or "hth"
        # bows are drawn in the LEFT hand (BOW cof has an LH layer, no RH); everything else —
        # melee, staves, orbs, wands, claws, crossbows, throwing — is drawn in the right hand.
        slot = "LH" if wclass == "bow" else "RH"
        graphics[slot] = _gfx(weapon)
        worn = [slot]
    elif armor:                                                   # helm / shield: armor.txt `component`
        # `component` IS the composite slot index (0=HD helm, 7=SH shield); >=len -> not drawn
        # (gloves/boots/belt are component 16). This is authoritative — no file-probing / wclass guess.
        try:
            idx = int((armor.get("component") or "").strip())
        except ValueError:
            idx = -1
        if 0 <= idx < len(cof.COMPONENTS):
            tok = cof.COMPONENTS[idx]
            graphics[tok] = _gfx(armor)                           # helm replaces HD; shield adds SH
            worn = [tok]
            kind = {"HD": "helm", "SH": "shield"}.get(tok, "worn")
        # else: gloves/boots/belt etc. -> base body

    cof_rel = cof_path(cc, mode, wclass)
    cof_data = assets.try_read_effective(cof_rel)
    if not cof_data:
        return None
    c = cof.decode(cof_data)

    components = {}
    for layer in c.layers:
        comp_tok = layer.token
        if comp_tok not in graphics:
            continue                                             # not drawn for this item
        rel = dcc_path(cc, comp_tok, graphics[comp_tok], mode, layer.weapon_class)
        components[comp_tok] = {"graphic": graphics[comp_tok], "dcc_rel": rel,
                                "wclass": layer.weapon_class}
    return {"class": cc, "mode": mode, "wclass": wclass, "kind": kind,
            "cof": c, "components": components,
            # the item's own worn slot(s) actually present in this COF (naked base excluded)
            "worn": [t for t in worn if t in components]}


def worn_art_exists(spec) -> bool:
    """True if the item's own worn-slot art actually decodes for this class. Some graphics are
    class-restricted (e.g. Paladin auric shields exist only as PASH*, not BASH*), so a shield can be
    'supported' yet render nothing on the wrong class — the caller uses this to pick a default class."""
    if not spec or not spec.get("worn"):
        return False
    return any(_load_dcc(spec["components"][t]["dcc_rel"]) is not None
               for t in spec["worn"] if t in spec["components"])


def default_class_for(code: str, classes, mode: str = "NU") -> str | None:
    """First class whose graphics actually render this item's worn art (or None if none do)."""
    for cls in classes:
        try:
            if worn_art_exists(resolve(code, cls, mode)):
                return cls
        except Exception:  # noqa: BLE001
            continue
    return None


def describe(code: str, classes, mode: str = "NU"):
    """Identify an item's worn kind AND the classes whose art actually renders it, by resolving it
    across classes. Class-locked items (Assassin claws use wclass 'ht1', Sorceress orbs, Paladin
    auric shields) only have a COF/art on their own class, so probing a single class would wrongly
    report 'none'. Pass `classes` with the preferred default first. Returns (kind, available_list)."""
    kind = "none"
    available = []
    for cls in classes:
        try:
            spec = resolve(code, cls, mode)
        except Exception:  # noqa: BLE001
            continue
        if not spec:
            continue
        if kind in ("none", "base") and spec["kind"] not in ("none", "base"):
            kind = spec["kind"]                 # first real worn kind wins (class-independent)
        if worn_art_exists(spec):
            available.append(cls)
    return kind, available


_DCC_CACHE = {}


def _load_dcc(rel: str):
    if rel not in _DCC_CACHE:
        data = assets.try_read_effective(rel)
        _DCC_CACHE[rel] = dcc.decode(data) if data and data[:1] == b"\x74" else None
    return _DCC_CACHE[rel]


def animate(spec: dict, direction: int = 0, palette=None, item_only: bool = False, colormap=None):
    """All frames of a direction composited onto a COMMON canvas (so playback doesn't jitter).
    With item_only, only the item's own worn slots are drawn (the character body is hidden) — but the
    canvas is still sized from the whole character, so the item keeps its on-body size and position.
    With colormap (a 256-entry palette index remap), the item's worn slots are recoloured like the
    game (unique/set tint); the naked base body keeps the normal palette.
    Returns (frames: list[Image], anchor_x, anchor_y)."""
    if not spec:
        return [], 0, 0
    palette = palette or assets._palette()
    # the item's worn slots use a recoloured palette; everything else the normal one
    worn_palette = [palette[colormap[i]] for i in range(256)] if colormap else palette
    c = spec["cof"]
    comp_id = {t: i for i, t in enumerate(cof.COMPONENTS)}
    worn_ids = {comp_id[t] for t in spec.get("worn", []) if t in comp_id}   # the item's own slot(s)
    dccs = {tok: _load_dcc(info["dcc_rel"]) for tok, info in spec["components"].items()}

    per_frame = []               # per frame: {component_id: DccFrame}
    minx = miny = 10 ** 9
    maxx = maxy = -10 ** 9
    for f in range(c.frames):
        fb = {}
        for tok, info in spec["components"].items():
            dc = dccs.get(tok)
            if not dc or direction >= dc.num_directions:
                continue
            frames = dc.direction(direction).frames
            if not frames:
                continue
            fr = frames[f % len(frames)]
            fb[comp_id[tok]] = fr
            minx = min(minx, fr.x_offset); maxx = max(maxx, fr.x_offset + fr.width)
            miny = min(miny, fr.y_offset); maxy = max(maxy, fr.y_offset + fr.height)
        per_frame.append(fb)
    if maxx < minx:
        return [], 0, 0
    W, H = maxx - minx, maxy - miny
    out = []
    for f in range(c.frames):
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for cid in c.draw_order(direction, f % c.frames):
            if item_only and cid not in worn_ids:      # hide the character body, keep the item
                continue
            fr = per_frame[f].get(cid)
            if fr is None:
                continue
            pal = worn_palette if cid in worn_ids else palette   # recolour only the item's slots
            w, h, rgba = dcc.frame_to_rgba(fr, pal)
            canvas.alpha_composite(Image.frombytes("RGBA", (w, h), rgba),
                                   (fr.x_offset - minx, fr.y_offset - miny))
        out.append(canvas)
    return out, -minx, -miny


def composite(spec: dict, direction: int = 0, frame: int = 0, palette=None) -> Image.Image | None:
    """Composite one (direction, frame) of the equipped character into an RGBA image, layers in the
    COF draw order at each component's DCC offset. Anchor (feet) is at the image's bottom-centre."""
    if not spec:
        return None
    palette = palette or assets._palette()
    c = spec["cof"]
    comp_id_by_token = {t: i for i, t in enumerate(cof.COMPONENTS)}

    # gather decoded frames for the drawn components
    drawn = []   # (component_id, DccFrame)
    for tok, info in spec["components"].items():
        dc = _load_dcc(info["dcc_rel"])
        if not dc or direction >= dc.num_directions:
            continue
        frames = dc.direction(direction).frames
        if not frames:
            continue
        fr = frames[frame % len(frames)]
        drawn.append((comp_id_by_token[tok], fr))
    if not drawn:
        return None

    # canvas = union of all component boxes (offsets are relative to the anchor at feet)
    minx = min(fr.x_offset for _, fr in drawn)
    maxx = max(fr.x_offset + fr.width for _, fr in drawn)
    miny = min(fr.y_offset for _, fr in drawn)
    maxy = max(fr.y_offset + fr.height for _, fr in drawn)
    W, H = maxx - minx, maxy - miny
    if W <= 0 or H <= 0:
        return None
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    order = c.draw_order(direction, frame % c.frames)      # component ids, back -> front
    frame_by_comp = {}
    for cid, fr in drawn:
        frame_by_comp.setdefault(cid, fr)
    for cid in order:
        fr = frame_by_comp.get(cid)
        if fr is None:
            continue
        w, h, rgba = dcc.frame_to_rgba(fr, palette)
        img = Image.frombytes("RGBA", (w, h), rgba)
        canvas.alpha_composite(img, (fr.x_offset - minx, fr.y_offset - miny))
    return canvas
