"""Blender headless render driver (S4) — GLB -> transparent sprite at a chosen angle.

Locates blender.exe, runs blender/render_glb.py in --background. Returns the PNG path(s).
"""

from __future__ import annotations

import glob
import os
import subprocess

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "blender", "render_glb.py")

_CANDIDATES = [
	r"C:\Program Files\Blender Foundation\*\blender.exe",
	r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe",
]


def blender_exe() -> str | None:
	if os.environ.get("BLENDER_EXE") and os.path.exists(os.environ["BLENDER_EXE"]):
		return os.environ["BLENDER_EXE"]
	for pat in _CANDIDATES:
		hits = sorted(glob.glob(pat))
		if hits:
			return hits[-1]  # newest version
	return None


def available() -> bool:
	return blender_exe() is not None


def open_gui(glb_path: str) -> bool:
	"""Launch the Blender GUI with a GLB loaded (non-blocking) so the user can inspect/tweak
	the model for later work. Returns True if launched."""
	exe = blender_exe()
	if not exe or not os.path.exists(glb_path):
		return False
	# --python-expr imports the GLB into a fresh scene on startup
	expr = ("import bpy; bpy.ops.wm.read_homefile(use_empty=True); "
	        f"bpy.ops.import_scene.gltf(filepath=r'{os.path.abspath(glb_path)}')")
	subprocess.Popen([exe, "--python-expr", expr])
	return True


def render(glb_path: str, out_png: str, *, size: int = 256, azim: float = 0.0,
           elev: float = 20.0, frames: int = 1, azim_step: float = 45.0,
           res_x: int = 0, res_y: int = 0, margin: float = 1.06,
           pair: bool = False, pair_yaw: float = 0.0, pair_gap: float = 0.55,
           pair_depth: float = 0.0, samples: int = 48,
           orient: tuple = (),
           timeout: float = 480.0) -> list[str]:
	"""Render glb_path to out_png (transparent, orthographic) at (azim, elev). Returns paths.

	res_x/res_y render at the item's cell aspect ratio (tight-framed to the object); 0 falls
	back to a square `size`. margin is the framing breathing room (1.0 = flush).

	`pair` renders the model twice as a mirrored left/right pair in ONE scene, so the
	hands really occlude each other and Cycles casts a true shadow between them.
	"""
	exe = blender_exe()
	if not exe:
		raise RuntimeError("Blender not found (set BLENDER_EXE or install Blender)")
	os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
	args = [exe, "--background", "--factory-startup", "--python", os.path.abspath(_SCRIPT), "--",
	        "--glb", os.path.abspath(glb_path), "--out", os.path.abspath(out_png),
	        "--size", str(int(size)), "--res_x", str(int(res_x)), "--res_y", str(int(res_y)),
	        "--margin", str(float(margin)),
	        "--azim", str(float(azim)), "--elev", str(float(elev)),
	        "--frames", str(int(frames)), "--azim-step", str(float(azim_step)),
	        "--samples", str(int(samples)),
	        "--pair", "1" if pair else "0", "--pair_yaw", str(float(pair_yaw)),
	        "--pair_gap", str(float(pair_gap)), "--pair_depth", str(float(pair_depth)),
	        "--orient", ";".join("%f,%f" % (float(a), float(e)) for a, e in (orient or []))]
	proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
	out = proc.stdout + proc.stderr
	oks = [ln.split(" ", 1)[1].strip() for ln in out.splitlines() if ln.startswith("RENDER_OK ")]
	if not oks:
		err = next((ln for ln in out.splitlines() if "RENDER_ERROR" in ln or "Error" in ln), "unknown")
		raise RuntimeError(f"Blender render produced no output ({err})")
	return oks
