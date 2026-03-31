"""Shared pytest setup for the repository test suite."""

from __future__ import annotations

from contextlib import suppress

try:
    import gi
except ModuleNotFoundError:  # pragma: no cover - no-GI smoke jobs
    gi = None

if gi is not None:  # pragma: no branch - tiny startup guard
    for namespace, version in (
        ("Gtk", "3.0"),
        ("Gdk", "3.0"),
        ("GdkPixbuf", "2.0"),
        ("Pango", "1.0"),
        ("Wnck", "3.0"),
    ):
        with suppress(ValueError):
            gi.require_version(namespace, version)
