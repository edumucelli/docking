"""Tests for the live theme screenshot automation."""

from pathlib import Path

import pytest

from tools.capture_theme_screenshots import (
    _bottom_capture_rect,
    _capture_config,
    _safe_theme_filename,
    _theme_names,
)


def test_capture_config_preserves_user_values_and_forces_stable_capture_state():
    original = {
        "theme": "glass",
        "position": "left",
        "hide_mode": "autohide",
        "startup_tips_enabled": True,
        "update_check_enabled": True,
        "icon_size": 48,
    }

    updated = _capture_config(original, theme="nord")

    assert updated == {
        "theme": "nord",
        "position": "bottom",
        "hide_mode": "none",
        "startup_tips_enabled": False,
        "update_check_enabled": False,
        "icon_size": 48,
    }
    assert original["theme"] == "glass"
    assert original["hide_mode"] == "autohide"


def test_bottom_capture_rect_matches_all_png_shape():
    rect = _bottom_capture_rect(
        monitor_x=0,
        monitor_y=0,
        monitor_width=1920,
        monitor_height=1080,
        requested_height=512,
    )

    assert (rect.x, rect.y, rect.width, rect.height) == (0, 568, 1920, 512)


def test_bottom_capture_rect_clamps_to_short_monitor():
    rect = _bottom_capture_rect(
        monitor_x=1920,
        monitor_y=-200,
        monitor_width=1280,
        monitor_height=400,
        requested_height=512,
    )

    assert (rect.x, rect.y, rect.width, rect.height) == (1920, -200, 1280, 400)


@pytest.mark.parametrize(
    ("theme", "filename"),
    [
        ("default", "default.png"),
        ("My Theme", "My-Theme.png"),
        ("nord.dark", "nord.dark.png"),
    ],
)
def test_safe_theme_filename(theme: str, filename: str):
    assert _safe_theme_filename(theme) == filename


def test_safe_theme_filename_rejects_empty_name():
    with pytest.raises(ValueError, match="no safe filename"):
        _safe_theme_filename("///")


def test_theme_names_merge_builtin_and_user_themes(tmp_path: Path, monkeypatch):
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()
    (builtin_dir / "default.json").write_text("{}", encoding="utf-8")
    (builtin_dir / "nord.json").write_text("{}", encoding="utf-8")
    config_path = tmp_path / "config" / "dock.json"
    user_dir = config_path.parent / "themes"
    user_dir.mkdir(parents=True)
    (user_dir / "custom.json").write_text("{}", encoding="utf-8")
    (user_dir / "template.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "tools.capture_theme_screenshots.BUILTIN_THEMES_DIR",
        builtin_dir,
    )

    assert _theme_names(config_path=config_path) == ["custom", "default", "nord"]
