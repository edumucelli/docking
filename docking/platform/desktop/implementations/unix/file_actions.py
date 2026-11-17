"""Unix terminal activation and the host desktop's application chooser."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import uuid4

import gi

gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

from docking.applets.worker import BackgroundWorker
from docking.core.paths import local_path_from_uri_or_path
from docking.log import get_logger
from docking.platform import commands
from docking.platform.desktop.base import OpenWithResult
from docking.platform.environment import flatpak, is_flatpak, is_x11_backend

log = get_logger("file_actions")
PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
OPEN_URI = "org.freedesktop.portal.OpenURI"
REQUEST = "org.freedesktop.portal.Request"


def open_in_terminal(path: Path) -> bool:
    argv = commands.build_terminal_argv()
    if argv is None:
        return False
    directory: str | None = str(path)
    if is_flatpak():
        host_argv = flatpak.host_command(argv, working_directory=directory)
        if host_argv is None:
            return False
        argv, directory = host_argv, None
    try:
        subprocess.Popen(
            argv,
            cwd=directory,
            shell=False,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log.warning("Could not open terminal: %s", exc)
        return False
    return True


def _parent_identifier(parent: object) -> str:
    window = cast(Gtk.Window, parent).get_window()
    if window is not None and is_x11_backend(display=window.get_display()):
        gi.require_version("GdkX11", "3.0")
        from gi.repository import GdkX11

        return f"x11:{cast(GdkX11.X11Window, window).get_xid():x}"
    # Layer-shell surfaces need not support exporting an xdg-foreign handle.
    return ""


class _PortalChooser:
    def __init__(
        self,
        uri: str,
        parent: object,
        on_result: Callable[[OpenWithResult], None],
    ) -> None:
        self._uri = uri
        self._parent = _parent_identifier(parent)
        self._on_result = on_result
        self._cancellable = Gio.Cancellable()
        self._bus: Gio.DBusConnection | None = None
        self._handle = ""
        self._subscription = 0
        self._done = False

    def start(self) -> None:
        BackgroundWorker().run(
            name="prepare-open-with-portal",
            fn=self._prepare,
            on_result=self._ready,
            on_error=self._failed,
        )

    def _prepare(self) -> tuple[Gio.DBusConnection, Gio.UnixFDList] | None:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, self._cancellable)
        version = bus.call_sync(
            PORTAL_NAME,
            PORTAL_PATH,
            "org.freedesktop.DBus.Properties",
            "Get",
            GLib.Variant("(ss)", (OPEN_URI, "version")),
            GLib.VariantType.new("(v)"),
            Gio.DBusCallFlags.NONE,
            1500,
            self._cancellable,
        )
        if version.unpack()[0] < 3:
            return None
        path = local_path_from_uri_or_path(self._uri)
        if path is None:
            raise ValueError("Invalid filesystem target")
        descriptors = Gio.UnixFDList.new()
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            descriptors.append(fd)
        finally:
            os.close(fd)
        return bus, descriptors

    @staticmethod
    def _close_descriptors(descriptors: Gio.UnixFDList) -> None:
        for fd in descriptors.steal_fds():
            os.close(fd)

    def _ready(
        self, prepared: tuple[Gio.DBusConnection, Gio.UnixFDList] | None
    ) -> None:
        if prepared is None:
            self._finish(OpenWithResult.UNSUPPORTED)
            return
        bus, descriptors = prepared
        if self._done:
            self._close_descriptors(descriptors)
            return
        self._bus = bus
        token = "docking_" + uuid4().hex
        unique_name = bus.get_unique_name()
        if unique_name is None:
            self._close_descriptors(descriptors)
            self._finish(OpenWithResult.FAILED)
            return
        sender = unique_name.removeprefix(":").replace(".", "_")
        self._handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        self._subscription = bus.signal_subscribe(
            PORTAL_NAME,
            REQUEST,
            "Response",
            self._handle,
            None,
            Gio.DBusSignalFlags.NONE,
            self._response,
        )

        def sent(connection: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
            try:
                reply, _fds = connection.call_with_unix_fd_list_finish(result)
                handle = reply.unpack()[0]
                if handle != self._handle and not self._done:
                    self._finish(OpenWithResult.FAILED)
            except GLib.Error as exc:
                self._failed(exc)
            finally:
                self._close_descriptors(descriptors)

        bus.call_with_unix_fd_list(
            PORTAL_NAME,
            PORTAL_PATH,
            OPEN_URI,
            "OpenFile",
            GLib.Variant(
                "(sha{sv})",
                (
                    self._parent,
                    0,
                    {
                        "handle_token": GLib.Variant("s", token),
                        "ask": GLib.Variant("b", True),
                        "writable": GLib.Variant("b", True),
                    },
                ),
            ),
            GLib.VariantType.new("(o)"),
            Gio.DBusCallFlags.NONE,
            5000,
            descriptors,
            self._cancellable,
            sent,
        )

    def _response(
        self,
        _bus: Gio.DBusConnection,
        _sender: str | None,
        _path: str,
        _interface: str,
        _signal: str,
        parameters: GLib.Variant,
    ) -> None:
        response, _results = parameters.unpack()
        self._finish(
            OpenWithResult.ACCEPTED
            if response == 0
            else OpenWithResult.CANCELLED
            if response == 1
            else OpenWithResult.FAILED
        )

    def _failed(self, error: Exception) -> None:
        if not self._done:
            log.warning("Application chooser unavailable: %s", error)
            self._finish(OpenWithResult.FAILED)

    def _finish(self, result: OpenWithResult) -> None:
        if self._done:
            return
        self._done = True
        if self._bus is not None and self._subscription:
            self._bus.signal_unsubscribe(self._subscription)
            self._subscription = 0
        self._on_result(result)

    def cancel(self) -> None:
        if self._done:
            return
        if self._bus is not None and self._handle:
            self._bus.call(
                PORTAL_NAME,
                self._handle,
                REQUEST,
                "Close",
                None,
                None,
                Gio.DBusCallFlags.NONE,
                1500,
                None,
                None,
            )
        self._cancellable.cancel()
        self._finish(OpenWithResult.CANCELLED)


def request_open_with(
    uri: str,
    *,
    parent: object,
    on_result: Callable[[OpenWithResult], None],
) -> Callable[[], None]:
    chooser = _PortalChooser(uri, parent, on_result)
    chooser.start()
    return chooser.cancel
