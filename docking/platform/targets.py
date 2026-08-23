"""File and folder target normalization, metadata, and default-handler opening."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote, urlparse

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib

from docking.log import get_logger, with_context
from docking.platform.environment import flatpak, is_flatpak
from docking.platform.icons import IconLoader, fallback_file_icon_name

log = with_context(get_logger(name="targets"))


class FileTargetInfo(NamedTuple):
    """Resolved file or folder metadata for dock entries."""

    target: str
    name: str
    icon_name: str
    icon: GdkPixbuf.Pixbuf | None
    is_dir: bool


def normalize_file_target(target: str) -> str | None:
    """Normalize a local path or file URI into a canonical file URI."""
    if not target:
        return None
    try:
        parsed = urlparse(target)
    except ValueError as exc:
        log.bind(target=target, action="normalize_file_target").debug(
            "Failed to parse file target %s: %s",
            target,
            exc,
        )
        return None
    if parsed.scheme == "file":
        if not parsed.path:
            return None
        path = Path(unquote(parsed.path))
    elif parsed.scheme == "":
        path = Path(target).expanduser()
    else:
        return None
    try:
        return path.resolve().as_uri()
    except ValueError as exc:
        log.bind(target=target, action="normalize_file_target").debug(
            "Failed to normalize file target %s: %s",
            target,
            exc,
        )
        return None


def open_target(target: str) -> bool:
    """Open a local path or any formed URI with its default handler."""
    try:
        parsed = urlparse(target)
    except ValueError as exc:
        log.bind(target=target, action="open_target").debug(
            "Failed to parse target %s: %s",
            target,
            exc,
        )
        return False
    if parsed.scheme and parsed.scheme != "file":
        uri = target
    else:
        uri = normalize_file_target(target)
    if uri is None:
        return False
    if is_flatpak() and urlparse(uri).scheme == "file":
        # Local files may be host-visible but not sandbox-openable; ask the
        # host desktop's gio to choose the default application.
        host_cmd = flatpak.host_command(["gio", "open", uri])
        if host_cmd is not None:
            try:
                subprocess.Popen(
                    host_cmd,
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except OSError as exc:
                log.bind(target=target, action="open_target").warning(
                    "Failed to open host target %s: %s",
                    target,
                    exc,
                )
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
        return True
    except GLib.Error as exc:
        log.bind(target=target, action="open_target").warning(
            f"Failed to open target {target}: {exc}"
        )
        return False


def default_directory_app_name() -> str | None:
    """Return the display name of the default application for folders."""
    try:
        app_info = Gio.AppInfo.get_default_for_type("inode/directory", False)
    except GLib.Error as exc:
        log.bind(action="default_directory_app_name").warning(
            "Failed to resolve default directory app: %s",
            exc,
        )
        return None
    if app_info is None:
        return None
    return app_info.get_display_name() or None


class TargetService:
    """Resolve target metadata using one composed icon-loading service."""

    def __init__(self, *, icon_loader: IconLoader) -> None:
        self._icon_loader = icon_loader

    @property
    def icon_loader(self) -> IconLoader:
        """Return the icon loader shared by target metadata resolution."""
        return self._icon_loader

    def normalize_file_target(self, target: str) -> str | None:
        """Delegate target normalization to the canonical module function."""
        return normalize_file_target(target)

    def open_target(self, target: str) -> bool:
        """Delegate default-handler opening to the canonical module function."""
        return open_target(target)

    def resolve_file(self, target: str, size: int) -> FileTargetInfo | None:
        """Resolve a file URI or local path into display metadata."""
        uri = normalize_file_target(target)
        if uri is None:
            return None
        try:
            gfile = Gio.File.new_for_uri(uri)
            info = gfile.query_info(
                "standard::display-name,standard::icon,standard::type,standard::content-type",
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
        except GLib.Error as exc:
            log.bind(target=target, action="resolve_file").warning(
                f"Failed to query file info: {exc}"
            )
            return None

        gicon = info.get_icon()
        is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
        icon_name = fallback_file_icon_name(is_dir=is_dir)
        return FileTargetInfo(
            target=uri,
            name=info.get_display_name()
            or Path(unquote(urlparse(uri).path)).name
            or uri,
            icon_name=icon_name,
            icon=self.resolve_file_icon(
                target=uri,
                gicon=gicon,
                content_type=info.get_content_type() or "",
                size=size,
                is_dir=is_dir,
            ),
            is_dir=is_dir,
        )

    def resolve_file_icon(
        self,
        *,
        target: str,
        gicon: Gio.Icon | None,
        content_type: str,
        size: int,
        is_dir: bool,
    ) -> GdkPixbuf.Pixbuf | None:
        """Resolve a target icon through the composed icon loader."""
        return self._icon_loader.resolve_file_icon(
            target=target,
            gicon=gicon,
            content_type=content_type,
            size=size,
            is_dir=is_dir,
        )

    def default_directory_app_name(self) -> str | None:
        """Return the presentation name of the default folder handler."""
        return default_directory_app_name()


__all__ = [
    "FileTargetInfo",
    "TargetService",
    "default_directory_app_name",
    "normalize_file_target",
    "open_target",
]
