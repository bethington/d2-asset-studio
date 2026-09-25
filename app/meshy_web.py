"""Meshy WEB-session client — drives Meshy's internal workspace API using the user's browser
login, so generations get the plan's free ×8 retries (the openapi key can't).

Full reverse-engineering in ../MESHY_WEB_API.md. Flow:
  register image (aspect-preserved) -> create DRAFT (geometry) -> poll -> [3D preview] ->
  re-roll draft -> TEXTURE the chosen draft -> poll -> [preview] -> download GLB.

Session: a DEDICATED Chrome (isolated profile) launched with a debug port; the user logs in
once; the Supabase JWT is read fresh from that browser over the DevTools Protocol (Network
header) each call, so hourly expiry is a non-issue while the browser stays logged in.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request

import websocket  # websocket-client

WEB = "https://api.meshy.ai/web"
WORKSPACE = os.environ.get("ASSET_STUDIO_WS", r"C:\Diablo2\AssetStudio")
PROFILE_DIR = os.path.join(WORKSPACE, "chrome-meshy-studio")
PORT = int(os.environ.get("MESHY_STUDIO_PORT", "9233"))
LOGIN_URL = "https://www.meshy.ai/workspace?model-tab=image-to-3d"

_CHROME = [
	r"C:\Program Files\Google\Chrome\Application\chrome.exe",
	r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
	os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


class MeshyWebError(RuntimeError):
	pass


# ---- dedicated-Chrome session -------------------------------------------

def _chrome_exe() -> str | None:
	for p in _CHROME:
		if os.path.exists(p):
			return p
	return os.environ.get("CHROME_EXE")


def _targets(port=PORT):
	try:
		with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as r:
			return json.loads(r.read())
	except Exception:  # noqa: BLE001
		return None


def browser_up(port=PORT) -> bool:
	return _targets(port) is not None


def launch_session() -> dict:
	"""Launch (or reuse) the dedicated Chrome at the Meshy workspace login. Idempotent."""
	if browser_up():
		return {"ok": True, "already": True}
	exe = _chrome_exe()
	if not exe:
		return {"ok": False, "error": "Chrome not found (set CHROME_EXE)"}
	os.makedirs(PROFILE_DIR, exist_ok=True)
	subprocess.Popen([exe, f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
	                  f"--user-data-dir={PROFILE_DIR}", "--no-first-run",
	                  "--no-default-browser-check", "--new-window", LOGIN_URL])
	for _ in range(20):
		time.sleep(0.5)
		if browser_up():
			return {"ok": True, "launched": True}
	return {"ok": False, "error": "Chrome launched but debug port never opened"}


def _meshy_ws(port=PORT):
	ts = _targets(port) or []
	page = next((t for t in ts if t.get("type") == "page" and "meshy.ai" in (t.get("url") or "")), None)
	if not page:
		page = next((t for t in ts if t.get("type") == "page" and t.get("webSocketDebuggerUrl")), None)
	return page


def read_token() -> str | None:
	"""Read the current Supabase JWT from the logged-in Meshy tab. The app attaches the Bearer to
	its OWN requests (axios interceptor), so we reload the tab to fire a burst of authenticated
	/web requests and sniff the Authorization header off them. Reloading the dedicated studio tab
	is harmless (it just shows the workspace)."""
	# prefer a workspace tab (it actively makes authenticated calls)
	ts = _targets() or []
	pages = [t for t in ts if t.get("type") == "page" and "meshy.ai" in (t.get("url") or "") and t.get("webSocketDebuggerUrl")]
	pages.sort(key=lambda t: 0 if "workspace" in (t.get("url") or "") else 1)
	for page in pages[:3]:
		try:
			ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=8, max_size=None)
		except Exception:  # noqa: BLE001
			continue
		mid = [0]
		def send(m, p=None):
			mid[0] += 1
			ws.send(json.dumps({"id": mid[0], "method": m, "params": p or {}}))
		try:
			send("Network.enable")
			send("Page.enable")
			send("Page.reload", {"ignoreCache": False})
			ws.settimeout(2)
			t0 = time.time()
			while time.time() - t0 < 18:
				try:
					m = json.loads(ws.recv())
				except Exception:  # noqa: BLE001
					continue
				if m.get("method") == "Network.requestWillBeSent":
					r = m["params"]["request"]
					if "api.meshy.ai/web" in r.get("url", ""):
						h = r.get("headers") or {}
						a = h.get("Authorization") or h.get("authorization")
						if a and "Bearer" in a:
							return a.split("Bearer ", 1)[1].strip()
		finally:
			try:
				ws.close()
			except Exception:  # noqa: BLE001
				pass
	return None


_TOKEN = {"v": None, "t": 0.0}


def token(force=False) -> str | None:
	"""Cached token (refreshed if older than ~10 min or forced)."""
	if not force and _TOKEN["v"] and time.time() - _TOKEN["t"] < 600:
		return _TOKEN["v"]
	tok = read_token()
	if tok:
		_TOKEN["v"] = tok
		_TOKEN["t"] = time.time()
	return tok


def session_status() -> dict:
	if not browser_up():
		return {"browser": False, "loggedIn": False, "note": "session browser not launched"}
	tok = token()
	if not tok:
		return {"browser": True, "loggedIn": False, "note": "log in at the Meshy tab"}
	try:
		tier = _web("GET", "/v1/me/tier").get("result", {})
	except MeshyWebError:
		return {"browser": True, "loggedIn": False, "note": "token not accepted -- re-log-in"}
	return {"browser": True, "loggedIn": True, "tier": tier.get("tier"),
	        "freeMonthlyCredits": tier.get("freeMonthlyCredits")}


# ---- authenticated web API ----------------------------------------------

def _web(method: str, path: str, body: dict | None = None, timeout: float = 40.0):
	tok = token()
	if not tok:
		raise MeshyWebError("no Meshy login (launch the session browser + log in)")
	data = json.dumps(body).encode() if body is not None else None
	req = urllib.request.Request(WEB + path, data=data, method=method, headers={
		"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
		"Origin": "https://app.meshy.ai"})
	try:
		with urllib.request.urlopen(req, timeout=timeout) as r:
			raw = r.read().decode()
			return json.loads(raw) if raw else {}
	except urllib.error.HTTPError as e:  # noqa: PERF203
		detail = e.read().decode(errors="replace")[:300]
		if e.code in (401, 403):
			token(force=True)  # refresh for next time
		raise MeshyWebError(f"web API {e.code} {path}: {detail}") from e


def register_image(png_bytes: bytes, filename="sprite.png") -> str:
	"""Upload a sprite as a workspace image; returns the image id the create call needs.
	Multipart form-data (field 'file'); built by hand to avoid a requests dependency here."""
	tok = token()
	if not tok:
		raise MeshyWebError("no Meshy login")
	boundary = "----AssetStudio" + str(int(time.time() * 1000))
	body = b"".join([
		f"--{boundary}\r\n".encode(),
		f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
		b"Content-Type: image/png\r\n\r\n", png_bytes, b"\r\n",
		f"--{boundary}--\r\n".encode()])
	req = urllib.request.Request(WEB + "/v1/files/images?removeBackground=false", data=body, method="POST",
		headers={"Authorization": f"Bearer {tok}", "Origin": "https://app.meshy.ai",
		         "Content-Type": f"multipart/form-data; boundary={boundary}"})
	with urllib.request.urlopen(req, timeout=60) as r:
		res = json.loads(r.read()).get("result", {})
	iid = res.get("id")
	if not iid:
		raise MeshyWebError(f"image register returned no id: {res}")
	return iid


import uuid as _uuid


def create_draft(image_id: str | list[str], *, ai_model="avocado", model_type="standard",
                 topology="triangle", symmetry=0, seed=0, parent: str | None = None) -> str:
	"""Create a DRAFT (geometry) task. parent set = a re-roll variant. Returns the task id.
	image_id may be a list — Meshy's multi-image image-to-3D (several views/parts of one
	object inform a single model); used for the boots 2-image experiment."""
	ids = image_id if isinstance(image_id, list) else [image_id]
	draft = {"aiModel": ai_model, "modelType": model_type, "topology": topology,
	         "prompt": "", "imageIds": ids, "shouldTransferImageStyle": True,
	         "symmetryMode": symmetry, "seed": seed, "license": "private"}
	body = {"phase": "draft", "batchId": str(_uuid.uuid4()), "args": {"draft": draft}}
	if parent:
		body["parent"] = parent
	res = _web("POST", "/v2/tasks", body)
	tid = res.get("result")
	if not tid:
		raise MeshyWebError(f"draft create returned no id: {res}")
	return tid


def create_texture(draft_task_id: str, image_id: str, *, art_style="realistic",
                   ai_model="avocado", enable_pbr=True, sr_mode="weak", prompt="") -> str:
	"""Texture an approved draft. Returns the texture task id.

	Meshy rejects prompt+image together ("prompt and image are mutually exclusive", observed
	2026-07-22): the texture is guided EITHER by the reference image (default) OR by a text
	prompt. A non-empty prompt therefore drops the imageId."""
	tex = {"artStyle": art_style, "aiModel": ai_model, "enablePBR": enable_pbr,
	       "srMode": sr_mode, "textureSize": 0}
	if prompt.strip():
		tex["prompt"] = prompt.strip()
	else:
		tex["prompt"] = ""
		tex["imageId"] = image_id
	body = {"phase": "texture", "parent": draft_task_id, "args": {"texture": tex}}
	res = _web("POST", "/v2/tasks", body)
	tid = res.get("result")
	if not tid:
		raise MeshyWebError(f"texture create returned no id: {res}")
	return tid


def list_tasks(page_num: int = 1, page_size: int = 30) -> list:
	"""Recent workspace tasks, newest first (full records incl. args + signed URLs)."""
	r = _web("GET", f"/v2/tasks?pageNum={page_num}&pageSize={page_size}")
	res = r.get("result") or {}
	return res if isinstance(res, list) else (res.get("tasks") or res.get("list") or [])


def retry_task(task_id: str) -> str | None:
	"""FREE ×8 in-place re-roll (captured live 2026-07-19): `POST /v2/tasks/{id}/retry`,
	EMPTY body, 200. Meshy REPLACES the task: a NEW task id appears (same name/params,
	retryCount+1) and the OLD id starts 404ing — remaining free re-rolls = 8 - retryCount.
	Returns the new task id (from the response when present, else scanned from the list)."""
	r = _web("POST", f"/v2/tasks/{task_id}/retry")
	res = (r or {}).get("result")
	if isinstance(res, str) and res:
		return res
	if isinstance(res, dict) and res.get("id"):
		return res["id"]
	# Response didn't carry the id — the replacement is the freshest retried task.
	lst = _web("GET", "/v2/tasks?pageNum=1&pageSize=10").get("result") or {}
	tasks = lst if isinstance(lst, list) else (lst.get("tasks") or lst.get("list") or [])
	for t in tasks:
		if t.get("status") in ("PENDING", "IN_PROGRESS") and (t.get("retryCount") or 0) > 0:
			return t.get("id")
	return None


def get_task(task_id: str) -> dict:
	"""Web-side task (status/phase/progress/mode). Stashes the id so the url helpers can fall back
	to the flat /v1 endpoint for the model URL (the /v2 phase slots are often empty)."""
	t = (_web("GET", f"/v2/tasks/{task_id}").get("result")) or {}
	t["_id"] = task_id
	return t


def _v1_result(task_id: str) -> dict:
	# NB: .get(k, {}) returns None when the key EXISTS with a null value (fresh tasks), so chain `or {}`
	try:
		r = _web("GET", f"/v1/tasks/{task_id}")
		return ((r.get("result") or {}).get("result")) or {}
	except Exception:  # noqa: BLE001
		return {}


def task_glb_url(task: dict) -> str | None:
	# The real glTF is the flat /v1 model.glb (assets.meshy.ai). The /v2 phase slots serve a "MESH"
	# fast-preview format that neither three.js nor Blender can load -- so prefer /v1.
	v1 = _v1_result(task.get("_id", "")).get("modelUrl")
	if v1:
		return v1
	res = task.get("result") or {}
	for ph in ("texture", "generate", "draft"):
		slot = res.get(ph)
		if isinstance(slot, dict) and slot.get("modelUrl"):
			return slot["modelUrl"]
	return res.get("modelUrl")


def find_textured_children(task_ids: set, pages: int = 4) -> dict:
	"""Map draft task id -> its TEXTURED descendant, where one exists.

	Texturing in Meshy produces a NEW task whose rootId/parent points back at the draft.
	A link made at draft time therefore points at geometry with no materials at all --
	which is why those models render as grey clay. Following the chain finds the version
	that actually carries the textures.
	"""
	out = {}
	for pg in range(1, pages + 1):
		try:
			batch = list_tasks(page_num=pg, page_size=30)
		except Exception:  # noqa: BLE001
			break
		if not batch:
			break
		for t in batch:
			if t.get("mode") != "texture" or t.get("status") != "SUCCEEDED":
				continue
			try:
				full = get_task(t["id"])
			except Exception:  # noqa: BLE001
				continue
			par = full.get("parent")
			pid = par.get("id") if isinstance(par, dict) else par
			for anc in (full.get("rootId"), pid):
				if anc in task_ids:
					# keep the newest textured result for a given draft
					prev = out.get(anc)
					if not prev or (full.get("createdAt") or "") > prev[1]:
						out[anc] = (t["id"], full.get("createdAt") or "")
	return {k: v[0] for k, v in out.items()}


def task_preview_url(task: dict) -> str | None:
	res = task.get("result") or {}
	return res.get("previewUrl") or _v1_result(task.get("_id", "")).get("previewUrl")


def download(url: str, timeout: float = 90.0) -> bytes:
	with urllib.request.urlopen(url, timeout=timeout) as r:
		return r.read()
