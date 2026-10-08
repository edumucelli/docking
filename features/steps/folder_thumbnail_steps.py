"""Production folder stack and real Gio directory enumeration contracts."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from behave import given, then, when

from docking.core.config import Config
from docking.core.items import DockItem
from docking.platform.targets import TargetService
from docking.ui.folder.stack import FolderStackController


@given("a thumbnail folder containing 30 files")
def thumbnail_folder(context):
    temporary = TemporaryDirectory(prefix="docking-bdd-thumbnails-")
    context.add_cleanup(temporary.cleanup)
    folder = Path(temporary.name)
    for index in range(30):
        (folder / f"file-{index:02d}.txt").write_text("x" * (30 - index))
    context.thumbnail_loader = MagicMock()
    context.thumbnail_loader.resolve_file_icon.side_effect = lambda **kw: kw["target"]
    context.thumbnail_config = Config()
    context.thumbnail_item = DockItem(
        desktop_id=folder.as_uri(), target=folder.as_uri(), kind="folder"
    )
    context.thumbnail_stack = FolderStackController(
        config=context.thumbnail_config,
        runtime=MagicMock(),
        dock_window=MagicMock(),
        target_service=TargetService(icon_loader=context.thumbnail_loader),
    )


@when('its stack is built using "{sort}" sorting')
def build_sorted_stack(context, sort):
    item = context.thumbnail_item
    context.thumbnail_config.item_prefs[item.target] = {"sort": sort}
    context.thumbnail_content = context.thumbnail_stack._stack_content_for_item(
        item=item,
        icon_px=48,
    )


@then("nine sorted stack icons and the remaining file count are available")
def visible_thumbnails(context):
    content = context.thumbnail_content
    assert len(content.entries) == 9
    assert "21" in content.action.label
    assert context.thumbnail_loader.resolve_file_icon.call_count == 9
    assert all(entry.icon == entry.key for entry in content.entries)


@when("its full directory menu is opened twice")
def full_menu(context):
    item = context.thumbnail_item
    for _ in range(2):
        context.thumbnail_rows = context.thumbnail_stack.list_directory(
            folder_item=item,
            target=item.target,
            icon_px=48,
        )


@then("all 30 menu icons are loaded only once")
def warm_menu(context):
    assert len(context.thumbnail_rows) == 30
    assert all(row["icon"] == row["target"] for row in context.thumbnail_rows)
    assert context.thumbnail_loader.resolve_file_icon.call_count == 30
