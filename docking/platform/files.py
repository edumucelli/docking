"""File construction facade preserving virtual URI semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from docking.platform.desktop.selection import desktop_services

if TYPE_CHECKING:
    from gi.repository import Gio


def file_for_uri(uri: str) -> Gio.File:
    return desktop_services().file_for_uri(uri)


def file_for_path(path: str) -> Gio.File:
    return desktop_services().file_for_path(path)
