"""Tests for the AI usage tracker applet."""

from __future__ import annotations

import datetime
import json
import logging
import os
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.aiusage.applet as aiusage_mod
import docking.applets.aiusage.render as aiusage_render_mod
from docking.applets.aiusage.applet import AiUsageApplet
from docking.applets.aiusage.state import (
    AiUsageState,
    DayEntry,
    DisplayMode,
    ModelUsage,
    Provider,
    cost_for_usage,
    day_cost,
    dominant_provider,
    match_model_tier,
    parse_claude_transcript,
    parse_codex_transcript,
    prefs_from_state,
    provider_for_model,
    query_codex_today,
    reset_today,
    set_session,
    state_from_prefs,
    tooltip_text,
)
from docking.core.config import Config


class _FakeBox:
    def __init__(self) -> None:
        self.children: list[object] = []

    def pack_start(self, child, *_args) -> None:
        self.children.append(child)


class _FakeLabel:
    def __init__(self, label: str = "") -> None:
        self.label = label
        self.markup = ""

    def override_color(self, *_args) -> None:
        return

    def set_markup(self, markup: str) -> None:
        self.markup = markup
        self.label = markup

    def set_xalign(self, _value: float) -> None:
        return


def _patch_tooltip_widgets(monkeypatch) -> None:
    monkeypatch.setattr(aiusage_mod.Gtk, "Box", lambda **_kwargs: _FakeBox())
    monkeypatch.setattr(aiusage_mod.Gtk, "Label", _FakeLabel)
    monkeypatch.setattr(aiusage_mod.Gdk, "RGBA", lambda *_args: None)
    monkeypatch.setattr(aiusage_mod.GLib, "markup_escape_text", lambda text: text)


# ---------------------------------------------------------------
# State basics
# ---------------------------------------------------------------


class TestState:
    def test_empty_prefs_yields_empty_state(self):
        state = state_from_prefs(prefs=None)
        assert state.days == ()

    def test_empty_dict_yields_empty_state(self):
        state = state_from_prefs(prefs={})
        assert state.days == ()

    def test_round_trip(self):
        usage = ModelUsage(
            input_tokens=100,
            output_tokens=50,
            cache_write_tokens=10,
            cache_read_tokens=200,
        )
        state = AiUsageState(
            days=(DayEntry(date="2026-03-26", sessions=3, by_model=(("opus", usage),)),)
        )
        restored = state_from_prefs(prefs=prefs_from_state(state=state))
        assert restored.days[0].date == "2026-03-26"
        assert restored.days[0].sessions == 3
        _, u = restored.days[0].by_model[0]
        assert u.input_tokens == 100
        assert u.cache_read_tokens == 200

    def test_add_session_creates_today(self):
        state = AiUsageState()
        usage = {"claude-opus-4": ModelUsage(input_tokens=1000, output_tokens=500)}
        result = set_session(session_id="test", state=state, model_usage=usage)
        assert len(result.days) == 1
        assert result.days[0].sessions == 1

    def test_set_session_accumulates_different_ids(self):
        state = AiUsageState()
        usage = {"claude-opus-4": ModelUsage(input_tokens=1000)}
        state = set_session(session_id="s1", state=state, model_usage=usage)
        state = set_session(session_id="s2", state=state, model_usage=usage)
        assert state.days[0].sessions == 2
        _, u = state.days[0].by_model[0]
        assert u.input_tokens == 2000

    def test_set_session_replaces_same_id(self):
        state = AiUsageState()
        state = set_session(
            session_id="s1",
            state=state,
            model_usage={"claude-opus-4": ModelUsage(input_tokens=1000)},
        )
        state = set_session(
            session_id="s1",
            state=state,
            model_usage={"claude-opus-4": ModelUsage(input_tokens=2000)},
        )
        assert state.days[0].sessions == 1
        _, u = state.days[0].by_model[0]
        assert u.input_tokens == 2000

    def test_add_session_caps_at_7_days(self):
        days = tuple(DayEntry(date=f"2026-03-{i:02d}", sessions=1) for i in range(1, 8))
        state = AiUsageState(days=days)
        usage = {"claude-opus-4": ModelUsage(input_tokens=100)}
        result = set_session(session_id="test", state=state, model_usage=usage)
        assert len(result.days) == 7

    def test_reset_today(self):
        state = set_session(
            session_id="test",
            state=AiUsageState(),
            model_usage={"m": ModelUsage(input_tokens=1)},
        )
        assert reset_today(state=state).days == ()


