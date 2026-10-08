"""Recent Files production-provider, GTK menu and bounded selection contracts."""

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from behave import given, then, when

from docking.applets.recentfiles.applet import RecentFilesApplet
from docking.applets.recentfiles.state import RecentEntry
from docking.core.config import Config
from tests.applets.recentfiles_support import RecentInfo


@given("a recent-files applet with {count:d} available history records")
def recent_history(context, count):
    context.recent_records = [
        RecentInfo(f"file-{index}.txt", index) for index in range(count)
    ]
    context.recent_manager = MagicMock()
    context.recent_manager.get_items.side_effect = lambda: context.recent_records.copy()
    manager_patch = patch(
        "docking.applets.recentfiles.applet.Gtk.RecentManager.get_default",
        return_value=context.recent_manager,
    )
    manager_patch.start()
    context.add_cleanup(manager_patch.stop)
    loader = MagicMock()
    loader.load_icon.return_value = None
    context.recent_targets = MagicMock()
    context.recent_targets.resolve_file.side_effect = lambda uri, _size, **_kwargs: (
        SimpleNamespace(icon=uri, is_thumbnail=False)
    )
    context.recent_applet = RecentFilesApplet(
        48,
        config=Config(),
        icon_loader=loader,
        target_service=context.recent_targets,
    )
    context.recent_notify = MagicMock()
    context.recent_applet.start(context.recent_notify)
    context.add_cleanup(context.recent_applet.stop)


@when("its recent stack is opened")
def open_recent_stack(context):
    context.recent_content = context.recent_applet.stack_content(48)


@then("only nine recent-file icons are resolved")
def visible_recent_icons(context):
    assert len(context.recent_content.entries) == 9
    assert context.recent_targets.resolve_file.call_count == 9
    assert context.recent_targets.resolve_file.call_args_list == [
        call(entry.key, 48, thumbnail_size=192)
        for entry in context.recent_content.entries
    ]
    assert all(entry.icon == entry.key for entry in context.recent_content.entries)


@then("all 15 recent files remain available in its menu")
def full_recent_menu(context):
    context.recent_menu = context.recent_applet.get_menu_items()
    assert len(context.recent_applet._entries) == 15
    assert len(context.recent_menu) == 17
    assert [item.get_label() for item in context.recent_menu[:15]] == [
        entry.name for entry in context.recent_applet._entries
    ]


@when("the last recent menu entry is activated")
def activate_last_recent(context):
    context.recent_menu[14].activate()


@then("the last retained recent file is opened")
def last_recent_opened(context):
    context.recent_targets.open_target.assert_called_once_with(
        context.recent_applet._entries[-1].uri
    )


@given("its seven newest recent files no longer exist")
def remove_newest_recent(context):
    for record in context.recent_records[-7:]:
        record.available = False


@when("its recent history changes")
def refresh_recent_history(context):
    for record in context.recent_records:
        record.checks = 0
    context.recent_applet._on_changed(context.recent_manager)


@then("the newest 15 files are selected with 15 filesystem checks")
@then("the newest 15 existing files are selected with 22 filesystem checks")
@then("all 8 recent files are selected with 8 filesystem checks")
def selected_recent_files(context):
    available = [record for record in context.recent_records if record.available]
    expected = [
        RecentEntry(record.name, record.get_uri())
        for record in sorted(
            available, key=lambda record: record.modified, reverse=True
        )[:15]
    ]
    assert context.recent_applet._entries == expected
    expected_checks = min(
        len(context.recent_records),
        15 + sum(not record.available for record in context.recent_records),
    )
    assert sum(record.checks for record in context.recent_records) == expected_checks


@then("the recent-file change is published once")
def recent_change_published(context):
    context.recent_notify.assert_called_once()
