"""Windows owns its surface lifecycle while reusing common GTK operations."""

import os
from unittest.mock import Mock, call

import pytest

from docking.core.position import Position
from docking.platform.backends.base import MonitorSnapshot, PlacementRequest, Rect, Size
from docking.platform.backends.reduced.services import ReducedSurfaceService
from docking.platform.backends.windows.surface import WindowsSurfaceService


def test_interactive_windows_popups_use_managed_gtk_windows():
    assert WindowsSurfaceService().popups_require_managed_windows()
    assert not ReducedSurfaceService().popups_require_managed_windows()


@pytest.fixture
def popup_return(monkeypatch):
    from docking.platform.desktop.implementations.windows import menus
    from docking.platform.native.windows import ffi

    service = WindowsSurfaceService()
    service._window = Mock()
    service._appbar = Mock()
    focus = Mock(spec=menus.WindowsMenuFocus)
    focus.foreground.return_value = (10, 91)
    focus.focus_if_current.return_value = True
    service._popup_focus = focus
    window = Mock()
    window.get_focus_on_map.return_value = True
    window.get_accept_focus.return_value = True
    native_window = window.get_window()
    monkeypatch.setattr(
        ffi, "window_handle", lambda value: 20 if value is native_window else 11
    )
    return service, focus, window


def test_native_popup_claim_precedes_mapping_and_focus(popup_return):
    service, focus, window = popup_return
    events = []
    claim = Mock(side_effect=lambda: events.append("claim") or True)
    window.show.side_effect = lambda: events.append("map")
    focus.focus_if_current.side_effect = lambda target, source: (
        events.append(("focus", target, source)) or True
    )

    assert service.restore_popup_focus(window, claim, validate=lambda: True)

    assert events == ["claim", "map", ("focus", 20, (10, 91))]
    assert window.set_focus_on_map.call_args_list == [call(False), call(True)]
    assert window.set_accept_focus.call_args_list == [call(False), call(True)]
    claim.assert_called_once_with()
    window.present.assert_not_called()
    window.hide.assert_not_called()


def test_denied_popup_claim_never_maps_or_presents(popup_return):
    service, focus, window = popup_return
    claim = Mock(return_value=False)

    assert not service.restore_popup_focus(window, claim, validate=lambda: True)

    claim.assert_called_once_with()
    window.show.assert_not_called()
    window.present.assert_not_called()
    window.set_focus_on_map.assert_not_called()
    window.set_accept_focus.assert_not_called()
    focus.focus_if_current.assert_not_called()


def test_native_popup_mapping_disables_win32_raise_activation(popup_return):
    service, focus, window = popup_return
    accepting = True

    def accept(value):
        nonlocal accepting
        accepting = value

    def mapped():
        # GDK's Win32 toplevel raise otherwise calls SetForegroundWindow
        # before GTK applies focus_on_map, invalidating the provider claim.
        assert not accepting

    def activate(target, expected):
        assert accepting
        assert (target, expected) == (20, (10, 91))
        return True

    window.set_accept_focus.side_effect = accept
    window.show.side_effect = mapped
    focus.focus_if_current.side_effect = activate

    assert service.restore_popup_focus(window, lambda: True, validate=lambda: True)
    assert accepting


@pytest.mark.parametrize("accepting", [False, True])
def test_failed_popup_mapping_restores_both_activation_flags(popup_return, accepting):
    service, focus, window = popup_return
    window.get_accept_focus.return_value = accepting
    window.show.side_effect = RuntimeError("Owned popup mapping failed")

    with pytest.raises(RuntimeError, match="Owned popup mapping failed"):
        service.restore_popup_focus(window, lambda: True, validate=lambda: True)

    assert window.set_accept_focus.call_args_list == [call(False), call(accepting)]
    assert window.set_focus_on_map.call_args_list == [call(False), call(True)]
    focus.focus_if_current.assert_not_called()


