"""Exercise real applet fetch callbacks after unload with deferred GTK delivery."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from behave import given, then, when
from pytest import MonkeyPatch

from docking.core.config import Config
from tests.applets.test_stopped_fetches import make_fetch_applet


@given('a "{name}" network reply is waiting for the GTK thread')
def pending_network_reply(context, name):
    directory = TemporaryDirectory(prefix="docking-bdd-unload-")
    context.add_cleanup(directory.cleanup)
    patch = MonkeyPatch()
    context.add_cleanup(patch.undo)
    applet, config, queue = make_fetch_applet(name, patch, Path(directory.name))
    applet._fetch_async()
    queue.read()
    context.unloaded_applet, context.unloaded_config = applet, config
    context.unloaded_queue, context.unloaded_name = queue, name
    context.unloaded_patch = patch


@when("I unload that applet and save a newer preference selection")
def unload_before_reply(context):
    context.unloaded_applet.stop()
    name = context.unloaded_name
    key = {"news": "active_source_index", "reddit": "active_subreddit_index"}.get(
        name, "active_index"
    )
    context.newer_preferences = {key: 1}
    context.unloaded_config.applet_prefs[name] = context.newer_preferences.copy()
    context.unloaded_config.save()
    context.unloaded_present = MagicMock()
    context.unloaded_patch.setattr(
        context.unloaded_applet, "present", context.unloaded_present
    )


@when("the old network reply is delivered")
def deliver_unloaded_reply(context):
    context.unloaded_queue.publish()


@then("the newer preferences remain saved and no further request starts")
def unchanged_after_unload(context):
    saved = Config.load(context.unloaded_config._path)
    assert saved.applet_prefs[context.unloaded_name] == context.newer_preferences
    context.unloaded_present.assert_not_called()
    assert not context.unloaded_queue.tasks
