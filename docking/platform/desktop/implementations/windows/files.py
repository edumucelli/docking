"""Gio file construction preserving Windows drive and UNC authorities."""

from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio

from docking.core.paths import local_path_from_uri_or_path


def file_for_uri(uri: str) -> Gio.File:
    if uri.lower().startswith("file:"):
        path = local_path_from_uri_or_path(uri)
        if path is None:
            raise ValueError("Invalid Windows filesystem URI")
        return Gio.File.new_for_path(str(path))
    return Gio.File.new_for_uri(uri)


def file_for_path(path: str) -> Gio.File:
    return Gio.File.new_for_path(path)
