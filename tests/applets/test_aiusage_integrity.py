"""Usage writes preserve preferences, dates, models, and applet lifetimes."""

import datetime as dt
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.applets.aiusage import applet, state, store
from docking.applets.aiusage.backends import claude, codex, opencode
from docking.core.config import Config
from tests.applets.deferred_worker import DeferredWorker


def _usage(count):
    return {"gpt-5.4": state.ModelUsage(input_tokens=count)}


def _timestamp(day, hour, minute):
    return (
        dt.datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00")
        .astimezone()
        .isoformat()
    )


def _counter(count, timestamp):
    return {
        "type": "event_msg",
        "timestamp": timestamp,
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {"input_tokens": count, "total_tokens": count},
            },
        },
    }


def test_usage_write_recovers_legacy_history_without_touching_corrupt_settings(
    tmp_path, monkeypatch
):
    path = tmp_path / "dock.json"
    old = state.set_session(state.AiUsageState(), "old", _usage(80), date="2026-04-28")
    backup = path.with_name("dock.json.bak")
    saved = json.dumps(
        {"theme": "glass", "applet_prefs": {"aiusage": state.prefs_from_state(old)}}
    )
    backup.write_text(saved)
    path.write_text("{")
    monkeypatch.setattr(store, "config_path", lambda: path)

    store.replace_session(session_id="new", model_usage=_usage(20), date="2026-04-29")

    assert path.read_text() == "{" and backup.read_text() == saved
    assert Config.load(path).theme == "glass"
    assert (
        state.week_tokens(state.state_from_prefs(store.read_prefs_from_disk())) == 100
    )


def test_ordinary_settings_save_cannot_erase_hook_usage(tmp_path, monkeypatch):
    path = tmp_path / "dock.json"
    config = Config.load(path, initial_pinned_factory=list)
    monkeypatch.setattr(store, "config_path", lambda: path)
    store.replace_session(session_id="session", model_usage=_usage(100))

    config.icon_size = 52
    config.save()

    assert Config.load(path).icon_size == 52
    assert (
        state.today_tokens(state.state_from_prefs(store.read_prefs_from_disk())) == 100
    )
    assert "aiusage" not in json.loads(path.read_text())["applet_prefs"]


def test_failed_atomic_usage_write_preserves_history_and_cleans_temporary_file(
    monkeypatch,
):
    store.replace_session(session_id="existing", model_usage=_usage(100))
    path = store.usage_path()
    original = path.read_bytes()
    original_replace = Path.replace

    def fail(source, target):
        if target == path:
            raise OSError("write failed")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="write failed"):
        store.replace_session(session_id="new", model_usage=_usage(20))

    assert path.read_bytes() == original
    assert list(path.parent.glob(f".{path.name}.*")) == []


def test_corrupt_usage_history_is_preserved_for_recovery():
    path = store.usage_path()
    path.parent.mkdir(parents=True)
    path.write_text("{")
    with pytest.raises(OSError, match="Cannot read saved AI usage"):
        store.replace_session(session_id="new", model_usage=_usage(20))
    assert path.read_text() == "{"


@pytest.mark.parametrize(
    "notify",
    [
        'notify = ["notify-send", "Finished"]\n',
        '  notify = ["notify-send", "Finished"]\n',
        '"notify" = ["notify-send", "Finished"]\n',
        "'notify' = ['notify-send', 'Finished']\n",
        'notify = ["/another-tool/hook_runner.py"]\n',
        'notify = [\n "another-tool",\n "Finished"\n]\n',
    ],
)
def test_codex_registration_preserves_any_unowned_handler(
    tmp_path, monkeypatch, notify
):
    path = tmp_path / "config.toml"
    path.write_text(notify)
    monkeypatch.setattr(codex, "_CODEX_CONFIG", path)
    codex.register_hook()
    assert path.read_text() == notify


def test_codex_daily_deltas_keep_model_switches_and_midnight_separate(tmp_path):
    path = tmp_path / "session.jsonl"
    yesterday = _timestamp("2026-04-28", 23, 59)
    today = _timestamp("2026-04-29", 0, 1)
    entries = [
        {"type": "turn_context", "payload": {"model": "gpt-5.4"}},
        _counter(100, yesterday),
        _counter(120, today),
        {"type": "turn_context", "payload": {"model": "gpt-5.4-mini"}},
        _counter(150, today),
        _counter(150, today),
        {"type": "turn_context", "payload": {"model": "gpt-5.4"}},
    ]
    path.write_text("\n".join(json.dumps(entry) for entry in entries))

    first = codex.parse_transcript(path, date="2026-04-28")
    second = codex.parse_transcript(path, date="2026-04-29")

    assert first == _usage(100)
    assert second == {**_usage(20), "gpt-5.4-mini": state.ModelUsage(input_tokens=30)}
    history = state.set_session(state.AiUsageState(), "s", first, date="2026-04-28")
    history = state.set_session(history, "s", second, date="2026-04-29")
    assert state.week_tokens(history) == 150


