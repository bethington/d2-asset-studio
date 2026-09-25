/* Tiny GLB viewer for the workflow panel (module script; exposes window.WF3D for workflow.js).
   Mirrors the studio.js viewer: orbit controls, hemi + key + fill lights, auto-framing. One
   viewer instance is reused — mount() moves the canvas into whichever section needs it. */
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { PairPreview } from "/static/pair3d.js";

let renderer = null, scene, camera, controls, model, host = null;

function ensure() {
	if (renderer) return;
	renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
	renderer.setPixelRatio(window.devicePixelRatio || 1);
	scene = new THREE.Scene();
	camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100);
	camera.position.set(0, 0.4, 3);
	controls = new OrbitControls(camera, renderer.domElement);
	controls.enableDamping = true;
	scene.add(new THREE.HemisphereLight(0xffffff, 0x404050, 1.1));
	const key = new THREE.DirectionalLight(0xffffff, 2.2);
	key.position.set(2, 3, 2);
	scene.add(key);
	const fill = new THREE.DirectionalLight(0xffffff, 0.9);
	fill.position.set(-2, 1, -1.5);
	scene.add(fill);
	(function loop() {
		requestAnimationFrame(loop);
		if (host && renderer.domElement.isConnected) {
			controls.update();
			renderer.render(scene, camera);
		}
	})();
}

function resize() {
	if (!host) return;
	const w = host.clientWidth || 300, h = host.clientHeight || 240;
	renderer.setSize(w, h);
	camera.aspect = w / h;
	camera.updateProjectionMatrix();
}

function clearModel() {
	if (model) {
		scene.remove(model);
		model.traverse((o) => { o.geometry?.dispose?.(); });
		model = null;
	}
}

window.WF3D = {
	_gen: 0,
	mount(el) {
		ensure();
		host = el;
		el.appendChild(renderer.domElement);
		resize();
	},
	load(url) {
		ensure();
		// generation guard: with two loads in flight (e.g. draft then texture), only the LATEST
		// request may install its scene — otherwise whichever download finishes last wins
		const gen = ++this._gen;
		return new Promise((res, rej) => {
			new GLTFLoader().load(url, (g) => {
				if (gen !== this._gen) { res(); return; }  // superseded by a newer load
				clearModel();
				model = g.scene;
				const box = new THREE.Box3().setFromObject(model);
				const c = box.getCenter(new THREE.Vector3());
				const s = box.getSize(new THREE.Vector3());
				const k = 1.6 / Math.max(s.x, s.y, s.z, 1e-6);
				model.position.sub(c);
				model.scale.setScalar(k);
				model.position.multiplyScalar(k);
				scene.add(model);
				controls.reset();
				camera.position.set(0, 0.4, 3);
				res();
			}, undefined, rej);
		});
	},
	clear() { clearModel(); },
	unmount() { host = null; },

	/** Browser-engine final render (no Blender): offscreen PairPreview — the SAME camera/
	 *  light rig render_glb.py uses — at supersampled size. Returns a PNG data URL.
	 *  opts: {glbUrl, single, pose:{yaw,gap,depth}, frame:{azim,elev,margin}, w, h} */
	async captureSprite({ glbUrl, single = false, pose = {}, frame = {}, w = 928, h = 928 }) {
		const canvas = document.createElement("canvas");
		canvas.width = w; canvas.height = h;
		const pv = new PairPreview(canvas);
		try {
			await pv.load(glbUrl, { single });
			if (!single) pv.pose(pose);
			pv.frame({ azim: frame.azim || 0, elev: frame.elev || 0, margin: frame.margin || 1.06 });
			return pv.capture(w, h);
		} finally {
			pv.dispose();
		}
	},
};
window.addEventListener("resize", resize);
