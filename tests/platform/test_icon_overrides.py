"""Tests for custom icon preference helpers."""

from pathlib import Path
from types import SimpleNamespace

from docking.core.icons import CUSTOM_ICON_PATH_KEY, ICON_SOURCE_PREF_KEY, IconSource
from docking.core.items import DockItem
from docking.platform import icon_overrides


def test_set_custom_icon_persists_item_preference_path():
    config = SimpleNamespace(item_prefs={})
    item = DockItem(desktop_id="firefox.desktop", target="firefox.desktop")
    path = Path("/home/user/icons/firefox.png")

    icon_overrides.set_custom_icon(config=config, item=item, path=path)

    assert config.item_prefs["firefox.desktop"] == {
        ICON_SOURCE_PREF_KEY: IconSource.CUSTOM.value,
        CUSTOM_ICON_PATH_KEY: str(path),
    }


def test_custom_icon_path_requires_custom_source_and_absolute_path():
    item = DockItem(desktop_id="firefox.desktop", target="firefox.desktop")
    config = SimpleNamespace(
        item_prefs={
            "firefox.desktop": {
                ICON_SOURCE_PREF_KEY: IconSource.SYSTEM.value,
                CUSTOM_ICON_PATH_KEY: "/home/user/icons/firefox.png",
            }
        }
    )

    assert icon_overrides.custom_icon_path(config=config, item=item) is None

    config.item_prefs["firefox.desktop"][ICON_SOURCE_PREF_KEY] = IconSource.CUSTOM.value
    config.item_prefs["firefox.desktop"][CUSTOM_ICON_PATH_KEY] = "relative.png"

    assert icon_overrides.custom_icon_path(config=config, item=item) is None

    config.item_prefs["firefox.desktop"][CUSTOM_ICON_PATH_KEY] = (
        "/home/user/icons/firefox.png"
    )

    assert icon_overrides.custom_icon_path(config=config, item=item) == Path(
        "/home/user/icons/firefox.png"
    )


def test_reset_custom_icon_preserves_unrelated_preferences():
    config = SimpleNamespace(
        item_prefs={
            "file:///tmp/docs": {
                ICON_SOURCE_PREF_KEY: IconSource.CUSTOM.value,
                CUSTOM_ICON_PATH_KEY: "/home/user/docs.png",
                "show_hidden": True,
            }
        }
    )
    item = DockItem(
        desktop_id="file:///tmp/docs",
        target="file:///tmp/docs",
        prefs_key="file:///tmp/docs",
    )

    icon_overrides.reset_custom_icon(config=config, item=item)

    assert config.item_prefs["file:///tmp/docs"] == {"show_hidden": True}
