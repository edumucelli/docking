"""Menu focus, deferred close ownership and native callback lifetime."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.platform.backends.windows import close_hook, menus


class Menu:
    def __init__(self):
        self.handlers = {}
        self.popdowns = 0

    def connect(self, name, callback):
        self.handlers[name] = callback

    def popdown(self):
        self.popdowns += 1
        for callback in self.handlers.values():
            callback(self)


class CaptureSurface(Menu):
    def __init__(self):
        super().__init__()
        self.destroys = 0

    def destroy(self):
        self.destroys += 1
        for callback in self.handlers.values():
            callback(self)


def test_capture_surface_uses_destroy_signal_and_restores_foreground_once(session):
    overlay = CaptureSurface()
    session.value.prepare_capture(overlay)
    assert set(overlay.handlers) == {"hide", "destroy"}
    session.parent.set_accept_focus.assert_called_with(True)
    overlay.destroy()
    assert len(session.sources) == 1
    session.sources.pop(session.value._finish_source)()
    session.native.restore.assert_called_once_with((10, 99), (20, 101))
    session.parent.set_accept_focus.assert_called_with(False)


def test_native_close_destroys_the_owned_capture_before_closing_parent(session):
    overlay = CaptureSurface()
    session.value.prepare_capture(overlay)
    session.parent.close.side_effect = lambda: (
        overlay.destroys == 1 or pytest.fail("Capture must close before its parent")
    )
    assert session.value._request_close()
    session.sources.pop(session.value._close_source)()
    assert overlay.destroys == 1 and not session.value._active
    session.value.stop()
    assert not session.sources and not session.value._capture_surfaces


def test_repeated_captures_cannot_remember_their_own_temporary_foreground(session):
    session.value._observe_foreground((10, 99))
    first = CaptureSurface()
    session.value.prepare_capture(first)
    session.value._observe_foreground((30, 101))  # A popup in the dock process.
    first.destroy()
    session.sources.pop(session.value._finish_source)()
    session.native.foreground.return_value = None  # Non-activating dock click.
    session.value.prepare_capture(CaptureSurface())
    assert session.value._previous == (10, 99)


def test_new_owned_foreground_after_capture_is_retained_for_the_next_action(session):
    overlay = CaptureSurface()
    session.value.prepare_capture(overlay)
    session.value._observe_foreground((30, 101))  # Search takes focus.
    overlay.destroy()
    session.native.foreground.return_value = (30, 101)
    session.sources.pop(session.value._finish_source)()
    assert session.value._last_foreground == (30, 101)


@pytest.fixture
def session(monkeypatch):
    sources = {}
    sequence = iter(range(1, 100))

    def idle(callback):
        source = next(sequence)
        sources[source] = callback
        return source

    monkeypatch.setattr(menus.GLib, "idle_add", idle)
    monkeypatch.setattr(
        menus.GLib, "timeout_add", lambda _delay, callback: idle(callback)
    )
    monkeypatch.setattr(menus.GLib, "source_remove", sources.pop)
    parent = MagicMock()
    parent.get_accept_focus.return_value = False
    native = MagicMock()
    native.foreground.return_value = (10, 99)
    native.active.return_value = (20, 101)
    hook = MagicMock()
    value = menus.WindowsMenuSession(parent, native=native, hook_factory=lambda _: hook)
    return SimpleNamespace(
        value=value, sources=sources, parent=parent, native=native, hook=hook
    )


def test_menu_focus_restores_once_after_activation_can_present_its_window(session):
    menu = Menu()
    session.value.prepare(menu)
    session.parent.set_accept_focus.assert_called_once_with(True)
    session.parent.present_with_time.assert_called_once()
    session.hook.install.assert_called_once_with(20)
    menu.popdown()  # GTK emits both hide and deactivate.
    assert len(session.sources) == 1
    session.parent.set_accept_focus.assert_called_with(False)
    session.native.restore.assert_not_called()
    assert not session.sources.pop(session.value._finish_source)()
    session.native.restore.assert_called_once_with((10, 99), (20, 101))


def test_menu_foreground_restoration_retries_pending_native_activation(session):
    session.native.restore.side_effect = [False, False, True]
    menu = Menu()
    session.value.prepare(menu)
    menu.popdown()
    for expected in (1, 2, 3):
        session.sources.pop(session.value._finish_source)()
        assert session.native.restore.call_count == expected
        assert len(session.sources) == int(expected < 3)
    assert not session.value._in_session and session.value._previous is None


def test_menu_foreground_retries_are_bounded_and_stop_cancels_pending_work(session):
    session.native.restore.return_value = False
    menu = Menu()
    session.value.prepare(menu)
    menu.popdown()
    for _ in range(5):
        session.sources.pop(session.value._finish_source)()
    assert session.native.restore.call_count == 5 and session.sources == {}
    menu = Menu()
    session.value.prepare(menu)
    menu.popdown()
    session.sources.pop(session.value._finish_source)()
    assert session.sources
    session.value.stop()
    assert session.sources == {} and not session.value._in_session


def test_reopening_before_idle_retains_original_foreground_and_focus_policy(session):
    first, second = Menu(), Menu()
    session.value.prepare(first)
    first.popdown()
    session.native.foreground.return_value = (20, 101)
    session.value.prepare(second)
    assert session.sources == {}
    second.popdown()
    session.sources.pop(session.value._finish_source)()
    assert session.native.foreground.call_count == 2
    session.parent.get_accept_focus.assert_called_once()
    session.native.restore.assert_called_once_with((10, 99), (20, 101))


def test_new_menu_during_pending_restore_tracks_the_new_foreground_owner(session):
    first, second = Menu(), Menu()
    session.value.prepare(first)
    first.popdown()
    session.native.restore.return_value = False
    session.sources.pop(session.value._finish_source)()
    session.native.foreground.return_value = (30, 102)
    session.value.prepare(second)
    second.popdown()
    session.native.restore.return_value = True
    session.sources.pop(session.value._finish_source)()
    assert session.native.restore.call_args.args == ((30, 102), (20, 101))


def test_only_last_owned_root_menu_ends_focus_session(session):
    first, second = Menu(), Menu()
    session.value.prepare(first)
    session.value.prepare(second)
    first.popdown()
    assert session.sources == {}
    session.parent.set_accept_focus.assert_called_with(True)
    second.popdown()
    session.parent.set_accept_focus.assert_called_with(False)
    assert len(session.sources) == 1


@pytest.mark.parametrize("transient", [None, (20, 101)])
def test_foreground_events_preserve_previous_app_across_nonactivating_click(
    session, transient
):
    session.value._observe_foreground((10, 99))
    first = Menu()
    session.value.prepare(first)
    first.popdown()
    session.sources.pop(session.value._finish_source)()
    session.native.restore.reset_mock()
    session.value._observe_foreground((30, 102))
    session.value._observe_foreground((20, 101))  # Ignore focus held by the dock.
    session.value._observe_foreground(None)
    session.native.foreground.return_value = transient
    second = Menu()
    session.value.prepare(second)
    second.popdown()
    session.sources.pop(session.value._finish_source)()
    session.native.restore.assert_called_once_with((30, 102), (20, 101))


def test_native_close_is_coalesced_and_normal_gtk_close_follows_menu_dismissal(session):
    menu = Menu()
    assert not session.value._request_close()
    session.value.prepare(menu)
    session.parent.close.side_effect = lambda: (
        not session.value._active or pytest.fail("Close ran before dismissal")
    )
    assert session.value._request_close()
    assert session.value._request_close()
    assert len(session.sources) == 1
    session.parent.close.assert_not_called()
    session.sources.pop(session.value._close_source)()
    session.parent.close.assert_called_once()
    assert menu.popdowns == 1
    assert not session.value._request_close()


def test_stop_cancels_pending_close_dismisses_owned_menu_and_releases_hook(session):
    owned, unrelated = Menu(), Menu()
    session.value.prepare(owned)
    session.value._request_close()
    session.value.stop()
    assert session.sources == {}
    assert owned.popdowns == 1
    assert unrelated.popdowns == 0
    session.hook.close.assert_called_once()
    session.native.stop.assert_called_once()
    session.parent.close.assert_not_called()
    session.value.prepare(Menu())
    assert not session.value._request_close()
    assert not session.value._active
    session.value._observe_foreground((30, 102))
    assert session.value._last_foreground is None


@pytest.mark.parametrize("saved", [False, True])
def test_original_focus_policy_is_preserved_on_stop(session, saved):
    session.parent.get_accept_focus.return_value = saved
    session.value.prepare(Menu())
    session.value.stop()
    session.parent.set_accept_focus.assert_called_with(saved)


@pytest.mark.parametrize(
    ("previous", "current", "identity", "restored"),
    [
        ((10, 99), (20, 101), (10, 99), True),
        ((10, 99), (30, 102), (10, 99), False),  # New dialog or another app.
        ((10, 99), (20, 101), (10, 100), False),  # HWND reused by another PID.
        ((10, 99), (20, 101), None, False),  # Original application closed.
        (None, (20, 101), None, False),
        ((20, 101), (20, 101), (20, 101), False),
    ],
)
def test_restore_never_takes_focus_from_new_window_or_reused_identity(
    previous, current, identity, restored
):
    native = menus.WindowsMenuFocus.__new__(menus.WindowsMenuFocus)
    native.foreground = MagicMock(return_value=current)
    native.identity = MagicMock(return_value=identity)
    native._set_foreground = MagicMock()
    native.restore(previous, (20, 101))
    assert native._set_foreground.call_count == int(restored)


def test_restore_waits_for_a_foreground_gap_without_activating_another_window():
    native = menus.WindowsMenuFocus.__new__(menus.WindowsMenuFocus)
    native.foreground = MagicMock(return_value=None)
    native.identity = MagicMock(return_value=(10, 99))
    native._set_foreground = MagicMock()
    assert native.restore((10, 99), (20, 101)) is False
    native._set_foreground.assert_not_called()


@pytest.fixture
def hook(monkeypatch):
    library = MagicMock()
    library.SetWindowSubclass.return_value = True
    library.DefSubclassProc.return_value = 123
    monkeypatch.setattr(
        close_hook.ctypes, "WinDLL", lambda *_a, **_kw: library, raising=False
    )
    monkeypatch.setattr(
        close_hook.ctypes,
        "WINFUNCTYPE",
        getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE),
        raising=False,
    )
    on_close = MagicMock(return_value=True)
    value = close_hook.WindowsCloseHook(on_close)
    return SimpleNamespace(value=value, library=library, on_close=on_close)


def test_subclass_is_installed_once_and_removed_before_replacing_its_window(hook):
    hook.value.install(20)
    hook.value.install(20)
    hook.library.SetWindowSubclass.assert_called_once()
    callback = hook.value._callback
    hook.value.install(30)
    hook.library.RemoveWindowSubclass.assert_called_once_with(
        20, callback, id(hook.value)
    )
    hook.value.close()
    hook.value.close()
    assert hook.library.RemoveWindowSubclass.call_count == 2
    assert hook.value._callback is callback


def test_close_callback_consumes_only_accepted_close_and_forwards_other_messages(hook):
    assert hook.value._message(20, 0x10, 1, 2, 0, 0) == 0
    hook.library.DefSubclassProc.assert_not_called()
    hook.on_close.return_value = False
    assert hook.value._message(20, 0x10, 1, 2, 0, 0) == 123
    assert hook.value._message(20, 0x1234, 3, 4, 0, 0) == 123
    hook.library.DefSubclassProc.assert_called_with(20, 0x1234, 3, 4)
    assert hook.on_close.call_count == 2


def test_native_callback_failure_is_logged_and_forwarded(hook, caplog):
    hook.on_close.side_effect = RuntimeError("callback failed")
    assert hook.value._message(20, 0x10, 0, 0, 0, 0) == 123
    assert "Native close callback failed" in caplog.text


def test_native_destroy_removes_subclass_and_still_forwards_destroy(hook):
    hook.value.install(20)
    assert hook.value._message(20, 0x82, 0, 0, 0, 0) == 123
    assert hook.value._hwnd is None
    hook.library.RemoveWindowSubclass.assert_called_once()
    hook.library.DefSubclassProc.assert_called_once_with(20, 0x82, 0, 0)


def test_foreground_hook_reports_identity_and_is_unhooked_before_callback_release(
    monkeypatch,
):
    library = MagicMock()
    library.GetForegroundWindow.return_value = 10
    library.SetWinEventHook.return_value = 500

    def owner(_hwnd, pid):
        ctypes.cast(pid, ctypes.POINTER(ctypes.wintypes.DWORD)).contents.value = 99
        return 55

    library.GetWindowThreadProcessId.side_effect = owner
    monkeypatch.setattr(
        menus.ctypes, "WinDLL", lambda *_a, **_kw: library, raising=False
    )
    monkeypatch.setattr(
        menus.ctypes,
        "WINFUNCTYPE",
        getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE),
        raising=False,
    )
    native = menus.WindowsMenuFocus()
    changed = MagicMock()
    native.start(changed)
    native.start(changed)
    changed.assert_called_once_with((10, 99))
    library.SetWinEventHook.assert_called_once_with(
        3, 3, None, native._event_callback, 0, 0, 0
    )
    native._event_callback(500, 3, 30, 0, 0, 55, 0)
    changed.assert_called_with((30, 99))
    native.stop()
    native.stop()
    library.UnhookWinEvent.assert_called_once_with(500)
    native._event_callback(500, 3, 40, 0, 0, 55, 0)
    assert changed.call_count == 2


def test_surface_owns_and_stops_one_menu_session(monkeypatch):
    from docking.platform.backends.windows.surface import WindowsSurfaceService

    factory = MagicMock()
    monkeypatch.setattr(menus, "WindowsMenuSession", factory)
    service = WindowsSurfaceService()
    menu = Menu()
    service.prepare_menu_popup(menu)
    factory.assert_not_called()
    service._window = parent = MagicMock()
    service.prepare_menu_popup(menu)
    service.prepare_menu_popup(menu)
    factory.assert_called_once_with(parent)
    assert factory.return_value.prepare.call_count == 2
    service.stop()
    factory.return_value.stop.assert_called_once()
    assert service._menus is None


def test_surface_starts_foreground_observation_at_realize_before_first_menu(
    monkeypatch,
):
    from docking.platform.backends.windows import appbar
    from docking.platform.backends.windows.surface import WindowsSurfaceService

    factory = MagicMock()
    monkeypatch.setattr(menus, "WindowsMenuSession", factory)
    monkeypatch.setattr(appbar, "WindowsAppBar", MagicMock())
    service = WindowsSurfaceService()
    parent = MagicMock()
    service.on_realize(parent)
    service.on_realize(parent)
    factory.assert_called_once_with(parent)
    menu = Menu()
    service.prepare_menu_popup(menu)
    factory.return_value.prepare.assert_called_once_with(menu)
    service.stop()
    factory.return_value.stop.assert_called_once()