# ---------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------


class TestProvider:
    def test_claude_model(self):
        assert provider_for_model(model="claude-opus-4-6") == Provider.CLAUDE

    def test_codex_model(self):
        assert provider_for_model(model="gpt-5.4") == Provider.CODEX

    def test_gpt_prefix(self):
        assert provider_for_model(model="gpt-4o") == Provider.CODEX

    def test_dominant_provider_claude_only(self):
        state = set_session(
            session_id="test",
            state=AiUsageState(),
            model_usage={"claude-opus-4-6": ModelUsage(input_tokens=1000)},
        )
        assert dominant_provider(state=state) == Provider.CLAUDE

    def test_dominant_provider_codex_only(self):
        state = set_session(
            session_id="test",
            state=AiUsageState(),
            model_usage={"gpt-5.4": ModelUsage(input_tokens=1000)},
        )
        assert dominant_provider(state=state) == Provider.CODEX

    def test_dominant_provider_none_when_empty(self):
        assert dominant_provider(state=AiUsageState()) is None


# ---------------------------------------------------------------
# Cost calculation - Claude
# ---------------------------------------------------------------


class TestClaudeCost:
    def test_match_opus_4_6(self):
        assert match_model_tier(model="claude-opus-4-6") == "opus-4-6"

    def test_match_sonnet(self):
        assert match_model_tier(model="claude-sonnet-4-20250514") == "sonnet"

    def test_cost_for_opus_4_6(self):
        usage = ModelUsage(input_tokens=1_000_000)
        assert cost_for_usage(model="claude-opus-4-6", usage=usage) == 5.0

    def test_cost_for_opus_4_legacy(self):
        usage = ModelUsage(input_tokens=1_000_000)
        assert cost_for_usage(model="claude-opus-4-20250514", usage=usage) == 15.0

    def test_claude_cache_cost(self):
        usage = ModelUsage(cache_write_tokens=1_000_000, cache_read_tokens=1_000_000)
        cost = cost_for_usage(model="claude-opus-4-6", usage=usage)
        assert cost == 6.25 + 0.50

    def test_unknown_model_zero(self):
        usage = ModelUsage(input_tokens=1_000_000)
        assert cost_for_usage(model="unknown-model", usage=usage) == 0.0


# ---------------------------------------------------------------
# Cost calculation - Codex
# ---------------------------------------------------------------


class TestCodexCost:
    def test_match_gpt_5(self):
        assert match_model_tier(model="gpt-5.4") == "gpt-5"

    def test_match_gpt_4o(self):
        assert match_model_tier(model="gpt-4o") == "gpt-4o"

    def test_cost_gpt5_no_cache(self):
        # 1M input, no cache -> $2.50
        usage = ModelUsage(input_tokens=1_000_000, output_tokens=0)
        assert cost_for_usage(model="gpt-5.4", usage=usage) == 2.50

    def test_cost_gpt5_with_cache(self):
        # 1M input, 500K cached -> 500K * $2.50 + 500K * $0.62 + 0 output
        usage = ModelUsage(input_tokens=1_000_000, cache_read_tokens=500_000)
        cost = cost_for_usage(model="gpt-5.4", usage=usage)
        expected = (500_000 * 2.50 + 500_000 * 0.62) / 1_000_000
        assert abs(cost - expected) < 0.001

    def test_cost_gpt5_output(self):
        usage = ModelUsage(output_tokens=1_000_000)
        assert cost_for_usage(model="gpt-5.4", usage=usage) == 10.0

    def test_day_cost_mixed_providers(self):
        entry = DayEntry(
            date="2026-03-26",
            sessions=2,
            by_model=(
                ("claude-opus-4-6", ModelUsage(input_tokens=1_000_000)),
                ("gpt-5.4", ModelUsage(input_tokens=1_000_000)),
            ),
        )
        assert day_cost(entry=entry) == 5.0 + 2.50


