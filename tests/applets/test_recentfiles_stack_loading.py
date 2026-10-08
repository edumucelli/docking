"""Recent Files hydrates only visible icons without truncating its menu."""

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from docking.applets.recentfiles.applet import RecentFilesApplet
from docking.applets.recentfiles.state import MAX_ENTRIES, RecentEntry
from docking.core.config import Config
from docking.ui.stack import FOLDER_STACK_MAX_VISIBLE_ROWS


@pytest.fixture
def applet():
    manager = MagicMock()
    manager.get_items.return_value = []
    loader = MagicMock()
    loader.load_icon.return_value = None
    targets = MagicMock()
    targets.resolve_file.return_value = None
    with patch(
        "docking.applets.recentfiles.applet.Gtk.RecentManager.get_default",
        return_value=manager,
    ):
        yield RecentFilesApplet(
            48, config=Config(), icon_loader=loader, target_service=targets
        )


@pytest.mark.parametrize("count", [0, 1, 4, FOLDER_STACK_MAX_VISIBLE_ROWS, MAX_ENTRIES])
def test_only_visible_stack_entries_resolve_icons(applet, count):
    entries = [
        RecentEntry(name=f"file-{index}.txt", uri=f"file:///file-{index}.txt")
        for index in range(count)
    ]
    applet._entries = entries.copy()
    visible = entries[:FOLDER_STACK_MAX_VISIBLE_ROWS]
    icons = {entry.uri: object() for entry in visible}
    applet._target_service.resolve_file.side_effect = lambda uri, _size, **_kwargs: (
        SimpleNamespace(icon=icons[uri], is_thumbnail=False)
    )

    content = applet.stack_content(32)

    assert applet._entries == entries
    assert applet._target_service.resolve_file.call_args_list == [
        call(entry.uri, 32, thumbnail_size=128) for entry in visible
    ]
    if not visible:
        assert content is None
        return
    assert [entry.key for entry in content.entries] == [entry.uri for entry in visible]
    assert [entry.label for entry in content.entries] == [
        entry.name for entry in visible
    ]
    assert [entry.icon for entry in content.entries] == list(icons.values())
    assert not any(entry.thumbnail_style for entry in content.entries)
    for entry in content.entries:
        entry.activate()
    assert applet._target_service.open_target.call_args_list == [
        call(entry.uri) for entry in visible
    ]


def test_hidden_stack_entries_remain_in_menu_and_can_be_opened(applet):
    entries = [
        RecentEntry(name=f"file-{index}.txt", uri=f"file:///file-{index}.txt")
        for index in range(MAX_ENTRIES)
    ]
    applet._entries = entries.copy()
    content = applet.stack_content(32)

    menu = applet.get_menu_items()

    assert len(content.entries) == FOLDER_STACK_MAX_VISIBLE_ROWS
    assert not any(entry.thumbnail_style for entry in content.entries)
    assert [item.get_label() for item in menu[:MAX_ENTRIES]] == [
        entry.name for entry in entries
    ]
    assert len(menu) == MAX_ENTRIES + 2
    last_item = menu[MAX_ENTRIES - 1]
    for callback, args in last_item._signals["activate"]:
        callback(last_item, *args)
    applet._target_service.open_target.assert_called_once_with(entries[-1].uri)
    assert applet._entries == entries


def test_stack_fallback_icons_are_loaded_only_for_visible_entries(applet):
    applet._entries = [
        RecentEntry(name=str(index), uri=f"file:///{index}")
        for index in range(MAX_ENTRIES)
    ]
    applet._icon_loader.load_icon.reset_mock()

    content = applet.stack_content(64)

    assert len(content.entries) == FOLDER_STACK_MAX_VISIBLE_ROWS
    assert (
        applet._icon_loader.load_icon.call_args_list
        == [call("text-x-generic", 64)] * FOLDER_STACK_MAX_VISIBLE_ROWS
    )


def test_only_successful_image_thumbnails_receive_rounded_style(applet):
    applet._entries = [
        RecentEntry(name=name, uri=f"file:///{name}")
        for name in ("photo.jpeg", "screenshot.png", "broken.png", "report.txt")
    ]
    applet._target_service.resolve_file.side_effect = [
        SimpleNamespace(icon=object(), is_thumbnail=True),
        SimpleNamespace(icon=object(), is_thumbnail=True),
        SimpleNamespace(icon=object(), is_thumbnail=False),
        SimpleNamespace(icon=object(), is_thumbnail=False),
    ]

    content = applet.stack_content(48)

    assert [entry.thumbnail_style for entry in content.entries] == [
        True,
        True,
        False,
        False,
    ]
