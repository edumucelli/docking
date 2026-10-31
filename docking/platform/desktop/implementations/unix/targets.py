"""Unix default-handler activation, including Flatpak host files."""

from __future__ import annotations

import subprocess
from urllib.parse import urlparse

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.log import get_logger, with_context
from docking.platform.environment import flatpak, is_flatpak

log = with_context(get_logger(name="targets"))


def open_uri(uri: str) -> bool:
    target = uri
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
