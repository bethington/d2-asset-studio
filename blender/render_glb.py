"""Headless Blender render: GLB -> transparent PNG at a chosen camera angle.

Invoked as:
  blender --background --factory-startup --python render_glb.py -- \
    --glb model.glb --out out.png --size 256 --azim 0 --elev 20 [--frames 1 --azim-step 45]

Renders an orthographic, transparent-background sprite of the model, framed to fill the image.
For a single item sprite use --frames 1. For a turntable / unit directions use --frames N with
--azim-step (each frame N gets azim + N*azim-step) writing out_000.png, out_001.png, ...
Uses Cycles CPU (reliable headless) with even studio lighting.
"""

import math
import sys

import bpy
import mathutils


def argv_after_dashes():
	a = sys.argv
	return a[a.index("--") + 1:] if "--" in a else []


def parse_args(av):
	d = {"glb": None, "out": None, "size": 256, "res_x": 0, "res_y": 0,
	     "azim": 0.0, "elev": 20.0, "frames": 1, "azim_step": 45.0,
	     "margin": 1.06, "samples": 48,
	     # pair mode: render the model twice as a mirrored left/right pair in ONE scene,
	     # so the overlap is real geometry and Cycles casts a true shadow into it
	     "pair": 0, "pair_yaw": 0.0, "pair_gap": 0.55, "pair_depth": 0.0,
	     # auto-fit orientation: row-major 3x3 rotation matrix, applied before pairing
	     "orient": ""}
	i = 0
	while i < len(av):
		k = av[i].lstrip("-").replace("-", "_")
		if k in d and i + 1 < len(av):
			v = av[i + 1]
			cur = d[k]
			if isinstance(cur, bool):
				d[k] = v.lower() in ("1", "true", "yes")
			elif isinstance(cur, int):
				d[k] = int(float(v))
			elif isinstance(cur, float):
				d[k] = float(v)
			else:  # str or None
				d[k] = v
			i += 2
		else:
			i += 1
	return d


def clear_scene():
	bpy.ops.wm.read_factory_settings(use_empty=True)


def import_glb(path):
	bpy.ops.import_scene.gltf(filepath=path)
	meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
	return meshes


def _camera_basis(azim_deg, elev_deg):
	"""Camera right/up/back for these angles -- the SAME offset place_camera() uses."""
	az, el = math.radians(azim_deg), math.radians(elev_deg)
	back = mathutils.Vector((math.cos(el) * math.sin(az),
	                         -math.cos(el) * math.cos(az),
	                         math.sin(el))).normalized()
	world_up = mathutils.Vector((0.0, 0.0, 1.0))
	right = world_up.cross(back)
	if right.length < 1e-6:                     # looking straight down the up axis
		right = mathutils.Vector((1.0, 0.0, 0.0))
	right.normalize()
	up = back.cross(right)
	return right, up, back


def _bake_matrix(azim_deg, elev_deg):
	"""Rotation that reproduces the (azim, elev) view from a camera back at 0/0.

	Viewing the model through camera C is the same as viewing C0*C^T applied to the model
	through the default camera C0 -- so baking leaves the picture untouched while making
	the model genuinely square to the default view.
	"""
	r, u, b = _camera_basis(azim_deg, elev_deg)
	r0, u0, b0 = _camera_basis(0.0, 0.0)
	C = mathutils.Matrix((r, u, b)).transposed()      # columns = basis vectors
	C0 = mathutils.Matrix((r0, u0, b0)).transposed()
	return (C0 @ C.transposed()).to_4x4()


def apply_orient(meshes, orient_deg):
	"""Rotate the imported model into the fit frame before anything else.

	The auto-fit measures the model's own principal axes and reports the rotation that
	puts the back of the hand square to the camera with the fingers upright. Baking it in
	here means the pair/roll/framing code below needs no knowledge of it.
	"""
	if not orient_deg:
		return
	mn, mx = scene_bounds(meshes)
	pivot = (mn + mx) / 2.0
	# The squaring is stored as the CAMERA ANGLES that were dialled in, and each engine
	# bakes them with its own camera formula. Passing a rotation matrix across engines
	# does not work: three.js keeps glTF's Y-up axes while Blender's importer converts to
	# Z-up, so identical numbers mean different rotations.
	R = mathutils.Matrix.Identity(4)
	for az_deg, el_deg in orient_deg:
		R = _bake_matrix(az_deg, el_deg) @ R
	T = mathutils.Matrix.Translation(pivot) @ R @ mathutils.Matrix.Translation(-pivot)
	for o in meshes:
		o.matrix_world = T @ o.matrix_world
	bpy.context.view_layer.update()


