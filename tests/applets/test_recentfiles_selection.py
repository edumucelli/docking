"""Bound recent-history filesystem work while preserving newest-valid selection."""

import random
from unittest.mock import MagicMock, patch

import pytest

from docking.applets.recentfiles.applet import RecentFilesApplet
from docking.applets.recentfiles.state import MAX_ENTRIES, RecentEntry
from docking.core.config import Config
from tests.applets.recentfiles_support import RecentInfo


@pytest.fixture
def recent():
    manager = MagicMock()
    manager.get_items.return_value = []
    loader = MagicMock()
    loader.load_icon.return_value = None
    with patch(
        "docking.applets.recentfiles.applet.Gtk.RecentManager.get_default",
        return_value=manager,
    ):
        applet = RecentFilesApplet(
            48, config=Config(), icon_loader=loader, target_service=MagicMock()
        )
        yield applet, manager


def _expected(history):
    return [
        RecentEntry(name=info.name, uri=info.get_uri())
        for info in sorted(
            (info for info in history if info.available),
            key=lambda info: info.modified,
            reverse=True,
        )[:MAX_ENTRIES]
    ]


@pytest.mark.parametrize("count", [0, 1, MAX_ENTRIES - 1, MAX_ENTRIES, 1000])
def test_checks_only_enough_existing_files(recent, count):
    applet, manager = recent
    history = [RecentInfo(f"file-{index}.txt", index) for index in range(count)]
    manager.get_items.return_value = history.copy()

    applet._refresh_entries()

    assert applet._entries == _expected(history)
    assert sum(info.checks for info in history) == min(count, MAX_ENTRIES)
    if count > MAX_ENTRIES:
        assert all(info.checks == 0 for info in history[:-MAX_ENTRIES])


def test_missing_newest_files_do_not_reduce_valid_file_limit(recent):
    applet, manager = recent
    history = [
        RecentInfo(f"file-{index}.txt", 100 - index, index >= 7) for index in range(100)
    ]
    manager.get_items.return_value = history.copy()

    applet._refresh_entries()

    assert applet._entries == _expected(history)
    assert len(applet._entries) == MAX_ENTRIES
    assert sum(info.checks for info in history) == MAX_ENTRIES + 7
    assert all(info.checks == 0 for info in history[MAX_ENTRIES + 7 :])


def test_inspects_entire_history_when_too_few_files_survive(recent):
    applet, manager = recent
    history = [
        RecentInfo(f"file-{index}.txt", index, index % 7 == 0) for index in range(60)
    ]
    manager.get_items.return_value = history.copy()

    applet._refresh_entries()

    assert applet._entries == _expected(history)
    assert sum(info.checks for info in history) == len(history)


def test_equal_timestamps_preserve_manager_order(recent):
    applet, manager = recent
    history = [RecentInfo(f"file-{index}.txt", 42, index != 2) for index in range(30)]
    random.Random(86927044).shuffle(history)
    manager.get_items.return_value = history.copy()

    applet._refresh_entries()

    assert applet._entries == _expected(history)


def test_randomized_selection_matches_filter_then_sort(recent):
    applet, manager = recent
    rng = random.Random(86927044)
    for _ in range(200):
        history = [
            RecentInfo(f"file-{index}.txt", rng.randrange(20), rng.random() > 0.4)
            for index in range(rng.randrange(100))
        ]
        rng.shuffle(history)
        manager.get_items.return_value = history.copy()

        applet._refresh_entries()

        assert applet._entries == _expected(history)
        checked = sorted(history, key=lambda info: info.modified, reverse=True)
        visited = [info for info in checked if info.checks]
        assert sum(info.checks for info in history) == len(visited)
        assert visited == checked[: len(visited)]
        assert (
            len(visited) == len(history)
            or sum(info.available for info in visited) == MAX_ENTRIES
        )


def test_change_reselects_newest_files_and_notifies(recent):
    applet, manager = recent
    notify = MagicMock()
    applet.start(notify)
    history = [RecentInfo(f"file-{index}.txt", index) for index in range(1000)]
    manager.get_items.return_value = history.copy()

    applet._on_changed(manager)

    assert applet._entries == _expected(history)
    assert applet.item.name == history[-1].name
    assert sum(info.checks for info in history) == MAX_ENTRIES
    notify.assert_called_once()
    applet.stop()
