"""Minimal read-side StormLib ctypes wrapper.

StormLib.dll is built from https://github.com/ladislav-zezula/StormLib
(x64, BUILD_SHARED_LIBS=ON) and dropped in bin/.
Write-side (archive authoring for patch.mpq export) comes in Phase 1.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import threading

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


# PD2 effective priority order, highest first (see AssetStudioPlan.md §3.1).
# Loose data\ overlay outranks all of these at runtime.
PD2_SEARCH_ORDER = [
	r"C:\Diablo2\ProjectD2\pd2data.mpq",
	r"C:\Diablo2\ProjectD2\pd2assets.mpq",
	r"C:\Diablo2\ProjectD2\pd2maps.mpq",
	r"C:\Diablo2\ProjectD2\patch_d2.mpq",
	r"C:\Diablo2\patch_d2.mpq",
	r"C:\Diablo2\d2exp.mpq",
	r"C:\Diablo2\d2data.mpq",
	r"C:\Diablo2\d2char.mpq",
]


# Read-only base archives are static for the whole session, but opening one of D2's large MPQs
# costs ~100ms+. The old code opened+closed EVERY archive on EVERY read, so a char graphic (in the
# last archive, d2char.mpq) cost ~1s. Cache the open handles instead; a global lock serializes the
# (now fast) reads since one StormLib handle isn't safe for concurrent access.
_OPEN_ARCHIVES = {}
_MPQ_LOCK = threading.Lock()


def read_effective(name: str, search_order=None) -> tuple[bytes, str]:
	"""Read `name` from the highest-priority archive that has it.

	Returns (data, archive_path). Raises FileNotFoundError if absent everywhere.
	"""
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
