"""Tests for Treeland-specific services layered on generic Wayland support."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.platform.backends.base import ActionResult, Rect
from docking.platform.backends.wayland import treeland
from docking.platform.backends.wayland.treeland import (
    TreelandDesktopActionService,
    TreelandOverlapAdapter,
    TreelandVisibilityService,
    TreelandWindowManagementAdapter,
)
from docking.platform.backends.wayland.treeland_session import TreelandSessionBackend


def _layer_shell() -> SimpleNamespace:
    return SimpleNamespace(
        Edge=SimpleNamespace(TOP=1, BOTTOM=2, LEFT=4, RIGHT=8),
        Layer=SimpleNamespace(TOP=1),
        KeyboardMode=SimpleNamespace(NONE=0),
        init_for_window=MagicMock(),
        set_namespace=MagicMock(),
        set_layer=MagicMock(),
        set_keyboard_mode=MagicMock(),
        set_anchor=MagicMock(),
        set_margin=MagicMock(),
        set_monitor=MagicMock(),
        set_size=MagicMock(),
        set_exclusive_zone=MagicMock(),
    )


def _runtime() -> SimpleNamespace:
    overlap = TreelandOverlapAdapter()
    overlap.available = True
    management = TreelandWindowManagementAdapter()
    management.available = True
    return SimpleNamespace(
        foreign_toplevel_protocol=None,
        workspace_protocol=None,
        preview_protocol=None,
        hyprland_preview_protocol=None,
        idle_protocol=None,
        treeland_overlap_protocol=overlap,
        treeland_window_management_protocol=management,
        stop=MagicMock(),
    )


def test_treeland_session_decorates_generic_wayland_services():
    backend = TreelandSessionBackend(
        layer_shell=_layer_shell(),
        model=MagicMock(),
        launcher=MagicMock(),
        protocol_runtime=_runtime(),
    )

    assert backend.name == "treeland"
    assert isinstance(backend.visibility, TreelandVisibilityService)
    assert isinstance(backend.desktop_actions, TreelandDesktopActionService)
    assert backend.capabilities.supports_overlap_any is True
    assert backend.capabilities.supports_overlap_active is False
    assert backend.capabilities.supports_show_desktop is True
    assert backend.capabilities.tracks_window_geometry is False


def test_treeland_show_desktop_tracks_and_toggles_protocol_state():
    manager = SimpleNamespace(set_desktop=MagicMock())
    adapter = TreelandWindowManagementAdapter()
    adapter._manager = manager
    adapter.available = True
    flush = MagicMock()
    adapter.set_flush_callback(flush)
    service = TreelandDesktopActionService(adapter=adapter)

    assert service.show_desktop() is ActionResult.OK
    manager.set_desktop.assert_called_once_with(1)
    flush.assert_called_once_with()

    adapter._on_show_desktop(manager, 1)
    assert service.show_desktop() is ActionResult.OK
    manager.set_desktop.assert_called_with(0)


def test_treeland_overlap_uses_output_and_bottom_anchor():
    checker = SimpleNamespace(update=MagicMock(), dispatcher={})
    output_proxy = object()
    adapter = TreelandOverlapAdapter()
    adapter._checker = checker
    adapter._outputs.append(
        treeland._OutputState(
            registry_name=1,
            proxy=output_proxy,
            x=0,
            y=0,
            width=1920,
            height=1080,
            scale=1,
        )
    )
    adapter.start(
        get_dock_rect=lambda: Rect(x=700, y=1016, width=520, height=64),
        on_change=MagicMock(),
    )

    checker.update.assert_called_with(520, 64, 2, output_proxy)


def test_treeland_overlap_avoids_broken_left_anchor():
    checker = SimpleNamespace(update=MagicMock(), dispatcher={})
    adapter = TreelandOverlapAdapter()
    adapter._checker = checker
    adapter._outputs.append(
        treeland._OutputState(
            registry_name=1,
            proxy=object(),
            width=1920,
            height=1080,
        )
    )
    adapter.start(
        get_dock_rect=lambda: Rect(x=0, y=300, width=64, height=480),
        on_change=MagicMock(),
    )

    checker.update.assert_not_called()


def test_treeland_output_transform_and_hotplug_are_tracked():
    proxy = SimpleNamespace(release=MagicMock(), dispatcher={})
    registry = SimpleNamespace(bind=MagicMock(return_value=proxy))
    adapter = TreelandOverlapAdapter()

    adapter.bind_output(registry=registry, name=7, version=4)
    state = adapter._outputs[0]
    proxy.dispatcher["geometry"](
        proxy,
        1920,
        0,
        300,
        500,
        0,
        "vendor",
        "model",
        1,
    )
    proxy.dispatcher["mode"](proxy, 1, 1080, 1920, 60_000)
    proxy.dispatcher["scale"](proxy, 2)

    assert state.logical_width == 960
    assert state.logical_height == 540

    adapter.unbind_output(7)
    proxy.release.assert_called_once_with()
    assert adapter._outputs == []
