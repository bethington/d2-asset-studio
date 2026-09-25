"""Pair Meshy workspace tasks with catalog items.

Links persist in <workspace>/meshy_links.json ({task_id: {item_id, image_id, phase,
source, name, linked_at}}) so pairings survive server restarts — the server seeds its
in-memory _STUDIO map from here at boot and writes back through remember().

Auto-pairing (scan) uses two layers:
  1. IMAGE MATCH (authoritative): dHash the task's INPUT image against each item's
     sprite run through the same prep as upload (dc6 -> png -> prep_image_for_meshy).
     Studio-created tasks re-hash to ~0 distance; hand-uploaded exports of the same
     sprite land within a few bits. There is no server-side image-name listing on
     Meshy's web API (POST /v1/files/images is upload-only, GET 404s) so content
     hashing is the only exact signal.
  2. NAME MATCH (suggestions only): difflib ratio between Meshy's auto-name
     ("Diamond Shield") and catalog item names; never auto-applied.
"""
from __future__ import annotations

import difflib
import io
import json
import os
import re
import time
import urllib.request

from PIL import Image

import app.assets as assets

# The user's re-imagined artwork library: AI-redrawn versions of the game sprites,
# named by DC6 file (`armors/invplt.png`, `gloves/invlgl-l1.png` where the -l1/-R
# suffixes are the split left/right hands of a paired glove sprite). THESE, not the
# DC6s, are what was fed to Meshy for hand-made generations — so a task's input image
# matches one of these bit-for-bit and its filename names the DC6 exactly. Without it
# pairing is guesswork: the AI redraw keeps the subject but none of the pixels.
REIMAGINED_ROOT = os.environ.get("PD2_REIMAGINED_ART", r"D:\d2\DC6\Data\global\items")

LINKS_PATH = os.path.join(assets.WORKSPACE, "meshy_links.json")
HASH_CACHE_PATH = os.path.join(assets.WORKSPACE, "item_dhash_cache.json")
# dHash distance thresholds (of 64 bits). Calibrated against a real workspace scan
# 2026-07-19 with a side-by-side contact sheet: every d==0 pair was correct
# (Studio-uploaded sprite round-trips bit-for-bit), while d in 5..8 was mostly WRONG
# — small dark sprites with similar silhouettes collide (a gauntlet matched a skeleton
# key at d=8, a glove matched a potion at d=7). So only near-exact auto-links; the
# rest become reviewable suggestions with the input thumbnail shown.
AUTO_MAX_DISTANCE = 2
SUGGEST_MAX_DISTANCE = 16  # image candidates worth eyeballing
SUGGEST_MIN_RATIO = 0.55   # name-similarity floor
IMAGE_CANDIDATES = 5       # the review page shows sprites, so offer a wider net
NAME_CANDIDATES = 3


# ---- persistent link store ------------------------------------------------

def load_links() -> dict:
	try:
		with open(LINKS_PATH, encoding="utf-8") as f:
			return json.load(f)
	except (FileNotFoundError, ValueError):
		return {}


def save_links(links: dict) -> None:
	tmp = LINKS_PATH + ".tmp"
	with open(tmp, "w", encoding="utf-8") as f:
		json.dump(links, f, indent=1)
	os.replace(tmp, LINKS_PATH)


