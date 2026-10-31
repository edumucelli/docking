"""Transparent Windows edge reveal uses the same input geometry as the dock."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.platform.backends.base import Rect
from docking.platform.backends.windows.geometry import ScreenMapping
from docking.platform.backends.windows.surface import WindowsSurfaceService


@pytest.mark.parametrize(
    ("physical", "inside"),
    [((-1280, 1500), True), ((-1280, 1498), False), ((-2000, 1500), False)],
)
def test_hidden_edge_uses_scaled_negative_monitor_geometry_without_focus(
    physical, inside
):
    service = WindowsSurfaceService()
    service._mapping = ScreenMapping(
        Rect(0, 0, 1280, 800), Rect(-2560, 0, 2560, 1600), Rect(-2560, 0, 2560, 1504), 2
    )
    service._placement = SimpleNamespace(x=0, y=565)
    service.update_input_region(Rect(576, 185, 128, 2))
    service._appbar = MagicMock()
    service._appbar.cursor_position.return_value = physical
    window = service._window = SimpleNamespace(
        dock_hovered=False, interaction=MagicMock(), present=MagicMock()
    )
    assert service._poll_pointer()
    assert window.interaction.on_effective_enter.call_count == int(inside)
    window.interaction.on_effective_leave.assert_not_called()
    window.present.assert_not_called()
    if inside:
        assert (window.cursor_x, window.cursor_y) == (640, 185)


def test_pointer_fallback_releases_hover_after_reveal_and_stop_removes_its_source(
    monkeypatch,
):
    from docking.platform.backends.windows import surface as module

    removed = []
    monkeypatch.setattr(module.GLib, "timeout_add", lambda *_args: 100)
    monkeypatch.setattr(module.GLib, "source_remove", removed.append)
    service = WindowsSurfaceService()
    service._mapping = ScreenMapping(
        Rect(0, 0, 1280, 800), Rect(0, 0, 1280, 800), Rect(0, 0, 1280, 752), 1
    )
    service._placement = SimpleNamespace(x=0, y=565)
    service.update_input_region(Rect(576, 134, 128, 53))
    service._appbar = native = MagicMock()
    native.cursor_position.return_value = (900, 400)
    window = service._window = SimpleNamespace(
        dock_hovered=True, interaction=MagicMock(), drawing_area=object()
    )
    service.update_pointer_barrier(monitor=None, position="bottom", enabled=True)
    service._poll_pointer()
    window.interaction.on_effective_leave.assert_called_once_with(window.drawing_area)
    service.stop()
    assert removed == [100]
    native.close.assert_called_once_with()
    assert service._pointer_source == 0


def test_surface_delegates_scope_only_after_native_window_exists(monkeypatch):
    from unittest.mock import Mock

    from docking.platform.backends.windows import ffi

    workspaces = Mock(supported=True)
    service = WindowsSurfaceService(workspaces=workspaces)
    handle = Mock(return_value=0x100000001)
    monkeypatch.setattr(ffi, "window_handle", handle)
    service.set_workspace_scope(current_workspace_only=False)
    handle.assert_not_called()
    parent = Mock()
    service._window = parent
    parent.get_window.return_value = None
    service.set_workspace_scope(current_workspace_only=False)
    handle.assert_not_called()
    parent.get_window.return_value = object()
    service.set_workspace_scope(current_workspace_only=True)
    workspaces.set_surface_scope.assert_called_once_with(
        0x100000001, current_workspace_only=True
    )


def test_gdk_handle_boundary_borrows_capsule_and_keeps_pointer_width(monkeypatch):
    import ctypes
    from types import SimpleNamespace
    from unittest.mock import Mock

    from docking.platform.backends.windows import ffi

    python, library = Mock(), Mock()
    python.PyCapsule_GetPointer.return_value = 0x200000002
    library.gdk_win32_window_get_handle.return_value = 0x100000001
    monkeypatch.setattr(ctypes, "pythonapi", python)
    monkeypatch.setattr(ctypes, "CDLL", lambda name: library)
    capsule = object()
    assert ffi.window_handle(SimpleNamespace(__gpointer__=capsule)) == 0x100000001
    python.PyCapsule_GetPointer.assert_called_once_with(capsule, None)
    library.gdk_win32_window_get_handle.assert_called_once_with(0x200000002)
    assert library.gdk_win32_window_get_handle.restype is ctypes.c_void_p
