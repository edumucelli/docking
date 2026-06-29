"""Tests for startup popup arbitration."""

from __future__ import annotations

from dataclasses import dataclass, field

import docking.ui.startup_popups as popups_mod
from docking.ui.startup_popups import StartupPopupCoordinator


class _FakeGLib:
    def __init__(self) -> None:
        self.next_id = 100
        self.scheduled: dict[int, tuple[int, object, tuple[object, ...]]] = {}
        self.removed: list[int] = []

    def timeout_add_seconds(self, seconds, callback, *args):
        self.next_id += 1
        self.scheduled[self.next_id] = (seconds, callback, args)
        return self.next_id

    def source_remove(self, source_id):
        self.removed.append(source_id)


@dataclass
class _Source:
    source_id: str
    priority: int
    max_wait_seconds: int | None = None
    visible: bool = False
    start_calls: int = 0
    stop_calls: int = 0
    show_calls: int = 0
    request_show: object | None = None
    visibility_changed: object | None = None
    events: list[str] = field(default_factory=list)

    def start(self, request_show, visibility_changed) -> None:
        self.start_calls += 1
        self.request_show = request_show
        self.visibility_changed = visibility_changed

    def stop(self) -> None:
        self.stop_calls += 1

    def show_pending(self) -> bool:
        self.show_calls += 1
        self.visible = True
        self.events.append(f"show:{self.source_id}")
        self.visibility_changed(self.source_id, True)
        return True

    def close(self) -> None:
        self.visible = False
        self.visibility_changed(self.source_id, False)


def test_start_and_stop_delegate_to_sources(monkeypatch):
    fake_glib = _FakeGLib()
    monkeypatch.setattr(popups_mod, "GLib", fake_glib)
    coordinator = StartupPopupCoordinator()
    new_year = _Source("new-year", 10)
    updates = _Source("updates", 20)

    coordinator.register(updates)
    coordinator.register(new_year)
    coordinator.start()
    coordinator.stop()

    assert new_year.start_calls == 1
    assert updates.start_calls == 1
    assert new_year.stop_calls == 1
    assert updates.stop_calls == 1


def test_higher_priority_pending_source_shows_first(monkeypatch):
    monkeypatch.setattr(popups_mod, "GLib", _FakeGLib())
    coordinator = StartupPopupCoordinator()
    shown: list[str] = []
    blocker = _Source("blocker", 5, events=shown)
    new_year = _Source("new-year", 10, events=shown)
    tips = _Source("startup-tips", 30, events=shown)
    coordinator.register(blocker)
    coordinator.register(new_year)
    coordinator.register(tips)
    coordinator.start()

    blocker.request_show("blocker")
    tips.request_show("startup-tips")
    new_year.request_show("new-year")

    assert shown == ["show:blocker"]
    blocker.close()
    assert shown == ["show:blocker", "show:new-year"]


def test_lower_priority_waits_while_higher_priority_is_visible(monkeypatch):
    monkeypatch.setattr(popups_mod, "GLib", _FakeGLib())
    coordinator = StartupPopupCoordinator()
    shown: list[str] = []
    updates = _Source("updates", 20, events=shown)
    tips = _Source("startup-tips", 30, events=shown)
    coordinator.register(updates)
    coordinator.register(tips)
    coordinator.start()

    updates.request_show("updates")
    tips.request_show("startup-tips")

    assert shown == ["show:updates"]
    updates.close()
    assert shown == ["show:updates", "show:startup-tips"]


def test_pending_source_expires(monkeypatch):
    fake_glib = _FakeGLib()
    monkeypatch.setattr(popups_mod, "GLib", fake_glib)
    coordinator = StartupPopupCoordinator()
    updates = _Source("updates", 20)
    tips = _Source("startup-tips", 30, max_wait_seconds=30)
    coordinator.register(updates)
    coordinator.register(tips)
    coordinator.start()

    updates.request_show("updates")
    tips.request_show("startup-tips")
    timeout_id = next(iter(fake_glib.scheduled))
    _seconds, callback, args = fake_glib.scheduled[timeout_id]

    assert callback(*args) is False
    updates.close()

    assert tips.show_calls == 0
