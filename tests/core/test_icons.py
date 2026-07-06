"""Tests for core icon helpers."""

from __future__ import annotations

from docking.core.icons import IconSource, icon_source_from_value


class TestIconSourceFromValue:
    def test_returns_value_when_already_icon_source(self):
        assert icon_source_from_value(IconSource.DOCKING) == IconSource.DOCKING

    def test_returns_none_for_non_string(self):
        assert icon_source_from_value(42) is None
        assert icon_source_from_value(None) is None
        assert icon_source_from_value(3.14) is None

    def test_returns_icon_source_for_valid_string(self):
        assert icon_source_from_value("docking") == IconSource.DOCKING
        assert icon_source_from_value("system") == IconSource.SYSTEM
        assert icon_source_from_value("custom") == IconSource.CUSTOM

    def test_returns_none_for_invalid_string(self):
        assert icon_source_from_value("invalid_value") is None
        assert icon_source_from_value("") is None
        assert icon_source_from_value("DOCKING") is None  # case-sensitive
