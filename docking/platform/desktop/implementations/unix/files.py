"""Gio file construction for Unix desktop URIs."""

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio


def file_for_uri(uri: str) -> Gio.File:
    return Gio.File.new_for_uri(uri)


def file_for_path(path: str) -> Gio.File:
    return Gio.File.new_for_path(path)
