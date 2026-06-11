"""Tests for update check state and release decisions."""

from __future__ import annotations

import json
import logging
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from docking.core.updates import (
    PROJECT_RELEASES_URL,
    ReleaseInfo,
    UpdateState,
    _aware_utc,
    _coerce_str,
    _version_parts,
    compare_versions,
    decide_update_popup,
    fetch_latest_release,
    is_newer_version,
    load_state,
    normalize_version,
    parse_github_release,
    parse_timestamp,
    save_state,
    should_check_for_updates,
    utc_now_iso,
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


class TestLoadStateEdgeCases:
    def test_corrupt_json_returns_defaults(self, tmp_path, caplog):
        path = tmp_path / "updates.json"
        path.write_text("{corrupt", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="docking.updates"):
            state = load_state(path=path)

        assert state == UpdateState()
        assert "Failed to load update state" in caplog.text

    def test_non_dict_payload_returns_defaults(self, tmp_path, caplog):
        path = tmp_path / "updates.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="docking.updates"):
            state = load_state(path=path)

        assert state == UpdateState()
        assert "Invalid update state payload" in caplog.text

    def test_load_state_with_str_path(self, tmp_path):
        """load_state accepts a string path."""
        state = load_state(path=str(tmp_path / "nonexistent.json"))
        assert state == UpdateState()

    def test_save_state_with_str_path(self, tmp_path):
        state = UpdateState(last_checked_at="2026-05-01T10:00:00+00:00")
        save_state(state, path=str(tmp_path / "updates.json"))
        loaded = load_state(path=tmp_path / "updates.json")
        assert loaded == state


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

    def test_no_release_does_not_show(self):
        decision = decide_update_popup(
            current_version="1.15.0",
            release=None,
            state=UpdateState(),
        )
        assert decision.should_show is False
        assert decision.reason == "no-release"

    def test_not_newer_does_not_show(self):
        release = ReleaseInfo(version="1.15.0", url="https://example.test", name="")
        decision = decide_update_popup(
            current_version="1.16.0",
            release=release,
            state=UpdateState(),
        )
        assert decision.should_show is False
        assert decision.reason == "not-newer"


class TestFetchReleaseErrors:
    def test_fetch_raises_on_urlerror(self, monkeypatch):
        def raise_urlerror(request, *, timeout):
            raise urllib.error.URLError("no network")

        monkeypatch.setattr("urllib.request.urlopen", raise_urlerror)
        with pytest.raises(urllib.error.URLError):
            fetch_latest_release()


class TestParseGithubReleaseErrors:
    def test_non_dict_payload_raises(self):
        with pytest.raises(ValueError, match="not an object"):
            parse_github_release([1, 2, 3])

    def test_missing_tag_name_raises(self):
        with pytest.raises(ValueError, match="missing tag_name"):
            parse_github_release({})

    def test_empty_tag_name_raises(self):
        with pytest.raises(ValueError, match="missing tag_name"):
            parse_github_release({"tag_name": ""})

    def test_missing_html_url_falls_back_to_releases_page(self):
        release = parse_github_release({"tag_name": "v1.16.0"})
        assert release.url == PROJECT_RELEASES_URL

    def test_missing_name_uses_tag(self):
        release = parse_github_release(
            {"tag_name": "v1.16.0", "html_url": "https://example.test"}
        )
        assert release.name == "v1.16.0"


class TestTimestampParsing:
    def test_none_value_returns_none(self):
        assert parse_timestamp(None) is None

    def test_empty_string_returns_none(self):
        assert parse_timestamp("   ") is None

    def test_invalid_iso_returns_none(self):
        assert parse_timestamp("not-a-date") is None

    def test_utc_now_iso_with_explicit_datetime(self):
        dt = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        iso = utc_now_iso(now=dt)
        assert "2026-06-01" in iso


class TestVersionParts:
    def test_empty_string_returns_empty_tuple(self):
        assert _version_parts("") == ()

    def test_wider_version_comparison(self):
        """Two-part vs three-part version works."""
        assert compare_versions("1.16", "1.15.0") > 0
        assert compare_versions("1.15", "1.16.0") < 0


class TestNormalizeVersion:
    def test_leading_v_prefix_stripped(self):
        assert normalize_version("v1.16.0") == "1.16.0"
        assert normalize_version("V2.0.0") == "2.0.0"

    def test_non_numeric_suffix_dropped(self):
        assert normalize_version("1.16.0-beta1") == "1.16.0"

    def test_plain_version_passes_through(self):
        assert normalize_version("1.16.0") == "1.16.0"


class TestCoerceStr:
    def test_string_trimmed(self):
        assert _coerce_str("  hello  ") == "hello"

    def test_non_string_returns_empty(self):
        assert _coerce_str(None) == ""
        assert _coerce_str(42) == ""
        assert _coerce_str([]) == ""


class TestVersionPartsEdgeCases:
    def test_non_numeric_tag_returns_empty_tuple(self):
        """A tag like 'beta' triggers ValueError on int('beta') → break → ()."""
        result = _version_parts("beta")
        assert result == ()

    def test_aware_utc_converts_naive_to_utc(self):
        """Naive datetime gets tzinfo=UTC."""
        naive = datetime(2026, 6, 1, 12, 0, 0)
        aware = _aware_utc(naive)
        assert aware.tzinfo is timezone.utc
