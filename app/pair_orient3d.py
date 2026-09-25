"""Work out how a Meshy glove model is oriented, so the pair can be auto-fitted.

A glove is a flattish slab, so its principal axes give the orientation almost for free:
the axis it varies LEAST along is the palm->back normal, and the axis it varies MOST
along is the finger direction. That fixes the two rotations that matter -- back of the
hand square to the camera, fingers upright -- without any guessing.

What principal axes cannot give is SIGNS: they are undirected lines, so they cannot tell
back from palm, or fingers from cuff. Those four combinations are resolved by rendering
each one and scoring the silhouette, reusing the same edge measurement as the 2D auto-fit.
"""
from __future__ import annotations

import json
import math
import struct

import numpy as np

# glTF component types -> numpy dtypes
_CTYPE = {5120: "<i1", 5121: "<u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _chunks(path: str):
    """Split a .glb into its JSON chunk and its binary chunk."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"glTF":
        raise ValueError("not a glb")
    n = len(data)
    off, js, bin_ = 12, None, b""
    while off + 8 <= n:
        clen, ctype = struct.unpack_from("<II", data, off)
        body = data[off + 8: off + 8 + clen]
        if ctype == 0x4E4F534A:      # 'JSON'
            js = json.loads(body.decode("utf-8"))
        elif ctype == 0x004E4942:    # 'BIN'
            bin_ = body
        off += 8 + clen
    if js is None:
        raise ValueError("glb has no JSON chunk")
    return js, bin_


def _accessor(js, bin_, idx) -> np.ndarray:
    acc = js["accessors"][idx]
    ncomp = _NCOMP[acc["type"]]
    dt = np.dtype(_CTYPE[acc["componentType"]])
    count = acc["count"]
    if "bufferView" not in acc:
        return np.zeros((count, ncomp), dtype=np.float32)
    bv = js["bufferViews"][acc["bufferView"]]
    base = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = bv.get("byteStride") or (dt.itemsize * ncomp)
    if stride == dt.itemsize * ncomp:
        raw = np.frombuffer(bin_, dtype=dt, count=count * ncomp, offset=base)
        return raw.reshape(count, ncomp).astype(np.float32)
    # interleaved: pull each element out at its own stride
    out = np.empty((count, ncomp), dtype=np.float32)
    for i in range(count):
        out[i] = np.frombuffer(bin_, dtype=dt, count=ncomp, offset=base + i * stride)
    return out


def _node_matrix(node) -> np.ndarray:
    if "matrix" in node:                       # glTF matrices are column-major
        return np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T
    m = np.eye(4)
    if "scale" in node:
        m = np.diag([*node["scale"], 1.0]) @ m
    if "rotation" in node:
        x, y, z, w = node["rotation"]
        r = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0],
            [0, 0, 0, 1]])
        m = r @ m
    if "translation" in node:
        t = np.eye(4)
        t[:3, 3] = node["translation"]
        m = t @ m
    return m


def vertices(path: str, max_points: int = 60000) -> np.ndarray:
    """Every mesh vertex in the file, in scene space (node transforms applied)."""
    js, bin_ = _chunks(path)
    nodes = js.get("nodes", [])
    meshes = js.get("meshes", [])
    out = []

    def walk(idx, parent):
        node = nodes[idx]
        world = parent @ _node_matrix(node)
        if "mesh" in node:
            for prim in meshes[node["mesh"]].get("primitives", []):
                pos = prim.get("attributes", {}).get("POSITION")
                if pos is None:
                    continue
                v = _accessor(js, bin_, pos)
                h = np.hstack([v, np.ones((len(v), 1), dtype=np.float32)])
                out.append((world @ h.T).T[:, :3])
        for c in node.get("children", []):
            walk(c, world)

    scenes = js.get("scenes", [])
    roots = scenes[js.get("scene", 0)]["nodes"] if scenes else range(len(nodes))
    for r in roots:
        walk(r, np.eye(4))
    if not out:
        raise ValueError("no POSITION data in glb")
    V = np.vstack(out).astype(np.float64)
    if len(V) > max_points:                     # PCA does not need every vertex
        V = V[np.random.default_rng(0).choice(len(V), max_points, replace=False)]
    return V


def principal_frame(V: np.ndarray) -> dict:
    """Principal axes of the vertex cloud, ordered thinnest -> longest.

    Returns the axes plus the extent along each, which is what tells us the model really
    is slab-shaped (and so that the 'thinnest axis is the palm normal' assumption holds).
    """
    c = V.mean(axis=0)
    X = V - c
    # SVD of the centred cloud: right singular vectors are the principal axes
    _u, s, vt = np.linalg.svd(X, full_matrices=False)
    order = np.argsort(s)                       # ascending: thinnest first
    axes = vt[order]                            # rows: normal, width, length
    ext = []
    for a in axes:
        p = X @ a
        ext.append(float(p.max() - p.min()))
    return {
        "centre": c,
        "normal": axes[0],       # palm -> back
        "width": axes[1],        # thumb -> pinky
        "length": axes[2],       # cuff -> fingers
        "extent": ext,           # along normal, width, length
        "slabness": ext[2] / max(ext[0], 1e-9),
        "flatness": ext[1] / max(ext[0], 1e-9),
    }


def triangles(path: str):
    """Vertices and triangle indices in scene space, for area-weighted normals."""
    js, bin_ = _chunks(path)
    nodes, meshes = js.get("nodes", []), js.get("meshes", [])
    verts, faces, base = [], [], 0

    def walk(idx, parent):
        nonlocal base
        node = nodes[idx]
        world = parent @ _node_matrix(node)
        if "mesh" in node:
            for prim in meshes[node["mesh"]].get("primitives", []):
                if prim.get("mode", 4) != 4:              # triangles only
                    continue
                pos = prim.get("attributes", {}).get("POSITION")
                if pos is None or "indices" not in prim:
                    continue
                v = _accessor(js, bin_, pos)
                h = np.hstack([v, np.ones((len(v), 1), dtype=np.float32)])
                verts.append((world @ h.T).T[:, :3])
                idxs = _accessor(js, bin_, prim["indices"]).astype(np.int64).reshape(-1)
                faces.append(idxs.reshape(-1, 3) + base)
                base += len(v)
        for c in node.get("children", []):
            walk(c, world)

    scenes = js.get("scenes", [])
    for r in (scenes[js.get("scene", 0)]["nodes"] if scenes else range(len(nodes))):
        walk(r, np.eye(4))
    if not verts:
        raise ValueError("no triangles in glb")
    return np.vstack(verts).astype(np.float64), np.vstack(faces)


def surface_normal_axis(path: str) -> dict:
    """The palm->back normal, from area-weighted surface normals.

    Far more robust than vertex PCA: the palm and the back are by a wide margin the two
    largest flat surfaces on a glove, so the dominant direction among triangle normals IS
    the palm normal. Vertex PCA instead measures the point cloud's shape, which on these
    gauntlets is nearly square in cross-section (flatness as low as 1.06) and so picks the
    axis almost arbitrarily.

    Normals are folded into a scatter matrix (n n^T), which is sign-blind -- exactly right
    here, since the palm and back point opposite ways but describe the same axis.
    """
    V, F = triangles(path)
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    cross = np.cross(b - a, c - a)
    area = np.linalg.norm(cross, axis=1)
    keep = area > 1e-12
    n = cross[keep] / area[keep, None]
    w = area[keep]
    S = (n * w[:, None]).T @ n / w.sum()
    evals, evecs = np.linalg.eigh(S)
    axis = evecs[:, int(np.argmax(evals))]
    return {"normal": axis / np.linalg.norm(axis),
            "dominance": float(evals.max() / max(evals.sum(), 1e-12))}


def _sphere_dirs(n: int = 4000) -> np.ndarray:
    """Roughly even directions on a hemisphere (Fibonacci lattice)."""
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - i / n)          # hemisphere: z >= 0
    theta = math.pi * (1 + 5 ** 0.5) * i
    return np.column_stack([np.cos(theta) * np.sin(phi),
                            np.sin(theta) * np.sin(phi), np.cos(phi)])


def widest_view_axis(path: str, refine: bool = True) -> dict:
    """The direction that shows the most of the model -- i.e. the flat of the hand.

    Projected area along d is exactly 0.5 * sum(area_i * |n_i . d|), so this is computed
    from the mesh directly rather than by rendering and looking. Maximising it finds the
    palm normal even on chunky gauntlets, where 'thinnest principal axis' and 'dominant
    surface normal' both proved unreliable (measured dominance only 0.41-0.53).
    """
    V, F = triangles(path)
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    cross = np.cross(b - a, c - a)
    area = np.linalg.norm(cross, axis=1)
    keep = area > 1e-12
    nrm = cross[keep] / area[keep, None]
    w = area[keep]

    def proj(dirs):
        # chunked: a big gauntlet is ~800k triangles, so the full outer product would
        # ask for tens of GB
        out = np.empty(len(dirs))
        step = max(1, int(2e7 // max(len(nrm), 1)))
        for i in range(0, len(dirs), step):
            d = dirs[i:i + step]
            out[i:i + step] = 0.5 * (np.abs(nrm @ d.T) * w[:, None]).sum(axis=0)
        return out

    dirs = _sphere_dirs()
    vals = proj(dirs)
    best = dirs[int(np.argmax(vals))]
    peak = float(vals.max())
    if refine:                                   # local jitter to sharpen the peak
        for scale in (0.08, 0.02, 0.005):
            rng = np.random.default_rng(0)
            cand = best + rng.normal(0, scale, (400, 3))
            cand /= np.linalg.norm(cand, axis=1)[:, None]
            v = proj(cand)
            if v.max() > peak:
                peak, best = float(v.max()), cand[int(np.argmax(v))]
    total = float(w.sum())
    return {"normal": best, "projected": peak,
            # how much flatter this view is than an average one; >1.3 means clearly slab-like
            "anisotropy": peak / max(0.25 * total, 1e-12)}


def finger_frame(V: np.ndarray) -> dict:
    """Principal frame measured on the FINGER half only.

    Whole-model PCA picks the palm normal badly: measured across these gauntlets the
    width and thickness come out near-equal (flatness as low as 1.06), because the bulky
    cuff is round in cross-section, so the 'thinnest axis' can land on either. The finger
    half is genuinely flat -- fingers splay in the plane of the hand -- and measures ~2.0
    to 2.3 there, which is a clear enough margin to trust.

    Which end is the fingers is decided by the same measurement: the flatter end.
    """
    g = principal_frame(V)
    L, c = g["length"], g["centre"]
    t = (V - c) @ L
    lo, hi = np.percentile(t, 5), np.percentile(t, 95)
    span = hi - lo
    halves = {}
    for name, sel in (("neg", t <= lo + 0.45 * span), ("pos", t >= hi - 0.45 * span)):
        W = V[sel]
        halves[name] = principal_frame(W) if len(W) >= 50 else None

    fl = {k: (v["flatness"] if v else -1.0) for k, v in halves.items()}
    finger_end = "pos" if fl["pos"] >= fl["neg"] else "neg"
    f = halves[finger_end] or g
    # the finger direction points from the model centre TOWARD the finger end
    length = L if finger_end == "pos" else -L
    normal = f["normal"]
    # re-orthogonalise against the (more reliable) global long axis
    normal = normal - length * float(normal @ length)
    n = np.linalg.norm(normal)
    normal = normal / n if n > 1e-9 else f["normal"]
    width = np.cross(length, normal)
    return {
        "centre": c, "normal": normal, "width": width, "length": length,
        "finger_flatness": float(fl[finger_end]),
        "flatness_margin": float(abs(fl["pos"] - fl["neg"])),
        "confident": bool(abs(fl["pos"] - fl["neg"]) >= 0.25),
    }


def basis_matrix(f: dict, normal_sign: float = 1.0) -> np.ndarray:
    """Rotation taking the model into fit space: +X = width, +Y = up, +Z = toward viewer.

    Fingers are put at the BOTTOM (-Y), matching the original game art and the renders
    already approved. `normal_sign` selects back-of-hand vs palm; which one is correct is
    decided by looking at a render, not guessed here.
    """
    z = f["normal"] * normal_sign          # toward the viewer
    y = -f["length"]                       # fingers point down, so up is -length
    z = z - y * float(z @ y)
    z /= max(np.linalg.norm(z), 1e-9)
    x = np.cross(y, z)
    x /= max(np.linalg.norm(x), 1e-9)
    # rows map model space -> fit space
    return np.array([x, y, z])


def euler_zyx(R: np.ndarray) -> tuple[float, float, float]:
    """R (rows = fit-space basis) as intrinsic XYZ euler degrees, for both renderers."""
    M = R.T                                 # fit -> model is the transform we apply
    sy = math.sqrt(M[0, 0] ** 2 + M[1, 0] ** 2)
    if sy > 1e-6:
        rx = math.atan2(M[2, 1], M[2, 2])
        ry = math.atan2(-M[2, 0], sy)
        rz = math.atan2(M[1, 0], M[0, 0])
    else:
        rx, ry, rz = math.atan2(-M[1, 2], M[1, 1]), math.atan2(-M[2, 0], sy), 0.0
    return tuple(round(math.degrees(v), 4) for v in (rx, ry, rz))


def orient_candidates(path: str) -> dict:
    """The two orientations worth rendering: back-of-hand, and its palm-side flip."""
    V = vertices(path)
    f = finger_frame(V)
    return {
        "frame": f,
        "candidates": [
            {"normal_sign": s, "euler": euler_zyx(basis_matrix(f, s))}
            for s in (1.0, -1.0)
        ],
    }


NATIVE_AXIS = np.array([0.0, 0.0, 1.0])   # glTF axis the default camera (azim 0, elev 0) looks along


def camera_refine(path: str, cone_deg: float = 30.0, samples: int = 6000) -> dict:
    """Camera angles that put the flat of the hand square to the viewer.

    Meshy authors these models already close to face-on, which changes the problem
    completely: instead of deducing the pose from scratch -- hopeless here, because the
    palm normal is only weakly determined on chunky gauntlets (widest/narrowest projected
    area measures just 1.3-2.3, where >4 would be decisive) -- we only refine within a
    small cone of where the model already sits. Inside that cone the projected area has
    one clean peak, and both 180-degree sign ambiguities are already resolved by the
    model's own authoring.

    Returns azim/elev for the existing renderers; no new orientation plumbing needed.
    """
    V, F = triangles(path)
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    cr = np.cross(b - a, c - a)
    ar = np.linalg.norm(cr, axis=1)
    keep = ar > 1e-12
    nrm, w = cr[keep] / ar[keep, None], ar[keep]

    def proj(dirs):
        out = np.empty(len(dirs))
        step = max(1, int(2e7 // max(len(nrm), 1)))
        for i in range(0, len(dirs), step):
            out[i:i + step] = 0.5 * (np.abs(nrm @ dirs[i:i + step].T) * w[:, None]).sum(axis=0)
        return out

    dirs = _sphere_dirs(samples)
    ang = np.degrees(np.arccos(np.clip(np.abs(dirs @ NATIVE_AXIS), 0, 1)))
    sel = dirs[ang <= cone_deg]
    if not len(sel):
        sel = NATIVE_AXIS[None, :]
    vals = proj(sel)
    best = sel[int(np.argmax(vals))]
    if float(best @ NATIVE_AXIS) < 0:
        best = -best
    native = float(proj(NATIVE_AXIS[None, :])[0])

    # glTF (x, y, z) -> the renderers' azim/elev, where azim 0 / elev 0 looks along -Z
    bx, by, bz = (float(v) for v in best)
    azim = math.degrees(math.atan2(bx, bz))
    elev = math.degrees(math.asin(max(-1.0, min(1.0, by))))
    return {
        "azim": round(azim, 2), "elev": round(elev, 2),
        "gain": round(float(vals.max()) / max(native, 1e-9) - 1.0, 4),
        "moved_deg": round(float(np.degrees(np.arccos(min(1, abs(float(best @ NATIVE_AXIS)))))), 2),
    }


def _edge_score(png_path: str):
    """Straight-run fraction and tilt on each outer edge of a rendered single hand.

    Reuses the 2D auto-fit's measurement so the 3D and 2D pipelines judge 'pinky edge'
    by exactly the same rule.
    """
    from PIL import Image

    import app.glove_pairs as gp

    img = Image.open(png_path).convert("RGBA")
    bb = img.split()[-1].getbbox()
    if bb:
        img = img.crop(bb)
    if img.height > gp.EDGE_SAMPLE_ROWS:
        img = img.resize((max(1, round(img.width * gp.EDGE_SAMPLE_ROWS / img.height)),
                          gp.EDGE_SAMPLE_ROWS), Image.LANCZOS)
    return {s: gp.longest_straight_run(gp._outer_edge_points(img, s)) for s in ("L", "R")}


def squaring_from_camera(azim_deg: float, elev_deg: float) -> list:
    """Turn the camera angles you dialled in into a squaring baked onto the MODEL.

    Rotating the model by the inverse of the camera's orbit leaves the picture pixel-for
    -pixel identical, but the model is now genuinely square to a camera sitting back at
    azim 0 / elev 0. That matters because the roll ('tilt apart') happens about the view
    axis: while the model sits crooked, rolling curls the hands into each other instead
    of fanning them out.

    Returned row-major so the same 9 numbers drive Blender and three.js.
    """
    az, el = math.radians(azim_deg), math.radians(elev_deg)
    # The camera sits at (cos el sin az, sin el, cos el cos az) -- note +sin(el) in Y, so
    # the elevation rotation is by MINUS el. Getting that sign backwards makes baking tip
    # the model the wrong way instead of leaving the picture untouched.
    ry = np.array([[math.cos(az), 0.0, math.sin(az)],
                   [0.0, 1.0, 0.0],
                   [-math.sin(az), 0.0, math.cos(az)]])
    rx = np.array([[1.0, 0.0, 0.0],
                   [0.0, math.cos(el), math.sin(el)],
                   [0.0, -math.sin(el), math.cos(el)]])
    # C's columns are the camera basis; rotating the model by its transpose reproduces
    # that view from a camera back at azim 0 / elev 0
    M = (ry @ rx).T
    return [round(float(v), 9) for v in M.reshape(-1)]


# three.js/glTF is Y-up; Blender's importer converts to Z-up. (x, y, z) -> (x, -z, y)
_Y2Z = np.array([[1.0, 0.0, 0.0],
                 [0.0, 0.0, -1.0],
                 [0.0, 1.0, 0.0]])


def to_blender_frame(m: list | None) -> list:
    """Re-express a squaring from the browser's frame in Blender's.

    The squaring is authored in three.js, which keeps glTF's Y-up axes. Blender's glTF
    importer rotates the model to Z-up, so the same nine numbers would be a DIFFERENT
    rotation there. Conjugating by the axis swap keeps both engines showing one pose.
    """
    if not m or len(m) != 9:
        return []
    R = np.array(m, dtype=float).reshape(3, 3)
    return [round(float(v), 9) for v in (_Y2Z @ R @ _Y2Z.T).reshape(-1)]


def compose_squaring(existing: list | None, azim_deg: float, elev_deg: float) -> list:
    """Fold a fresh camera correction into whatever squaring the model already carries."""
    new = np.array(squaring_from_camera(azim_deg, elev_deg), dtype=float).reshape(3, 3)
    if existing and len(existing) == 9:
        cur = np.array(existing, dtype=float).reshape(3, 3)
        new = new @ cur
    return [round(float(v), 9) for v in new.reshape(-1)]


IDENTITY_SQUARING = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]


def projected_extent(path: str, roll_deg: float = 0.0) -> tuple[float, float]:
    """Width and height the model covers on screen at the native camera, after `roll`.

    Computed from the vertices rather than a render, so the framing maths needs no extra
    round trip. At the native camera the image axes are simply glTF X (right) and Y (up).
    """
    V = vertices(path)
    r = math.radians(roll_deg)
    x, y = V[:, 0], V[:, 1]
    xr = x * math.cos(r) - y * math.sin(r)
    yr = x * math.sin(r) + y * math.cos(r)
    return float(xr.max() - xr.min()), float(yr.max() - yr.min())


def autofit_pose3d(glb_path: str, out_w: int, out_h: int, render_fn,
                   workdir: str | None = None) -> dict:
    """Auto-fit a 3D pair the way the 2D auto-fit fits flat art.

    The camera stays at the model's native angle -- measured across these models, every
    geometric 'find the face-on view' criterion disagreed with the pose Meshy already
    authored, and the native pose is the one that reads correctly. What IS measured is
    the roll and the framing, both from the rendered silhouette:

      * roll   -- the pinky edge is the long straight run down one outer side, so rolling
                  by its tilt stands it parallel to the image border.
      * side   -- each hand goes to the border its pinky faces (mirroring puts the other
                  hand's pinky on the opposite side automatically, so both end up outward).
      * gap    -- chosen so the pair's aspect matches the sprite's, which makes it fill the
                  height AND touch both side borders at margin 1.0. If that means the
                  hands must overlap, they overlap -- height wins.
    """
    import os
    import tempfile

    workdir = workdir or tempfile.mkdtemp(prefix="autofit3d_")
    os.makedirs(workdir, exist_ok=True)
    probe = os.path.join(workdir, "probe.png")
    render_fn(glb_path, probe, 0.0)              # one hand, native camera, no roll
    runs = _edge_score(probe)

    # the pinky side is the one carrying the longer straight run
    side = "R" if runs["R"]["frac"] >= runs["L"]["frac"] else "L"
    roll = float(runs[side]["tilt"])
    margin_frac = round(abs(runs["R"]["frac"] - runs["L"]["frac"]), 3)

    w, h = projected_extent(glb_path, roll)
    aspect = max(out_w, 1) / max(out_h, 1)
    # hands sit at +/- gap*w and each is w wide, so the pair spans w * (2*gap + 1)
    target_w = h * aspect
    gap = (target_w / max(w, 1e-9) - 1.0) / 2.0
    overlapped = gap < 0.0
    gap = max(gap, -0.45)                        # never let one hand swallow the other
    # gap's SIGN says which side the unmirrored model goes: its pinky must face outward
    signed_gap = gap if side == "R" else -gap

    return {
        "azim": 0.0, "elev": 0.0,
        "yaw": round(roll, 2),
        "gap": round(signed_gap, 4),
        "depth": 0.35,
        "margin": 1.0,                           # flush: no padding around the pair
        "_pinky_side": side,
        "_pinky_confidence": margin_frac,
        "_run_frac": round(runs[side]["frac"], 3),
        "_overlapped": overlapped,
        "_probe": probe,
    }


def solve(glb_path: str, render_fn=None, workdir: str | None = None) -> dict:
    """Full orientation solve for one model.

    Geometry fixes the two rotations that matter; a render of each of the two remaining
    sign choices decides back-of-hand versus palm, by which one puts the pinky's long
    straight edge on the OUTER side (the model is placed on the right of the sprite, so
    its pinky must face right). The winning render also supplies the residual roll.
    """
    import os
    import tempfile

    cand = orient_candidates(glb_path)
    frame = cand["frame"]
    out = {"candidates": [], "geometry_only": render_fn is None,
           "finger_flatness": round(frame["finger_flatness"], 2),
           "flatness_margin": round(frame["flatness_margin"], 2),
           "confident_ends": frame["confident"]}
    workdir = workdir or tempfile.mkdtemp(prefix="orient3d_")

    best = None
    for i, c in enumerate(cand["candidates"]):
        row = {"normal_sign": c["normal_sign"], "euler": c["euler"]}
        if render_fn is not None:
            png = os.path.join(workdir, "cand%d.png" % i)
            try:
                render_fn(glb_path, png, c["euler"])
                runs = _edge_score(png)
                # pinky must end up on the RIGHT edge; score is how much better R is
                row["run_R"] = round(runs["R"]["frac"], 3)
                row["run_L"] = round(runs["L"]["frac"], 3)
                row["score"] = row["run_R"] - row["run_L"]
                row["tilt"] = round(runs["R"]["tilt"], 2)
                row["png"] = png
            except Exception as e:  # noqa: BLE001
                row["error"] = str(e)[:200]
        out["candidates"].append(row)
        if "score" in row and (best is None or row["score"] > best["score"]):
            best = row

    if best is None:                       # no renderer: fall back to the geometric guess
        best = dict(out["candidates"][0], score=None, tilt=0.0)
        out["fallback"] = "no renderer available -- using the geometric orientation unchecked"

    # roll so the measured pinky edge stands parallel to the border, exactly as the 2D
    # auto-fit does. `tilt` is dx per dy, so rolling by +tilt stands it upright.
    out["euler"] = best["euler"]
    out["roll"] = round(float(best.get("tilt") or 0.0), 2)
    out["normal_sign"] = best["normal_sign"]
    out["decided_by"] = "render" if best.get("score") is not None else "geometry"
    out["margin"] = None if best.get("score") is None else round(best["score"], 3)
    return out


def describe(path: str) -> dict:
    V = vertices(path)
    f = principal_frame(V)
    return {
        "n": int(len(V)),
        "extent": [round(x, 4) for x in f["extent"]],
        "slabness": round(f["slabness"], 2),
        "flatness": round(f["flatness"], 2),
        "normal": [round(x, 3) for x in f["normal"]],
        "length_axis": [round(x, 3) for x in f["length"]],
    }
