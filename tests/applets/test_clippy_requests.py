"""Deferred clipboard replies must respect ownership, privacy and applet lifetime."""

from types import SimpleNamespace

import gi
import pytest

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

import docking.applets.clippy.applet as clippy_mod
from docking.applets.clippy.applet import ClippyApplet
from docking.applets.clippy.state import MAX_IMAGE_BYTES, Clip
from docking.core.config import Config


class Clipboard:
    def __init__(self):
        self.handlers = {}
        self.targets = []
        self.images = []
        self.texts = []
        self.copied_text = []
        self.copied_images = []
        self.stores = 0

    def connect(self, signal, callback, selection):
        assert signal == "owner-change"
        self.handlers[1] = (callback, selection)
        return 1

    def disconnect(self, handler):
        del self.handlers[handler]

    def changed(self):
        callback, selection = self.handlers[1]
        callback(self, None, selection)

    def request_targets(self, callback, data=None):
        self.targets.append((callback, data))

    def request_image(self, callback, data):
        self.images.append((callback, data))

    def request_text(self, callback, data):
        self.texts.append((callback, data))

    def finish_targets(self, names):
        callback, data = self.targets.pop(0)
        targets = (
            None if names is None else [Gdk.Atom.intern(name, False) for name in names]
        )
        callback(self, targets, len(targets) if targets is not None else 0, data)

    def finish_text(self, value):
        callback, data = self.texts.pop(0)
        callback(self, value, data)

    def finish_image(self, image):
        callback, data = self.images.pop(0)
        callback(self, image, data)

    def set_text(self, value, length):
        assert length == -1
        self.copied_text.append(value)

    def set_image(self, image):
        self.copied_images.append(image)

    def store(self):
        self.stores += 1


class Clock:
    def __init__(self):
        self.next_id = 1
        self.pending = {}
        self.delays = []

    def timeout_add(self, delay, callback, *args):
        timer = self.next_id
        self.next_id += 1
        self.pending[timer] = (callback, args)
        self.delays.append(delay)
        return timer

    def source_remove(self, timer):
        del self.pending[timer]

    def run(self):
        for timer in list(self.pending):
            callback, args = self.pending.pop(timer)
            assert callback(*args) is False


@pytest.fixture
def capture(monkeypatch):
    clipboard, primary, clock = Clipboard(), Clipboard(), Clock()
    monkeypatch.setattr(clippy_mod.GLib, "timeout_add", clock.timeout_add)
    monkeypatch.setattr(clippy_mod.GLib, "source_remove", clock.source_remove)
    monkeypatch.setattr(
        clippy_mod.Gtk.Clipboard,
        "get",
        lambda selection: primary if selection == Gdk.SELECTION_PRIMARY else clipboard,
    )
    monkeypatch.setattr(clippy_mod, "is_x11_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        clippy_mod.Gdk.Display,
        "get_default",
        lambda: SimpleNamespace(supports_selection_notification=lambda: True),
    )
    applet = ClippyApplet(48, Config())
    applet.start(lambda: None)
    yield applet, clipboard, primary, clock
    applet.stop()


def toggle(value):
    return SimpleNamespace(get_active=lambda: value)


def request_text(clipboard, clock):
    clipboard.changed()
    clock.run()
    clipboard.finish_targets(["UTF8_STRING"])


def image(width=4, height=2):
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, width, height)
    pixbuf.fill(0x123456FF)
    return pixbuf


def test_debounces_owner_changes_and_reads_text_asynchronously(capture):
    applet, clipboard, _, clock = capture
    clipboard.changed()
    clipboard.changed()
    assert len(clock.pending) == 1
    assert clock.delays == [500, 500]
    assert not clipboard.targets
    clock.run()
    clipboard.finish_targets(["UTF8_STRING"])
    assert not applet._clips
    clipboard.finish_text("latest text")
    assert applet._clips == [Clip(text="latest text")]
    assert applet.item.name == "latest text"


@pytest.mark.parametrize(
    "targets", [None, [], ["UTF8_STRING", "x-kde-passwordManagerHint"]]
)
def test_unavailable_or_sensitive_targets_never_request_a_payload(capture, targets):
    applet, clipboard, _, clock = capture
    clipboard.changed()
    clock.run()
    clipboard.finish_targets(targets)
    assert not clipboard.images and not clipboard.texts
    assert not applet._clips


def test_owner_change_invalidates_an_older_reply(capture):
    applet, clipboard, _, clock = capture
    request_text(clipboard, clock)
    request_text(clipboard, clock)
    clipboard.finish_text("obsolete")
    assert not applet._clips
    clipboard.finish_text("current")
    assert applet._clips == [Clip(text="current")]


