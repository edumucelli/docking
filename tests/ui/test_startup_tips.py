"""Tests for the startup tips popup controller."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import docking.ui.startup_tips as tips_ui
from docking.core.position import Position
from docking.core.tips import FIRST_TIP_ID, StartupTip
from docking.ui.startup_tips import StartupTipsController


class _FakeGLib:
    def __init__(self) -> None:
        self.next_id = 40
        self.scheduled: list[tuple[int, object]] = []
        self.removed: list[int] = []

    def timeout_add_seconds(self, seconds, callback):
        self.next_id += 1
        self.scheduled.append((seconds, callback))
        return self.next_id

    def source_remove(self, source_id):
        self.removed.append(source_id)


class _FakePopup:
    def __init__(self, *args, **kwargs) -> None:
        self.child = None
        self.destroyed = False
        self.hidden = False
        self.shown = False
        self.moved_to = None
        self.connections = {}

    def set_decorated(self, value):
        self.decorated = value

    def set_skip_taskbar_hint(self, value):
        self.skip_taskbar = value

    def set_resizable(self, value):
        self.resizable = value

    def set_type_hint(self, value):
        self.type_hint = value

    def set_transient_for(self, window):
        self.transient_for = window

    def get_transient_for(self):
        return getattr(self, "transient_for", None)

    def get_screen(self):
        return SimpleNamespace(get_width=lambda: 800, get_height=lambda: 600)

    def connect(self, signal, callback):
        self.connections[signal] = callback

    def get_child(self):
        return self.child

    def remove(self, child):
        if self.child is child:
            self.child = None

    def add(self, child):
        self.child = child

    def show_all(self):
        self.shown = True

    def get_preferred_size(self):
        return (None, SimpleNamespace(width=260, height=130))

    def move(self, x, y):
        self.moved_to = (x, y)

    def hide(self):
        self.hidden = True

    def destroy(self):
        self.destroyed = True


class _FakeFrame:
    def set_shadow_type(self, value):
        self.shadow_type = value

    def add(self, child):
        self.child = child


class _FakeBox:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.children = []
        self.border_width = 0
        self.hexpand = False
        self.style_context = _FakeStyleContext()

    def set_border_width(self, value):
        self.border_width = value

    def set_hexpand(self, value):
        self.hexpand = value

    def pack_start(self, child, *args):
        self.children.append(child)

    def get_style_context(self):
        return self.style_context


class _FakeStyleContext:
    def __init__(self) -> None:
        self.classes = []

    def add_class(self, class_name):
        self.classes.append(class_name)


class _FakeLabel:
    def __init__(self, *, label="") -> None:
        self.label = label
        self.markup = ""
        self.style_context = _FakeStyleContext()

    def set_xalign(self, value):
        self.xalign = value

    def set_yalign(self, value):
        self.yalign = value

    def set_line_wrap(self, value):
        self.line_wrap = value

    def set_max_width_chars(self, value):
        self.max_width_chars = value

    def set_markup(self, value):
        self.markup = value

    def get_style_context(self):
        return self.style_context


class _FakeButton:
    def __init__(self, *, label="") -> None:
        self.label = label
        self.handlers = {}

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def click(self):
        self.handlers["clicked"](self)


class _FakeCheckButton(_FakeButton):
    def __init__(self, *, label="") -> None:
        super().__init__(label=label)
        self.active = False

    def set_active(self, value):
        self.active = value

    def get_active(self):
        return self.active


class _FakeImage:
    def __init__(self, icon_name, icon_size) -> None:
        self.icon_name = icon_name
        self.icon_size = icon_size

    @classmethod
    def new_from_icon_name(cls, icon_name, icon_size):
        return cls(icon_name, icon_size)

    def set_valign(self, value):
        self.valign = value


class _FakeIconTheme:
    @staticmethod
    def get_default():
        return _FakeIconTheme()

    def has_icon(self, icon_name):
        return icon_name == tips_ui.STARTUP_TIP_ICON_NAME


class _FakeWindow:
    def __init__(self, *, realized=True, pos=Position.BOTTOM) -> None:
        self.realized = realized
        self.config = SimpleNamespace(pos=pos)
        self.surface_service = SimpleNamespace(
            popups_use_parent_relative_coordinates=False,
        )

    def get_realized(self):
        return self.realized

    def get_position(self):
        return (100, 200)

    def get_size(self):
        return (300, 48)


@pytest.fixture
def fake_gtk(monkeypatch):
    glib = _FakeGLib()
    monkeypatch.setattr(tips_ui, "GLib", glib)
    monkeypatch.setattr(
        tips_ui,
        "Gtk",
        SimpleNamespace(
            Window=_FakePopup,
            WindowType=SimpleNamespace(POPUP="popup"),
            Frame=_FakeFrame,
            ShadowType=SimpleNamespace(OUT="out"),
            Box=_FakeBox,
            Orientation=SimpleNamespace(VERTICAL="vertical", HORIZONTAL="horizontal"),
            Align=SimpleNamespace(START="start"),
            IconSize=SimpleNamespace(DIALOG="dialog"),
            IconTheme=_FakeIconTheme,
            Image=_FakeImage,
            Label=_FakeLabel,
            Button=_FakeButton,
            CheckButton=_FakeCheckButton,
        ),
    )
    monkeypatch.setattr(
        tips_ui,
        "Gdk",
        SimpleNamespace(WindowTypeHint=SimpleNamespace(NOTIFICATION="notification")),
    )
    monkeypatch.setattr(
        tips_ui,
        "configure_transparent_startup_popup_window",
        lambda _popup: None,
    )
    monkeypatch.setattr(tips_ui, "wrap_startup_popup_content", _wrap_fake_popup)
    return glib


def _wrap_fake_popup(content):
    frame = _FakeFrame()
    frame.add(content)
    return frame


def _config(*, enabled=True):
    return SimpleNamespace(startup_tips_enabled=enabled, save=lambda: None)


def test_start_is_idempotent_and_stop_cleans_source(fake_gtk):
    requests = []
    controller = StartupTipsController(
        window=_FakeWindow(),
        config=_config(),
    )

    controller.start(lambda source_id: requests.append(source_id), lambda *_a: None)
    controller.start(lambda source_id: requests.append(source_id), lambda *_a: None)

    assert len(fake_gtk.scheduled) == 1
    source_id = controller._start_source_id

    fake_gtk.scheduled[0][1]()
    assert requests == [tips_ui.STARTUP_TIP_POPUP_ID]

    controller.stop()

    assert fake_gtk.removed == []
    assert controller._start_source_id == 0

    controller.start(lambda *_a: None, lambda *_a: None)
    controller.stop()

    assert fake_gtk.removed == [source_id + 1]


def test_start_disabled_does_not_schedule(fake_gtk):
    controller = StartupTipsController(
        window=_FakeWindow(),
        config=_config(enabled=False),
    )

    controller.start()

    assert fake_gtk.scheduled == []


def test_show_popup_skips_unrealized_window(fake_gtk):
    controller = StartupTipsController(
        window=_FakeWindow(realized=False),
        config=_config(),
    )

    controller._show_popup(
        tip=StartupTip(FIRST_TIP_ID, "Title", "Body"),
    )

    assert controller._popup is None


def test_show_next_tip_skips_unrealized_window_without_state(fake_gtk, tmp_path):
    path = tmp_path / "tips.json"
    controller = StartupTipsController(
        window=_FakeWindow(realized=False),
        config=_config(),
        state_path=path,
    )

    controller.show_pending()

    assert controller._popup is None
    assert not path.exists()


def test_show_popup_marks_active_and_positions(fake_gtk):
    visible = []
    controller = StartupTipsController(
        window=_FakeWindow(),
        config=_config(),
    )
    controller.start(
        lambda *_a: None, lambda source_id, state: visible.append((source_id, state))
    )

    controller._show_popup(tip=StartupTip(FIRST_TIP_ID, "Title", "Body"))

    assert isinstance(controller._popup, _FakePopup)
    assert controller._popup.shown
    assert controller._popup.moved_to is not None
    assert visible[-1] == (tips_ui.STARTUP_TIP_POPUP_ID, True)


def test_close_hides_and_deactivates(fake_gtk):
    visible = []
    controller = StartupTipsController(
        window=_FakeWindow(),
        config=_config(),
    )
    controller.start(
        lambda *_a: None, lambda source_id, state: visible.append((source_id, state))
    )
    controller._show_popup(tip=StartupTip(FIRST_TIP_ID, "Title", "Body"))

    controller._on_close(_FakeButton(label="Close"))

    assert controller._popup.hidden
    assert visible[-1] == (tips_ui.STARTUP_TIP_POPUP_ID, False)


def test_close_with_startup_checkbox_disabled_saves_preference(fake_gtk):
    saved = []
    config = SimpleNamespace(
        startup_tips_enabled=True,
        save=lambda: saved.append(True),
    )
    controller = StartupTipsController(
        window=_FakeWindow(),
        config=config,
    )
    controller._show_popup(tip=StartupTip(FIRST_TIP_ID, "Title", "Body"))
    controller._show_on_startup_check.set_active(False)

    controller._on_close(_FakeButton(label="Close"))

    assert config.startup_tips_enabled is False
    assert saved == [True]


def test_never_show_disables_saves_and_destroys(fake_gtk):
    saved = []
    config = SimpleNamespace(
        startup_tips_enabled=True,
        save=lambda: saved.append(True),
    )
    visible = []
    controller = StartupTipsController(
        window=_FakeWindow(),
        config=config,
    )
    controller.start(
        lambda *_a: None, lambda source_id, state: visible.append((source_id, state))
    )
    controller._show_popup(tip=StartupTip(FIRST_TIP_ID, "Title", "Body"))
    popup = controller._popup

    controller._on_never_show(_FakeButton(label="Never"))

    assert config.startup_tips_enabled is False
    assert saved == [True]
    assert popup.destroyed
    assert visible[-1] == (tips_ui.STARTUP_TIP_POPUP_ID, False)


def test_next_tip_selects_and_replaces_content(fake_gtk, tmp_path):
    controller = StartupTipsController(
        window=_FakeWindow(),
        config=_config(),
        state_path=tmp_path / "tips.json",
        chooser=lambda tips: tips[0],
    )

    controller.show_pending()
    first_child = controller._popup.child
    controller._on_next_tip(_FakeButton(label="Next"))

    assert controller._popup.child is not first_child