def make_pair(meshes, yaw_deg, gap, depth):
	"""Duplicate the model as a mirrored opposite hand and pose both.

	Mirroring in 3D (scale.x = -1) yields a true opposite hand AND keeps both lit by the
	same scene lights, so highlights fall correctly on each -- flipping a 2D render
	instead flips its lighting with it.

	`gap` and `depth` are fractions of the model's own width, so the pose is
	scale-independent: each hand moves +/-gap sideways and one comes `depth` toward the
	camera so it genuinely occludes the other.
	"""
	mn, mx = scene_bounds(meshes)
	size = mx - mn
	centre = (mn + mx) / 2.0
	width = max(size.x, 1e-6)

	# group the imported meshes under one empty so each hand moves as a unit
	def group(objs, name):
		empty = bpy.data.objects.new(name, None)
		bpy.context.scene.collection.objects.link(empty)
		empty.location = centre
		for o in objs:
			o.parent = empty
			o.matrix_parent_inverse = empty.matrix_world.inverted()
		return empty

	left = group(list(meshes), "HandL")

	# duplicate every mesh for the other hand
	copies = []
	for o in list(meshes):
		c = o.copy()
		c.data = o.data.copy()
		bpy.context.scene.collection.objects.link(c)
		copies.append(c)
	right = group(copies, "HandR")

	# mirror the right hand. Negative scale inverts winding, so Blender/Cycles would
	# shade it inside-out; flipping the normals back keeps the shading correct.
	right.scale.x = -1.0
	for c in copies:
		c.data.flip_normals()

	# ROLL, not yaw. Turning about the vertical axis swung each hand toward a profile
	# view; rolling about the axis that points at the camera tips the hand within the
	# picture instead, so the back of the hand stays square on -- the same thing the 2D
	# auto-fit did when it stood the pinky edge upright.
	# At the default azimuth the camera looks along -Y, so that is the roll axis.
	roll = math.radians(yaw_deg)
	left.rotation_euler = (0.0, roll, 0.0)
	right.rotation_euler = (0.0, -roll, 0.0)
	# LEFT hand sits on the RIGHT of the sprite (and vice versa), matching the 2D
	# auto-fit convention where each hand snaps to the border its pinky edge faces.
	left.location.x = centre.x + gap * width
	right.location.x = centre.x - gap * width
	# one hand nearer the camera (-Y is toward the default camera azimuth)
	right.location.y = centre.y - depth * width

	# matrix_world is stale until the dependency graph re-evaluates, so framing would be
	# computed from the PRE-pose transforms and crop the pair at the frame edge.
	bpy.context.view_layer.update()
	return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def scene_bounds(objs):
	mn = mathutils.Vector((1e9, 1e9, 1e9))
	mx = mathutils.Vector((-1e9, -1e9, -1e9))
	for o in objs:
		for c in o.bound_box:
			w = o.matrix_world @ mathutils.Vector(c)
			for i in range(3):
				mn[i] = min(mn[i], w[i])
				mx[i] = max(mx[i], w[i])
	return mn, mx


def setup(dcfg):
	scn = bpy.context.scene
	scn.render.engine = "CYCLES"
	scn.cycles.device = "CPU"
	scn.cycles.samples = int(dcfg["samples"])
	# Firefly elimination (2026-07-22; see UPSCALE_3D_PANEL_DESIGN.md §6h). At 48 samples the
	# raw pathtrace leaves isolated hot pixels that survive the DC6 downscale as cream specks:
	#  - OpenImageDenoise removes the residual per-pixel variance,
	#  - clamping indirect samples kills the rare huge-energy bounce paths that CAUSE fireflies,
	#  - glossy blur + no caustics remove the sharp specular light paths metal PBR loves.
	scn.cycles.use_denoising = True
	try:
		scn.cycles.denoiser = "OPENIMAGEDENOISE"
	except TypeError:
		pass  # older Blender: keep the default denoiser
	scn.cycles.sample_clamp_indirect = 10.0
	scn.cycles.blur_glossy = 1.0
	scn.cycles.caustics_reflective = False
	scn.cycles.caustics_refractive = False
	scn.render.film_transparent = True
	# Render at the item's cell aspect ratio when res_x/res_y are given (so a tall item is
	# rendered tall, not squeezed into a square); else fall back to a square `size`.
	rx = int(dcfg["res_x"]) or int(dcfg["size"])
	ry = int(dcfg["res_y"]) or int(dcfg["size"])
	scn.render.resolution_x = rx
	scn.render.resolution_y = ry
	scn.render.image_settings.file_format = "PNG"
	scn.render.image_settings.color_mode = "RGBA"

	# bright, even studio lighting: strong world ambient + key + fill suns
	world = bpy.data.worlds.new("W")
	scn.world = world
	world.use_nodes = True
	bg = world.node_tree.nodes.get("Background")
	if bg:
		bg.inputs[1].default_value = 1.1  # ambient strength (bright, even base)
	key = bpy.data.lights.new("Key", "SUN")
	key.angle = math.radians(3.0)   # slightly soft edge on the cast shadow
	key.energy = 3.5
	ko = bpy.data.objects.new("Key", key)
	scn.collection.objects.link(ko)
	ko.rotation_euler = (math.radians(50), 0, math.radians(35))
	fill = bpy.data.lights.new("Fill", "SUN")
	fill.energy = 1.5
	fo = bpy.data.objects.new("Fill", fill)
	scn.collection.objects.link(fo)
	fo.rotation_euler = (math.radians(60), 0, math.radians(-140))