def remember(links: dict, task_id: str, *, item_id: str, image_id: str | None,
             phase: str, source: str, name: str = "", invfile: str | None = None,
             art_file: str | None = None) -> dict:
	# `invfile` is the real subject of the pairing (the art file an override replaces);
	# `item_id` is the representative item the Studio needs for cell dims + activation.
	links[task_id] = {"item_id": item_id, "invfile": invfile, "image_id": image_id,
	                  "phase": phase, "source": source, "name": name,
	                  # the matched library filename -- carries the HAND for split art
	                  # (invtgl-l4.png = left), so pairing never has to infer it
	                  "art_file": art_file,
	                  "linked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
	save_links(links)
	return links[task_id]


def set_squaring(links: dict, task_id: str, squaring: list | None) -> dict | None:
	"""Store (or clear) a model's top-down squaring.

	This lives on the LINK, not the art file's template: how crooked a model sits is a
	property of that individual Meshy generation, while a row can hold four of them. The
	layout -- tilt, separation, depth -- stays shared per art file.
	"""
	l = links.get(task_id)
	if l is None:
		return None
	if squaring:
		# a list of [azim, elev] bakes, in the order they were applied
		l["squaring"] = [[float(a), float(e)] for a, e in squaring]
	else:
		l.pop("squaring", None)
	save_links(links)
	return l


# Pose values stored per MODEL rather than per art file, with their clamps. Both interact
# with the individual model's proportions: the same shared `gap` gave 0.124 clearance on
# one generation and 0.000 on its three siblings, and tilt compounds that, so tuning one
# model used to misplace the rest of the row.
PER_MODEL_POSE = {"gap": (-2.0, 2.0), "yaw": (-60.0, 60.0), "depth": (-2.0, 2.0)}


def set_pose_field(links: dict, task_id: str, field: str, value: float | None) -> dict | None:
	"""Store (or clear) one of this model's own pose values."""
	l = links.get(task_id)
	if l is None or field not in PER_MODEL_POSE:
		return None
	if value is None:
		l.pop(field, None)
	else:
		lo, hi = PER_MODEL_POSE[field]
		l[field] = max(lo, min(hi, float(value)))
	save_links(links)
	return l


def set_gap(links: dict, task_id: str, gap: float | None) -> dict | None:
	"""Back-compat helper: separation is one of the per-model pose fields."""
	return set_pose_field(links, task_id, "gap", gap)


def set_ignored(links: dict, task_id: str, ignored: bool = True) -> dict | None:
	"""Mark a generation as deliberately unlinked ("none"). Kept as an entry rather
	than deleted so a rescan can't silently re-pair it."""
	if ignored:
		links[task_id] = {"item_id": None, "invfile": None, "image_id": None,
		                  "phase": None, "source": "ignored", "ignored": True,
		                  "name": (links.get(task_id) or {}).get("name", ""),
		                  "linked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
		save_links(links)
		return links[task_id]
	links.pop(task_id, None)
	save_links(links)
	return None


def forget(links: dict, task_id: str) -> bool:
	if task_id in links:
		del links[task_id]
		save_links(links)
		return True
	return False


# ---- perceptual hash ------------------------------------------------------

def dhash(png_bytes: bytes) -> int:
	"""64-bit difference hash: grayscale 9x8, each bit = left pixel > right pixel."""
	im = Image.open(io.BytesIO(png_bytes)).convert("L").resize((9, 8), Image.LANCZOS)
	px = list(im.getdata())
	h = 0
	for row in range(8):
		for col in range(8):
			h = (h << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
	return h


def hamming(a: int, b: int) -> int:
	return bin(a ^ b).count("1")


def _load_hash_cache() -> dict:
	try:
		with open(HASH_CACHE_PATH, encoding="utf-8") as f:
			return json.load(f)
	except (FileNotFoundError, ValueError):
		return {}


def _rep_item(group: list) -> dict:
	"""Canonical owner of a shared DC6: prefer a base item (the real owner of the art)
	over the uniques/sets that inherit it, then shortest name for stability."""
	return sorted(group, key=lambda i: (i["category"] != "base", len(i["name"]), i["name"]))[0]


def dc6_hashes(items: list, progress=None) -> dict:
	"""Hash by ART FILE, not by item: {invfile: {hash, items, rep}}.

	D2 items share inventory sprites heavily — 1406 catalog items resolve to only 577
	distinct DC6s (`invamu` alone backs 23 amulets; `invne4` backs Gargoyle Head,
	Cantor Trophy, Succubae Skull, Trang-Oul's Wing and Boneflame). Ranking per ITEM
	therefore spent every candidate slot re-showing one picture, and picked an
	arbitrary label for it — the `invtow` sprite came back as "Tower Shield" when the
	same file is equally Aegis, Pavise and Sigon's Guard.

	The art file is also the true unit of work: an override replaces `invtow.dc6` for
	everything that uses it. Hashing per file also decodes 577 DC6s instead of 1406.
	"""
	groups = {}
	for it in items:
		f = (it["invfile"] or "").lower()
		if f:
			groups.setdefault(f, []).append(it)

	cache = _load_hash_cache()
	out, dirty, done = {}, False, 0
	for invfile, group in groups.items():
		rep = _rep_item(group)
		cached = cache.get(invfile)
		if cached is not None:
			h = int(cached, 16) if isinstance(cached, str) else int(cached)
		else:
			try:
				png = assets.dc6_to_png_bytes(assets.read_original_dc6(rep["invfile"]))
				h = dhash(assets.prep_image_for_meshy(png))
				cache[invfile] = f"{h:016x}"
				dirty = True
			except Exception:  # noqa: BLE001 -- unreadable DC6: skip, retry next scan
				continue
			done += 1
			if progress and done % 100 == 0:
				progress(done)
		out[invfile] = {"hash": h, "items": group, "rep": rep}
	if dirty:
		tmp = HASH_CACHE_PATH + ".tmp"
		with open(tmp, "w", encoding="utf-8") as f:
			json.dump(cache, f)
		os.replace(tmp, HASH_CACHE_PATH)
	return out


# ---- auto-pair ------------------------------------------------------------

def dc6_name_from_art_path(path: str, known: set) -> str | None:
	"""`gloves/invlgl-l1.png` -> `invlgl`. Variant suffixes (-L/-R/-l1/-r7) are the
	split hands / numbered redraws of one sprite. Validated against `known` so a
	strip never invents a DC6 that doesn't exist."""
	stem = os.path.splitext(os.path.basename(path))[0].lower()
	if stem in known:
		return stem
	s = re.sub(r"-[lr]?\d*$", "", stem)
	if s in known:
		return s
	s = re.split(r"[-_]", stem)[0]
	return s if s in known else None


def reimagined_hashes(known: set, root: str | None = None) -> dict:
	"""{abs_path: (dhash, dc6_name_or_None)} over the re-imagined art library."""
	root = root or REIMAGINED_ROOT
	out = {}
	if not os.path.isdir(root):
		return out
	for dirpath, _dirs, fnames in os.walk(root):
		for fn in fnames:
			if not fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
				continue
			p = os.path.join(dirpath, fn)
			try:
				with open(p, "rb") as fh:
					h = dhash(fh.read())
			except Exception:  # noqa: BLE001
				continue
			out[p] = (h, dc6_name_from_art_path(p, known))
	return out


def _task_input_url(task: dict) -> str | None:
	d = (task.get("args") or {}).get("draft") or {}
	return d.get("imageUrl") or (d.get("imageUrls") or [None])[0]


def _task_image_id(task: dict) -> str | None:
	d = (task.get("args") or {}).get("draft") or {}
	return d.get("imageId") or (d.get("imageIds") or [None])[0]


def _download(url: str, timeout: float = 30.0) -> bytes:
	with urllib.request.urlopen(url, timeout=timeout) as r:
		return r.read()


def is_high_confidence(source: str) -> bool:
	"""Was this link made on evidence, or on a blind guess? Near-exact image matches,
	Studio-created links, and human review count; anything else (notably the legacy
	`fuzzy-confirmed` picks made before candidates showed sprites) gets re-offered."""
	s = (source or "").strip()
	if s.startswith("auto-image d"):
		try:
			return int(s.rsplit("d", 1)[1]) <= AUTO_MAX_DISTANCE
		except ValueError:
			return False
	return s.startswith(("manual", "reviewed", "studio-"))


def auto_pair(tasks: list, items: list, links: dict, progress=None,
              review_low_confidence: bool = True) -> dict:
	"""Pair unlinked root drafts. Returns {auto, suggestions, unmatched, errors};
	`auto` entries are ALREADY written into `links` (source auto-image).

	With `review_low_confidence`, tasks already linked on weak evidence are re-offered
	as suggestions carrying `current_item_id`, so a blind guess can be corrected."""
	files = dc6_hashes(items, progress=progress)
	reimagined = reimagined_hashes(set(files))
	by_id = {it["id"]: it for it in items}
	auto, suggestions, unmatched, errors = [], [], [], []
	# name lookup resolves to the ART FILE: every item sharing a DC6 is a valid alias
	# for it, so "Aegis" and "Tower Shield" both point at invtow.
	names = [(f, it["name"]) for f, g in files.items() for it in g["items"]]

	def as_cand(invfile, why, rank):
		g = files[invfile]
		nm = [i["name"] for i in g["items"]]
		return {"invfile": invfile, "item_id": g["rep"]["id"], "item_name": g["rep"]["name"],
		        "items": nm[:8], "item_count": len(nm), "why": why, "rank": rank}

	for t in tasks:
		tid = t.get("id")
		if not tid:
			continue
		existing = links.get(tid)
		if existing and existing.get("ignored"):
			continue  # deliberately unlinked by the user; never re-pair it
		if existing and not (review_low_confidence
		                     and not is_high_confidence(existing.get("source", ""))):
			continue
		if t.get("phase") not in ("generate", "draft"):
			continue  # texture/etc. follow their root's link
		url = _task_input_url(t)
		ranked, art_hit = [], None
		if url:
			try:
				h = dhash(_download(url))
				# 1) the RE-IMAGINED library first: hand-made generations were fed these,
				#    so a hit is bit-exact and its filename names the DC6 outright.
				if reimagined:
					ad, ap, adc6 = min(((hamming(h, v[0]), p, v[1])
					                    for p, v in reimagined.items()), key=lambda x: x[0])
					if ad <= AUTO_MAX_DISTANCE and adc6:
						art_hit = {"distance": ad, "path": ap, "invfile": adc6}
				# 2) the DC6 sprites themselves: Studio-created tasks uploaded these directly.
				ranked = sorted(((hamming(h, g["hash"]), f) for f, g in files.items()))[:IMAGE_CANDIDATES]
			except Exception as e:  # noqa: BLE001 -- signed URL expired / decode fail
				errors.append({"task_id": tid, "error": str(e)[:120]})

		# An exact re-imagined-art hit is the strongest evidence there is: it identifies
		# the DC6 by FILENAME, not by resemblance.
		if art_hit and not existing and art_hit["invfile"] in files:
			f = art_hit["invfile"]
			rep = files[f]["rep"]
			remember(links, tid, item_id=rep["id"], image_id=_task_image_id(t),
			         phase=t.get("phase") or "draft",
			         source=f"auto-art d{art_hit['distance']} ({os.path.basename(art_hit['path'])})",
			         name=t.get("name") or "", invfile=f, art_file=art_hit["path"])
			auto.append({"task_id": tid, "task_name": t.get("name") or "",
			             "item_id": rep["id"], "item_name": rep["name"], "invfile": f,
			             "shared_by": len(files[f]["items"]), "distance": art_hit["distance"],
			             "via": os.path.basename(art_hit["path"])})
			continue
		if ranked and ranked[0][0] <= AUTO_MAX_DISTANCE and not existing:
			best_d, best_f = ranked[0]
			rep = files[best_f]["rep"]
			remember(links, tid, item_id=rep["id"], image_id=_task_image_id(t),
			         phase=t.get("phase") or "draft", source=f"auto-image d{best_d}",
			         name=t.get("name") or "", invfile=best_f)
			auto.append({"task_id": tid, "task_name": t.get("name") or "",
			             "item_id": rep["id"], "item_name": rep["name"], "invfile": best_f,
			             "shared_by": len(files[best_f]["items"]), "distance": best_d})
			continue

		# Not near-exact: offer art files to eyeball. Image-similarity first (it beats
		# name matching when Meshy's auto-name is a description, e.g. the Plate Mail
		# sprite it named "Chainmail hauberk"), then name similarity.
		cand, seen = [], set()
		for d, f in ranked:
			if d <= SUGGEST_MAX_DISTANCE and f not in seen:
				seen.add(f)
				cand.append(as_cand(f, f"image d{d}", d))
		tname = (t.get("name") or "").strip()
		if tname:
			best_by_file = {}
			for f, n in names:
				s = difflib.SequenceMatcher(None, tname.lower(), n.lower()).ratio()
				if s > best_by_file.get(f, (0,))[0]:
					best_by_file[f] = (s, n)
			for f, (s, n) in sorted(best_by_file.items(), key=lambda kv: -kv[1][0])[:NAME_CANDIDATES]:
				if s >= SUGGEST_MIN_RATIO and f not in seen:
					seen.add(f)
					cand.append(as_cand(f, f"name {int(s * 100)}% ({n})", 100 - s))
		if art_hit and art_hit["invfile"] in files and art_hit["invfile"] not in seen:
			seen.add(art_hit["invfile"])
			cand.append(as_cand(art_hit["invfile"],
			                    f"re-imagined art d{art_hit['distance']} "
			                    f"({os.path.basename(art_hit['path'])})", -2))
		cand.sort(key=lambda c: c["rank"])
		cur_id = (existing or {}).get("item_id")
		cur_file = (existing or {}).get("invfile") or (
			by_id[cur_id]["invfile"].lower() if cur_id in by_id else None)
		if cur_file and cur_file in files and cur_file not in seen:
			seen.add(cur_file)
			cand.insert(0, as_cand(cur_file, "current pick", -1))
		row = {"task_id": tid, "task_name": tname,
		       "preview": ((t.get("result") or {}).get("previewUrl") or ""),
		       "input_image": url or "",
		       "best_distance": (ranked[0][0] if ranked else None),
		       "current_item_id": cur_id,
		       "current_invfile": cur_file,
		       "current_item_name": (by_id[cur_id]["name"] if cur_id in by_id else None),
		       "current_source": (existing or {}).get("source"),
		       "candidates": cand}
		# A task with a weak existing link always needs review, even with no candidates.
		(suggestions if (cand or cur_id) else unmatched).append(row)
	return {"auto": auto, "suggestions": suggestions, "unmatched": unmatched, "errors": errors}
