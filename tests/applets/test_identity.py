"""Tests for applet identity and category helpers."""

import pytest

from docking.applets.identity import (
    AppletCategory,
    applet_desktop_id,
    applet_id_from,
    category_for,
    is_applet_desktop_id,
    parse_applet_id,
)


class TestParseAppletId:
    def test_parses_simple_desktop_id(self):
        assert parse_applet_id(desktop_id="applet://clock") == "clock"

    def test_parses_multi_instance_desktop_id(self):
        assert parse_applet_id(desktop_id="applet://separator#5") == "separator"

    def test_returns_none_for_non_applet_desktop_id(self):
        assert parse_applet_id(desktop_id="firefox.desktop") is None

    def test_parses_unknown_applet_id(self):
        # parse_applet_id only validates the format, not existence.
        assert parse_applet_id(desktop_id="applet://unknown") == "unknown"

    def test_parses_music(self):
        assert parse_applet_id(desktop_id="applet://music") == "music"

    def test_parses_notifications(self):
        assert parse_applet_id(desktop_id="applet://notifications") == "notifications"

    def test_parses_bluetooth(self):
        assert parse_applet_id(desktop_id="applet://bluetooth") == "bluetooth"

    def test_parses_powerprofiles(self):
        assert parse_applet_id(desktop_id="applet://powerprofiles") == "powerprofiles"


class TestAppletIdFrom:
    def test_returns_applet_id(self):
        assert applet_id_from(desktop_id="applet://calendar") == "calendar"

    def test_raises_for_non_applet_desktop_id(self):
        with pytest.raises(ValueError):
            applet_id_from(desktop_id="firefox.desktop")

    def test_returns_unknown_applet_id(self):
        # Unknown applets parse fine; they fail at load time, not parse time.
        assert applet_id_from(desktop_id="applet://nope") == "nope"

    def test_returns_music_applet_id(self):
        assert applet_id_from(desktop_id="applet://music") == "music"


class TestAppletDesktopId:
    def test_builds_simple_desktop_id(self):
        assert applet_desktop_id(applet_id="clock") == "applet://clock"

    def test_builds_multi_instance_desktop_id(self):
        assert applet_desktop_id(applet_id="separator", instance=3) == (
            "applet://separator#3"
        )


class TestCategoryFor:
    def test_returns_mapped_category(self):
        assert category_for(applet_id="pomodoro") == AppletCategory.PRODUCTIVITY

    def test_systemmonitor_is_grouped_under_system(self):
        assert category_for(applet_id="systemmonitor") == AppletCategory.SYSTEM

    def test_information_category_label(self):
        assert AppletCategory.INFORMATION.value == "Information and Environment"

    def test_music_is_grouped_under_system(self):
        assert category_for(applet_id="music") == AppletCategory.SYSTEM

    def test_notifications_is_grouped_under_system(self):
        assert category_for(applet_id="notifications") == AppletCategory.SYSTEM

    def test_bluetooth_is_grouped_under_system(self):
        assert category_for(applet_id="bluetooth") == AppletCategory.SYSTEM

    def test_powerprofiles_is_grouped_under_system(self):
        assert category_for(applet_id="powerprofiles") == AppletCategory.SYSTEM


class TestIsAppletDesktopId:
    def test_true_for_applet_desktop_id(self):
        assert is_applet_desktop_id(desktop_id="applet://clock")

    def test_false_for_desktop_file(self):
        assert not is_applet_desktop_id(desktop_id="firefox.desktop")