# ---------------------------------------------------------------
# Tooltip
# ---------------------------------------------------------------


class TestTooltip:
    def test_no_usage(self):
        assert "no usage" in tooltip_text(state=AiUsageState())

    def test_with_sessions(self):
        state = set_session(
            session_id="test",
            state=AiUsageState(),
            model_usage={"claude-opus-4": ModelUsage(input_tokens=1000)},
        )
        text = tooltip_text(state=state)
        assert "$" in text


# ---------------------------------------------------------------
# Claude transcript parsing
# ---------------------------------------------------------------


class TestClaudeTranscript:
    def test_parses_valid_jsonl(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-opus-4-20250514",
                        "usage": {"input_tokens": 100, "output_tokens": 50},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-opus-4-20250514",
                        "usage": {"input_tokens": 300, "output_tokens": 100},
                    },
                }
            ),
        ]
        jsonl.write_text("\n".join(lines))
        result = parse_claude_transcript(path=jsonl)
        assert result["claude-opus-4-20250514"].input_tokens == 400

    def test_missing_file(self, tmp_path):
        assert parse_claude_transcript(path=tmp_path / "x.jsonl") == {}

    def test_ignores_non_assistant(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        jsonl.write_text(json.dumps({"type": "user", "message": {"role": "user"}}))
        assert parse_claude_transcript(path=jsonl) == {}


# ---------------------------------------------------------------
# Codex transcript parsing
# ---------------------------------------------------------------


class TestCodexTranscript:
    def test_parses_valid_session(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        lines = [
            json.dumps(
                {
                    "type": "turn_context",
                    "payload": {"model": "gpt-5.4"},
                }
            ),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 1000,
                                "cached_input_tokens": 500,
                                "output_tokens": 200,
                                "total_tokens": 1200,
                            }
                        },
                    },
                }
            ),
        ]
        jsonl.write_text("\n".join(lines))
        result = parse_codex_transcript(path=jsonl)
        assert "gpt-5.4" in result
        u = result["gpt-5.4"]
        assert u.input_tokens == 1000
        assert u.cache_read_tokens == 500
        assert u.output_tokens == 200

    def test_takes_highest_total(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        lines = [
            json.dumps({"type": "turn_context", "payload": {"model": "gpt-5.4"}}),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 100,
                                "cached_input_tokens": 0,
                                "output_tokens": 50,
                                "total_tokens": 150,
                            }
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 500,
                                "cached_input_tokens": 200,
                                "output_tokens": 300,
                                "total_tokens": 800,
                            }
                        },
                    },
                }
            ),
        ]
        jsonl.write_text("\n".join(lines))
        result = parse_codex_transcript(path=jsonl)
        assert result["gpt-5.4"].input_tokens == 500

    def test_missing_file(self, tmp_path):
        assert parse_codex_transcript(path=tmp_path / "x.jsonl") == {}

    def test_no_model_returns_empty(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        jsonl.write_text(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"total_tokens": 100}},
                    },
                }
            )
        )
        assert parse_codex_transcript(path=jsonl) == {}

    def test_query_codex_today_reads_recent_sessions(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / ".codex" / "sessions" / "2026" / "04" / "29"
        sessions_dir.mkdir(parents=True)
        jsonl = sessions_dir / "rollout-2026-04-29T08-00-00-thread-1.jsonl"
        jsonl.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {"id": "thread-1"},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "turn_context",
                            "payload": {"model": "gpt-5.5"},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "total_token_usage": {
                                        "input_tokens": 1200,
                                        "cached_input_tokens": 200,
                                        "output_tokens": 50,
                                        "total_tokens": 1250,
                                    }
                                },
                            },
                        }
                    ),
                ]
            )
        )
        today_ts = datetime.datetime.fromisoformat("2026-04-29T12:00:00").timestamp()
        os.utime(jsonl, (today_ts, today_ts))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(
            "docking.applets.aiusage.state._today_iso",
            lambda: "2026-04-29",
        )

        result = query_codex_today()

        assert result["thread-1"]["gpt-5.5"].input_tokens == 1200


