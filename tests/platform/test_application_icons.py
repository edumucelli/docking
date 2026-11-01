"""Shared artwork retains native/Gio sources and current painted dock pixels."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from gi.repository import GdkPixbuf, Gio

from docking.platform.application_icons import ApplicationIcons
from docking.platform.applications.types import TransientApplicationInfo
from tests.platform.application_fakes import application


def _image(color):
    image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 48, 48)
    image.fill(color)
    return image


def test_scaled_current_artwork_is_reused_and_invalidated_by_live_presentation_changes():
    model, loader, registry = MagicMock(), MagicMock(), MagicMock()
    original = _image(0xCC4422FF)
    model.find_by_desktop_id.return_value = SimpleNamespace(icon=original)
    source = ApplicationIcons(model=model, registry=registry, loader=loader)
    unsubscribe = source.subscribe(lambda _id: None)
    first = source.load("app", 16)
    assert source.load("app", 16) is first
    original.fill(0x2288CCFF)
    model.add_applet_change_listener.call_args.args[0]("app")
    changed = source.load("app", 16)
    assert changed is not first
    assert bytes(changed.get_pixels())[:4] == b"\x22\x88\xcc\xff"
    unsubscribe()


def test_menu_listings_keep_retained_gio_artwork_and_idless_launch_contexts():
    model, loader, registry = MagicMock(), MagicMock(), MagicMock()
    model.find_by_desktop_id.return_value = None
    gicon = Gio.ThemedIcon.new("test-gio-icon")
    handle = SimpleNamespace(get_icon=lambda: gicon)
    registry._gio_handle_for.return_value = handle
    registry._gio_handle_for_unidentified.return_value = handle
    source = ApplicationIcons(model=model, registry=registry, loader=loader)
    canonical = application("app")
    source.load_listing(canonical, 16)
    loader.load_desktop_icon.assert_called_once_with(
        info=canonical, size=16, gicon=gicon
    )
    listing = TransientApplicationInfo(
        listing_key="opaque:1", name="File handler", declared_icon="", desktop_file=None
    )
    source.load_listing(listing, 16)
    registry._gio_handle_for_unidentified.assert_called_once_with("opaque:1")
    loader.load_gicon.assert_called_once_with(gicon=gicon, size=16)
