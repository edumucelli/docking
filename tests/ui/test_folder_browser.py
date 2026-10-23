"""Real directory enumeration contracts for partial thumbnail hydration."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from docking.core.config import Config
from docking.core.items import DockItem
from docking.platform.targets import TargetService
from docking.ui.folder._browser import FolderBrowser, FolderPrefs
from docking.ui.folder.stack import FolderStackController


@pytest.fixture
def folder(tmp_path):
    for index in range(30):
        (tmp_path / f"file-{index:02d}.txt").write_text("x" * (30 - index))
    (tmp_path / ".hidden").write_text("hidden")
    return tmp_path


@pytest.fixture
def browser():
    loader = MagicMock()
    loader.resolve_file_icon.side_effect = lambda **kwargs: kwargs["target"]
    return FolderBrowser(target_service=TargetService(icon_loader=loader)), loader


@pytest.mark.parametrize("sort", ["name", "kind", "size", "created", "modified"])
def test_partial_icons_follow_final_sort_and_keep_all_metadata(folder, browser, sort):
    subject, loader = browser
    prefs = FolderPrefs(sort=sort)
    metadata = subject.list_directory(target=str(folder), prefs=prefs, icon_limit=0)
    expected = sorted(metadata, key=lambda row: subject.sort_key(row, sort))

    rows = subject.list_directory(target=str(folder), prefs=prefs, icon_limit=9)

    assert len(rows) == 30
    assert [row.target for row in rows] == [row.target for row in expected]
    assert [
        call.kwargs["target"] for call in loader.resolve_file_icon.call_args_list
    ] == [row.target for row in expected[:9]]
    assert all(row.icon == row.target for row in rows[:9])
    assert all(row.icon is None for row in rows[9:])


def test_stack_then_full_menu_preserves_warm_icons(folder, browser):
    subject, loader = browser
    options = {"target": str(folder), "prefs": FolderPrefs()}
    subject.list_directory(**options, icon_limit=9)
    assert loader.resolve_file_icon.call_count == 9
    rows = subject.list_directory(**options)
    assert loader.resolve_file_icon.call_count == 30
    assert all(row.icon is not None for row in rows)
    assert subject.list_directory(**options) == rows
    subject.list_directory(**options, icon_limit=9)
    assert loader.resolve_file_icon.call_count == 30


def test_sort_changes_reuse_loaded_icons(folder, browser):
    subject, loader = browser
    by_name = subject.list_directory(
        target=str(folder), prefs=FolderPrefs(), icon_limit=9
    )
    by_size = subject.list_directory(
        target=str(folder), prefs=FolderPrefs(sort="size"), icon_limit=9
    )
    needed = {row.target for row in by_name[:9] + by_size[:9]}
    assert loader.resolve_file_icon.call_count == len(needed)
    assert all(row.icon == row.target for row in by_size[:9])


def test_warm_menu_retains_icons_beyond_thumbnail_loader_capacity(tmp_path, browser):
    for index in range(300):
        (tmp_path / f"file-{index:03d}.txt").write_text("x")
    subject, loader = browser
    options = {"target": str(tmp_path), "prefs": FolderPrefs()}
    subject.list_directory(**options, icon_limit=9)
    first = subject.list_directory(**options)
    second = subject.list_directory(**options)
    assert len(first) == 300 and first == second
    assert loader.resolve_file_icon.call_count == 300


def test_hidden_policy_and_icon_size_are_separate_cache_entries(folder, browser):
    subject, loader = browser
    for size in (16, 64):
        for show_hidden in (False, True):
            rows = subject.list_directory(
                target=str(folder),
                prefs=FolderPrefs(show_hidden=show_hidden),
                icon_px=size,
            )
            assert len(rows) == 30 + show_hidden
            assert all(row.icon is not None for row in rows)
    assert loader.resolve_file_icon.call_count == 122
    assert {
        call.kwargs["size"] for call in loader.resolve_file_icon.call_args_list
    } == {16, 64}


def test_failed_icons_stay_cached_until_invalidation(folder, browser):
    subject, loader = browser
    loader.resolve_file_icon.side_effect = None
    loader.resolve_file_icon.return_value = None
    options = {"target": str(folder), "prefs": FolderPrefs(), "icon_limit": 9}
    subject.list_directory(**options)
    subject.list_directory(**options)
    assert loader.resolve_file_icon.call_count == 9
    subject.invalidate_target(str(folder))
    subject.list_directory(**options)
    assert loader.resolve_file_icon.call_count == 18


def test_missing_empty_negative_limit_and_child_visibility(tmp_path, browser):
    subject, loader = browser
    options = {"prefs": FolderPrefs(), "icon_limit": -1}
    assert subject.list_directory(target=str(tmp_path / "missing"), **options) == []
    assert subject.list_directory(target=str(tmp_path), **options) == []
    child = tmp_path / "child"
    child.mkdir()
    (child / ".hidden").write_text("x")
    subject.invalidate_target(str(tmp_path))
    rows = subject.list_directory(target=str(tmp_path), **options)
    assert rows[0].is_dir and not rows[0].has_children
    rows = subject.list_directory(
        target=str(tmp_path), prefs=FolderPrefs(show_hidden=True), icon_limit=0
    )
    assert rows[0].has_children
    loader.resolve_file_icon.assert_not_called()


def test_directory_lru_remains_bounded(tmp_path, browser):
    subject, _loader = browser
    for index in range(50):
        child = tmp_path / str(index)
        child.mkdir()
        subject.list_directory(target=str(child), prefs=FolderPrefs(), icon_limit=0)
    assert len(subject._directory_rows) == 48
    assert all(
        Path(key[0].removeprefix("file://")).name not in {"0", "1"}
        for key in subject._directory_rows
    )


def test_production_stack_requests_only_visible_icons_and_menu_requests_all(
    folder, browser
):
    subject, loader = browser
    controller = FolderStackController(
        config=Config(),
        runtime=MagicMock(),
        dock_window=MagicMock(),
        target_service=subject._target_service,
    )
    item = DockItem(desktop_id=folder.as_uri(), target=folder.as_uri(), kind="folder")
    content = controller._stack_content_for_item(item=item, icon_px=64)
    assert len(content.entries) == 9
    assert content.action is not None and "21" in content.action.label
    assert loader.resolve_file_icon.call_count == 9
    assert (
        len(controller.list_directory(folder_item=item, target=item.target, icon_px=64))
        == 30
    )
    assert loader.resolve_file_icon.call_count == 30
