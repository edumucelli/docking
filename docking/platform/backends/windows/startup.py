"""Locate the native GTK DLLs before importing PyGObject on Windows."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_DLL_DIRECTORIES: dict[Path, object] = {}


def prepare_windows_runtime() -> bool:
    """Keep DLL search-directory handles alive for the process lifetime.

    MSYS2 stores DLLs beside Python; Conda-style installations use Library/bin.
    An explicit PYGI_DLL_PATH supplied by the user continues to take priority.
    """
    # GTK's legacy Win32 file-drop path has no drag-motion feedback. Select
    # its OLE2 implementation before GDK opens the display; it also carries
    # same-widget reorder drags through the native Windows drag loop.
    os.environ.setdefault("GDK_WIN32_USE_EXPERIMENTAL_OLE2_DND", "1")
    candidates = (
        Path(sys.executable).parent,
        Path(sys.prefix) / "bin",
        Path(sys.prefix) / "Library" / "bin",
    )
    directories = list(dict.fromkeys(path for path in candidates if path.is_dir()))
    for path in directories:
        if path not in _DLL_DIRECTORIES:
            _DLL_DIRECTORIES[path] = os.add_dll_directory(str(path))
    os.environ.setdefault("PYGI_DLL_PATH", os.pathsep.join(map(str, directories)))
    return bool(directories)