def place_camera(center, radius, azim_deg, elev_deg, margin, corners, res_x, res_y):
	"""Ortho camera framed TIGHTLY to the object's projected silhouette (not its 3D diagonal).

	The old code set ortho_scale = 3D-diagonal, which always over-frames (the diagonal is
	longer than what you see), leaving the object small with padding. Here we project the 8
	bounding-box corners into camera space and fit the ortho view to that actual extent at the
	render's aspect ratio, so the object fills the frame with only `margin` breathing room.
	"""
	az = math.radians(azim_deg)
	el = math.radians(elev_deg)
	dist = radius * 3.0 + 1.0
	cam_data = bpy.data.cameras.new("Cam")
	cam_data.type = "ORTHO"
	cam = bpy.data.objects.new("Cam", cam_data)
	bpy.context.scene.collection.objects.link(cam)
	bpy.context.scene.camera = cam
	pos = mathutils.Vector((
		center.x + dist * math.cos(el) * math.sin(az),
		center.y - dist * math.cos(el) * math.cos(az),
		center.z + dist * math.sin(el),
	))
	cam.location = pos
	direction = (center - pos).normalized()
	cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
	bpy.context.view_layer.update()  # so matrix_world is current for the projection below

	# project the bbox corners into camera space; get the object's width/height as seen
	view = cam.matrix_world.inverted()
	xs, ys = [], []
	for c in corners:
		cc = view @ c
		xs.append(cc.x)
		ys.append(cc.y)
	obj_w = max(1e-6, max(xs) - min(xs))
	obj_h = max(1e-6, max(ys) - min(ys))
	ar = (res_x or 1) / (res_y or 1)          # frame aspect (w/h)
	# ortho_scale is the view size along the sensor-fit axis; pick the limiting dimension so the
	# object fills the frame without cropping, then add the margin.
	if obj_w / obj_h >= ar:                     # width-limited
		cam_data.sensor_fit = "HORIZONTAL"
		cam_data.ortho_scale = obj_w * margin
	else:                                       # height-limited
		cam_data.sensor_fit = "VERTICAL"
		cam_data.ortho_scale = obj_h * margin
	return cam


def main():
	cfg = parse_args(argv_after_dashes())
	if not cfg["glb"] or not cfg["out"]:
		print("RENDER_ERROR missing --glb/--out")
		sys.exit(2)
	clear_scene()
	meshes = import_glb(cfg["glb"])
	# "az,el;az,el;..." -- the camera angles baked in, in the order they were set
	orient = []
	for part in str(cfg["orient"]).split(";"):
		part = part.strip()
		if not part:
			continue
		try:
			az, el = (float(v) for v in part.split(",")[:2])
		except ValueError:
			continue
		orient.append((az, el))
	apply_orient(meshes, orient)
	if int(cfg["pair"]):
		meshes = make_pair(meshes, cfg["pair_yaw"], cfg["pair_gap"], cfg["pair_depth"])
	if not meshes:
		print("RENDER_ERROR no meshes in glb")
		sys.exit(3)
	mn, mx = scene_bounds(meshes)
	center = (mn + mx) * 0.5
	radius = max((mx - mn).length * 0.5, 0.001)
	# 8 world-space bounding-box corners, for tight projected framing per camera angle
	corners = [mathutils.Vector((x, y, z)) for x in (mn.x, mx.x)
	           for y in (mn.y, mx.y) for z in (mn.z, mx.z)]
	setup(cfg)
	rx = int(cfg["res_x"]) or int(cfg["size"])
	ry = int(cfg["res_y"]) or int(cfg["size"])

	frames = int(cfg["frames"])
	for f in range(frames):
		az = cfg["azim"] + f * cfg["azim_step"]
		# remove any prior camera
		for o in [o for o in bpy.context.scene.objects if o.type == "CAMERA"]:
			bpy.data.objects.remove(o, do_unlink=True)
		place_camera(center, radius, az, cfg["elev"], cfg["margin"], corners, rx, ry)
		out = cfg["out"] if frames == 1 else cfg["out"].replace(".png", f"_{f:03d}.png")
		bpy.context.scene.render.filepath = out
		bpy.ops.render.render(write_still=True)
		print(f"RENDER_OK {out}")


main()
