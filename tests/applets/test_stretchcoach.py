"""Tests for the Stretch Coach applet."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.stretchcoach.applet as stretchcoach_applet_mod
import docking.applets.stretchcoach.state as stretchcoach_state_mod
from docking.applets.stretchcoach import (
    DEFAULT_INTERVAL,
    StretchCard,
    StretchCoachApplet,
    StretchCoachState,
    acknowledge_reminder,
    load_cards,
    set_cards_enabled,
    set_interval,
    show_preview_card,
    state_from_prefs,
    tick,
    tooltip_text,
    trigger_reminder,
)
from docking.applets.stretchcoach.render import render_icon
from docking.core.config import Config

CARD = StretchCard(
    title="Shoulder Roll",
    steps=("Roll forward.", "Roll backward."),
)


class TestStateHelpers:
    def test_state_from_prefs_defaults(self):
        state = state_from_prefs(None)
        assert state.interval_min == DEFAULT_INTERVAL
        assert state.remaining == DEFAULT_INTERVAL * 60
        assert state.cards_enabled is True

    def test_state_from_prefs_applies_values(self):
        state = state_from_prefs({"interval": 45, "cards_enabled": False})
        assert state.interval_min == 45
        assert state.remaining == 45 * 60
        assert state.cards_enabled is False

    def test_trigger_reminder_sets_due_and_card(self):
        state = trigger_reminder(
            StretchCoachState(),
            cards=[CARD],
            chooser=lambda cards: cards[0],
        )
        assert state.due is True
        assert state.remaining == 0
        assert state.active_card == CARD

    def test_trigger_reminder_omits_card_when_disabled(self):
        state = set_cards_enabled(StretchCoachState(), False)
        state = trigger_reminder(state, cards=[CARD], chooser=lambda cards: cards[0])
        assert state.active_card is None

    def test_acknowledge_resets_interval(self):
        state = trigger_reminder(StretchCoachState(interval_min=15), cards=[CARD])
        reset = acknowledge_reminder(state)
        assert reset.due is False
        assert reset.remaining == 15 * 60
        assert reset.active_card is None

    def test_show_preview_card_keeps_timer_running(self):
        state = StretchCoachState(remaining=600)
        previewed = show_preview_card(
            state,
            cards=[CARD],
            chooser=lambda cards: cards[0],
        )
        assert previewed.remaining == 600
        assert previewed.preview_card == CARD

    def test_set_interval_resets_countdown_when_idle(self):
        state = StretchCoachState(interval_min=30, remaining=120)
        updated = set_interval(state, 60)
        assert updated.interval_min == 60
        assert updated.remaining == 60 * 60

    def test_tick_becomes_due(self):
        result = tick(
            StretchCoachState(remaining=1),
            cards=[CARD],
            chooser=lambda cards: cards[0],
        )
        assert result.became_due is True
        assert result.should_refresh is True
        assert result.state.due is True
        assert result.state.active_card == CARD

    def test_tick_refreshes_every_ten_seconds(self):
        result = tick(StretchCoachState(remaining=21), cards=[CARD])
        assert result.state.remaining == 20
        assert result.should_refresh is True

    def test_tick_noop_when_due(self):
        result = tick(StretchCoachState(due=True), cards=[CARD])
        assert result.state.due is True
        assert result.should_refresh is False


class TestTooltipText:
    def test_idle(self):
        text = tooltip_text(StretchCoachState(remaining=15 * 60))
        assert text == "Next stretch in 15:00"

    def test_due_without_card(self):
        assert (
            tooltip_text(StretchCoachState(due=True, remaining=0)) == "Time to stretch!"
        )

    def test_due_with_card(self):
        text = tooltip_text(StretchCoachState(due=True, active_card=CARD))
        assert "Time to stretch!" in text
        assert "Shoulder Roll" in text
        assert "Roll forward." in text

    def test_preview_card_appends_to_idle_tooltip(self):
        text = tooltip_text(StretchCoachState(preview_card=CARD))
        assert "Next stretch in 30:00" in text
        assert "Shoulder Roll" in text


class TestLoadCards:
    def test_load_cards_reads_valid_json(self, monkeypatch, tmp_path):
        asset_dir = tmp_path / "stretch"
        asset_dir.mkdir()
        payload = asset_dir / "cards.json"
        payload.write_text(
            '[{"title": "Desk Twist", "steps": ["Sit tall.", "Rotate gently."]}]',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            stretchcoach_state_mod.resources,
            "files",
            lambda _package: tmp_path,
        )
        monkeypatch.setattr(
            stretchcoach_state_mod.resources,
            "as_file",
            lambda path: nullcontext(path),
        )

        cards = load_cards()

        assert cards == [
            StretchCard(title="Desk Twist", steps=("Sit tall.", "Rotate gently."))
        ]

    def test_load_cards_falls_back_on_missing_asset(self, monkeypatch):
        def _raise(_package):
            raise FileNotFoundError("missing")

        monkeypatch.setattr(stretchcoach_state_mod.resources, "files", _raise)
        cards = load_cards()
        assert cards
        assert cards[0].title == "Neck Reset"

    def test_load_cards_falls_back_on_invalid_json(self, monkeypatch, tmp_path):
        asset_dir = tmp_path / "stretch"
        asset_dir.mkdir()
        payload = asset_dir / "cards.json"
        payload.write_text("{broken", encoding="utf-8")
        monkeypatch.setattr(
            stretchcoach_state_mod.resources,
            "files",
            lambda _package: tmp_path,
        )
        monkeypatch.setattr(
            stretchcoach_state_mod.resources,
            "as_file",
            lambda path: nullcontext(path),
        )

        cards = load_cards()

        assert cards
        assert cards[0].title == "Neck Reset"


class TestRenderIcon:
    def test_render_icon_returns_pixbuf(self):
        pixbuf = render_icon(size=48, state=StretchCoachState())
        assert pixbuf is not None
        assert pixbuf.get_width() == 48
        assert pixbuf.get_height() == 48


class TestStretchCoachApplet:
    def test_creates_with_icon(self, monkeypatch):
        monkeypatch.setattr(stretchcoach_applet_mod, "load_cards", lambda: [CARD])
        applet = StretchCoachApplet(48)
        assert applet.item.icon is not None
        assert applet.item.name == "Next stretch in 30:00"

    def test_click_triggers_then_acknowledges_break(self, monkeypatch):
        monkeypatch.setattr(stretchcoach_applet_mod, "load_cards", lambda: [CARD])
        applet = StretchCoachApplet(48)

        applet.on_clicked()
        assert applet._state.due is True
        assert applet.item.is_urgent is True
        assert applet._state.active_card == CARD

        applet.on_clicked()
        assert applet._state.due is False
        assert applet.item.is_urgent is False
        assert applet._state.remaining == DEFAULT_INTERVAL * 60

    def test_menu_contains_core_actions(self, monkeypatch):
        monkeypatch.setattr(stretchcoach_applet_mod, "load_cards", lambda: [CARD])
        applet = StretchCoachApplet(48)
        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]
        assert "Take Break Now" in labels
        assert "Show Random Stretch" in labels
        assert "Random Stretch Cards" in labels
        assert "30 min" in labels

    def test_due_menu_uses_acknowledge_label(self, monkeypatch):
        monkeypatch.setattr(stretchcoach_applet_mod, "load_cards", lambda: [CARD])
        applet = StretchCoachApplet(48)
        applet._state = trigger_reminder(
            applet._state,
            cards=[CARD],
            chooser=lambda cards: cards[0],
        )
        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]
        assert "Acknowledge Break" in labels

    def test_show_random_stretch_updates_tooltip(self, monkeypatch):
        monkeypatch.setattr(stretchcoach_applet_mod, "load_cards", lambda: [CARD])
        applet = StretchCoachApplet(48)
        applet._show_random_stretch()
        assert "Shoulder Roll" in applet.item.name

    def test_toggle_cards_updates_state_and_saves(self, monkeypatch):
        monkeypatch.setattr(stretchcoach_applet_mod, "load_cards", lambda: [CARD])
        applet = StretchCoachApplet(48)
        widget = MagicMock()
        widget.get_active.return_value = False
        applet._save = MagicMock()
        applet.present = MagicMock()

        applet._on_toggle_cards(widget)

        assert applet._state.cards_enabled is False
        applet._save.assert_called_once()
        applet.present.assert_called_once()

    def test_set_interval_saves_and_refreshes(self, monkeypatch):
        monkeypatch.setattr(stretchcoach_applet_mod, "load_cards", lambda: [CARD])
        applet = StretchCoachApplet(48)
        applet._save = MagicMock()
        applet.present = MagicMock()

        applet._set_interval(45)

        assert applet._state.interval_min == 45
        assert applet._state.remaining == 45 * 60
        applet._save.assert_called_once()
        applet.present.assert_called_once()

    def test_tick_due_branch_marks_urgent(self, monkeypatch):
        monkeypatch.setattr(stretchcoach_applet_mod, "load_cards", lambda: [CARD])
        applet = StretchCoachApplet(48)
        applet._state = StretchCoachState(remaining=1)
        applet.present = MagicMock()

        assert applet._tick() is True
        assert applet._state.due is True
        assert applet.item.is_urgent is True
        assert applet.present.call_count == 1

    def test_start_and_stop_manage_timer(self, monkeypatch):
        monkeypatch.setattr(stretchcoach_applet_mod, "load_cards", lambda: [CARD])
        applet = StretchCoachApplet(48)
        monkeypatch.setattr(
            stretchcoach_applet_mod.GLib,
            "timeout_add_seconds",
            lambda _seconds, _callback: 818,
        )
        removed: list[int] = []
        monkeypatch.setattr(
            stretchcoach_applet_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )

        applet.start(lambda: None)
        assert applet._timer_id == 818

        applet.stop()
        assert removed == [818]
        assert applet._timer_id == 0

    def test_prefs_persist_to_config(self, monkeypatch, tmp_path):
        monkeypatch.setattr(stretchcoach_applet_mod, "load_cards", lambda: [CARD])
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        applet = StretchCoachApplet(48, config=config)

        applet._state = set_interval(applet._state, 45)
        applet._state = set_cards_enabled(applet._state, False)
        applet._save()

        reloaded = Config.load(path)
        assert reloaded.applet_prefs["stretchcoach"] == {
            "interval": 45,
            "cards_enabled": False,
        }


class TestStretchCoachAppletBranches:
    def test_tick_refresh_branch_updates_presentation(self, monkeypatch):
        monkeypatch.setattr(stretchcoach_applet_mod, "load_cards", lambda: [CARD])
        applet = StretchCoachApplet(48)
        applet.present = MagicMock()
        monkeypatch.setattr(
            stretchcoach_applet_mod,
            "tick",
            lambda state, cards: SimpleNamespace(
                state=state,
                became_due=False,
                should_refresh=True,
            ),
        )

        assert applet._tick() is True
        applet.present.assert_called_once()

    def test_tick_tooltip_only_branch_notifies_without_icon_refresh(self, monkeypatch):
        monkeypatch.setattr(stretchcoach_applet_mod, "load_cards", lambda: [CARD])
        applet = StretchCoachApplet(48)
        applet.present = MagicMock()
        applet._notify = MagicMock()
        monkeypatch.setattr(
            stretchcoach_applet_mod,
            "tick",
            lambda state, cards: SimpleNamespace(
                state=StretchCoachState(remaining=1799),
                became_due=False,
                should_refresh=False,
            ),
        )

        assert applet._tick() is True
        assert applet.item.name == "Next stretch in 29:59"
        applet.present.assert_not_called()
        applet._notify.assert_called_once()
