"""Tests for the AI usage tracker applet."""

from __future__ import annotations

import json

import docking.applets.aiusage.applet as aiusage_mod
from docking.applets.aiusage.applet import AiUsageApplet
from docking.applets.aiusage.state import (
    AiUsageState,
    DayEntry,
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
    reset_today,
    set_session,
    state_from_prefs,
    tooltip_text,
)

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

    def test_tooltip_widget_returns_box(self):
        applet = AiUsageApplet(48)
        from gi.repository import Gtk

        assert isinstance(applet._build_tooltip_widget(), Gtk.Box)


# ---------------------------------------------------------------
# Hook registration
# ---------------------------------------------------------------


class TestClaudeHookRegistration:
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
