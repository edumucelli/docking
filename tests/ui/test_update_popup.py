"""Tests for the update popup controller."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import docking.ui.update_popup as mod
from docking.core.position import Position
from docking.core.updates import ReleaseInfo, UpdateState
from docking.ui.update_popup import (
    PROJECT_RELEASES_URL,
    REMIND_LATER_HOURS,
    UPDATE_CHECK_DELAY_S,
    UPDATE_POPUP_GAP_PX,
    UpdateCheckController,
)

# ---------------------------------------------------------------------------
# Fake GTK objects for testing popup construction
# ---------------------------------------------------------------------------


class _FakeStyleContext:
    def __init__(self) -> None:
        self.classes: list[str] = []

    def add_class(self, class_name: str) -> None:
        self.classes.append(class_name)


class _FakePopup:
    def __init__(self, *args, **kwargs) -> None:
        self.child = None
        self.destroyed = False
        self.hidden = False
        self.shown = False
        self.moved_to: tuple[int, int] | None = None
        self.connections: dict[str, object] = {}
        self.decorated = True
        self.skip_taskbar = False
        self.resizable = True
        self.type_hint = None
        self.transient_for = None
        self.app_paintable = False
        self.visual = None
        self.bg_override = None
        self.style_context_classes: list[str] = []

    def set_decorated(self, value):
        self.decorated = value

    def set_app_paintable(self, value):
        self.app_paintable = value

    def set_skip_taskbar_hint(self, value):
        self.skip_taskbar = value

    def set_resizable(self, value):
        self.resizable = value

    def set_type_hint(self, value):
        self.type_hint = value

    def set_transient_for(self, window):
        self.transient_for = window

    def get_screen(self):
        return SimpleNamespace(get_rgba_visual=lambda: None)

    def set_visual(self, visual):
        self.visual = visual

    def override_background_color(self, state, color):
        self.bg_override = (state, color)

    def get_style_context(self):
        ctx = SimpleNamespace(classes=self.style_context_classes)

        def _add_class(name):
            self.style_context_classes.append(name)

        ctx.add_class = _add_class
        return ctx

    def connect(self, signal, callback):
        self.connections[signal] = callback

    def get_child(self):
        return self.child

    def remove(self, child):
        if self.child is child:
            self.child = None

    def add(self, child):
        self.child = child

    def show_all(self):
        self.shown = True

    def get_preferred_size(self):
        return (None, SimpleNamespace(width=300, height=120))

    def move(self, x, y):
        self.moved_to = (x, y)

    def hide(self):
        self.hidden = True

    def destroy(self):
        self.destroyed = True


class _FakeFrame:
    def __init__(self) -> None:
        self.shadow_type = None
        self._child = None

    def set_shadow_type(self, value):
        self.shadow_type = value

    def add(self, child):
        self._child = child


class _FakeBox:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.children: list[object] = []
        self.border_width = 0

    def set_border_width(self, value):
        self.border_width = value

    def pack_start(self, child, *args):
        self.children.append(child)


class _FakeLabel:
    def __init__(self, *, label="") -> None:
        self.label = label
        self.xalign = None

    def set_xalign(self, value):
        self.xalign = value


class _FakeButton:
    def __init__(self, *, label="") -> None:
        self.label = label
        self.handlers: dict[str, object] = {}

    def connect(self, signal, callback):
        self.handlers[signal] = callback


class _FakeWindow:
    def __init__(self, *, realized=True, pos=Position.BOTTOM) -> None:
        self.realized = realized
        self.config = SimpleNamespace(pos=pos)
        self._size = (800, 48)

    def get_realized(self):
        return self.realized

    def get_position(self):
        return (100, 200)

    def get_size(self):
        return self._size


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_gtk_window():
    return _FakeWindow()


@pytest.fixture
def fake_config():
    return MagicMock()


@pytest.fixture
def controller(fake_gtk_window, fake_config):
    return UpdateCheckController(window=fake_gtk_window, config=fake_config)


@pytest.fixture
def popup_gtk(monkeypatch):
    """Replace Gtk/Gdk/GLib with fakes suitable for popup construction."""
    monkeypatch.setattr(
        mod,
        "Gtk",
        SimpleNamespace(
            Window=_FakePopup,
            WindowType=SimpleNamespace(POPUP="popup"),
            Frame=_FakeFrame,
            ShadowType=SimpleNamespace(OUT="out", NONE="none"),
            Box=_FakeBox,
            Orientation=SimpleNamespace(VERTICAL="vertical", HORIZONTAL="horizontal"),
            Label=_FakeLabel,
            Button=_FakeButton,
        ),
    )
    monkeypatch.setattr(
        mod,
        "Gdk",
        SimpleNamespace(
            WindowTypeHint=SimpleNamespace(NOTIFICATION="notification"),
        ),
    )
    monkeypatch.setattr(mod, "GLib", MagicMock())

    # Also patch popup_surface helpers that _show_popup now uses
    import docking.ui.popup_surface as ps

    monkeypatch.setattr(
        mod,
        "configure_transparent_startup_popup_window",
        lambda popup: None,
    )
    monkeypatch.setattr(
        mod,
        "wrap_startup_popup_content",
        lambda content: content,
    )
    # Ensure popup_surface's Gtk/Gdk are patched too for configure/wrap calls
    monkeypatch.setattr(ps, "Gdk", MagicMock())
    monkeypatch.setattr(ps, "Gtk", MagicMock())
    monkeypatch.setattr(ps, "ensure_startup_popup_css", lambda: None)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestUpdateCheckConstants:
    def test_constants_are_reasonable(self):
        assert UPDATE_CHECK_DELAY_S > 0
        assert UPDATE_POPUP_GAP_PX > 0
        assert REMIND_LATER_HOURS > 0


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestUpdateCheckControllerInit:
    def test_init_sets_window_and_config(self):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        assert controller._window is window
        assert controller._config is config
        assert controller._popup is None
        assert controller._latest_release is None
        assert controller._start_source_id == 0

    def test_set_window_attaches_window(self):
        config = MagicMock()
        controller = UpdateCheckController(window=MagicMock(), config=config)
        window = MagicMock()
        controller.set_window(window)
        assert controller._window is window


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


class TestUpdateCheckControllerStart:
    def test_start_already_scheduled_returns(self):
        window = MagicMock()
        config = MagicMock()
        config.update_check_enabled = True
        controller = UpdateCheckController(window=window, config=config)
        controller._start_source_id = 42
        controller.start()
        assert controller._start_source_id == 42

    def test_start_disabled_returns(self):
        window = MagicMock()
        config = MagicMock()
        config.update_check_enabled = False
        controller = UpdateCheckController(window=window, config=config)
        controller.start()
        assert controller._start_source_id == 0

    def test_start_schedules_timeout(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        config.update_check_enabled = True
        config.update_check_interval_hours = 24
        controller = UpdateCheckController(window=window, config=config)

        monkeypatch.setattr(
            mod, "load_state", lambda: SimpleNamespace(last_checked_at=None)
        )
        monkeypatch.setattr(mod, "should_check_for_updates", lambda **kw: True)
        monkeypatch.setattr(mod.GLib, "timeout_add_seconds", lambda delay, cb, *a: 99)

        controller.start()

        assert controller._start_source_id == 99

    def test_start_should_not_check_returns(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        config.update_check_enabled = True
        config.update_check_interval_hours = 24
        controller = UpdateCheckController(window=window, config=config)

        monkeypatch.setattr(
            mod, "load_state", lambda: SimpleNamespace(last_checked_at=None)
        )
        monkeypatch.setattr(mod, "should_check_for_updates", lambda **kw: False)

        controller.start()

        assert controller._start_source_id == 0


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------


class TestUpdateCheckControllerStop:
    def test_stop_removes_source_and_destroys_popup(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        controller._start_source_id = 42
        fake_popup = MagicMock()
        controller._popup = fake_popup

        visibility_calls: list[tuple[str, bool]] = []
        controller._visibility_changed = lambda src, vis: visibility_calls.append(
            (src, vis)
        )
        monkeypatch.setattr(mod.GLib, "source_remove", MagicMock())

        controller.stop()

        mod.GLib.source_remove.assert_called_once_with(42)
        assert controller._start_source_id == 0
        fake_popup.destroy.assert_called_once()
        assert controller._popup is None
        assert visibility_calls == [("updates", False)]

    def test_stop_without_source_or_popup(self):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)

        # Should not raise
        controller.stop()

        assert controller._start_source_id == 0


# ---------------------------------------------------------------------------
# Check now / open releases
# ---------------------------------------------------------------------------


class TestUpdateCheckManualActions:
    def test_check_now_starts_thread(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)

        thread_calls: list[dict] = []

        def _fake_run(*, automatic):
            thread_calls.append({"automatic": automatic})

        controller._run_check_in_thread = _fake_run

        controller.check_now()

        assert thread_calls == [{"automatic": False}]

    def test_open_releases_page_calls_open_url(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)

        url_calls: list[str] = []
        controller._open_url = lambda u: url_calls.append(u)

        controller.open_releases_page()

        assert url_calls == [PROJECT_RELEASES_URL]


# ---------------------------------------------------------------------------
# Startup delay elapsed
# ---------------------------------------------------------------------------


class TestUpdateCheckOnStartupDelayElapsed:
    def test_clears_source_and_runs_check(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        controller._start_source_id = 99

        thread_calls: list[dict] = []

        def _fake_run(*, automatic):
            thread_calls.append({"automatic": automatic})

        controller._run_check_in_thread = _fake_run

        result = controller._on_startup_delay_elapsed()

        assert result is False
        assert controller._start_source_id == 0
        assert thread_calls == [{"automatic": True}]


# ---------------------------------------------------------------------------
# Check worker
# ---------------------------------------------------------------------------


class TestUpdateCheckWorker:
    def test_check_worker_fetches_and_dispatches(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)

        release = ReleaseInfo(version="3.0.0", name="v3.0.0", url="https://x.com")
        monkeypatch.setattr(mod, "fetch_latest_release", lambda: release)

        idle_calls: list[tuple] = []

        def _fake_idle(cb, *args):
            idle_calls.append((cb, args))
            return 1

        monkeypatch.setattr(mod.GLib, "idle_add", _fake_idle)

        controller._check_worker(automatic=True)

        assert len(idle_calls) == 1
        _cb, args = idle_calls[0]
        assert args[0] is release
        assert args[1] == ""
        assert args[2] is True

    def test_check_worker_error_dispatches_with_error(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)

        monkeypatch.setattr(
            mod, "fetch_latest_release", MagicMock(side_effect=RuntimeError("boom"))
        )

        idle_calls: list[tuple] = []

        def _fake_idle(cb, *args):
            idle_calls.append((cb, args))
            return 2

        monkeypatch.setattr(mod.GLib, "idle_add", _fake_idle)

        controller._check_worker(automatic=False)

        assert len(idle_calls) == 1
        _cb, args = idle_calls[0]
        assert args[0] is None  # release is None on error
        assert args[1] == "boom"
        assert args[2] is False


# ---------------------------------------------------------------------------
# On check finished
# ---------------------------------------------------------------------------


class TestUpdateCheckOnCheckFinished:
    def test_automatic_with_request_show_queues_popup(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)

        show_calls: list[str] = []
        controller._request_show = lambda src: show_calls.append(src)

        release = ReleaseInfo(version="4.0.0", name="v4", url="https://x.com")
        monkeypatch.setattr(mod, "load_state", lambda: UpdateState())
        monkeypatch.setattr(mod, "save_state", MagicMock())
        monkeypatch.setattr(
            mod,
            "decide_update_popup",
            lambda **kw: SimpleNamespace(should_show=True, release=release),
        )

        result = controller._on_check_finished(release, "", automatic=True)

        assert result is False
        assert controller._pending_release is release
        assert show_calls == ["updates"]

    def test_manual_shows_popup_directly(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)

        release = ReleaseInfo(version="4.0.0", name="v4", url="https://x.com")
        monkeypatch.setattr(mod, "load_state", lambda: UpdateState())
        monkeypatch.setattr(mod, "save_state", MagicMock())
        monkeypatch.setattr(
            mod,
            "decide_update_popup",
            lambda **kw: SimpleNamespace(should_show=True, release=release),
        )

        show_calls: list[ReleaseInfo] = []

        def _fake_show(*, release):
            show_calls.append(release)
            return True

        controller._show_popup = _fake_show

        result = controller._on_check_finished(release, "", automatic=False)

        assert result is False
        assert show_calls == [release]

    def test_automatic_without_request_show_shows_directly(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        controller._request_show = None

        release = ReleaseInfo(version="4.0.0", name="v4", url="https://x.com")
        monkeypatch.setattr(mod, "load_state", lambda: UpdateState())
        monkeypatch.setattr(mod, "save_state", MagicMock())
        monkeypatch.setattr(
            mod,
            "decide_update_popup",
            lambda **kw: SimpleNamespace(should_show=True, release=release),
        )

        show_calls: list[ReleaseInfo] = []

        def _fake_show(*, release):
            show_calls.append(release)
            return True

        controller._show_popup = _fake_show

        result = controller._on_check_finished(release, "", automatic=True)

        assert result is False
        assert show_calls == [release]

    def test_no_release_no_popup(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        save_called = []

        def _save(s):
            save_called.append(s)

        monkeypatch.setattr(mod, "save_state", _save)
        monkeypatch.setattr(mod, "load_state", lambda: UpdateState())
        monkeypatch.setattr(
            mod,
            "decide_update_popup",
            lambda **kw: SimpleNamespace(should_show=False, release=None),
        )

        result = controller._on_check_finished(None, "")

        assert result is False
        assert len(save_called) == 1

    def test_error_sets_error_state(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)

        saved_states: list[UpdateState] = []
        monkeypatch.setattr(mod, "save_state", lambda s: saved_states.append(s))
        monkeypatch.setattr(mod, "load_state", lambda: UpdateState())
        monkeypatch.setattr(
            mod,
            "decide_update_popup",
            lambda **kw: SimpleNamespace(should_show=False, release=None),
        )

        controller._on_check_finished(None, "Network error")

        assert saved_states[0].last_result == "error"
        assert saved_states[0].last_error == "Network error"


# ---------------------------------------------------------------------------
# Show pending
# ---------------------------------------------------------------------------


class TestUpdateCheckShowPending:
    def test_show_pending_no_release_returns_false(self):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)

        result = controller.show_pending()

        assert result is False

    def test_show_pending_with_release_shows_popup(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)

        release = ReleaseInfo(version="5.0.0", name="v5", url="https://x.com")
        controller._pending_release = release

        show_calls: list[ReleaseInfo] = []

        def _fake_show(*, release):
            show_calls.append(release)
            return True

        controller._show_popup = _fake_show

        result = controller.show_pending()

        assert result is True
        assert show_calls == [release]
        assert controller._pending_release is None


# ---------------------------------------------------------------------------
# Show popup (full construction)
# ---------------------------------------------------------------------------


class TestUpdateCheckShowPopup:
    def test_show_popup_skips_unrealized_window(self, monkeypatch):
        window = _FakeWindow(realized=False)
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        release = ReleaseInfo(version="3.0.0", name="v3", url="https://x.com")

        result = controller._show_popup(release=release)

        assert result is False

    def test_show_popup_creates_and_positions(self, popup_gtk, monkeypatch):
        window = _FakeWindow(realized=True)
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        release = ReleaseInfo(version="3.0.0", name="v3", url="https://x.com")

        # Stub out position_popup to avoid needing full geometry chain
        monkeypatch.setattr(controller, "_position_popup", lambda: None)
        monkeypatch.setattr(controller, "_notify_visible", lambda v: None)

        result = controller._show_popup(release=release)

        assert result is True
        assert isinstance(controller._popup, _FakePopup)
        assert controller._popup.shown
        # Verify content was built
        assert controller._popup.child is not None

    def test_show_popup_reuses_existing_popup(self, popup_gtk, monkeypatch):
        window = _FakeWindow(realized=True)
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)

        # Pre-create a popup
        existing = _FakePopup()
        existing.add(_FakeLabel(label="old"))
        controller._popup = existing

        release = ReleaseInfo(version="3.0.0", name="v3", url="https://x.com")

        monkeypatch.setattr(controller, "_position_popup", lambda: None)
        monkeypatch.setattr(controller, "_notify_visible", lambda v: None)

        result = controller._show_popup(release=release)

        assert result is True
        assert controller._popup is existing
        # Old child should be removed
        assert existing.child is not None


# ---------------------------------------------------------------------------
# Popup content
# ---------------------------------------------------------------------------


class TestUpdateCheckBuildPopupContent:
    def test_content_has_title_and_buttons(self, popup_gtk, monkeypatch):
        window = _FakeWindow()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)

        release = ReleaseInfo(version="4.2.0", name="v4.2.0", url="https://x.com/r")

        content = controller._build_popup_content(release=release)

        # With wrap_startup_popup_content patched as pass-through, we get the
        # inner box directly (normally it would be wrapped in a themed Frame)
        assert isinstance(content, _FakeBox)
        # The box should contain: title label, detail label, buttons box
        assert len(content.children) >= 3
        assert content.children[0].label == "Docking 4.2.0 is available"
        assert "You are using" in content.children[1].label
        assert isinstance(content.children[2], _FakeBox)  # buttons bar
        assert len(content.children[2].children) == 3  # View, Later, Ignore


# ---------------------------------------------------------------------------
# Hide and dismiss
# ---------------------------------------------------------------------------


class TestUpdateCheckHideAndDismiss:
    def test_hide_popup_hides_window(self, controller):
        fake_popup = MagicMock()
        controller._popup = fake_popup

        controller._hide_popup()

        fake_popup.hide.assert_called_once()

    def test_hide_popup_no_popup_no_error(self, controller):
        controller._popup = None
        controller._hide_popup()  # Should not raise

    def test_open_url_calls_gio_launch(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        monkeypatch.setattr(mod.Gio.AppInfo, "launch_default_for_uri", MagicMock())

        controller._open_url("https://example.com")

        mod.Gio.AppInfo.launch_default_for_uri.assert_called_once_with(
            "https://example.com", None
        )

    def test_open_url_exception_is_handled(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        monkeypatch.setattr(
            mod.Gio.AppInfo,
            "launch_default_for_uri",
            MagicMock(side_effect=RuntimeError("no browser")),
        )

        # Should not raise
        controller._open_url("https://example.com")

    def test_on_view_release_hides_and_opens_url(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        controller._latest_release = ReleaseInfo(
            version="3.0.0",
            name="v3.0.0",
            url="https://example.com/release",
        )
        fake_popup = MagicMock()
        controller._popup = fake_popup
        monkeypatch.setattr(mod.Gio.AppInfo, "launch_default_for_uri", MagicMock())

        controller._on_view_release(MagicMock())

        fake_popup.hide.assert_called_once()

    def test_on_view_release_no_latest_release_just_hides(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        controller._latest_release = None
        fake_popup = MagicMock()
        controller._popup = fake_popup
        monkeypatch.setattr(mod.Gio.AppInfo, "launch_default_for_uri", MagicMock())

        controller._on_view_release(MagicMock())

        fake_popup.hide.assert_called_once()
        mod.Gio.AppInfo.launch_default_for_uri.assert_not_called()

    def test_on_later_saves_reminder_and_hides(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        controller._latest_release = ReleaseInfo(
            version="3.0.0",
            name="v3.0.0",
            url="https://example.com/release",
        )
        fake_popup = MagicMock()
        controller._popup = fake_popup
        monkeypatch.setattr(mod, "save_state", MagicMock())
        monkeypatch.setattr(mod, "load_state", lambda: UpdateState())
        monkeypatch.setattr(mod, "utc_now_iso", lambda _dt=None: "2025-01-01T00:00:00Z")

        controller._on_later(MagicMock())

        fake_popup.hide.assert_called_once()
        mod.save_state.assert_called_once()

    def test_on_later_without_release_still_hides(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        controller._latest_release = None
        fake_popup = MagicMock()
        controller._popup = fake_popup
        monkeypatch.setattr(mod, "save_state", MagicMock())
        monkeypatch.setattr(mod, "load_state", lambda: UpdateState())

        controller._on_later(MagicMock())

        fake_popup.hide.assert_called_once()
        mod.save_state.assert_called_once()

    def test_on_ignore_saves_and_hides(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        controller._latest_release = ReleaseInfo(
            version="3.0.0",
            name="v3.0.0",
            url="https://example.com/release",
        )
        fake_popup = MagicMock()
        controller._popup = fake_popup
        monkeypatch.setattr(mod, "save_state", MagicMock())
        monkeypatch.setattr(mod, "load_state", lambda: UpdateState())

        controller._on_ignore(MagicMock())

        fake_popup.hide.assert_called_once()
        mod.save_state.assert_called_once()

    def test_on_ignore_without_release_still_hides(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        controller._latest_release = None
        fake_popup = MagicMock()
        controller._popup = fake_popup
        monkeypatch.setattr(mod, "save_state", MagicMock())
        monkeypatch.setattr(mod, "load_state", lambda: UpdateState())

        controller._on_ignore(MagicMock())

        fake_popup.hide.assert_called_once()

    def test_on_popup_destroy_notifies(self, monkeypatch):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)

        visibility_calls: list[tuple[str, bool]] = []
        controller._visibility_changed = lambda src, vis: visibility_calls.append(
            (src, vis)
        )

        controller._on_popup_destroy(MagicMock())

        assert visibility_calls == [("updates", False)]

    def test_notify_visible_no_callback(self, controller):
        # Should not raise
        controller._notify_visible(True)
        controller._notify_visible(False)

    def test_notify_visible_with_callback(self, controller):
        calls: list[tuple[str, bool]] = []
        controller._visibility_changed = lambda src, vis: calls.append((src, vis))

        controller._notify_visible(True)
        controller._notify_visible(False)

        assert calls == [("updates", True), ("updates", False)]


# ---------------------------------------------------------------------------
# Run check in thread (real thread)
# ---------------------------------------------------------------------------


class TestUpdateCheckRunCheckInThread:
    def test_run_check_in_thread_starts_thread(self, monkeypatch):
        """Cover the real _run_check_in_thread path."""
        import threading

        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)

        # Prevent the worker from doing anything
        worker_calls: list[dict] = []

        def _fake_worker(*, automatic):
            worker_calls.append({"automatic": automatic})

        controller._check_worker = _fake_worker

        # Let the thread start but run immediately (synchronously)
        original_thread = threading.Thread

        class _FakeThread(original_thread):
            def start(self):
                # Run synchronously instead of in a real thread
                self.run()

        monkeypatch.setattr(threading, "Thread", _FakeThread)

        controller._run_check_in_thread(automatic=False)

        assert len(worker_calls) == 1
        assert worker_calls[0] == {"automatic": False}


# ---------------------------------------------------------------------------
# Position popup
# ---------------------------------------------------------------------------


class TestUpdateCheckPositionPopup:
    def test_position_popup_no_popup_returns_early(self):
        window = MagicMock()
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)
        controller._popup = None

        # Should not raise
        controller._position_popup()

    def test_position_popup_computes_and_moves(self, monkeypatch):
        import docking.ui.update_popup as mod

        window = _FakeWindow(realized=True, pos=Position.BOTTOM)
        config = MagicMock()
        controller = UpdateCheckController(window=window, config=config)

        popup = _FakePopup()
        controller._popup = popup

        # Stub the position helpers
        monkeypatch.setattr(
            mod,
            "window_screen_position",
            lambda w: type("Pos", (), {"x": 100, "y": 200})(),
        )
        monkeypatch.setattr(
            mod,
            "compute_tooltip_position",
            lambda **kw: (150, 175),
        )
        monkeypatch.setattr(
            mod,
            "clamp_popup",
            lambda popup, x, y, w, h: type("Clamped", (), {"x": x, "y": y})(),
        )

        controller._position_popup()

        assert popup.moved_to == (150, 175)
