"""Tests for shared native Wayland service composition."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.platform.backends.base import PlatformCapabilities
from docking.platform.backends.wayland.composed_session import (
    ComposedWaylandSessionBackend,
    WaylandSessionServices,
)


class _Backend(ComposedWaylandSessionBackend):
    @property
    def name(self) -> str:
        return "test-wayland"

    @property
    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities()


def test_composed_session_starts_and_stops_services_in_reverse_order() -> None:
    events: list[str] = []

    def service(name: str) -> MagicMock:
        value = MagicMock()
        value.start.side_effect = lambda: events.append(f"start:{name}")
        value.stop.side_effect = lambda: events.append(f"stop:{name}")
        return value

    runtime = MagicMock()
    runtime.stop.side_effect = lambda: events.append("stop:runtime")
    services = WaylandSessionServices(
        windows=service("windows"),
        previews=service("previews"),
        surface=service("surface"),
        visibility=service("visibility"),
        workspaces=service("workspaces"),
        desktop_actions=service("desktop-actions"),
        screen_capture=service("screen-capture"),
        idle=service("idle"),
        window_picker=service("window-picker"),
        protocol_runtime=runtime,
    )
    backend = _Backend(services=services)

    backend.start()
    backend.stop()

    assert events == [
        "start:previews",
        "start:windows",
        "start:surface",
        "start:visibility",
        "start:workspaces",
        "start:desktop-actions",
        "start:screen-capture",
        "start:idle",
        "start:window-picker",
        "stop:window-picker",
        "stop:idle",
        "stop:screen-capture",
        "stop:desktop-actions",
        "stop:workspaces",
        "stop:visibility",
        "stop:surface",
        "stop:windows",
        "stop:previews",
        "stop:runtime",
    ]
