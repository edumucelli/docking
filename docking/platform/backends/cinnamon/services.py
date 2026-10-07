"""Cinnamon workspace, overlap, preview and desktop-action services."""

from __future__ import annotations

import base64
import binascii
import os
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib

from docking.core.config import HideMode
from docking.log import get_logger
from docking.platform.backends.base import (
    ActionResult,
    DesktopActionService,
    PreviewImage,
    PreviewService,
    Rect,
    VisibilityMonitor,
    VisibilityService,
    WindowId,
    WorkspaceService,
    WorkspaceSnapshot,
)

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.backends.cinnamon.shell import CinnamonShellClient
    from docking.platform.backends.cinnamon.windows import CinnamonWindowService

log = get_logger(name="backend.cinnamon.previews")


class CinnamonWorkspaceService(WorkspaceService):
    def __init__(self, *, windows: CinnamonWindowService) -> None:
        self._windows = windows
        self._watchers: dict[object, Callable[[], None]] = {}
        self._handle: object | None = None
        self._last: tuple[WorkspaceSnapshot, ...] = ()

    def start(self) -> None:
        if self._handle is None:
            self._handle = self._windows.watch(self._changed)

    def stop(self) -> None:
        if self._handle is not None:
            self._windows.unwatch(self._handle)
            self._handle = None
        self._watchers.clear()
        self._last = ()

    def list_workspaces(self) -> Sequence[WorkspaceSnapshot]:
        return self._windows.shell.workspace_snapshots

    def active_workspace(self) -> WorkspaceSnapshot | None:
        return next((w for w in self.list_workspaces() if w.active), None)

    def activate(self, workspace_id: str) -> ActionResult:
        self._windows.refresh()
        if self._windows.shell.last_query_failed:
            return ActionResult.FAILED
        if not any(w.id == workspace_id for w in self.list_workspaces()):
            return ActionResult.NOT_FOUND
        result = self._windows.shell.activate_workspace(workspace_id)
        self._windows.refresh()
        return result

    def watch_active_workspace(self, on_change: Callable[[], None]) -> object:
        handle = object()
        self._watchers[handle] = on_change
        return handle

    def unwatch_active_workspace(self, handle: object) -> None:
        self._watchers.pop(handle, None)

    def _changed(self) -> None:
        current = tuple(self.list_workspaces())
        if current != self._last:
            self._last = current
            for callback in tuple(self._watchers.values()):
                callback()


class CinnamonVisibilityMonitor(VisibilityMonitor):
    def __init__(
        self,
        *,
        windows: CinnamonWindowService,
        config: Config | None,
        get_dock_rect: Callable[[], Rect | None],
        on_change: Callable[[bool], None],
    ) -> None:
        self._windows = windows
        self._config = config
        self._get_rect = get_dock_rect
        self._on_change = on_change
        self._handle: object | None = None
        self._hidden = False

    def start(self) -> None:
        if self._handle is None:
            self._handle = self._windows.watch(self.evaluate_now)
            self.evaluate_now()

    def stop(self) -> None:
        if self._handle is not None:
            self._windows.unwatch(self._handle)
            self._handle = None

    def evaluate_now(self) -> None:
        shell = self._windows.shell
        if shell.last_query_failed:
            return
        rect = self._get_rect()
        mode = self._config.hide_mode_enum if self._config else HideMode.NONE
        active_workspace = next(
            (w.id for w in shell.workspace_snapshots if w.active), None
        )
        candidates = [
            row
            for row in shell.rows
            if not row.get("desktop-or-dock")
            and not row.get("minimized")
            and row.get("visible", True)
            and row.get("pid") != os.getpid()
            and (
                row.get("sticky")
                or active_workspace is None
                or str(row.get("workspace")) == active_workspace
            )
        ]
        focused = next((r for r in candidates if r.get("focused")), None)

        def same_app(row) -> bool:
            if not focused:
                return False
            return bool(
                (focused.get("pid", 0) > 0 and row.get("pid") == focused.get("pid"))
                or (
                    focused.get("wm-class")
                    and row.get("wm-class") == focused.get("wm-class")
                )
            )

        def overlaps(row) -> bool:
            geometry = row.get("frame-rect")
            return bool(
                rect
                and isinstance(geometry, list)
                and len(geometry) == 4
                and all(type(v) is int for v in geometry)
                and geometry[2] > 0
                and geometry[3] > 0
                and rect.overlaps(Rect(*geometry))
            )

        hidden = False
        if mode is HideMode.DODGE_ACTIVE:
            hidden = bool(focused and overlaps(focused))
        elif mode is HideMode.WINDOW_DODGE:
            hidden = any(overlaps(r) for r in candidates)
        elif mode is HideMode.INTELLIGENT:
            hidden = any(same_app(r) and overlaps(r) for r in candidates)
        elif mode is HideMode.DODGE_MAXIMIZED:
            hidden = bool(
                focused
                and (focused.get("maximized") or focused.get("fullscreen"))
                and overlaps(focused)
            ) or any(
                r.get("dialog") and same_app(r) and overlaps(r) for r in candidates
            )
        if hidden != self._hidden:
            self._hidden = hidden
            self._on_change(hidden)


