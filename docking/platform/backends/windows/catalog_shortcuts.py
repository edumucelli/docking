"""Resolve Start Menu shortcut sources by native identity on the catalog worker."""

from __future__ import annotations

import ctypes
import errno
import os
from collections.abc import Callable, Iterator
from pathlib import Path

from docking.log import get_logger

from .appresolver import shortcut_application_id
from .com import GUID, HRESULT, apartment
from .ffi import bind

log = get_logger("windows.catalog_shortcuts")
ShortcutSources = dict[str, tuple[tuple[Path, os.stat_result], ...]]
_MAX_ENTRIES = 10000


def _program_directories() -> tuple[Path, ...]:
    roots = []
    with apartment() as ole:
        folder = bind(
            ctypes.WinDLL("shell32", use_last_error=True),
            "SHGetKnownFolderPath",
            HRESULT,
            ctypes.POINTER(GUID),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        free = bind(ole, "CoTaskMemFree", None, ctypes.c_void_p)
        for identifier in (
            "a77f5d77-2e2b-44c3-a6a2-aba601054a51",  # FOLDERID_Programs
            "0139d44e-6afe-49f2-8690-3dafcae6ffb8",  # FOLDERID_CommonPrograms
        ):
            value = ctypes.c_void_p()
            try:
                status = folder(
                    ctypes.byref(GUID.parse(identifier)), 0, None, ctypes.byref(value)
                )
                if status < 0:
                    if status & 0xFFFFFFFF in (0x80070002, 0x80070003):
                        continue
                    raise OSError(
                        status & 0xFFFFFFFF, "Could not locate Start Menu programs"
                    )
                if not value.value:
                    raise OSError("Missing Start Menu programs directory")
                root = Path(ctypes.wstring_at(value))
                if root not in roots:
                    roots.append(root)
            finally:
                if value.value:
                    free(value)
    return tuple(roots)


def _links(roots: tuple[Path, ...], cancelled: Callable[[], bool]) -> Iterator[Path]:
    pending = list(roots)
    visited = set()
    entries = 0
    while pending:
        if cancelled():
            return
        directory = pending.pop()
        try:
            identity = directory.stat()
            key = identity.st_dev, identity.st_ino
            if key in visited:
                continue
            visited.add(key)
            with os.scandir(directory) as children:
                for child in children:
                    entries += 1
                    if entries > _MAX_ENTRIES:
                        raise OSError(
                            errno.E2BIG,
                            "Start Menu exceeds the shortcut traversal limit",
                        )
                    if cancelled():
                        return
                    try:
                        if child.is_symlink():
                            continue
                        if (
                            os.name == "nt"
                            and child.stat(follow_symlinks=False).st_file_attributes
                            & 0x400
                        ):
                            continue  # Never traverse junctions/reparse points.
                        if child.is_dir(follow_symlinks=False):
                            pending.append(Path(child.path))
                        elif child.name.lower().endswith(".lnk") and child.is_file(
                            follow_symlinks=False
                        ):
                            yield Path(child.path)
                    except FileNotFoundError:
                        continue
        except FileNotFoundError:
            continue  # An installer can remove a directory while it is scanned.


def read_shortcut_sources(
    application_ids: frozenset[str], *, cancelled: Callable[[], bool]
) -> ShortcutSources:
    """One bounded pass; executable, title and basename never select a source."""
    if not application_ids or cancelled():
        return {}
    sources: dict[str, list[tuple[Path, os.stat_result]]] = {}
    for path in _links(_program_directories(), cancelled):
        if cancelled():
            return {}
        try:
            before = path.stat()
            identifier = shortcut_application_id(path)
            if identifier in application_ids:
                sources.setdefault(identifier, []).append((path, before))
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.debug("Could not resolve a Start Menu shortcut (error %s)", exc.errno)
    return {identifier: tuple(candidates) for identifier, candidates in sources.items()}
