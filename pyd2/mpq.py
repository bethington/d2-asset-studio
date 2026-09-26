"""Minimal read-side StormLib ctypes wrapper.

StormLib.dll is built from https://github.com/ladislav-zezula/StormLib
(x64, BUILD_SHARED_LIBS=ON) and dropped in bin/.
Write-side (archive authoring for patch.mpq export) comes in Phase 1.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
import re
import threading
import time

_DEFAULT_DLL = os.path.join(os.path.dirname(__file__), "..", "bin", "StormLib.dll")

MAX_PATH = 260


class SFILE_FIND_DATA(ctypes.Structure):
	_fields_ = [
		("cFileName", ctypes.c_char * MAX_PATH),
		("szPlainName", ctypes.c_char_p),
		("dwHashIndex", wt.DWORD),
		("dwBlockIndex", wt.DWORD),
		("dwFileSize", wt.DWORD),
		("dwFileFlags", wt.DWORD),
		("dwCompSize", wt.DWORD),
		("dwFileTimeLo", wt.DWORD),
		("dwFileTimeHi", wt.DWORD),
		("lcLocale", wt.LCID),
	]


class MpqError(OSError):
	pass


class _Storm:
	_lib = None

	@classmethod
	def lib(cls):
		if cls._lib is None:
			lib = ctypes.WinDLL(os.path.abspath(os.environ.get("STORMLIB_DLL", _DEFAULT_DLL)))
			HANDLE = ctypes.c_void_p
			BOOL = wt.BOOL
			# 64-bit safety: without prototypes ctypes truncates handles to int.
			lib.SFileOpenArchive.argtypes = [ctypes.c_char_p, wt.DWORD, wt.DWORD, ctypes.POINTER(HANDLE)]
			lib.SFileOpenArchive.restype = BOOL
			lib.SFileCloseArchive.argtypes = [HANDLE]
			lib.SFileCloseArchive.restype = BOOL
			lib.SFileHasFile.argtypes = [HANDLE, ctypes.c_char_p]
			lib.SFileHasFile.restype = BOOL
			lib.SFileOpenFileEx.argtypes = [HANDLE, ctypes.c_char_p, wt.DWORD, ctypes.POINTER(HANDLE)]
			lib.SFileOpenFileEx.restype = BOOL
			lib.SFileGetFileSize.argtypes = [HANDLE, ctypes.POINTER(wt.DWORD)]
			lib.SFileGetFileSize.restype = wt.DWORD
			lib.SFileReadFile.argtypes = [HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
			lib.SFileReadFile.restype = BOOL
			lib.SFileCloseFile.argtypes = [HANDLE]
			lib.SFileCloseFile.restype = BOOL
			lib.SFileFindFirstFile.argtypes = [HANDLE, ctypes.c_char_p, ctypes.POINTER(SFILE_FIND_DATA), ctypes.c_char_p]
			lib.SFileFindFirstFile.restype = HANDLE
			lib.SFileFindNextFile.argtypes = [HANDLE, ctypes.POINTER(SFILE_FIND_DATA)]
			lib.SFileFindNextFile.restype = BOOL
			lib.SFileFindClose.argtypes = [HANDLE]
			lib.SFileFindClose.restype = BOOL
			# --- write side (archive authoring) ---
			lib.SFileCreateArchive.argtypes = [ctypes.c_char_p, wt.DWORD, wt.DWORD, ctypes.POINTER(HANDLE)]
			lib.SFileCreateArchive.restype = BOOL
			lib.SFileAddFileEx.argtypes = [HANDLE, ctypes.c_char_p, ctypes.c_char_p, wt.DWORD, wt.DWORD, wt.DWORD]
			lib.SFileAddFileEx.restype = BOOL
			lib.SFileCompactArchive.argtypes = [HANDLE, ctypes.c_char_p, BOOL]
			lib.SFileCompactArchive.restype = BOOL
			cls._lib = lib
		return cls._lib


# StormLib create/add flags
MPQ_CREATE_LISTFILE = 0x00100000
MPQ_CREATE_ATTRIBUTES = 0x00200000
MPQ_FILE_COMPRESS = 0x00000200
MPQ_FILE_REPLACEEXISTING = 0x80000000
MPQ_COMPRESSION_ZLIB = 0x02
MPQ_COMPRESSION_PKWARE = 0x08  # PKWARE DCL implode — the compression D2 archives use


def add_files(mpq_path: str, files: dict) -> None:
	"""Add/replace {archived_name: disk_path} into an EXISTING archive in place."""
	lib = _Storm.lib()
	h = ctypes.c_void_p()
	# open writable (no READ_ONLY flag)
	_check(lib.SFileOpenArchive(mpq_path.encode("mbcs"), 0, 0, ctypes.byref(h)),
	       f"SFileOpenArchive(rw, {mpq_path})")
	try:
		for archived, disk in files.items():
			_check(lib.SFileAddFileEx(h, disk.encode("mbcs"), archived.encode("mbcs"),
			                          MPQ_FILE_COMPRESS | MPQ_FILE_REPLACEEXISTING,
			                          MPQ_COMPRESSION_ZLIB, MPQ_COMPRESSION_ZLIB),
			       f"SFileAddFileEx({archived})")
	finally:
		lib.SFileCloseArchive(h)


# MPQ format-version flags (SFileCreateArchive high bits)
MPQ_CREATE_ARCHIVE_V1 = 0x00000000  # format v1 — the only version Diablo II's Storm.dll reads


def build_archive(mpq_path: str, files: dict, replace: bool = True,
                  compress: str = "pkware") -> None:
	"""Author a Diablo II-compatible MPQ at mpq_path from {archived_name: disk_path}.

	archived_name uses backslashes, e.g. 'data\\global\\items\\invhp1.dc6'.
	Format is forced to MPQ v1 (D2's Storm.dll only reads v1). Compression:
	  "pkware" — PKWARE DCL implode (what D2 archives use; Storm always decodes it)
	  "none"   — stored uncompressed (guaranteed-readable; fine for tiny sprites)
	  "zlib"   — DO NOT USE for D2: the 1.13c Storm.dll can't inflate it -> game hang.
	Overwrites mpq_path if it exists.
	"""
	if replace and os.path.exists(mpq_path):
		os.remove(mpq_path)
	lib = _Storm.lib()
	h = ctypes.c_void_p()
	max_files = max(16, len(files) * 2)
	_check(lib.SFileCreateArchive(mpq_path.encode("mbcs"),
	                              MPQ_CREATE_ARCHIVE_V1 | MPQ_CREATE_LISTFILE | MPQ_CREATE_ATTRIBUTES,
	                              max_files, ctypes.byref(h)),
	       f"SFileCreateArchive({mpq_path})")
	if compress == "none":
		add_flags, comp = MPQ_FILE_REPLACEEXISTING, 0
	elif compress == "zlib":
		add_flags, comp = MPQ_FILE_COMPRESS | MPQ_FILE_REPLACEEXISTING, MPQ_COMPRESSION_ZLIB
	else:  # pkware (D2-native)
		add_flags, comp = MPQ_FILE_COMPRESS | MPQ_FILE_REPLACEEXISTING, MPQ_COMPRESSION_PKWARE
	try:
		for archived, disk in files.items():
			_check(lib.SFileAddFileEx(h, disk.encode("mbcs"), archived.encode("mbcs"),
			                          add_flags, comp, comp),
			       f"SFileAddFileEx({archived})")
	finally:
		lib.SFileCompactArchive(h, None, True)
		lib.SFileCloseArchive(h)


def _check(ok, what):
	if not ok:
		raise MpqError(f"{what} failed (GetLastError={ctypes.get_last_error() or ctypes.windll.kernel32.GetLastError()})")


class MpqArchive:
	def __init__(self, path: str):
		self.path = path
		self._h = ctypes.c_void_p()
		lib = _Storm.lib()
		# Our StormLib build is ANSI (no UNICODE define) -> char* paths.
		opened = lib.SFileOpenArchive(path.encode("mbcs"), 0, 0x0100, ctypes.byref(self._h))  # STREAM_FLAG_READ_ONLY
		_check(opened, f"SFileOpenArchive({path})")

	def close(self):
		if self._h:
			_Storm.lib().SFileCloseArchive(self._h)
			self._h = ctypes.c_void_p()

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		self.close()

	def list_files(self):
		"""Yield (name, size) for every enumerable file (needs (listfile))."""
		lib = _Storm.lib()
		fd = SFILE_FIND_DATA()
		hfind = lib.SFileFindFirstFile(self._h, b"*", ctypes.byref(fd), None)
		if not hfind:
			return
		try:
			while True:
				yield fd.cFileName.decode("mbcs", "replace"), fd.dwFileSize
				if not lib.SFileFindNextFile(hfind, ctypes.byref(fd)):
					break
		finally:
			lib.SFileFindClose(hfind)

	def has_file(self, name: str) -> bool:
		return bool(_Storm.lib().SFileHasFile(self._h, name.encode("mbcs")))

	def read_file(self, name: str) -> bytes:
		lib = _Storm.lib()
		hf = ctypes.c_void_p()
		_check(lib.SFileOpenFileEx(self._h, name.encode("mbcs"), 0, ctypes.byref(hf)),
		       f"SFileOpenFileEx({name})")
		try:
			high = wt.DWORD(0)
			size = lib.SFileGetFileSize(hf, ctypes.byref(high))
			if size == 0xFFFFFFFF:
				# Phantom hash entry (protected/PD2 archives): opens but has no
				# readable block. Treat as absent.
				raise MpqError(f"SFileGetFileSize({name}): invalid size (phantom entry)")
			buf = ctypes.create_string_buffer(size)
			read = wt.DWORD(0)
			_check(lib.SFileReadFile(hf, buf, size, ctypes.byref(read), None),
			       f"SFileReadFile({name})")
			return buf.raw[: read.value]
		finally:
			lib.SFileCloseFile(hf)


# PD2 effective priority order, highest first (see AssetStudioPlan.md §3.1). The NAMES and their
# relative priority are known; WHERE they live comes from PD2_GAME (configure() below), so a moved
# install or a differently-rooted game folder just works. Loose data\ overlay outranks all of these
# at runtime.
_KNOWN_PD2 = ("pd2data.mpq", "pd2assets.mpq", "pd2maps.mpq", "patch_d2.mpq")   # in <root>\ProjectD2\
_KNOWN_ROOT = ("patch_d2.mpq", "d2exp.mpq", "d2data.mpq", "d2char.mpq")         # in <root>\
# Names that could plausibly carry item data. Audio/video archives (d2music, d2speech, ...) are
# ignored, so only a new pd2*/patch* archive triggers the "unrecognised archive" warning.
_WATCH_NAME = re.compile(r"^(pd2|patch)", re.IGNORECASE)

log = logging.getLogger("pyd2.mpq")

_CONFIG = {"game": None, "extra": []}
PD2_DIR = ""
D2_ROOT = ""
PD2_SEARCH_ORDER: list = []
_UNKNOWN_LOGGED = None


# Read-only base archives are static WHILE THE GAME IS UNCHANGED, but opening one of D2's large
# MPQs costs ~100ms+. The old code opened+closed EVERY archive on EVERY read, so a char graphic (in
# the last archive, d2char.mpq) cost ~1s. Cache the open handles instead; a global lock serializes
# the (now fast) reads since one StormLib handle isn't safe for concurrent access. A game update
# replaces those files, so refresh_if_changed() drops the handles (and every registered derived
# cache) when any archive's size/mtime changes.
_OPEN_ARCHIVES = {}
_MPQ_LOCK = threading.Lock()
_CHANGE_CALLBACKS: list = []
_GENERATION = 0
_LAST_FP = None
_LAST_CHECK = 0.0


def configure(pd2_game: str, extra=()) -> None:
	"""Point the archive search at an install: `pd2_game` is its Game.exe (ProjectD2\\Game.exe),
	`extra` are additional archives to read first (see studio_config.PD2_EXTRA_MPQS)."""
	global PD2_DIR, D2_ROOT, PD2_SEARCH_ORDER, _LAST_FP, _LAST_CHECK, _UNKNOWN_LOGGED
	pd2_dir = os.path.dirname(os.path.abspath(pd2_game))
	root = os.path.dirname(pd2_dir)
	extras = [os.path.abspath(p) for p in extra]
	with _MPQ_LOCK:
		PD2_DIR, D2_ROOT = pd2_dir, root
		PD2_SEARCH_ORDER = (extras
		                    + [os.path.join(pd2_dir, n) for n in _KNOWN_PD2]
		                    + [os.path.join(root, n) for n in _KNOWN_ROOT])
		_CONFIG.update(game=pd2_game, extra=extras)
		_LAST_FP, _LAST_CHECK, _UNKNOWN_LOGGED = None, 0.0, None
	_warn_unknown()


def unknown_archives() -> list:
	"""pd2*/patch* archives in the game folders that are NOT in the search order. They are not
	read (their priority is unknown); the caller should surface them, not ignore them."""
	known = {os.path.normcase(os.path.abspath(p)) for p in PD2_SEARCH_ORDER}
	out = []
	for d in (PD2_DIR, D2_ROOT):
		try:
			names = sorted(os.listdir(d))
		except OSError:
			continue
		for n in names:
			p = os.path.join(d, n)
			if n.lower().endswith(".mpq") and _WATCH_NAME.match(n) \
					and os.path.normcase(os.path.abspath(p)) not in known:
				out.append(p)
	return out


def _warn_unknown() -> None:
	global _UNKNOWN_LOGGED
	unknown = tuple(unknown_archives())
	if unknown and unknown != _UNKNOWN_LOGGED:
		log.warning("Unrecognised PD2 archive(s) NOT being read: %s. If they carry game data, list "
		            "them in PD2_EXTRA_MPQS (highest priority first, separated by %r).",
		            ", ".join(unknown), os.pathsep)
	_UNKNOWN_LOGGED = unknown


def fingerprint() -> tuple:
	"""(path, size, mtime_ns) of every archive in the search order plus any unrecognised one;
	(path, None) when absent. Two stats per archive -- cheap enough to run on every request."""
	fp = []
	for p in list(PD2_SEARCH_ORDER) + unknown_archives():
		try:
			st = os.stat(p)
		except OSError:
			fp.append((p, None))
		else:
			fp.append((p, st.st_size, st.st_mtime_ns))
	return tuple(fp)


def on_change(fn):
	"""Register `fn()` to run after a game update is detected (clear whatever you derived from
	MPQ data). Runs outside the MPQ lock, so it may itself call read_effective()."""
	_CHANGE_CALLBACKS.append(fn)
	return fn


def generation() -> int:
	"""Bumped each time a change to the game files is detected."""
	return _GENERATION


def refresh_if_changed(min_interval: float = 1.0) -> bool:
	"""If any game archive changed since the last look: close the cached handles, bump the
	generation, run the on_change callbacks. Returns True when that happened. The first call after
	configure() only records the baseline. Looks at most once per `min_interval` seconds."""
	global _LAST_FP, _LAST_CHECK, _GENERATION
	now = time.monotonic()
	with _MPQ_LOCK:
		if _LAST_FP is not None and now - _LAST_CHECK < min_interval:
			return False
		_LAST_CHECK = now
		fp = fingerprint()
		if _LAST_FP is None:
			_LAST_FP = fp
			return False
		if fp == _LAST_FP:
			return False
		_LAST_FP = fp
		for arc in _OPEN_ARCHIVES.values():
			try:
				arc.close()
			except Exception:  # noqa: BLE001 - a dead handle must not block the refresh
				pass
		_OPEN_ARCHIVES.clear()
		_GENERATION += 1
		callbacks = list(_CHANGE_CALLBACKS)
	log.warning("Game archives changed on disk -- reopened, derived caches cleared (generation %d)",
	            _GENERATION)
	_warn_unknown()
	for cb in callbacks:
		try:
			cb()
		except Exception:  # noqa: BLE001
			log.exception("on_change callback %r failed", cb)
	return True


def status() -> dict:
	"""What Studio is reading, for a UI/status endpoint."""
	archives = []
	for p in PD2_SEARCH_ORDER:
		try:
			st = os.stat(p)
		except OSError:
			continue
		archives.append({"path": p, "size": st.st_size, "mtime": st.st_mtime})
	return {"generation": _GENERATION, "game": _CONFIG["game"], "archives": archives,
	        "missing": [p for p in PD2_SEARCH_ORDER if not os.path.exists(p)],
	        "unknown": unknown_archives()}


try:
	from studio_config import PD2_EXTRA_MPQS, PD2_GAME
except ImportError:  # pyd2 used outside the repo root
	PD2_GAME = os.environ.get("PD2_GAME") or r"C:\Diablo2\ProjectD2\Game.exe"
	PD2_EXTRA_MPQS = [p for p in (os.environ.get("PD2_EXTRA_MPQS") or "").split(os.pathsep) if p]
configure(PD2_GAME, PD2_EXTRA_MPQS)


def read_effective(name: str, search_order=None) -> tuple[bytes, str]:
	"""Read `name` from the highest-priority archive that has it.

	Returns (data, archive_path). Raises FileNotFoundError if absent everywhere.
	"""
	refresh_if_changed()   # a game update since the last read? reopen before serving stale data
	with _MPQ_LOCK:
		for arc_path in search_order or PD2_SEARCH_ORDER:
			if not os.path.exists(arc_path):
				continue
			arc = _OPEN_ARCHIVES.get(arc_path)
			if arc is None:
				arc = MpqArchive(arc_path)          # opened once, kept open for the session
				_OPEN_ARCHIVES[arc_path] = arc
			# SFileHasFile yields false positives on phantom hash entries in the PD2/protected
			# archives; attempt the read and fall through.
			if arc.has_file(name):
				try:
					return arc.read_file(name), arc_path
				except MpqError:
					continue
		raise FileNotFoundError(name)
