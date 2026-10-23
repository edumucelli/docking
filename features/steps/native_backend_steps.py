"""Native-service contracts; separate container probes validate compositor pixels."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

from behave import given, then, when
from gi.repository import GLib

from docking.core.config import HideMode
from docking.platform.backends.base import ActionResult
from docking.platform.backends.visibility import (
    SnapshotVisibilityMonitor,
    WindowChanges,
    should_hide,
)
from docking.platform.backends.wayland.session import WaylandLayerShellSessionBackend
from tests.integration.test_native_backend_bus import run_private
from tests.platform.application_fakes import identity_services
from tests.platform.test_hyprland_ipc import _model
from tests.platform.test_native_visibility import DOCK, WINDOW
from tests.platform.test_wayland_layer_shell_session import _empty_runtime, _layer_shell


@given('a native overlapping window that is "{state}"')
def native_window(context, state):
    options = {
        "visible": {},
        "minimized": {"minimized": True},
        "off-workspace": {"visible": False},
        "unknown": {"geometry": None},
    }
    context.native_window = replace(WINDOW, **options[state])


@when("native window dodge is evaluated")
def evaluate(context):
    context.native_hidden = should_hide(
        windows=[context.native_window], dock=DOCK, mode=HideMode.WINDOW_DODGE
    )


@then('native dodge hides the dock "{hidden}"')
def hides(context, hidden):
    assert context.native_hidden is (hidden == "yes")


@given("a native event-driven overlap monitor")
def event_monitor(context):
    changes = WindowChanges()
    windows = MagicMock()
    windows.watch.side_effect = changes.watch
    windows.unwatch.side_effect = changes.unwatch
    context.native_changes = changes
    context.native_monitor = SnapshotVisibilityMonitor(
        windows=windows,
        visible_windows=lambda _rect: [WINDOW],
        config=SimpleNamespace(hide_mode_enum=HideMode.WINDOW_DODGE),
        get_dock_rect=lambda: DOCK,
        on_change=lambda _hidden: None,
        poll_ms=0,
    )
    context.native_monitor.start()


@when("100 native changes arrive before dispatch")
def changes(context):
    context.native_changes.notify()
    context.native_pending = context.native_monitor._pending
    for _ in range(100):
        context.native_changes.notify()
        assert context.native_monitor._pending == context.native_pending


@then("one pending evaluation remains and shutdown cancels it")
def cleanup(context):
    assert context.native_pending
    context.native_monitor.stop()
    assert GLib.MainContext.default().find_source_by_id(context.native_pending) is None


@given("a Wayland session with only standard toplevel listing")
def standard_listing(context):
    runtime = _empty_runtime()
    runtime.standard_toplevel_protocol = SimpleNamespace(
        start=lambda _service: None, stop=lambda: None
    )
    context.native_backend = WaylandLayerShellSessionBackend(
        layer_shell=_layer_shell(),
        model=_model(),
        protocol_runtime=runtime,
        **identity_services(),
    )
    windows = context.native_backend.windows
    handle = object()
    windows.toplevel_created(handle)
    windows.app_id_changed(handle, "Alacritty")
    windows.title_changed(handle, "Native fixture")
    windows.done(handle)


@then("the native fixture is listed but all management actions are unsupported")
def no_management(context):
    backend = context.native_backend
    rows = backend.windows.list_windows("Alacritty.desktop")
    assert len(rows) == 1 and rows[0].title == "Native fixture"
    assert not backend.capabilities.supports_activate
    assert not backend.capabilities.tracks_active_window
    assert rows[0].minimized is None and rows[0].geometry is None
    assert backend.windows.activate(rows[0].id) is ActionResult.UNSUPPORTED
    assert backend.windows.close(rows[0].id) is ActionResult.UNSUPPORTED


@when('a private compositor screenshot service responds "{response}"')
def screenshot(context, response):
    context.native_capture = run_private(response)


@then('native preview is available "{available}" without a screen fallback')
def preview(context, available):
    assert context.native_capture["preview"] is (available == "yes")
