/* Live 3D pair preview — the browser half of the hybrid renderer.
 *
 * Deliberately mirrors blender/render_glb.py so the preview predicts the Blender result
 * and either engine produces substantially the same sprite:
 *   - ORTHOGRAPHIC camera placed by the same azim/elev spherical formula
 *   - the same two suns (key 3.5 / fill 1.5) plus a neutral ambient for the world's 1.1
 *   - the model imported once, duplicated, and mirrored with scale.x = -1
 *   - the same scale-independent pose: yaw inward, gap sideways, depth toward camera
 *
 * Mirroring the MODEL (not a 2D flip) is what keeps the lighting correct on both hands.
 */
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

// must track blender/render_glb.py::setup()
const KEY_ENERGY = 3.5, FILL_ENERGY = 1.5, AMBIENT = 1.1;
// Cycles gathers indirect bounce light that WebGL's direct-only model does not, so at
// identical light values the browser renders darker. Measured against a Blender render
// of the same model and pose: mean luminance inside the silhouette was 142.2 vs 180.6,
// a factor of 1.27. Applied as exposure so the preview predicts the final tone rather
// than merely the final shape. Re-measure with this repo scripts if the
// Blender rig's light values ever change.
const CYCLES_GAIN = 1.45;   // iterated: 1.27 closed the gap to 25/255, 1.45 to ~12

/** Blender sun rotation_euler (rx, 0, rz) -> a world direction, converted to three's
 *  Y-up axes. A Blender sun points along -Z before rotation. */
function sunDir(rxDeg, rzDeg) {
  const rx = THREE.MathUtils.degToRad(rxDeg), rz = THREE.MathUtils.degToRad(rzDeg);
  // Rz * Rx * (0,0,-1) in Blender's Z-up frame
  const bx = -Math.sin(rz) * Math.sin(rx);
  const by = Math.cos(rz) * Math.sin(rx);
  const bz = -Math.cos(rx);
  // Blender (x, y, z) -> three (x, z, -y)
  return new THREE.Vector3(bx, bz, -by).normalize();
}