# ---------------------------------------------------------------
# Hook
# ---------------------------------------------------------------


class TestHook:
    def test_claude_stop_updates_config(self, tmp_path, monkeypatch):
        jsonl = tmp_path / "session.jsonl"
        jsonl.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-opus-4",
                        "usage": {"input_tokens": 1000, "output_tokens": 500},
                    },
                }
            )
        )

        config_path = tmp_path / "dock.json"
        config_path.write_text("{}")

        from docking.applets.aiusage import hook

        monkeypatch.setattr(hook, "_config_path", lambda: config_path)
        hook._handle_claude_stop(data={"transcript_path": str(jsonl)})

        config = json.loads(config_path.read_text())
        prefs = config["applet_prefs"]["aiusage"]
        assert len(prefs["days"]) == 1
        assert prefs["days"][0]["sessions"] == 1

    def test_codex_turn_updates_config(self, tmp_path, monkeypatch):
        jsonl = tmp_path / "session.jsonl"
        lines = [
            json.dumps({"type": "turn_context", "payload": {"model": "gpt-5.4"}}),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 500,
                                "cached_input_tokens": 100,
                                "output_tokens": 200,
                                "total_tokens": 700,
                            }
                        },
                    },
                }
            ),
        ]
        jsonl.write_text("\n".join(lines))

        config_path = tmp_path / "dock.json"
        config_path.write_text("{}")

        from docking.applets.aiusage import hook
        from docking.applets.aiusage import state as state_mod

        monkeypatch.setattr(hook, "_config_path", lambda: config_path)
        monkeypatch.setattr(state_mod, "find_codex_session", lambda thread_id: jsonl)

        hook._handle_codex_turn(json_arg=json.dumps({"thread-id": "test"}))

        config = json.loads(config_path.read_text())
        prefs = config["applet_prefs"]["aiusage"]
        assert "gpt-5.4" in prefs["days"][0]["by_model"]

    def test_no_transcript_path_is_noop(self, tmp_path, monkeypatch):
        config_path = tmp_path / "dock.json"
        config_path.write_text("{}")

        from docking.applets.aiusage import hook

        monkeypatch.setattr(hook, "_config_path", lambda: config_path)
        hook._handle_claude_stop(data={})
        assert json.loads(config_path.read_text()) == {}


# ---------------------------------------------------------------
# Applet
# ---------------------------------------------------------------


