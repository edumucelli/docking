"""Drive native capture callbacks and verify that cached bitmap pixels change."""

from behave import given, then, when
from gi.repository import GdkPixbuf
from pytest import MonkeyPatch

from docking.platform.backends.base import DisplayServer, PreviewImage, WindowId
from docking.platform.backends.wayland import previews as preview_mod
from tests.platform.test_wayland_previews import _SizedPreviewRig


@given('a "{kind}" native preview showing a red window')
def red_preview(context, kind):
    monkeypatch = MonkeyPatch()
    context.add_cleanup(monkeypatch.undo)
    context.preview_rig = _SizedPreviewRig(kind, monkeypatch)
    context.preview_now = 0.0
    context.preview_color = 0xFF0000FF
    context.preview_rig.service._clock = lambda: context.preview_now
    context.preview_window = WindowId(DisplayServer.WAYLAND, 1)

    def image_from_window(_request):
        image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 2, 2)
        image.fill(context.preview_color)
        return PreviewImage(image=image, width=2, height=2)

    monkeypatch.setattr(preview_mod, "_pixbuf_from_request", image_from_window)
    assert context.preview_rig.capture(context.preview_window, 2, 2) is None
    context.preview_rig.complete(0)
    context.red_preview = context.preview_rig.capture(context.preview_window, 2, 2)
    assert context.red_preview.image.get_pixels()[:4] == b"\xff\x00\x00\xff"


@when("the native window turns green and two seconds pass")
def changed_window(context):
    context.preview_now = 2.0
    context.preview_color = 0x00FF00FF
    assert (
        context.preview_rig.capture(context.preview_window, 2, 2) is context.red_preview
    )
    assert len(context.preview_rig.captures) == 2
    context.preview_rig.complete(1)


@then("the native preview refreshes to green pixels")
def green_preview(context):
    updated = context.preview_rig.capture(context.preview_window, 2, 2)
    assert updated is not context.red_preview
    assert updated.image.get_pixels()[:4] == b"\x00\xff\x00\xff"
    assert len(context.preview_rig.captures) == 2
