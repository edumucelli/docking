from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

import docking.ui.new_year as new_year_mod
from docking.core.position import Position
from docking.ui.new_year import NewYearGreetingController
from docking.ui.popup import PopupAnchor


class _FakeGLib:
    def __init__(self) -> None:
        self.next_id = 10
        self.scheduled: list[tuple[int, object]] = []
        self.removed: list[int] = []

    def timeout_add_seconds(self, seconds, callback):
        self.next_id += 1
        self.scheduled.append((seconds, callback))
        return self.next_id

    def source_remove(self, source_id):
        self.removed.append(source_id)


class _FakeScreen:
    def __init__(self) -> None:
        self.visual = object()

    def get_rgba_visual(self):
        return self.visual

    def get_width(self):
        return 800

    def get_height(self):
        return 600


class _FakePopup:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.child = None
        self.destroyed = False
        self.hidden = False
        self.shown = False
        self.moved_to = None
        self.screen = _FakeScreen()
        self.connections: dict[str, object] = {}
        self.visual = None

    def set_decorated(self, value):
        self.decorated = value

    def set_skip_taskbar_hint(self, value):
        self.skip_taskbar = value

    def set_resizable(self, value):
        self.resizable = value

    def set_type_hint(self, value):
        self.type_hint = value

    def set_app_paintable(self, value):
        self.app_paintable = value

    def set_transient_for(self, window):
        self.transient_for = window

    def get_transient_for(self):
        return getattr(self, "transient_for", None)

    def connect(self, signal, callback):
        self.connections[signal] = callback

    def get_screen(self):
        return self.screen

    def set_visual(self, visual):
        self.visual = visual

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
        return (None, SimpleNamespace(width=180, height=64))

    def move(self, x, y):
        self.moved_to = (x, y)

    def destroy(self):
        self.destroyed = True

    def hide(self):
        self.hidden = True


class _FakeBox:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.margins: dict[str, int] = {}
        self.children: list[object] = []

    def set_margin_start(self, value):
        self.margins["start"] = value

    def set_margin_end(self, value):
        self.margins["end"] = value

    def set_margin_top(self, value):
        self.margins["top"] = value

    def set_margin_bottom(self, value):
        self.margins["bottom"] = value

    def pack_start(self, child, *args):
        self.children.append((child, args))


class _FakeImage:
    @classmethod
    def new_from_icon_name(cls, name, size):
        obj = cls()
        obj.name = name
        obj.size = size
        return obj

    def set_valign(self, value):
        self.valign = value


class _FakeLabel:
    def __init__(self, *, label="") -> None:
        self.label = label

    def set_xalign(self, value):
        self.xalign = value

    def set_yalign(self, value):
        self.yalign = value

    def override_color(self, *args):
        self.color = args


class _FakeWindow:
    def __init__(self, *, pos=Position.BOTTOM, realized=True) -> None:
        self.config = SimpleNamespace(pos=pos)
        self.realized = realized
        self.surface_service = SimpleNamespace(
            popups_use_parent_relative_coordinates=False,
        )

    def get_realized(self):
        return self.realized

    def get_position(self):
        return (100, 200)

    def get_size(self):
        return (300, 48)

    def popup_anchor(self):
        if not self.realized:
            return None
        x = 250 if self.config.pos in (Position.BOTTOM, Position.TOP) else 100
        y = 200 if self.config.pos in (Position.BOTTOM, Position.RIGHT) else 248
        return PopupAnchor(
            x=x,
            y=y,
            position=self.config.pos,
            parent=self,
        )


class _FakeCr:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def __getattr__(self, name):
        def method(*args):
            self.calls.append((name, args))

        return method


@pytest.fixture
def fake_gtk(monkeypatch):
    glib = _FakeGLib()
    monkeypatch.setattr(new_year_mod, "GLib", glib)
    monkeypatch.setattr(
        new_year_mod,
        "Gtk",
        SimpleNamespace(
            Window=_FakePopup,
            WindowType=SimpleNamespace(POPUP="popup"),
            Box=_FakeBox,
            Orientation=SimpleNamespace(HORIZONTAL="horizontal"),
            Image=_FakeImage,
            IconSize=SimpleNamespace(DIALOG="dialog"),
            Label=_FakeLabel,
            Align=SimpleNamespace(CENTER="center"),
            StateFlags=SimpleNamespace(NORMAL="normal"),
        ),
    )
    monkeypatch.setattr(
        new_year_mod,
        "Gdk",
        SimpleNamespace(
            WindowTypeHint=SimpleNamespace(NOTIFICATION="notification"),
            RGBA=lambda *args: args,
        ),
    )
    return glib


