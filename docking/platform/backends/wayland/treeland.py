"""Small adapters for Treeland features not covered by standard protocols."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from docking.platform.backends.base import (
    ActionResult,
    DesktopActionService,
    Rect,
    VisibilityMonitor,
    VisibilityService,
)

_OUTPUT_MODE_CURRENT = 1
_ANCHOR_TOP = 1
_ANCHOR_BOTTOM = 2
_ANCHOR_LEFT = 4
_ANCHOR_RIGHT = 8
_DESKTOP_NORMAL = 0
_DESKTOP_SHOW = 1


@dataclass
class _OutputState:
    registry_name: int
    proxy: object
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    scale: int = 1
    transform: int = 0

    @property
    def swaps_axes(self) -> bool:
        return self.transform in {1, 3, 5, 7}

    @property
    def logical_width(self) -> int:
        width = self.height if self.swaps_axes else self.width
        return width // max(1, self.scale)

    @property
    def logical_height(self) -> int:
        height = self.width if self.swaps_axes else self.height
        return height // max(1, self.scale)


class TreelandOverlapAdapter:
    """Translate Treeland overlap events into one dock visibility signal."""

    def __init__(self) -> None:
        self._manager = None
        self._checker = None
        self._flush: Callable[[], None] | None = None
        self._on_change: Callable[[bool], None] | None = None
        self._get_dock_rect: Callable[[], Rect | None] | None = None
        self._outputs: list[_OutputState] = []
        self.available = False

    def set_flush_callback(self, callback: Callable[[], None] | None) -> None:
        self._flush = callback

    def bind(self, *, registry, name: int, version: int) -> None:
        from docking.platform.backends.wayland.protocols.treeland_shell import (
            TreelandDDEShellManagerV1,
        )

        self._manager = registry.bind(
            name,
            TreelandDDEShellManagerV1,
            min(version, TreelandDDEShellManagerV1.version),
        )
        self.available = True

    def bind_output(self, *, registry, name: int, version: int) -> None:
        from pywayland.protocol.wayland import WlOutput

        proxy = registry.bind(name, WlOutput, min(version, WlOutput.version))
        state = _OutputState(registry_name=name, proxy=proxy)
        self._outputs.append(state)
        proxy.dispatcher["geometry"] = lambda _output, x, y, *details: (
            self._on_output_geometry(state, x, y, details[-1])
        )
        proxy.dispatcher["mode"] = lambda _output, flags, width, height, _refresh: (
            self._on_output_mode(state, flags, width, height)
        )
        proxy.dispatcher["scale"] = lambda _output, scale: self._on_output_scale(
            state, scale
        )

    def unbind_output(self, registry_name: int) -> None:
        for output in tuple(self._outputs):
            if output.registry_name != registry_name:
                continue
            release = getattr(output.proxy, "release", None)
            if callable(release):
                release()
            self._outputs.remove(output)

    def start(
        self,
        *,
        get_dock_rect: Callable[[], Rect | None],
        on_change: Callable[[bool], None],
    ) -> None:
        self._get_dock_rect = get_dock_rect
        self._on_change = on_change
        self._ensure_checker()
        self.evaluate_now()

    def stop_monitoring(self) -> None:
        self._get_dock_rect = None
        self._on_change = None

    def evaluate_now(self) -> None:
        if self._checker is None or self._get_dock_rect is None:
            return
        rect = self._get_dock_rect()
        if rect is None:
            return
        output = self._output_for(rect)
        if output is None:
            return
        anchor = self._nearest_anchor(rect, output)
        # Treeland 4e22194 constructs the left checker rectangle like a top
        # rectangle. Avoid false overlap events until that compositor bug is fixed.
        if anchor == _ANCHOR_LEFT:
            return
        self._checker.update(rect.width, rect.height, anchor, output.proxy)
        if self._flush is not None:
            self._flush()

    def stop(self) -> None:
        checker = self._checker
        if checker is not None:
            destroy = getattr(checker, "destroy", None)
            if callable(destroy):
                destroy()
        for output in self._outputs:
            release = getattr(output.proxy, "release", None)
            if callable(release):
                release()
        self._manager = None
        self._checker = None
        self._flush = None
        self._on_change = None
        self._get_dock_rect = None
        self._outputs.clear()
        self.available = False

    def _ensure_checker(self) -> None:
        if self._checker is not None or self._manager is None:
            return
        self._checker = self._manager.get_window_overlap_checker()
        self._checker.dispatcher["enter"] = lambda _checker: self._publish(True)
        self._checker.dispatcher["leave"] = lambda _checker: self._publish(False)

    def _publish(self, overlapped: bool) -> None:
        if self._on_change is not None:
            self._on_change(overlapped)

    @staticmethod
    def _on_output_geometry(
        state: _OutputState,
        x: int,
        y: int,
        transform: int,
    ) -> None:
        state.x = int(x)
        state.y = int(y)
        state.transform = int(transform)

    @staticmethod
    def _on_output_mode(
        state: _OutputState, flags: int, width: int, height: int
    ) -> None:
        if int(flags) & _OUTPUT_MODE_CURRENT:
            state.width = int(width)
            state.height = int(height)

    @staticmethod
    def _on_output_scale(state: _OutputState, scale: int) -> None:
        state.scale = max(1, int(scale))

    def _output_for(self, rect: Rect) -> _OutputState | None:
        center_x = rect.x + rect.width // 2
        center_y = rect.y + rect.height // 2
        for output in self._outputs:
            if (
                output.x <= center_x < output.x + output.logical_width
                and output.y <= center_y < output.y + output.logical_height
            ):
                return output
        return self._outputs[0] if len(self._outputs) == 1 else None

    @staticmethod
    def _nearest_anchor(rect: Rect, output: _OutputState) -> int:
        distances = (
            (abs(rect.y - output.y), _ANCHOR_TOP),
            (
                abs(rect.bottom - (output.y + output.logical_height)),
                _ANCHOR_BOTTOM,
            ),
            (abs(rect.x - output.x), _ANCHOR_LEFT),
            (
                abs(rect.right - (output.x + output.logical_width)),
                _ANCHOR_RIGHT,
            ),
        )
        return min(distances, key=lambda item: item[0])[1]


class TreelandWindowManagementAdapter:
    """Track and change Treeland's compositor-wide Show Desktop state."""

    def __init__(self) -> None:
        self._manager = None
        self._flush: Callable[[], None] | None = None
        self.showing_desktop = False
        self.available = False

    def set_flush_callback(self, callback: Callable[[], None] | None) -> None:
        self._flush = callback

    def bind(self, *, registry, name: int, version: int) -> None:
        from docking.platform.backends.wayland.protocols.treeland_shell import (
            TreelandWindowManagementV1,
        )

        self._manager = registry.bind(
            name,
            TreelandWindowManagementV1,
            min(version, TreelandWindowManagementV1.version),
        )
        self._manager.dispatcher["show_desktop"] = self._on_show_desktop
        self.available = True

    def set_show_desktop(self, show: bool) -> bool:
        if self._manager is None:
            return False
        self._manager.set_desktop(_DESKTOP_SHOW if show else _DESKTOP_NORMAL)
        if self._flush is not None:
            self._flush()
        return True

    def stop(self) -> None:
        self._manager = None
        self._flush = None
        self.showing_desktop = False
        self.available = False

    def _on_show_desktop(self, _manager: object, state: int) -> None:
        self.showing_desktop = int(state) != _DESKTOP_NORMAL


