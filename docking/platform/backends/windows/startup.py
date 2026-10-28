"""Locate the native GTK DLLs before importing PyGObject on Windows."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_DLL_DIRECTORIES: dict[Path, object] = {}


def _prepare_bundle(prefix: Path) -> None:
    """Resolve native resources from an installed, relocatable application."""
    if not (prefix / "share/docking/runtime.json").is_file():
        return
    binary = str(prefix / "bin")
    os.environ["PATH"] = binary + os.pathsep + os.environ.get("PATH", "")
    resources = {
        "PYGI_DLL_PATH": prefix / "bin",
        "GI_TYPELIB_PATH": prefix / "lib/girepository-1.0",
        "GTK_DATA_PREFIX": prefix,
        "GTK_EXE_PREFIX": prefix,
        "XDG_DATA_DIRS": prefix / "share",
        "GSETTINGS_SCHEMA_DIR": prefix / "share/glib-2.0/schemas",
        "GDK_PIXBUF_MODULE_FILE": prefix / "lib/gdk-pixbuf-2.0/2.10.0/loaders.cache",
        "GDK_PIXBUF_MODULEDIR": prefix / "lib/gdk-pixbuf-2.0/2.10.0/loaders",
        "GST_PLUGIN_SYSTEM_PATH_1_0": prefix / "lib/gstreamer-1.0",
        "GST_PLUGIN_SCANNER_1_0": prefix
        / "libexec/gstreamer-1.0/gst-plugin-scanner.exe",
        "FONTCONFIG_PATH": prefix / "etc/fonts",
    }
    os.environ.update({name: str(path) for name, path in resources.items()})


def prepare_windows_runtime() -> bool:
    """Keep DLL search-directory handles alive for the process lifetime.

    MSYS2 stores DLLs beside Python; Conda-style installations use Library/bin.
    An explicit PYGI_DLL_PATH supplied by the user continues to take priority.
    """
    _prepare_bundle(Path(sys.prefix))
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