class CinnamonVisibilityService(VisibilityService):
    def __init__(
        self, *, windows: CinnamonWindowService, config: Config | None
    ) -> None:
        self._windows = windows
        self._config = config
        self._monitors: list[CinnamonVisibilityMonitor] = []

    def start(self) -> None:
        """Monitors own their subscriptions to the shared snapshot."""

    def stop(self) -> None:
        for monitor in self._monitors:
            monitor.stop()
        self._monitors.clear()

    def create_monitor(self, *, get_dock_rect, on_change) -> CinnamonVisibilityMonitor:
        monitor = CinnamonVisibilityMonitor(
            windows=self._windows,
            config=self._config,
            get_dock_rect=get_dock_rect,
            on_change=on_change,
        )
        self._monitors.append(monitor)
        return monitor


class CinnamonPreviewService(PreviewService):
    """Bounded, on-demand actor captures; never capture in the state poll."""

    def __init__(self, *, client: CinnamonShellClient) -> None:
        self._client = client
        self._cache: OrderedDict[
            tuple[WindowId, int, int], tuple[float, PreviewImage]
        ] = OrderedDict()

    def start(self) -> None:
        """No persistent shell resources."""

    def stop(self) -> None:
        self._cache.clear()

    def capture(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        if not 0 < width <= 1024 or not 0 < height <= 1024:
            return None
        key = (window_id, width, height)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < 0.15:
            return cached[1]
        encoded = self._client.for_window(
            window_id,
            """
            const Cairo = imports.cairo, GLib = imports.gi.GLib;
            const actor = w.get_compositor_private();
            const source = actor?.get_image?.(null);
            if (!source || source.getWidth() <= 0 || source.getHeight() <= 0)
                return null;
            const width = __WIDTH__, height = __HEIGHT__;
            const target = new Cairo.ImageSurface(Cairo.Format.ARGB32, width, height);
            const cr = new Cairo.Context(target);
            const scale = Math.min(width/source.getWidth(), height/source.getHeight());
            cr.translate((width-source.getWidth()*scale)/2,
                         (height-source.getHeight()*scale)/2);
            cr.scale(scale, scale); cr.setSourceSurface(source, 0, 0); cr.paint();
            cr.$dispose();
            const [fd, path] = GLib.file_open_tmp('docking-preview-XXXXXX');
            GLib.close(fd);
            try {
                target.writeToPNG(path);
                const [ok, bytes] = GLib.file_get_contents(path);
                return ok && bytes.length <= 4194304 ? GLib.base64_encode(bytes) : null;
            } finally { GLib.unlink(path); }
        """.replace("__WIDTH__", str(width)).replace("__HEIGHT__", str(height)),
        )
        if not isinstance(encoded, str) or len(encoded) > 5592408:
            self._cache.pop(key, None)
            return None
        try:
            data = base64.b64decode(encoded, validate=True)
            loader = GdkPixbuf.PixbufLoader.new_with_type("png")
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
            if (
                pixbuf is None
                or pixbuf.get_width() != width
                or pixbuf.get_height() != height
            ):
                return None
            image = PreviewImage(image=pixbuf, width=width, height=height)
        except (GLib.Error, ValueError, binascii.Error) as exc:
            log.debug("Preview decode failed: %s", exc)
            return None
        self._cache[key] = (time.monotonic(), image)
        self._cache.move_to_end(key)
        while len(self._cache) > 8:
            self._cache.popitem(last=False)
        return image

    def thumbnail(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        return self.capture(window_id, width=width, height=height)


class CinnamonDesktopActionService(DesktopActionService):
    def __init__(self, *, client: CinnamonShellClient) -> None:
        self._client = client

    def start(self) -> None:
        """No persistent state."""

    def stop(self) -> None:
        """No persistent state."""

    def show_desktop(self, show: bool | None = None) -> ActionResult:
        return self._client.show_desktop(show)
