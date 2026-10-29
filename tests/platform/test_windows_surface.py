"""Windows owns its surface lifecycle while reusing common GTK operations."""

from unittest.mock import Mock, call

import pytest

from docking.core.position import Position
from docking.platform.backends.base import MonitorSnapshot, PlacementRequest, Rect, Size
from docking.platform.backends.reduced.services import ReducedSurfaceService
from docking.platform.backends.windows.surface import WindowsSurfaceService


def test_fullscreen_lowers_the_native_dock_and_exit_restores_topmost():
    service = WindowsSurfaceService()
    service._window = window = Mock()

    service._fullscreen_changed(True)
    assert window.mock_calls == [
        call.set_keep_above(False),
        call.get_window(),
        call.get_window().lower(),
    ]

    window.reset_mock()
    service._fullscreen_changed(False)
    window.set_keep_above.assert_called_once_with(True)
    window.get_window.assert_not_called()
    window.present.assert_not_called()


def test_fullscreen_notification_before_realization_has_no_native_window():
    service = WindowsSurfaceService()
    service._window = window = Mock()
    window.get_window.return_value = None
    service._fullscreen_changed(True)
    window.set_keep_above.assert_called_once_with(False)


def test_initial_fullscreen_is_checked_after_the_mapped_window_is_placed():
    service = WindowsSurfaceService()
    service._window = window = Mock()
    service._appbar = appbar = Mock()
    service._mapping = mapping = Mock()
    service._reservation = Mock()
    service._placement = PlacementRequest(
        monitor=MonitorSnapshot(index=0, geometry=Rect(0, 0, 1280, 800)),
        position=Position.BOTTOM,
        x=0,
        y=565,
        size=Size(1280, 187),
    )
    events = []
    window.get_window.return_value.move_resize.side_effect = lambda *args: (
        events.append("place")
    )

    def fullscreen(_mapping):
        events.append("fullscreen")
        return False

    appbar.fullscreen_active.side_effect = fullscreen
    assert service._restore_placement() is False
    assert events == ["place", "fullscreen"]
    appbar.fullscreen_active.assert_called_once_with(mapping)
    service.stop()


def test_unregistered_modes_do_not_inherit_appbar_fullscreen_stacking():
    service = WindowsSurfaceService()
    service._window = Mock()
    service._appbar = appbar = Mock()
    service._mapping = Mock()
    service._placement = PlacementRequest(
        monitor=MonitorSnapshot(index=0, geometry=Rect(0, 0, 1280, 800)),
        position=Position.BOTTOM,
        x=0,
        y=565,
        size=Size(1280, 187),
    )
    assert service._restore_placement() is False
    appbar.fullscreen_active.assert_not_called()


@pytest.fixture
def fullscreen_poll(monkeypatch):
    from docking.platform.backends.windows import surface

    added = Mock(return_value=9)
    removed = Mock()
    monkeypatch.setattr(surface.GLib, "timeout_add", added)
    monkeypatch.setattr(surface.GLib, "source_remove", removed)
    service = WindowsSurfaceService()
    service._window = Mock()
    service._appbar = Mock()
    service._mapping = Mock()
    service._fullscreen_changed(True)
    return service, added, removed


def test_fullscreen_owns_one_exit_observer_without_activating_the_dock(fullscreen_poll):
    service, added, _removed = fullscreen_poll
    service._fullscreen_changed(True)
    added.assert_called_once_with(250, service._poll_fullscreen)
    service._window.present.assert_not_called()
    assert service._fullscreen_source == 9


@pytest.mark.parametrize("active", [True, None])
def test_fullscreen_observer_retains_lowering_on_active_or_unknown_data(
    fullscreen_poll, active
):
    service, _added, removed = fullscreen_poll
    service._window.reset_mock()
    service._appbar.fullscreen_active.return_value = active
    assert service._poll_fullscreen() is True
    service._window.set_keep_above.assert_not_called()
    removed.assert_not_called()
    assert service._fullscreen_source == 9


def test_fullscreen_exit_without_a_shell_notification_restores_and_stops(
    fullscreen_poll,
):
    service, _added, removed = fullscreen_poll
    service._window.reset_mock()
    service._appbar.fullscreen_active.return_value = False
    assert service._poll_fullscreen() is False
    service._window.set_keep_above.assert_called_once_with(True)
    removed.assert_called_once_with(9)
    assert service._fullscreen_source == 0


def test_reentrant_fullscreen_notification_keeps_its_new_exit_observer(fullscreen_poll):
    service, added, removed = fullscreen_poll
    added.return_value = 10
    service._appbar.fullscreen_active.return_value = False

    def changing_z_order(above):
        if above:
            service._fullscreen_changed(True)

    service._window.set_keep_above.side_effect = changing_z_order
    assert service._poll_fullscreen() is False
    removed.assert_called_once_with(9)
    assert service._fullscreen_source == 10
    assert added.call_count == 2


def test_surface_stop_removes_its_fullscreen_observer(fullscreen_poll):
    service, _added, removed = fullscreen_poll
    service.stop()
    removed.assert_called_once_with(9)
    assert service._fullscreen_source == 0
    assert service._poll_fullscreen() is False


