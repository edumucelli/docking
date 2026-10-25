"""Adapt filesystem URIs to Gio without losing Windows UNC authority."""

from __future__ import annotations

from gi.repository import Gio

from docking.core.paths import local_path_from_uri_or_path
from docking.platform.environment import is_windows


def file_for_uri(uri: str) -> Gio.File:
    """Construct a Gio file, retaining native Windows drive and share paths.

    Gio's Win32 URI parser discards a standard file URL's server authority.
    Decode it first and use the path constructor; virtual URIs and the other
    platforms keep Gio's existing handling.
    """
    if is_windows() and uri.lower().startswith("file:"):
        path = local_path_from_uri_or_path(uri)
        if path is None:
            raise ValueError("Invalid Windows filesystem URI")
        return Gio.File.new_for_path(str(path))
    return Gio.File.new_for_uri(uri)