class TreelandVisibilityService(VisibilityService):
    def __init__(self, *, adapter: TreelandOverlapAdapter) -> None:
        self._adapter = adapter
        self._monitors: list[TreelandVisibilityMonitor] = []

    def start(self) -> None:
        """Monitors subscribe individually after the dock is constructed."""

    def stop(self) -> None:
        for monitor in tuple(self._monitors):
            monitor.stop()
        self._monitors.clear()

    def create_monitor(
        self,
        *,
        get_dock_rect: Callable[[], Rect | None],
        on_change: Callable[[bool], None],
    ) -> VisibilityMonitor:
        monitor = TreelandVisibilityMonitor(
            adapter=self._adapter,
            get_dock_rect=get_dock_rect,
            on_change=on_change,
        )
        self._monitors.append(monitor)
        return monitor


class TreelandVisibilityMonitor(VisibilityMonitor):
    def __init__(
        self,
        *,
        adapter: TreelandOverlapAdapter,
        get_dock_rect: Callable[[], Rect | None],
        on_change: Callable[[bool], None],
    ) -> None:
        self._adapter = adapter
        self._get_dock_rect = get_dock_rect
        self._on_change = on_change
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._adapter.start(
            get_dock_rect=self._get_dock_rect,
            on_change=self._on_change,
        )

    def stop(self) -> None:
        if self._started:
            self._adapter.stop_monitoring()
        self._started = False

    def evaluate_now(self) -> None:
        self._adapter.evaluate_now()


class TreelandDesktopActionService(DesktopActionService):
    def __init__(self, *, adapter: TreelandWindowManagementAdapter) -> None:
        self._adapter = adapter

    def start(self) -> None:
        """The protocol runtime already receives state events."""

    def stop(self) -> None:
        """The protocol runtime owns the manager resource."""

    def show_desktop(self, show: bool | None = None) -> ActionResult:
        requested = not self._adapter.showing_desktop if show is None else bool(show)
        return (
            ActionResult.OK
            if self._adapter.set_show_desktop(requested)
            else ActionResult.UNSUPPORTED
        )