@pytest.mark.parametrize(
    "action", ["clear", "pause", "stop", "restart", "disable_images"]
)
@pytest.mark.parametrize("payload", ["text", "image"])
def test_pending_payload_cannot_survive_state_change(capture, action, payload):
    applet, clipboard, _, clock = capture
    clipboard.changed()
    clock.run()
    clipboard.finish_targets(["image/png"] if payload == "image" else ["UTF8_STRING"])
    if action == "clear":
        applet._clear()
    elif action == "pause":
        applet._on_tracking_toggled(toggle(False))
    elif action in ("stop", "restart"):
        applet.stop()
        if action == "restart":
            applet.start(lambda: None)
    else:
        applet._on_images_toggled(toggle(False))
    if payload == "image":
        clipboard.finish_image(image())
    else:
        clipboard.finish_text("late text")
    assert not applet._clips
    assert not clipboard.texts and not clipboard.images


def test_selections_debounce_and_complete_independently(capture):
    applet, clipboard, primary, clock = capture
    primary.changed()
    assert not clock.pending
    applet._on_selections_toggled(toggle(True))
    clipboard.changed()
    primary.changed()
    assert len(clock.pending) == 2
    clock.run()
    clipboard.finish_targets(["UTF8_STRING"])
    primary.finish_targets(["UTF8_STRING"])
    primary.finish_text("selected text")
    clipboard.finish_text("copied text")
    assert applet._clips == [Clip(text="selected text"), Clip(text="copied text")]


def test_pausing_keeps_history_copyable_without_capturing(capture):
    applet, clipboard, _, clock = capture
    applet.add_clip("saved")
    applet._on_tracking_toggled(toggle(False))
    clipboard.changed()
    assert not clock.pending
    applet.on_clicked()
    assert clipboard.copied_text == ["saved"]
    assert clipboard.stores == 1
    assert "paused" in applet.item.name
    assert applet._config.applet_prefs["clippy"]["tracking_enabled"] is False


def test_zero_limit_and_stop_cancel_pending_timers(capture):
    applet, clipboard, primary, clock = capture
    clipboard.changed()
    applet._set_max_entries(0)
    assert not clock.pending
    clipboard.changed()
    assert not clock.pending
    applet._set_max_entries(5)
    clipboard.changed()
    applet.stop()
    assert not clock.pending
    assert not clipboard.handlers and not primary.handlers


def test_images_round_trip_without_resizing_the_original(capture):
    applet, clipboard, _, clock = capture
    original = image()
    clipboard.changed()
    clock.run()
    clipboard.finish_targets(["image/png", "UTF8_STRING"])
    clipboard.finish_image(original)
    assert applet.item.name == "Image (4 × 2)"
    applet.on_clicked()
    copied = clipboard.copied_images[0]
    assert (copied.get_width(), copied.get_height()) == (4, 2)
    assert copied.get_pixels() == original.get_pixels()
    assert clipboard.stores == 1


def test_disabled_or_failed_image_capture_uses_text(capture):
    applet, clipboard, _, clock = capture
    clipboard.changed()
    clock.run()
    clipboard.finish_targets(["image/png", "UTF8_STRING"])
    clipboard.finish_image(None)
    clipboard.finish_text("image fallback")
    applet._on_images_toggled(toggle(False))
    clipboard.changed()
    clock.run()
    clipboard.finish_targets(["image/png", "UTF8_STRING"])
    assert not clipboard.images
    clipboard.finish_text("text only")
    assert [clip.text for clip in applet._clips] == ["image fallback", "text only"]


def test_oversized_image_is_rejected_before_copying_pixels(capture):
    applet, clipboard, _, clock = capture
    clipboard.changed()
    clock.run()
    clipboard.finish_targets(["image/png"])
    oversized = SimpleNamespace(
        get_bits_per_sample=lambda: 8,
        get_colorspace=lambda: GdkPixbuf.Colorspace.RGB,
        get_byte_length=lambda: MAX_IMAGE_BYTES + 1,
    )
    clipboard.finish_image(oversized)
    clipboard.finish_text(None)
    assert not applet._clips


def test_limit_changes_trim_immediately_and_preserve_selected_entry(capture):
    applet, _, _, _ = capture
    for text in ("old", "selected", "new"):
        applet.add_clip(text)
    applet._cur_position = 2
    applet._set_max_entries(2)
    assert applet._clips[applet._cur_position - 1].text == "selected"
    applet._set_max_entries(1)
    assert applet._cur_position == 1 and applet._clips[0].text == "new"
    applet._set_max_entries(0)
    assert applet._clips == [] and applet._cur_position == 0


def test_non_x11_selection_control_is_disabled():
    applet = ClippyApplet(48, Config())
    assert not applet.get_menu_items()[1].get_sensitive()