export class PairPreview {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true,
                                              preserveDrawingBuffer: true });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;

    this.scene = new THREE.Scene();
    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 100);
    this.scene.add(new THREE.AmbientLight(0xffffff, AMBIENT * CYCLES_GAIN));

    // key casts the shadow that makes the overlap read; fill only lifts the dark side
    const key = new THREE.DirectionalLight(0xffffff, KEY_ENERGY * CYCLES_GAIN);
    key.position.copy(sunDir(50, 35).multiplyScalar(-6));
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.bias = -0.0008;          // tuned against contact-point acne
    key.shadow.normalBias = 0.02;
    this.key = key;
    this.scene.add(key, key.target);

    const fill = new THREE.DirectionalLight(0xffffff, FILL_ENERGY * CYCLES_GAIN);
    fill.position.copy(sunDir(60, -140).multiplyScalar(-6));
    this.scene.add(fill, fill.target);

    this.root = new THREE.Group();
    this.scene.add(this.root);
    this.model = null;
  }

  async load(url, { single = false } = {}) {
    const gltf = await new GLTFLoader().loadAsync(url);
    this.root.clear();
    this.model = gltf.scene;
    this.model.traverse((o) => {
      if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; }
    });
    // normalise: centre at the origin, unit size, so pose numbers are scale-independent
    const box = new THREE.Box3().setFromObject(this.model);
    const size = box.getSize(new THREE.Vector3());
    const centre = box.getCenter(new THREE.Vector3());
    const s = 1 / Math.max(size.x, size.y, size.z, 1e-6);
    this.model.position.sub(centre);
    const holder = new THREE.Group();
    holder.add(this.model);
    holder.scale.setScalar(s);
    // The SQUARING group sits between the model and the hand, so the model is already
    // square to the camera by the time pose() rolls it. Without this the roll axis and
    // the hand's own plane disagree, which is what made "tilt apart" curl the gloves
    // into each other instead of fanning them out.
    this.squareL = new THREE.Group();
    this.squareL.add(holder);

    this.left = new THREE.Group();
    this.left.add(this.squareL);
    // SINGLE mode (non-pair items: weapons, armour, amulets...): one model, centred, no mirror.
    if (single) {
      this.right = null;
      this.squareR = null;
      this.root.add(this.left);
    } else {
      this.right = new THREE.Group();
      const clone = this.squareL.clone(true);
      this.squareR = clone;
      // true opposite hand. Negative scale inverts winding, so flip the material side to
      // stop the mirrored hand shading inside-out (three's equivalent of flip_normals).
      this.right.add(clone);
      this.right.scale.x = -1;
      // Negative scale inverts the winding. BackSide alone is wrong here: these meshes are
      // not closed (the cuff is open), so back-face-only rendering left the cuff interior
      // unlit black. DoubleSide draws it correctly; shadowSide FrontSide keeps the shadow
      // pass single-sided so the silhouette does not speckle with self-shadow acne.
      clone.traverse((o) => {
        if (o.isMesh && o.material) {
          o.material = o.material.clone();
          o.material.side = THREE.DoubleSide;
          o.material.shadowSide = THREE.FrontSide;
        }
      });
      this.root.add(this.left, this.right);
    }
    this.modelWidth = size.x * s;
    if (this._pendingSquare) this.square(this._pendingSquare);
    return this;
  }

  /** Camera basis for these angles — the same offset frame() uses. */
  static _camBasis(azimDeg, elevDeg) {
    const az = THREE.MathUtils.degToRad(azimDeg), el = THREE.MathUtils.degToRad(elevDeg);
    const back = new THREE.Vector3(Math.cos(el) * Math.sin(az), Math.sin(el),
                                   Math.cos(el) * Math.cos(az)).normalize();
    const worldUp = new THREE.Vector3(0, 1, 0);
    let right = new THREE.Vector3().crossVectors(worldUp, back);
    if (right.lengthSq() < 1e-12) right = new THREE.Vector3(1, 0, 0);
    right.normalize();
    const up = new THREE.Vector3().crossVectors(back, right);
    return [right, up, back];
  }

  /** Apply the saved squaring: the camera angles that were baked in, as [[az, el], …].
   *  Each engine bakes with its OWN camera formula — a rotation matrix cannot be shared,
   *  because three keeps glTF's Y-up axes while Blender's importer converts to Z-up, so
   *  identical numbers would mean different rotations. */
  square(bakes) {
    this._pendingSquare = bakes;
    if (!this.squareL) return this;
    const M = new THREE.Matrix4();               // identity
    for (const b of (Array.isArray(bakes) ? bakes : [])) {
      const [az, el] = Array.isArray(b) ? b : [b.azim, b.elev];
      const [r, u, k] = PairPreview._camBasis(az || 0, el || 0);
      const [r0, u0, k0] = PairPreview._camBasis(0, 0);
      const C = new THREE.Matrix4().makeBasis(r, u, k);
      const C0 = new THREE.Matrix4().makeBasis(r0, u0, k0);
      // C0 * C^T : reproduces that view from the default camera
      const step = C0.multiply(C.transpose());
      M.premultiply(step);
    }
    for (const g of [this.squareL, this.squareR]) if (g) g.rotation.setFromRotationMatrix(M);
    // gap/depth are fractions of the model's width, and Blender measures that width AFTER
    // squaring (apply_orient runs before make_pair). Re-measure here or the two engines
    // space the hands differently once a squaring is applied.
    this.squareL.updateMatrixWorld(true);
    const b = new THREE.Box3().setFromObject(this.squareL);
    this.modelWidth = Math.max(b.getSize(new THREE.Vector3()).x, 1e-6);
    return this;
  }

  /** Same pose contract as make_pair(): gap/depth are fractions of the model's width.
   *  `yaw` is a ROLL in the view plane, not a turn about the vertical axis -- turning
   *  about vertical swung each hand toward profile; rolling tips it within the picture
   *  and keeps the back of the hand square to the camera, like the 2D auto-fit. */
  pose({ yaw = 0, gap = 0.55, depth = 0 } = {}) {
    if (!this.left) return this;
    const w = this.modelWidth || 1;
    const r = THREE.MathUtils.degToRad(yaw);
    // three is Y-up and the camera looks down -Z at azim 0, so roll is about Z
    this.left.rotation.set(0, 0, -r);
    if (!this.right) {                 // single model: centred, no mirror
      this.left.position.set(0, 0, 0);
      return this;
    }
    this.right.rotation.set(0, 0, r);
    // LEFT hand on the RIGHT of the sprite, matching the 2D auto-fit convention
    this.left.position.set(gap * w, 0, 0);
    this.right.position.set(-gap * w, 0, depth * w);
    return this;
  }

  /** Ortho camera on the same azim/elev sphere the Blender rig uses, framed to the pair. */
  frame({ azim = 0, elev = 0, margin = 1.06 } = {}) {
    const az = THREE.MathUtils.degToRad(azim), el = THREE.MathUtils.degToRad(elev);
    const box = new THREE.Box3().setFromObject(this.root);
    const centre = box.getCenter(new THREE.Vector3());
    const radius = Math.max(box.getSize(new THREE.Vector3()).length() * 0.5, 0.001);
    const dist = radius * 3 + 1;
    // Blender: x = sin(az), y = -cos(az), z = sin(el)  ->  three: (x, z, -y)
    this.camera.position.set(
      centre.x + dist * Math.cos(el) * Math.sin(az),
      centre.y + dist * Math.sin(el),
      centre.z + dist * Math.cos(el) * Math.cos(az));
    this.camera.up.set(0, 1, 0);
    this.camera.lookAt(centre);
    this.camera.updateMatrixWorld();

    // fit the ortho box to the pair's PROJECTED extent, as place_camera() does
    const inv = new THREE.Matrix4().copy(this.camera.matrixWorld).invert();
    let minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9;
    for (const cx of [box.min.x, box.max.x])
      for (const cy of [box.min.y, box.max.y])
        for (const cz of [box.min.z, box.max.z]) {
          const v = new THREE.Vector3(cx, cy, cz).applyMatrix4(inv);
          minX = Math.min(minX, v.x); maxX = Math.max(maxX, v.x);
          minY = Math.min(minY, v.y); maxY = Math.max(maxY, v.y);
        }
    const w = (maxX - minX) * margin, h = (maxY - minY) * margin;
    const ar = this.canvas.width / Math.max(1, this.canvas.height);
    const fitW = (w / h >= ar) ? w : h * ar;
    this.camera.left = -fitW / 2; this.camera.right = fitW / 2;
    this.camera.top = (fitW / ar) / 2; this.camera.bottom = -(fitW / ar) / 2;
    this.camera.near = 0.01; this.camera.far = dist * 4;
    this.camera.updateProjectionMatrix();

    // point the suns at the pair so the shadow camera covers it
    for (const l of [this.key]) {
      l.target.position.copy(centre);
      l.target.updateMatrixWorld();
      const r = radius * 2.2;
      l.shadow.camera.left = -r; l.shadow.camera.right = r;
      l.shadow.camera.top = r; l.shadow.camera.bottom = -r;
      l.shadow.camera.near = 0.01; l.shadow.camera.far = dist * 4;
      l.shadow.camera.updateProjectionMatrix();
    }
    return this;
  }

  render() { this.renderer.render(this.scene, this.camera); return this; }

  /** Free the WebGL context. Batch builds (Accept all) create one preview per glove; without
      this the browser accumulates contexts and eventually refuses new ones. */
  dispose() {
    try { this.root && this.root.clear(); this.renderer.dispose(); this.renderer.forceContextLoss(); }
    catch (e) { /* already gone */ }
  }

  /** Render at `w`x`h` and return a PNG data URL — the supersampled capture. */
  capture(w, h) {
    const prev = { w: this.canvas.width, h: this.canvas.height };
    this.renderer.setSize(w, h, false);
    this.frame(this._frameArgs || {});
    this.render();
    const url = this.canvas.toDataURL("image/png");
    this.renderer.setSize(prev.w, prev.h, false);
    this.frame(this._frameArgs || {});
    this.render();
    return url;
  }

  setFrameArgs(a) { this._frameArgs = a; return this; }
}