@pytest.mark.parametrize("during_mapping", [False, True])
def test_invalidated_popup_return_cannot_map_or_focus(popup_return, during_mapping):
    service, focus, window = popup_return
    eligible = not during_mapping
    claim = Mock(return_value=True)

    def mapped():
        nonlocal eligible
        eligible = False

    if during_mapping:
        eligible = True
        window.show.side_effect = mapped
    else:
        eligible = False

    assert not service.restore_popup_focus(window, claim, validate=lambda: eligible)

    focus.focus_if_current.assert_not_called()
    if during_mapping:
        claim.assert_called_once_with()
        window.hide.assert_called_once_with()
    else:
        claim.assert_not_called()
        window.show.assert_not_called()


@pytest.mark.parametrize("source", [11, 20, 99])
def test_only_dock_or_requested_popup_can_return_focus_in_our_process(
    popup_return, source
):
    service, focus, window = popup_return
    focus.foreground.return_value = (source, os.getpid())
    claim = Mock(return_value=True)

    assert service.restore_popup_focus(window, claim, validate=lambda: True) is (
        source != 99
    )

    if source == 99:  # Another Docking window became active before worker dispatch.
        claim.assert_not_called()
        window.show.assert_not_called()
        focus.focus_if_current.assert_not_called()
    else:
        claim.assert_called_once_with()


def test_popup_mapping_cannot_focus_after_foreground_changes(popup_return):
    service, focus, window = popup_return
    focus.focus_if_current.return_value = False
    claim = Mock(return_value=True)

    assert not service.restore_popup_focus(window, claim, validate=lambda: True)

    claim.assert_called_once_with()
    window.hide.assert_called_once_with()
    assert window.set_focus_on_map.call_args_list == [call(False), call(True)]
    window.present.assert_not_called()


@pytest.mark.parametrize("change", ["native-window", "surface-stop"])
def test_reentrant_mapping_cannot_activate_a_replaced_or_stopped_surface(
    popup_return, change
):
    service, focus, window = popup_return

    def changed():
        if change == "native-window":
            window.get_window.return_value = None
        else:
            service.stop()

    window.show.side_effect = changed
    claim = Mock(return_value=True)

    assert not service.restore_popup_focus(window, claim, validate=lambda: True)

    claim.assert_called_once_with()
    focus.focus_if_current.assert_not_called()
    window.hide.assert_called_once_with()


@pytest.mark.parametrize("missing", ["foreground", "native-window", "stopped"])
def test_ineligible_surface_cannot_consume_a_popup_return(popup_return, missing):
    service, focus, window = popup_return
    if missing == "foreground":
        focus.foreground.return_value = None
    elif missing == "native-window":
        window.get_window.return_value = None
    else:
        service.stop()
    claim = Mock(return_value=True)

    assert not service.restore_popup_focus(window, claim, validate=lambda: True)

    claim.assert_not_called()
    window.show.assert_not_called()
    window.present.assert_not_called()
    focus.focus_if_current.assert_not_called()


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


def test_application_drag_adapter_is_lazy_owned_and_recreated_for_a_surface_lifetime():
    from unittest.mock import Mock

    source, second = Mock(), Mock()
    create = Mock(side_effect=(source, second))
    surface = WindowsSurfaceService(file_drag_source_factory=create)
    widget, window = object(), object()
    data = (("Shell IDList Array", b"owned-shell-data"),)
    assert not surface.prepare_file_drag_source(widget, application_id="App", data=data)
    create.assert_not_called()
    surface._window = window
    assert surface.prepare_file_drag_source(widget, application_id="App", data=data)
    assert surface.prepare_file_drag_source(widget, application_id="App", data=data)
    create.assert_called_once_with(window)
    source.prepare.assert_called_with(widget, application_id="App", data=data)
    surface.stop()
    source.stop.assert_called_once_with()
    surface._window = window
    assert surface.prepare_file_drag_source(widget, application_id="App", data=data)
    assert create.call_count == 2
    assert second.prepare.call_count == 1
    surface.stop()
    second.stop.assert_called_once_with()
