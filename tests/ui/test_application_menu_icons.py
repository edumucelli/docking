"""Real menu rows retain widgets and release their borrowed artwork subscriptions."""

from unittest.mock import MagicMock

from gi.repository import GdkPixbuf, Gtk

from docking.applets.applications.icons import MenuIcons
from docking.applets.applications.render import make_menu_item_with_icon
from tests.platform.application_fakes import application


def _image(color):
    value = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 16, 16)
    value.fill(color)
    return value


def test_native_completion_updates_the_existing_row_and_drag_artwork():
    source = MagicMock()
    first, native = _image(0xCC4422FF), _image(0x2288CCFF)
    source.load_listing.return_value = first
    owner = MenuIcons(source)
    image = Gtk.Image()
    row = make_menu_item_with_icon("Application", image=image)
    app = application("app")
    try:
        owner.bind(row, app, image)
        source.load_listing.return_value = native
        source.subscribe.call_args.args[0](app.desktop_id)
        assert image.get_pixbuf() is native
        assert row.get_child().get_children()[0] is image
        drag = owner.drag_icon(row)
        assert (drag.get_width(), drag.get_height()) == (32, 32)
        assert bytes(drag.get_pixels())[:4] == bytes(native.get_pixels())[:4]
    finally:
        owner.stop()
        row.destroy()


def test_removed_filter_rows_and_closed_menus_reject_late_updates():
    source = MagicMock()
    source.load_listing.return_value = _image(0xCC4422FF)
    owner = MenuIcons(source)
    row, image = Gtk.MenuItem(), Gtk.Image()
    try:
        owner.bind(row, application("app"), image)
        original = image.get_pixbuf()
        owner.forget(row)
        source.load_listing.return_value = _image(0x2288CCFF)
        callback = source.subscribe.call_args.args[0]
        callback("app")
        assert image.get_pixbuf() is original
        owner.stop()
        owner.stop()
        callback(None)
        source.subscribe.return_value.assert_called_once_with()
        assert owner.drag_icon(row) is None
    finally:
        owner.stop()
        row.destroy()


def test_drag_artwork_preserves_non_square_custom_icon_shape():
    source = MagicMock()
    source.load_listing.return_value = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, True, 8, 16, 8
    )
    owner = MenuIcons(source)
    row, image = Gtk.MenuItem(), Gtk.Image()
    try:
        owner.bind(row, application("app"), image)
        drag = owner.drag_icon(row)
        assert (drag.get_width(), drag.get_height()) == (32, 16)
    finally:
        owner.stop()
        row.destroy()