class TestAiUsageApplet:
    def test_loads_legacy_config_prefs(self):
        state = set_session(
            session_id="legacy",
            state=AiUsageState(),
            model_usage={"claude-opus-4-6": ModelUsage(input_tokens=123)},
        )
        config = Config(applet_prefs={"claude": prefs_from_state(state=state)})

        applet = AiUsageApplet(48, config=config)

        assert applet._state.days[0].sessions == 1

    def test_creates_with_icon(self):
        applet = AiUsageApplet(48)
        assert applet.item.icon is not None

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = AiUsageApplet(size)
            pixbuf = applet.create_icon(size=size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_tooltip_no_usage(self):
        applet = AiUsageApplet(48)
        assert "no usage" in applet.item.name

    def test_create_icon_forwards_selected_provider_and_display_mode(self, monkeypatch):
        applet = AiUsageApplet(48)
        applet._selected_provider = Provider.CODEX
        applet._display_mode = DisplayMode.TOKENS
        render_icon = MagicMock(return_value="pixbuf")
        monkeypatch.setattr(aiusage_mod, "render_icon", render_icon)

        assert applet.create_icon(size=64) == "pixbuf"
        render_icon.assert_called_once_with(
            size=64,
            state=applet._state,
            selected_provider=Provider.CODEX,
            display_mode=DisplayMode.TOKENS,
        )

    def test_start_and_stop_manage_timer(self, monkeypatch):
        applet = AiUsageApplet(48)
        monkeypatch.setattr(
            aiusage_mod.GLib, "timeout_add_seconds", lambda _s, _cb: 999
        )
        removed = []
        monkeypatch.setattr(
            aiusage_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )
        monkeypatch.setattr(aiusage_mod, "_register_claude_hooks", lambda: None)
        monkeypatch.setattr(aiusage_mod, "_register_codex_hook", lambda: None)

        applet.start(lambda: None)
        assert applet._timer_id == 999

        applet.stop()
        assert removed == [999]
        assert applet._timer_id == 0

    def test_menu_has_reset(self):
        applet = AiUsageApplet(48)
        labels = [mi.get_label() for mi in applet.get_menu_items()]
        assert "Reset Today" in labels

    def test_tooltip_widget_builds_headless_box(self, monkeypatch):
        applet = AiUsageApplet(48)
        _patch_tooltip_widgets(monkeypatch)

        box = applet._build_tooltip_widget()

        assert isinstance(box, _FakeBox)
        assert len(box.children) == 1
        assert box.children[0].label == "AI Usage: no usage today"

    def test_tooltip_widget_renders_token_breakdown_and_week_total(self, monkeypatch):
        applet = AiUsageApplet(48)
        _patch_tooltip_widgets(monkeypatch)
        today = set_session(
            session_id="today",
            state=AiUsageState(),
            model_usage={
                "claude-opus-4-6": ModelUsage(input_tokens=1_200, output_tokens=300)
            },
        ).days[0]
        week_entry = DayEntry(
            date="2026-03-20",
            sessions=1,
            by_model=(("claude-opus-4-6", ModelUsage(input_tokens=2_400)),),
        )
        applet._state = AiUsageState(days=(today, week_entry))
        applet._display_mode = DisplayMode.TOKENS

        box = applet._build_tooltip_widget()

        labels = [child.label for child in box.children]
        assert "<b>Today: 1.5K</b>" in labels[0]
        assert "Opus-4-6: 1.5K" in labels[1]
        assert "This week: 3.9K" in labels[2]

    def test_tooltip_widget_filters_selected_provider_costs(self, monkeypatch):
        applet = AiUsageApplet(48)
        _patch_tooltip_widgets(monkeypatch)
        applet._selected_provider = Provider.CODEX
        applet._state = set_session(
            session_id="mix",
            state=AiUsageState(),
            model_usage={
                "claude-opus-4-6": ModelUsage(input_tokens=1_000_000),
                "gpt-5.4": ModelUsage(input_tokens=1_000_000),
            },
        )

        box = applet._build_tooltip_widget()

        labels = [child.label for child in box.children]
        assert "<b>Codex: $2.50</b>" in labels[0]
        assert "Gpt-5: $2.50" in labels[1]

    def test_on_scroll_cycles_providers_and_presents(self, monkeypatch):
        applet = AiUsageApplet(48)
        applet.present = MagicMock()

        applet.on_scroll(direction_up=True)
        applet.on_scroll(direction_up=True)
        applet.on_scroll(direction_up=False)

        assert applet._selected_provider == Provider.CLAUDE
        assert applet.present.call_count == 3

    def test_set_provider_and_display_mode_present(self):
        applet = AiUsageApplet(48)
        applet.present = MagicMock()

        applet._set_provider(provider=Provider.OPENCODE)
        applet._set_display_mode(mode=DisplayMode.TOKENS)

        assert applet._selected_provider == Provider.OPENCODE
        assert applet._display_mode == DisplayMode.TOKENS
        assert applet.present.call_count == 2

    def test_tick_merges_opencode_sessions_and_updates_state(self, monkeypatch):
        applet = AiUsageApplet(48)
        applet.present = MagicMock()
        monkeypatch.setattr(aiusage_mod, "_read_prefs_from_disk", lambda: None)
        monkeypatch.setattr(aiusage_mod, "query_codex_today", dict)
        monkeypatch.setattr(
            aiusage_mod,
            "query_opencode_today",
            lambda: {
                "abc": {
                    "opencode:gpt-oss": ModelUsage(
                        input_tokens=10,
                        output_tokens=5,
                        precalculated_cost=1.25,
                    )
                }
            },
        )

        assert applet._tick() is True
        assert applet._opencode_poll_error is None
        assert applet._state.days[0].sessions == 1
        assert applet.present.call_count == 1

    def test_tick_merges_codex_sessions_and_updates_state(self, monkeypatch):
        applet = AiUsageApplet(48)
        applet.present = MagicMock()
        monkeypatch.setattr(aiusage_mod, "_read_prefs_from_disk", lambda: None)
        monkeypatch.setattr(aiusage_mod, "query_opencode_today", dict)
        monkeypatch.setattr(
            aiusage_mod,
            "query_codex_today",
            lambda: {
                "thread-1": {
                    "gpt-5.5": ModelUsage(
                        input_tokens=1200,
                        output_tokens=50,
                        cache_read_tokens=200,
                    )
                }
            },
        )

        assert applet._tick() is True
        assert applet._codex_poll_error is None
        assert applet._state.days[0].sessions == 1
        assert applet._state.days[0].by_model[0][0] == "gpt-5.5"
        assert applet.present.call_count == 1

    def test_reset_today_saves_prefs_and_presents(self):
        applet = AiUsageApplet(48)
        applet._state = set_session(
            session_id="test",
            state=AiUsageState(),
            model_usage={"claude-opus-4-6": ModelUsage(input_tokens=1)},
        )
        applet.save_prefs = MagicMock()
        applet.present = MagicMock()

        applet._reset_today()

        assert applet._state.days == ()
        applet.save_prefs.assert_called_once()
        applet.present.assert_called_once()

    def test_tick_warns_once_when_opencode_poll_fails(self, monkeypatch, caplog):
        applet = AiUsageApplet(48)
        monkeypatch.setattr(aiusage_mod, "_read_prefs_from_disk", lambda: None)
        monkeypatch.setattr(aiusage_mod, "query_codex_today", dict)

        def fail_query():
            raise RuntimeError("database locked")

        monkeypatch.setattr(aiusage_mod, "query_opencode_today", fail_query)

        with caplog.at_level(logging.WARNING, logger="docking.aiusage"):
            assert applet._tick() is True
            assert applet._tick() is True

        assert caplog.text.count("Failed to poll OpenCode usage") == 1


# ---------------------------------------------------------------
# Hook registration
# ---------------------------------------------------------------


class TestClaudeHookRegistration:
    def test_read_prefs_from_disk_returns_none_for_invalid_json(
        self, tmp_path, monkeypatch
    ):
        config_dir = tmp_path / "cfg"
        dock_json = config_dir / "docking" / "dock.json"
        dock_json.parent.mkdir(parents=True)
        dock_json.write_text("{")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))

        assert aiusage_mod._read_prefs_from_disk() is None

    def test_registers_hooks(self, tmp_path, monkeypatch):
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text("{}")
        monkeypatch.setattr(aiusage_mod, "_CLAUDE_SETTINGS", settings_path)

        aiusage_mod._register_claude_hooks()

        settings = json.loads(settings_path.read_text())
        assert "Stop" in settings["hooks"]
        assert "SessionStart" in settings["hooks"]

    def test_preserves_existing_hooks(self, tmp_path, monkeypatch):
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        existing = {
            "hooks": {
                "Stop": [
                    {
                        "matcher": "*",
                        "hooks": [{"type": "command", "command": "other-tool Stop"}],
                    }
                ]
            }
        }
        settings_path.write_text(json.dumps(existing))
        monkeypatch.setattr(aiusage_mod, "_CLAUDE_SETTINGS", settings_path)

        aiusage_mod._register_claude_hooks()

        settings = json.loads(settings_path.read_text())
        assert len(settings["hooks"]["Stop"]) == 2

    def test_idempotent(self, tmp_path, monkeypatch):
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text("{}")
        monkeypatch.setattr(aiusage_mod, "_CLAUDE_SETTINGS", settings_path)

        aiusage_mod._register_claude_hooks()
        aiusage_mod._register_claude_hooks()

        settings = json.loads(settings_path.read_text())
        assert len(settings["hooks"]["Stop"]) == 1

    def test_register_hooks_warns_on_invalid_json(self, tmp_path, monkeypatch):
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text("{")
        logger = SimpleNamespace(warning=MagicMock())
        monkeypatch.setattr(aiusage_mod, "_CLAUDE_SETTINGS", settings_path)
        monkeypatch.setattr(aiusage_mod.log, "bind", lambda **_kwargs: logger)

        aiusage_mod._register_claude_hooks()

        logger.warning.assert_called_once()

    def test_register_hooks_warns_on_write_error(self, tmp_path, monkeypatch):
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text("{}")
        logger = SimpleNamespace(warning=MagicMock())
        monkeypatch.setattr(aiusage_mod, "_CLAUDE_SETTINGS", settings_path)
        monkeypatch.setattr(aiusage_mod.log, "bind", lambda **_kwargs: logger)
        original_write_text = Path.write_text

        def fail_write_text(self, *args, **kwargs):
            if self == settings_path:
                raise OSError("nope")
            return original_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", fail_write_text)

        aiusage_mod._register_claude_hooks()

        logger.warning.assert_called_once()

    def test_has_hook_detects_existing_command(self):
        assert aiusage_mod._has_hook(
            entries=[{"hooks": [{"command": "prefix value"}]}],
            needle="prefix",
        )


