"""Tests for greeting state and New Year trigger rules."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from docking.core.greeting import (
    StartupGreetingState,
    _coerce_bool,
    _coerce_int,
    consume_new_year_greeting,
    load_state,
    save_state,
)


class TestLoadState:
    def test_missing_file_returns_defaults(self, tmp_path):
        path = tmp_path / "startup.json"

        state = load_state(path=path)

        assert state == StartupGreetingState()

    def test_invalid_payload_returns_defaults(self, tmp_path):
        path = tmp_path / "startup.json"
        path.write_text("{not json", encoding="utf-8")

        state = load_state(path=path)

        assert state == StartupGreetingState()

    def test_non_dict_payload_returns_defaults(self, tmp_path, caplog):
        path = tmp_path / "startup.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="docking.greeting"):
            state = load_state(path=path)

        assert state == StartupGreetingState()
        assert "Invalid greeting state payload" in caplog.text

    def test_round_trip(self, tmp_path):
        path = tmp_path / "startup.json"
        state = StartupGreetingState(
            completed_first_launch=True,
            last_year_greeted=2026,
        )

        save_state(state, path=path)

        assert load_state(path=path) == state

    def test_load_state_with_str_path(self, tmp_path):
        """load_state accepts a string path."""
        state = load_state(path=str(tmp_path / "nonexistent.json"))
        assert state == StartupGreetingState()


class TestConsumeNewYearGreeting:
    def test_first_launch_in_january_is_suppressed(self, tmp_path):
        path = tmp_path / "startup.json"

        year = consume_new_year_greeting(
            path=path,
            now=datetime(2026, 1, 3, 9, 0, 0),
        )

        assert year is None
        assert load_state(path=path) == StartupGreetingState(
            completed_first_launch=True,
            last_year_greeted=0,
        )

    def test_second_launch_in_january_shows_greeting(self, tmp_path):
        path = tmp_path / "startup.json"
        save_state(
            StartupGreetingState(completed_first_launch=True, last_year_greeted=0),
            path=path,
        )

        year = consume_new_year_greeting(
            path=path,
            now=datetime(2026, 1, 3, 9, 0, 0),
        )

        assert year == 2026
        assert load_state(path=path) == StartupGreetingState(
            completed_first_launch=True,
            last_year_greeted=2026,
        )

    def test_same_year_greeting_is_not_repeated(self, tmp_path):
        path = tmp_path / "startup.json"
        save_state(
            StartupGreetingState(
                completed_first_launch=True,
                last_year_greeted=2026,
            ),
            path=path,
        )

        year = consume_new_year_greeting(
            path=path,
            now=datetime(2026, 1, 5, 9, 0, 0),
        )

        assert year is None
        assert load_state(path=path).last_year_greeted == 2026

    def test_outside_first_half_of_january_is_suppressed(self, tmp_path):
        path = tmp_path / "startup.json"
        save_state(
            StartupGreetingState(completed_first_launch=True, last_year_greeted=2025),
            path=path,
        )

        year = consume_new_year_greeting(
            path=path,
            now=datetime(2026, 1, 16, 9, 0, 0),
        )

        assert year is None
        assert load_state(path=path).last_year_greeted == 2025

    def test_non_january_launch_can_enable_next_new_year(self, tmp_path):
        path = tmp_path / "startup.json"

        first = consume_new_year_greeting(
            path=path,
            now=datetime(2025, 6, 1, 9, 0, 0),
        )
        second = consume_new_year_greeting(
            path=path,
            now=datetime(2026, 1, 2, 9, 0, 0),
        )

        assert first is None
        assert second == 2026

    def test_stringy_state_values_are_coerced(self, tmp_path):
        path = tmp_path / "startup.json"
        path.write_text(
            json.dumps(
                {
                    "completed_first_launch": "true",
                    "last_year_greeted": "2025",
                }
            ),
            encoding="utf-8",
        )

        year = consume_new_year_greeting(
            path=path,
            now=datetime(2026, 1, 1, 9, 0, 0),
        )

        assert year == 2026


class TestCoerceBool:
    def test_true_like_strings(self):
        assert _coerce_bool("true", default=False) is True
        assert _coerce_bool("1", default=False) is True
        assert _coerce_bool("yes", default=False) is True
        assert _coerce_bool("on", default=False) is True

    def test_false_like_strings(self):
        assert _coerce_bool("false", default=True) is False
        assert _coerce_bool("0", default=True) is False
        assert _coerce_bool("no", default=True) is False
        assert _coerce_bool("off", default=True) is False

    def test_actual_bool_passes_through(self):
        assert _coerce_bool(True, default=False) is True
        assert _coerce_bool(False, default=True) is False

    def test_int_coerced_to_bool(self):
        assert _coerce_bool(1, default=False) is True
        assert _coerce_bool(0, default=True) is False

    def test_unknown_value_returns_default(self):
        assert _coerce_bool("unknown", default=True) is True
        assert _coerce_bool(3.14, default=False) is False


class TestCoerceInt:
    def test_bool_to_int(self):
        assert _coerce_int(True, default=0) == 1
        assert _coerce_int(False, default=0) == 0

    def test_float_to_int(self):
        assert _coerce_int(3.14, default=0) == 3

    def test_string_stripped_and_parsed(self):
        assert _coerce_int("  42  ", default=0) == 42

    def test_invalid_string_returns_default(self):
        assert _coerce_int("abc", default=7) == 7

    def test_none_returns_default(self):
        assert _coerce_int(None, default=99) == 99
