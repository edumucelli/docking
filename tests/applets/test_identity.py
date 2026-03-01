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


class TestAppletIdFrom:
    def test_returns_typed_applet_id(self):
        assert applet_id_from(desktop_id="applet://weather") == AppletId.WEATHER

    def test_raises_for_non_applet_desktop_id(self):
        with pytest.raises(ValueError):
            applet_id_from(desktop_id="firefox.desktop")

    def test_raises_for_unknown_applet_id(self):
        with pytest.raises(ValueError):
            applet_id_from(desktop_id="applet://nope")


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


class TestIsAppletDesktopId:
    def test_true_for_applet_desktop_id(self):
        assert is_applet_desktop_id(desktop_id="applet://clock")

    def test_false_for_desktop_file(self):
        assert not is_applet_desktop_id(desktop_id="firefox.desktop")
