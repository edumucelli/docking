"""Tests for update check state and release decisions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from docking.core.updates import (
    ReleaseInfo,
    UpdateState,
    compare_versions,
    decide_update_popup,
    fetch_latest_release,
    is_newer_version,
    load_state,
    parse_github_release,
    save_state,
    should_check_for_updates,
)


class TestVersionComparison:
    def test_newer_versions_compare_greater(self):
        assert is_newer_version("1.16.0", "1.15.0") is True
        assert is_newer_version("v1.16.0", "1.15.0") is True
        assert is_newer_version("1.16.0", "1.16.0") is False
        assert compare_versions("1.15.0", "1.16.0") == -1


class TestUpdateState:
    def test_load_missing_state_returns_defaults(self, tmp_path):
        state = load_state(path=tmp_path / "updates.json")

        assert state == UpdateState()

    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "updates.json"
        expected = UpdateState(
            last_checked_at="2026-05-01T10:00:00+00:00",
            last_seen_version="1.16.0",
            ignored_version="1.15.0",
            remind_after="2026-05-02T10:00:00+00:00",
            last_result="ok",
            last_error="",
        )

        save_state(expected, path=path)

        assert load_state(path=path) == expected
        assert json.loads(path.read_text())["last_seen_version"] == "1.16.0"


class TestCheckCadence:
    def test_disabled_checks_never_run(self):
        assert (
            should_check_for_updates(
                enabled=False,
                interval_hours=24,
                state=UpdateState(),
                now=datetime(2026, 5, 1, tzinfo=timezone.utc),
            )
            is False
        )

    def test_check_runs_when_never_checked(self):
        assert should_check_for_updates(
            enabled=True,
            interval_hours=24,
            state=UpdateState(),
            now=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )

    def test_check_waits_for_interval(self):
        now = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
        state = UpdateState(last_checked_at=(now - timedelta(hours=2)).isoformat())

        assert (
            should_check_for_updates(
                enabled=True,
                interval_hours=24,
                state=state,
                now=now,
            )
            is False
        )

        old_state = UpdateState(last_checked_at=(now - timedelta(hours=25)).isoformat())
        assert should_check_for_updates(
            enabled=True,
            interval_hours=24,
            state=old_state,
            now=now,
        )


class TestReleaseParsing:
    def test_parse_github_release(self):
        release = parse_github_release(
            {
                "tag_name": "v1.16.0",
                "html_url": "https://example.test/release",
                "name": "Docking 1.16.0",
                "published_at": "2026-05-01T10:00:00Z",
            }
        )

        assert release == ReleaseInfo(
            version="1.16.0",
            url="https://example.test/release",
            name="Docking 1.16.0",
            published_at="2026-05-01T10:00:00Z",
        )

    def test_fetch_latest_release_reads_github_response(self, monkeypatch):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return b'{"tag_name":"v1.16.0","html_url":"https://example.test"}'

        calls = []

        def urlopen(request, *, timeout):
            calls.append((request, timeout))
            return Response()

        monkeypatch.setattr("urllib.request.urlopen", urlopen)

        release = fetch_latest_release(timeout=2)

        assert release.version == "1.16.0"
        assert release.url == "https://example.test"
        assert calls[0][1] == 2
        assert calls[0][0].headers["User-agent"] == "Docking update checker"


class TestUpdateDecision:
    def test_newer_release_shows(self):
        release = ReleaseInfo(version="1.16.0", url="https://example.test", name="")

        decision = decide_update_popup(
            current_version="1.15.0",
            release=release,
            state=UpdateState(),
            now=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )

        assert decision.should_show is True
        assert decision.reason == "newer"

    def test_ignored_and_remind_later_do_not_show(self):
        release = ReleaseInfo(version="1.16.0", url="https://example.test", name="")
        now = datetime(2026, 5, 1, tzinfo=timezone.utc)

        ignored = decide_update_popup(
            current_version="1.15.0",
            release=release,
            state=UpdateState(ignored_version="v1.16.0"),
            now=now,
        )
        later = decide_update_popup(
            current_version="1.15.0",
            release=release,
            state=UpdateState(remind_after=(now + timedelta(hours=1)).isoformat()),
            now=now,
        )

        assert ignored.should_show is False
        assert ignored.reason == "ignored"
        assert later.should_show is False
        assert later.reason == "remind-later"
