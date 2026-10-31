"""Passive GTK input-module facts for owned native input acceptance widgets."""

from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GObject", "2.0")
from gi.repository import GObject, Gtk

_PATH_ENVIRONMENT = (
    "GTK_IM_MODULE_FILE",
    "GTK_PATH",
    "GTK_EXE_PREFIX",
    "GTK_DATA_PREFIX",
    "PYGI_DLL_PATH",
)


def _path_facts(value: str | None) -> dict:
    # GTK configuration may refer to private user directories or network hosts.
    # Selection diagnostics need presence and shape, never their spelling.
    return {
        "set": value is not None,
        "entries": len(value.split(os.pathsep)) if value else 0,
        "network": bool(value and value.startswith(("\\\\", "//"))),
    }


def snapshot(entry: Gtk.Entry | None = None) -> dict:
    """Observe selection on the GUI thread without focusing another IM context.

    Gtk.Entry's im-module property is its explicit override, not its selected
    slave. A separate, unfocused multicontext follows the same selection rules
    and mirrors that override. Binding its client window initializes selection;
    it never filters a key, resets the real entry or installs a focused filter.
    Win32 GTK can compile native IME support into its main DLL, so a missing
    standalone im-ime.dll or module cache does not establish missing support.
    """
    override = entry.get_property("im-module") if entry is not None else None
    settings = Gtk.Settings.get_default()
    cache = Gtk.rc_get_im_module_file()
    result = {
        "entry_override": override,
        "settings_module": settings.get_property("gtk-im-module"),
        "environment": {
            "GTK_IM_MODULE": os.environ.get("GTK_IM_MODULE"),
            **{name: _path_facts(os.environ.get(name)) for name in _PATH_ENVIRONMENT},
        },
        "module_cache": _path_facts(cache),
        "module_cache_exists": (
            Path(cache).is_file()
            if cache and not _path_facts(cache)["network"]
            else None
        ),
    }
    context = Gtk.IMMulticontext()
    try:
        if override is not None:
            context.set_context_id(override)
        if entry is not None:
            context.set_client_window(entry.get_window())
        context.get_preedit_string()
        result["probe_context_id"] = context.get_context_id()
        types = sorted(
            kind.name for kind in GObject.type_children(Gtk.IMContext.__gtype__)
        )
        result["registered_context_types"] = types
        result["native_ime_type_registered"] = "GtkIMContextIME" in types
    finally:
        context.set_client_window(None)
    return result