class TestCodexHookRegistration:
    def test_registers_notify(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.toml"
        config_path.write_text('model = "gpt-5.4"\n')
        monkeypatch.setattr(aiusage_mod, "_CODEX_CONFIG", config_path)

        aiusage_mod._register_codex_hook()

        content = config_path.read_text()
        assert "docking.applets.aiusage.hook" in content
        assert "notify" in content

    def test_skips_if_codex_sync_present(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            'model = "gpt-5"\nnotify = ["codex-sync", "hook", "agent-turn-complete"]\n'
        )
        monkeypatch.setattr(aiusage_mod, "_CODEX_CONFIG", config_path)

        aiusage_mod._register_codex_hook()

        content = config_path.read_text()
        assert "codex-sync" in content
        assert "docking.applets.aiusage.hook" not in content

    def test_idempotent(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.toml"
        config_path.write_text('model = "gpt-5.4"\n')
        monkeypatch.setattr(aiusage_mod, "_CODEX_CONFIG", config_path)

        aiusage_mod._register_codex_hook()
        aiusage_mod._register_codex_hook()

        content = config_path.read_text()
        assert content.count("notify") == 1

    def test_inserts_notify_before_first_section(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[profiles]\ndefault = "x"\n')
        monkeypatch.setattr(aiusage_mod, "_CODEX_CONFIG", config_path)

        aiusage_mod._register_codex_hook()

        content = config_path.read_text()
        assert content.startswith("notify = ")
        assert "[profiles]" in content

    def test_warns_when_codex_config_cannot_be_written(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[profiles]\ndefault = "x"\n')
        logger = SimpleNamespace(warning=MagicMock(), info=MagicMock())
        monkeypatch.setattr(aiusage_mod, "_CODEX_CONFIG", config_path)
        monkeypatch.setattr(aiusage_mod.log, "bind", lambda **_kwargs: logger)
        original_write_text = Path.write_text

        def fail_write_text(self, *args, **kwargs):
            if self == config_path:
                raise OSError("denied")
            return original_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", fail_write_text)

        aiusage_mod._register_codex_hook()

        logger.warning.assert_called_once()


class TestHookCli:
    def test_update_config_recovers_from_invalid_existing_json(
        self, tmp_path, monkeypatch
    ):
        from docking.applets.aiusage import hook

        config_path = tmp_path / "dock.json"
        config_path.write_text("{")
        monkeypatch.setattr(hook, "_config_path", lambda: config_path)

        hook._update_config(
            session_id="s1",
            model_usage={"gpt-5.4": ModelUsage(input_tokens=12)},
        )

        data = json.loads(config_path.read_text())
        assert data["applet_prefs"]["aiusage"]["days"][0]["sessions"] == 1

    def test_main_handles_invalid_claude_stdin(self, monkeypatch):
        from docking.applets.aiusage import hook

        monkeypatch.setattr(aiusage_mod.sys, "argv", ["hook", "claude", "Stop"])
        monkeypatch.setattr(aiusage_mod.sys, "stdin", StringIO("{"))

        hook.main()

    def test_handle_codex_turn_ignores_invalid_json(self, monkeypatch):
        from docking.applets.aiusage import hook

        find_session = MagicMock(return_value=None)
        monkeypatch.setattr(aiusage_mod.sys, "argv", ["hook"])
        monkeypatch.setattr(hook.aiusage_state, "find_codex_session", find_session)

        hook._handle_codex_turn(json_arg="{")

        find_session.assert_called_once_with(thread_id=None)


class TestRender:
    def test_render_icon_draws_opencode_logo_with_token_label(self, monkeypatch):
        draw_opencode = MagicMock()
        draw_label = MagicMock()
        monkeypatch.setattr(aiusage_render_mod, "_draw_opencode_logo", draw_opencode)
        monkeypatch.setattr(aiusage_render_mod, "_draw_codex_logo", MagicMock())
        monkeypatch.setattr(aiusage_render_mod, "_draw_claude_logo", MagicMock())
        monkeypatch.setattr(aiusage_render_mod, "draw_icon_label", draw_label)
        monkeypatch.setattr(
            aiusage_render_mod.Gdk,
            "pixbuf_get_from_surface",
            lambda *_args: "pixbuf",
        )
        state = set_session(
            session_id="oc",
            state=AiUsageState(),
            model_usage={
                "opencode:gpt-oss": ModelUsage(
                    input_tokens=2_000,
                    output_tokens=500,
                    precalculated_cost=0.25,
                )
            },
        )

        result = aiusage_render_mod.render_icon(
            size=48,
            state=state,
            selected_provider=Provider.OPENCODE,
            display_mode=DisplayMode.TOKENS,
        )

        assert result == "pixbuf"
        draw_opencode.assert_called_once()
        draw_label.assert_called_once()
        assert draw_label.call_args.kwargs["text"] == "2.5K"

    def test_render_icon_skips_label_when_cost_is_zero(self, monkeypatch):
        draw_label = MagicMock()
        monkeypatch.setattr(aiusage_render_mod, "_draw_codex_logo", MagicMock())
        monkeypatch.setattr(aiusage_render_mod, "draw_icon_label", draw_label)
        monkeypatch.setattr(
            aiusage_render_mod.Gdk,
            "pixbuf_get_from_surface",
            lambda *_args: "pixbuf",
        )

        result = aiusage_render_mod.render_icon(
            size=48,
            state=AiUsageState(),
            selected_provider=Provider.CODEX,
            display_mode=DisplayMode.COST,
        )

        assert result == "pixbuf"
        draw_label.assert_not_called()
