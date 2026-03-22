"""Tests for typed applet identity and category helpers."""

import pytest

from docking.applets.identity import (
    AppletCategory,
    AppletId,
    applet_desktop_id,
    applet_id_from,
    category_for,
    is_applet_desktop_id,
    parse_applet_id,
)


class TestParseAppletId:
    def test_parses_simple_desktop_id(self):
        assert parse_applet_id(desktop_id="applet://clock") == AppletId.CLOCK

    def test_parses_multi_instance_desktop_id(self):
        assert parse_applet_id(desktop_id="applet://separator#5") == AppletId.SEPARATOR

    def test_returns_none_for_non_applet_desktop_id(self):
        assert parse_applet_id(desktop_id="firefox.desktop") is None

    def test_returns_none_for_unknown_applet_id(self):
        assert parse_applet_id(desktop_id="applet://unknown") is None

    def test_parses_music(self):
        assert parse_applet_id(desktop_id="applet://music") == AppletId.MUSIC

    def test_parses_notifications(self):
        assert (
            parse_applet_id(desktop_id="applet://notifications")
            == AppletId.NOTIFICATIONS
        )

    def test_parses_bluetooth(self):
        assert parse_applet_id(desktop_id="applet://bluetooth") == AppletId.BLUETOOTH

    def test_parses_powerprofiles(self):
        assert (
            parse_applet_id(desktop_id="applet://powerprofiles")
            == AppletId.POWERPROFILES
        )


class TestAppletIdFrom:
    def test_returns_typed_applet_id(self):
        assert applet_id_from(desktop_id="applet://weather") == AppletId.WEATHER

    def test_raises_for_non_applet_desktop_id(self):
        with pytest.raises(ValueError):
            applet_id_from(desktop_id="firefox.desktop")

    def test_raises_for_unknown_applet_id(self):
        with pytest.raises(ValueError):
            applet_id_from(desktop_id="applet://nope")

    def test_returns_music_applet_id(self):
        assert applet_id_from(desktop_id="applet://music") == AppletId.MUSIC


class TestAppletDesktopId:
    def test_builds_simple_desktop_id(self):
        assert applet_desktop_id(applet_id=AppletId.CLOCK) == "applet://clock"

    def test_builds_multi_instance_desktop_id(self):
        assert applet_desktop_id(applet_id=AppletId.SEPARATOR, instance=3) == (
            "applet://separator#3"
        )


class TestCategoryFor:
    def test_returns_mapped_category(self):
        assert category_for(applet_id=AppletId.POMODORO) == AppletCategory.PRODUCTIVITY

    def test_systemmonitor_is_grouped_under_system(self):
        assert category_for(applet_id=AppletId.SYSTEMMONITOR) == AppletCategory.SYSTEM

    def test_information_category_label(self):
        assert AppletCategory.INFORMATION.value == "Information and Environment"

    def test_music_is_grouped_under_system(self):
        assert category_for(applet_id=AppletId.MUSIC) == AppletCategory.SYSTEM

    def test_notifications_is_grouped_under_system(self):
        assert category_for(applet_id=AppletId.NOTIFICATIONS) == AppletCategory.SYSTEM

    def test_bluetooth_is_grouped_under_system(self):
        assert category_for(applet_id=AppletId.BLUETOOTH) == AppletCategory.SYSTEM

    def test_powerprofiles_is_grouped_under_system(self):
        assert category_for(applet_id=AppletId.POWERPROFILES) == AppletCategory.SYSTEM


class TestIsAppletDesktopId:
    def test_true_for_applet_desktop_id(self):
        assert is_applet_desktop_id(desktop_id="applet://clock")

    def test_false_for_desktop_file(self):
        assert not is_applet_desktop_id(desktop_id="firefox.desktop")
