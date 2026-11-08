"""Canonical and live Search artwork without changing provider descriptors."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from gi.repository import GdkPixbuf

from docking.search.icons import SearchResultIcons
from docking.search.types import SearchIdentity, SearchResult


def image(width=48, height=48, color=0x2288CCFF):
    value = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, width, height)
    value.fill(color)
    return value


def result(provider="applications", desktop_id="app!App", icon_name=None):
    return SearchResult(
        identity=SearchIdentity(provider, "result"),
        title="Result",
        metadata=(("desktop_id", desktop_id),),
        icon_name=icon_name,
    )


@pytest.fixture
def icons():
    model, registry, loader = MagicMock(), MagicMock(), MagicMock()
    model.find_by_desktop_id.return_value = None
    registry.get.return_value = None
    return SimpleNamespace(
        value=SearchResultIcons(model=model, registry=registry, loader=loader),
        model=model,
        registry=registry,
        loader=loader,
    )


@pytest.mark.parametrize("provider", ["applications", "windows"])
def test_unpinned_application_uses_canonical_loader_including_native_windows(
    icons, provider
):
    application = icons.registry.get.return_value = object()
    expected = icons.loader.load_desktop_icon.return_value = image(32, 32)
    assert icons.value.load(result(provider), 32) is expected
    icons.registry.get.assert_called_once_with("app!App")
    icons.loader.load_desktop_icon.assert_called_once_with(info=application, size=32)
    icons.loader.load_icon.assert_not_called()


@pytest.mark.parametrize("provider", ["dock", "applications", "windows"])
def test_current_dock_artwork_preserves_applets_custom_icons_and_aspect_ratio(
    icons, provider
):
    source = image(80, 40)
    icons.model.find_by_desktop_id.return_value = SimpleNamespace(icon=source)
    actual = icons.value.load(result(provider), 32)
    assert (actual.get_width(), actual.get_height()) == (32, 16)
    assert bytes(actual.get_pixels())[:4] == bytes(source.get_pixels())[:4]
    icons.registry.get.assert_not_called()
    icons.loader.load_icon.assert_not_called()


def test_current_artwork_is_reused_when_already_at_result_size(icons):
    source = image(32, 32)
    icons.model.find_by_desktop_id.return_value = SimpleNamespace(icon=source)
    assert icons.value.load(result("dock"), 32) is source


def test_removed_item_missing_application_and_unrelated_provider_use_theme_fallback(
    icons,
):
    expected = icons.loader.load_icon.return_value = image(32, 32)
    assert icons.value.load(result(icon_name="notes"), 32) is expected
    icons.loader.load_icon.assert_called_with(icon_name="notes", size=32)
    icons.model.find_by_desktop_id.reset_mock()
    assert icons.value.load(result("calculator"), 32) is expected
    icons.model.find_by_desktop_id.assert_not_called()
    icons.loader.load_icon.assert_called_with(icon_name="system-search", size=32)


def test_live_item_icon_is_read_again_after_a_presentation_change(icons):
    item = SimpleNamespace(icon=image(32, 32))
    icons.model.find_by_desktop_id.return_value = item
    first = icons.value.load(result("dock"), 32)
    item.icon = image(32, 32, 0xCC4422FF)
    assert icons.value.load(result("dock"), 32) is item.icon
    assert item.icon is not first


def test_change_subscription_releases_native_model_and_applet_listeners_once(icons):
    changed = MagicMock()
    unsubscribe = icons.value.subscribe(changed)
    native_changed = icons.loader.subscribe.call_args.args[0]
    model_changed = icons.model.add_change_listener.call_args.args[0]
    applet_changed = icons.model.add_applet_change_listener.call_args.args[0]
    native_changed("app!App")
    applet_changed("applet://clock")
    model_changed()
    assert [call.args for call in changed.call_args_list] == [
        ("app!App",),
        ("applet://clock",),
        (None,),
    ]
    unsubscribe()
    unsubscribe()
    icons.loader.subscribe.return_value.assert_called_once()
    icons.model.remove_change_listener.assert_called_once_with(model_changed)
    icons.model.remove_applet_change_listener.assert_called_once_with(applet_changed)


def test_invalid_icon_size_does_not_request_native_or_theme_work(icons):
    assert icons.value.load(result(), 0) is None
    icons.model.find_by_desktop_id.assert_not_called()
    icons.loader.load_icon.assert_not_called()
