"""Tests for the update popup controller."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.core.position import Position
from docking.core.updates import ReleaseInfo, UpdateState
from docking.ui.popup import PopupAnchor
from docking.ui.update_popup import (
    REMIND_LATER_HOURS,
    UPDATE_CHECK_DELAY_S,
    UPDATE_POPUP_GAP_PX,
    UpdateCheckController,
)


def _anchor_provider(anchor: PopupAnchor | None = None):
    provider = MagicMock()
    provider.popup_anchor.return_value = anchor or PopupAnchor(
        x=100,
        y=200,
        position=Position.BOTTOM,
        parent=MagicMock(),
    )
    return provider


class TestUpdateCheckConstants:
    def test_constants_are_reasonable(self):
        assert UPDATE_CHECK_DELAY_S > 0
        assert UPDATE_POPUP_GAP_PX > 0
        assert REMIND_LATER_HOURS > 0


class TestUpdateCheckControllerInit:
    def test_init_sets_anchor_provider_and_config(self):
        anchor_provider = _anchor_provider()
        config = MagicMock()
        controller = UpdateCheckController(
            config=config,
            anchor_provider=anchor_provider,
        )
        assert controller._anchor_provider is anchor_provider
        assert controller._config is config
        assert controller._popup is None
        assert controller._latest_release is None
        assert controller._start_source_id == 0


class TestUpdateCheckControllerStart:
    def test_start_already_scheduled_returns(self):
        config = MagicMock()
        config.update_check_enabled = True
        controller = UpdateCheckController(
            config=config, anchor_provider=_anchor_provider()
        )
        controller._start_source_id = 42
        controller.start()
        assert controller._start_source_id == 42

    def test_start_disabled_returns(self):
        config = MagicMock()
        config.update_check_enabled = False
        controller = UpdateCheckController(
            config=config, anchor_provider=_anchor_provider()
        )
        controller.start()
        assert controller._start_source_id == 0

    def test_start_schedules_timeout(self, monkeypatch):
        import docking.ui.update_popup as mod

        config = MagicMock()
        config.update_check_enabled = True
        config.update_check_interval_hours = 24
        controller = UpdateCheckController(
            config=config, anchor_provider=_anchor_provider()
        )

        monkeypatch.setattr(
            mod, "load_state", lambda: SimpleNamespace(last_checked_at=None)
        )
        monkeypatch.setattr(mod, "should_check_for_updates", lambda **kw: True)
        monkeypatch.setattr(mod.GLib, "timeout_add_seconds", lambda delay, cb, *a: 99)

        controller.start()

        assert controller._start_source_id == 99


class TestUpdateCheckHideAndDismiss:
    def test_hide_popup_hides_window(self):
        config = MagicMock()
        controller = UpdateCheckController(
            config=config, anchor_provider=_anchor_provider()
        )
        fake_popup = MagicMock()
        controller._popup = fake_popup

        controller._hide_popup()

        fake_popup.hide.assert_called_once()

    def test_hide_popup_no_popup_no_error(self):
        config = MagicMock()
        controller = UpdateCheckController(
            config=config, anchor_provider=_anchor_provider()
        )
        controller._popup = None
        controller._hide_popup()  # Should not raise

    def test_open_url_calls_gio_launch(self, monkeypatch):
        import docking.ui.update_popup as mod

        config = MagicMock()
        controller = UpdateCheckController(
            config=config, anchor_provider=_anchor_provider()
        )
        monkeypatch.setattr(mod.Gio.AppInfo, "launch_default_for_uri", MagicMock())

        controller._open_url("https://example.com")

        mod.Gio.AppInfo.launch_default_for_uri.assert_called_once_with(
            "https://example.com", None
        )

    def test_on_view_release_hides_and_opens_url(self, monkeypatch):
        import docking.ui.update_popup as mod

        config = MagicMock()
        controller = UpdateCheckController(
            config=config, anchor_provider=_anchor_provider()
        )
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

    def test_on_later_saves_reminder_and_hides(self, monkeypatch):
        import docking.ui.update_popup as mod

        config = MagicMock()
        controller = UpdateCheckController(
            config=config, anchor_provider=_anchor_provider()
        )
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

    def test_on_ignore_saves_and_hides(self, monkeypatch):
        import docking.ui.update_popup as mod

        config = MagicMock()
        controller = UpdateCheckController(
            config=config, anchor_provider=_anchor_provider()
        )
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

    def test_on_check_finished_no_release_no_popup(self, monkeypatch):
        import docking.ui.update_popup as mod

        config = MagicMock()
        controller = UpdateCheckController(
            config=config, anchor_provider=_anchor_provider()
        )
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
        assert len(save_called) == 1  # State is always saved