def test_codex_reuses_one_transcript_pass_for_metadata_and_unchanged_usage(
    tmp_path, monkeypatch
):
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "session"}}))
    read = MagicMock(wraps=codex.transcript_entries)
    monkeypatch.setattr(codex, "transcript_entries", read)

    assert codex.session_id(path) == "session"
    assert codex.parse_transcript(path) == {}
    assert codex.session_id(path) == "session"
    read.assert_called_once()


def test_claude_only_counts_messages_on_the_requested_local_day(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": _timestamp(day, 12, 0),
                    "message": {
                        "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": count},
                    },
                }
            )
            for day, count in [("2026-04-28", 100), ("2026-04-29", 20)]
        )
    )

    assert claude.parse_transcript(path, date="2026-04-29") == {
        "claude-sonnet-4-6": state.ModelUsage(input_tokens=20)
    }


def test_opencode_counts_today_in_resumed_sessions(tmp_path, monkeypatch):
    path = tmp_path / "opencode.db"
    day = "2026-04-29"
    today = int(dt.datetime.fromisoformat(day).timestamp() * 1000)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE message (session_id TEXT, data TEXT, time_created INTEGER)"
        )
        for offset, count in [(-60000, 100), (60000, 20), (2 * 86400000, 50)]:
            conn.execute(
                "INSERT INTO message VALUES (?, ?, ?)",
                (
                    "old-session",
                    json.dumps({"modelID": "example", "tokens": {"input": count}}),
                    today + offset,
                ),
            )
    monkeypatch.setattr(opencode, "_OPENCODE_DB", path)
    monkeypatch.setattr(opencode, "_today_iso", lambda: day)

    assert opencode.query_today() == {
        "oc:old-session": {"opencode:example": state.ModelUsage(input_tokens=20)}
    }


def test_polled_history_survives_midnight_and_an_applet_restart(monkeypatch):
    provider = SimpleNamespace(
        provider=state.Provider.OPENCODE,
        poll_today=lambda: {
            "oc:s": {"opencode:example": state.ModelUsage(input_tokens=30)}
        },
    )
    monkeypatch.setattr(applet, "BACKENDS", (provider,))
    worker = DeferredWorker()
    monkeypatch.setattr(applet, "SerialWorker", lambda **_kwargs: worker.worker)
    first = applet.AiUsageApplet(48, Config())
    first.present = MagicMock()
    monkeypatch.setattr(applet, "_today_iso", lambda: "2026-04-28")
    first._tick()
    worker.drain()
    assert state.week_tokens(first._state) == 30

    provider.poll_today = dict
    monkeypatch.setattr(applet, "_today_iso", lambda: "2026-04-29")
    second = applet.AiUsageApplet(48, Config())
    second._tick()
    worker.drain()

    assert state.week_tokens(second._state) == 30
    assert second._state.days[0].date == "2026-04-28"


@pytest.mark.parametrize("stop_before_delivery", [True, False])
def test_polling_defers_io_and_ignores_results_after_stop(
    monkeypatch, stop_before_delivery
):
    queued = DeferredWorker()
    worker = SimpleNamespace(
        run=queued.worker.run, run_guarded=queued.worker.run_guarded, stop=MagicMock()
    )
    monkeypatch.setattr(applet, "SerialWorker", lambda **_kwargs: worker)
    poll = MagicMock(return_value={"s": _usage(20)})
    monkeypatch.setattr(
        applet,
        "BACKENDS",
        (SimpleNamespace(provider=state.Provider.CODEX, poll_today=poll),),
    )
    instance = applet.AiUsageApplet(48, Config())
    instance.present = MagicMock()

    instance._tick()
    instance._tick()
    poll.assert_not_called()
    assert len(queued.tasks) == 1
    queued.read()
    assert state.week_tokens(instance._state) == 0
    if stop_before_delivery:
        instance.stop()
    queued.publish()

    assert state.week_tokens(instance._state) == (0 if stop_before_delivery else 20)
    assert instance.present.call_count == (0 if stop_before_delivery else 1)
