"""Tests for greeting state and New Year trigger rules."""

from __future__ import annotations

import json
from datetime import datetime

from docking.core.greeting import (
    StartupGreetingState,
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

    def test_round_trip(self, tmp_path):
        path = tmp_path / "startup.json"
        state = StartupGreetingState(
            completed_first_launch=True,
            last_year_greeted=2026,
        )

        save_state(state, path=path)

        assert load_state(path=path) == state


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
