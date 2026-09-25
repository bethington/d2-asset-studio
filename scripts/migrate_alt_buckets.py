"""One-time migration: fold legacy per-item alternate folders + manifest entries into the
shared per-DC6 buckets (see assets.py "shared-by-DC6 bucketing").

Every item whose inventory graphic (invfile) resolves to the same .dc6 now shares one pool
of alternates under alternates/dc6/<invfile>/ and one active choice keyed 'dc6/<invfile>' in
manifest.json. Flippy alternates move to alternates/flip/<flippyfile>/ ('flip/<flippyfile>').

Usage:
  python -m scripts.migrate_alt_buckets            # dry-run: print the plan, touch nothing
  python -m scripts.migrate_alt_buckets --apply     # back up, then perform the migration

Idempotent: once manifest['bucketed'] is set, --apply is a no-op.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.assets as assets  # noqa: E402
import app.server as server  # noqa: E402  (registers the live-catalog invfile resolver)

ALT = assets.ALTERNATES
RESERVED = {"dc6", "flip"}  # already-bucketed roots


def _same_bytes(a: str, b: str) -> bool:
	try:
		with open(a, "rb") as fa, open(b, "rb") as fb:
			return fa.read() == fb.read()
	except OSError:
		return False


def _legacy_item_dirs():
	"""Yield (item_id, dirpath) for every legacy per-item alternate folder -- a dir holding
	.dc6 files (or a flippy child) that is not already a dc6/ or flip/ bucket."""
	if not os.path.isdir(ALT):
		return
	roots = [d for d in sorted(os.listdir(ALT))
	         if d not in RESERVED and os.path.isdir(os.path.join(ALT, d))]
	for root in roots:
		for dirpath, dirnames, filenames in os.walk(os.path.join(ALT, root)):
			if os.path.basename(dirpath) == "flippy":
				dirnames[:] = []  # its parent handles it
				continue
			if any(n.endswith(".dc6") for n in filenames) or "flippy" in dirnames:
				yield os.path.relpath(dirpath, ALT).replace(os.sep, "/"), dirpath


def _alt_ids(dirpath: str):
	return sorted(n[:-4] for n in os.listdir(dirpath)
	              if n.endswith(".dc6") and os.path.isfile(os.path.join(dirpath, n)))


def _plan_dir(item_id, src, dst, log, *, apply):
	"""Merge one legacy folder's alternates into its bucket. Dedup identical alts; on a real
	content collision keep both by suffixing the incoming alt_id (sidecars renamed with it)."""
	if os.path.abspath(src) == os.path.abspath(dst):
		return  # resolver couldn't map this item -> already in place, leave it
	for alt_id in _alt_ids(src):
		files = [n for n in os.listdir(src) if n.startswith(alt_id + ".")
		         and os.path.isfile(os.path.join(src, n))]
		final = alt_id
		dst_dc6 = os.path.join(dst, alt_id + ".dc6")
		if os.path.exists(dst_dc6):
			if _same_bytes(os.path.join(src, alt_id + ".dc6"), dst_dc6):
				log.append(f"    dedup  {item_id}:{alt_id} (identical, dropping)")
				if apply:
					for n in files:
						os.remove(os.path.join(src, n))
				continue
			n = 2
			while os.path.exists(os.path.join(dst, f"{alt_id}-{n}.dc6")):
				n += 1
			final = f"{alt_id}-{n}"
			log.append(f"    COLLIDE {item_id}:{alt_id} -> {final} (different art, kept both)")
		else:
			log.append(f"    move   {item_id}:{alt_id} -> {os.path.relpath(dst, ALT)}/{final}")
		if apply:
			os.makedirs(dst, exist_ok=True)
			for n in files:
				ext = n[len(alt_id):]  # ".dc6" / ".meta.json" / ".glb" / ...
				os.replace(os.path.join(src, n), os.path.join(dst, final + ext))


def _rekey_manifest(log, *, apply):
	m = assets._load_manifest()
	assets_map = m.get("assets", {})
	new = {}
	conflicts = 0
	for key, entry in assets_map.items():
		if key.startswith("dc6/") or key.startswith("flip/"):
			new.setdefault(key, {}).update(entry)  # already bucketed
			continue
		item_id = key
		# split the legacy combined entry into its inventory + flippy buckets
		if "active" in entry or "invfile" in entry:
			bkey = assets._inv_bucket(item_id)
			slot = {k: entry[k] for k in ("active", "invfile") if k in entry}
			if bkey in new and new[bkey].get("active") not in (None, slot.get("active")):
				conflicts += 1
				log.append(f"    active conflict on {bkey}: "
				           f"{new[bkey].get('active')} vs {slot.get('active')} (keeping latter)")
			new.setdefault(bkey, {}).update(slot)
		if "flippy_active" in entry or "flippyfile" in entry:
			fkey = assets._flip_bucket(item_id)
			slot = {k: entry[k] for k in ("flippy_active", "flippyfile") if k in entry}
			new.setdefault(fkey, {}).update(slot)
		log.append(f"    rekey  {item_id} -> {assets._inv_bucket(item_id)}")
	if apply:
		m["assets"] = new
		m["bucketed"] = True
		assets._save_manifest(m)
	return conflicts


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("--apply", action="store_true", help="perform the migration (default: dry-run)")
	args = ap.parse_args()
	apply = args.apply

	m = assets._load_manifest()
	if m.get("bucketed"):
		print("manifest already marked bucketed -- nothing to do.")
		return

	log: list[str] = []
	print(f"workspace alternates: {ALT}")
	print(f"manifest:             {assets.MANIFEST}\n")

	if apply:
		bak = ALT + ".pre_bucket_bak"
		if os.path.isdir(ALT) and not os.path.isdir(bak):
			shutil.copytree(ALT, bak)
			print(f"backed up alternates -> {bak}")
		mbak = assets.MANIFEST + ".pre_bucket_bak"
		if os.path.exists(assets.MANIFEST) and not os.path.exists(mbak):
			shutil.copy2(assets.MANIFEST, mbak)
			print(f"backed up manifest   -> {mbak}\n")

	log.append("folders:")
	dirs = list(_legacy_item_dirs())
	for item_id, src in dirs:
		fsub = os.path.join(src, "flippy")
		if os.path.isdir(fsub):
			_plan_dir(item_id, fsub, assets.flippy_dir(item_id), log, apply=apply)
		_plan_dir(item_id, src, assets.alt_dir(item_id), log, apply=apply)

	if apply:  # prune emptied legacy trees (leave dc6/ and flip/ alone)
		for root in [d for d in os.listdir(ALT) if d not in RESERVED]:
			for dp, _dn, _fn in os.walk(os.path.join(ALT, root), topdown=False):
				try:
					os.rmdir(dp)
				except OSError:
					pass

	log.append("manifest:")
	conflicts = _rekey_manifest(log, apply=apply)

	print("\n".join(log))
	print(f"\n{'APPLIED' if apply else 'DRY-RUN'}: {len(dirs)} legacy folders, "
	      f"{conflicts} active-choice conflicts resolved (last wins).")
	if not apply:
		print("Re-run with --apply to perform it.")


if __name__ == "__main__":
	main()