def test_removing_reservation_releases_fullscreen_stacking(fullscreen_poll):
    service, _added, removed = fullscreen_poll
    appbar = service._appbar
    service._window.reset_mock()
    service.clear_reservation()
    removed.assert_called_once_with(9)
    assert service._fullscreen_source == 0
    assert service._reservation is None
    service._window.set_keep_above.assert_called_once_with(True)
    appbar.clear.assert_called_once_with()


def test_windows_surface_preserves_gtk_hints_and_placement_without_reduced_services(
    monkeypatch,
):
    forbidden = Mock(side_effect=AssertionError("Reduced surface must be independent"))
    monkeypatch.setattr(ReducedSurfaceService, "__init__", forbidden)
    service = WindowsSurfaceService()
    window = Mock()
    placement = PlacementRequest(
        monitor=MonitorSnapshot(index=0, geometry=Rect(0, 0, 1280, 800)),
        position=Position.BOTTOM,
        x=10,
        y=20,
        size=Size(300, 40),
    )
    service.start()
    service.position_or_anchor(placement)
    window.move.assert_not_called()
    service.configure_before_realize(window)
    # Native resources are tested separately; this check exercises the GTK
    # lifecycle on every host without loading Windows DLLs.
    service._appbar = appbar = Mock()
    service._menus = menus = Mock()
    service.on_realize(window)
    assert window.set_skip_taskbar_hint.call_args_list == [call(True), call(True)]
    assert window.set_skip_pager_hint.call_args_list == [call(True), call(True)]
    assert window.set_accept_focus.call_args_list == [call(False), call(False)]
    assert window.set_focus_on_map.call_args_list == [call(False), call(False)]
    assert window.stick.call_count == 2
    assert window.set_keep_above.call_args_list == [call(True), call(True)]
    assert window.set_type_hint.call_count == 2
    menus.bind_parent.assert_called_once_with(window.get_window.return_value)
    service.position_or_anchor(placement)
    window.set_size_request.assert_called_once_with(300, 40)
    window.resize.assert_called_once_with(300, 40)
    window.move.assert_called_once_with(10, 20)
    service.set_blur_region(None)
    service.stop()
    appbar.close.assert_called_once_with()
    menus.stop.assert_called_once_with()
    forbidden.assert_not_called()


@pytest.fixture
def topmost_poll(monkeypatch):
    from docking.platform.backends.windows import surface

    added, removed = Mock(return_value=11), Mock()
    monkeypatch.setattr(surface.GLib, "timeout_add", added)
    monkeypatch.setattr(surface.GLib, "source_remove", removed)
    service = WindowsSurfaceService()
    service._window = Mock()
    service._appbar = Mock()
    service._mapping = Mock()
    service._reservation = Mock()
    service._fullscreen_changed(False)
    return service, added, removed


def test_restoration_confirms_native_stacking_before_stopping(topmost_poll):
    service, added, removed = topmost_poll
    service._appbar.fullscreen_active.return_value = False
    service._appbar.ensure_keep_above.side_effect = [False, True]
    assert service._poll_topmost() is True
    assert service._topmost_source == 11
    assert service._poll_topmost() is False
    assert service._topmost_source == 0
    added.assert_called_once_with(250, service._poll_topmost)
    removed.assert_not_called()
    service._window.present.assert_not_called()


def test_repeated_exit_notifications_share_one_native_restoration_observer(
    topmost_poll,
):
    service, added, _removed = topmost_poll
    service._fullscreen_changed(False)
    added.assert_called_once_with(250, service._poll_topmost)
    assert service._topmost_source == 11


def test_unknown_foreground_keeps_restoration_pending_without_writing(topmost_poll):
    service, _added, _removed = topmost_poll
    service._window.reset_mock()
    service._appbar.fullscreen_active.return_value = None
    assert service._poll_topmost() is True
    service._appbar.ensure_keep_above.assert_not_called()
    service._window.set_keep_above.assert_not_called()


def test_new_fullscreen_cancels_native_restoration_before_lowering(topmost_poll):
    service, added, removed = topmost_poll
    added.return_value = 22
    service._appbar.fullscreen_active.return_value = True
    assert service._poll_topmost() is False
    removed.assert_called_once_with(11)
    assert service._topmost_source == 0 and service._fullscreen_source == 22
    service._window.set_keep_above.assert_called_with(False)
    service._window.get_window.return_value.lower.assert_called_once_with()
    service._appbar.ensure_keep_above.assert_not_called()


def test_reentrant_fullscreen_cannot_be_raised_by_pending_native_restoration(
    topmost_poll,
):
    service, added, removed = topmost_poll
    added.return_value = 22
    service._appbar.fullscreen_active.return_value = False

    def changing_z_order(above):
        if above:
            service._fullscreen_changed(True)

    service._window.set_keep_above.side_effect = changing_z_order
    assert service._poll_topmost() is False
    assert service._topmost_source == 0 and service._fullscreen_source == 22
    removed.assert_called_once_with(11)
    service._appbar.ensure_keep_above.assert_not_called()


@pytest.mark.parametrize("action", ["stop", "clear_reservation"])
def test_releasing_surface_or_reservation_cancels_native_restoration(
    topmost_poll, action
):
    service, _added, removed = topmost_poll
    if action == "stop":
        service.stop()
    else:
        service.clear_reservation()
    removed.assert_called_once_with(11)
    assert service._topmost_source == 0
    assert service._poll_topmost() is False