def test_start_is_idempotent_and_stop_cleans_sources(fake_gtk):
    controller = NewYearGreetingController(anchor_provider=_FakeWindow())
    controller._popup = _FakePopup()

    controller.start()
    controller.start()

    assert len(fake_gtk.scheduled) == 1
    assert controller._start_source_id

    controller._hide_source_id = 77
    controller.stop()

    assert fake_gtk.removed == [controller._start_source_id or 11, 77]
    assert controller._start_source_id == 0
    assert controller._hide_source_id == 0
    assert controller._popup is None


def test_startup_complete_consumes_greeting_and_shows_popup(monkeypatch):
    controller = NewYearGreetingController(
        anchor_provider=_FakeWindow(),
        state_path="/tmp/state.json",
        now_fn=lambda: datetime(2026, 1, 2),
    )
    shown: list[int] = []

    monkeypatch.setattr(
        new_year_mod,
        "consume_new_year_greeting",
        lambda **kwargs: 2026,
    )
    monkeypatch.setattr(controller, "_show_popup", lambda *, year: shown.append(year))

    controller._start_source_id = 42
    assert controller._on_startup_complete() is False
    assert controller._start_source_id == 0
    assert shown == [2026]


def test_startup_complete_noops_when_not_due(monkeypatch):
    controller = NewYearGreetingController(anchor_provider=_FakeWindow())
    monkeypatch.setattr(new_year_mod, "consume_new_year_greeting", lambda **_k: None)
    monkeypatch.setattr(
        controller,
        "_show_popup",
        lambda **_k: (_ for _ in ()).throw(AssertionError("unexpected")),
    )

    assert controller._on_startup_complete() is False


def test_show_popup_skips_unrealized_window(fake_gtk):
    controller = NewYearGreetingController(anchor_provider=_FakeWindow(realized=False))

    controller._show_popup(year=2026)

    assert controller._popup is None
    assert fake_gtk.scheduled == []


def test_show_popup_builds_positions_and_reuses_popup(fake_gtk):
    controller = NewYearGreetingController(
        anchor_provider=_FakeWindow(pos=Position.BOTTOM)
    )

    controller._show_popup(year=2026)
    popup = controller._popup

    assert isinstance(popup, _FakePopup)
    assert popup.shown
    assert popup.moved_to is not None
    assert popup.child is not None
    assert fake_gtk.scheduled[-1][0] == new_year_mod.NEW_YEAR_GREETING_DURATION_S

    first_child = popup.child
    controller._hide_source_id = 88
    controller._show_popup(year=2027)

    assert popup.child is not first_child
    assert fake_gtk.removed[-1] == 88


@pytest.mark.parametrize(
    ("pos", "margin_key"),
    [
        (Position.BOTTOM, "bottom"),
        (Position.TOP, "top"),
        (Position.LEFT, "start"),
        (Position.RIGHT, "end"),
    ],
)
def test_build_popup_content_margins_by_position(fake_gtk, pos, margin_key):
    controller = NewYearGreetingController(anchor_provider=_FakeWindow(pos=pos))

    box = controller._build_popup_content(year=2026, position=pos)

    assert isinstance(box, _FakeBox)
    assert box.margins[margin_key] == (
        new_year_mod.GREETING_MARGIN_PX + new_year_mod.GREETING_TIP_HEIGHT_PX
    )
    assert len(box.children) == 2


@pytest.mark.parametrize("pos", list(Position))
def test_position_popup_handles_all_edges(fake_gtk, pos):
    controller = NewYearGreetingController(anchor_provider=_FakeWindow(pos=pos))
    controller._popup = _FakePopup()

    controller._position_popup(anchor=_FakeWindow(pos=pos).popup_anchor())

    assert controller._popup.moved_to is not None


def test_position_popup_noops_without_popup(fake_gtk):
    controller = NewYearGreetingController(anchor_provider=_FakeWindow())

    controller._position_popup(anchor=_FakeWindow().popup_anchor())

    assert controller._popup is None


@pytest.mark.parametrize("pos", list(Position))
def test_draw_popup_handles_all_tips(fake_gtk, pos):
    controller = NewYearGreetingController(anchor_provider=_FakeWindow(pos=pos))
    cr = _FakeCr()
    widget = SimpleNamespace(
        get_allocation=lambda: SimpleNamespace(width=180, height=64)
    )

    assert controller._on_popup_draw(widget, cr) is False

    called = [name for name, _args in cr.calls]
    assert "fill_preserve" in called
    assert "stroke" in called


def test_hide_and_button_press_remove_timer(fake_gtk):
    controller = NewYearGreetingController(anchor_provider=_FakeWindow())
    controller._popup = _FakePopup()
    controller._hide_source_id = 99

    assert controller._on_popup_button_press(None, None) is True

    assert fake_gtk.removed == [99]
    assert controller._hide_source_id == 0
    assert controller._popup.hidden

    controller._hide_source_id = 100
    assert controller._hide_popup() is False
    assert controller._hide_source_id == 0
