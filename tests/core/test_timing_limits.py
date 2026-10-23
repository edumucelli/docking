"""Saved timing values must remain usable by real scheduling APIs."""

import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from docking.core.config import Config
from docking.core.updates import UpdateState, should_check_for_updates
from docking.ui.autohide import AutoHideController, HideState


def _wait_for_state(controller, state):
    from gi.repository import GLib

    deadline = time.monotonic() + 2
    while controller.state is not state and time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
    assert controller.state is state


@pytest.mark.parametrize("field", ["hide_delay_ms", "unhide_delay_ms", "hide_time_ms"])
def test_recovered_saved_timing_completes_hide_and_reveal(tmp_path, field):
    from gi.repository import Gtk

    path = tmp_path / "dock.json"
    path.write_text(json.dumps({"hide_mode": "autohide", field: 10**400}))
    window = Gtk.Window()
    window.show_all()
    controller = AutoHideController(
        SimpleNamespace(queue_redraw=window.queue_draw), Config.load(path)
    )
    try:
        controller.on_mouse_enter()
        controller.on_mouse_leave()
        _wait_for_state(controller, HideState.HIDDEN)
        assert controller.hide_offset == 1
        controller.on_mouse_enter()
        _wait_for_state(controller, HideState.VISIBLE)
        assert controller.hide_offset == 0
    finally:
        controller.reset()
        window.destroy()


@pytest.mark.parametrize("field", ["hide_delay_ms", "unhide_delay_ms", "hide_time_ms"])
@pytest.mark.parametrize("value", [2**32, 10**400, str(10**400)])
def test_oversized_saved_animation_timing_uses_default(tmp_path, field, value):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hide_mode": "autohide", field: value}))
    config = Config.load(path)
    assert getattr(config, field) == getattr(Config(), field)
    controller = AutoHideController(SimpleNamespace(queue_redraw=lambda: None), config)
    try:
        if field == "hide_delay_ms":
            controller.set_hovered(True)
            controller.set_hovered(False)
            assert controller._hide_timer_id > 0
        elif field == "unhide_delay_ms":
            controller.state = HideState.HIDDEN
            controller.hide_offset = 1.0
            controller.set_hovered(True)
            assert controller.state is HideState.SHOWING
        else:
            controller._start_hiding()
            controller._animation_tick()
    finally:
        controller.reset()


@pytest.mark.parametrize("field", ["hide_delay_ms", "unhide_delay_ms", "hide_time_ms"])
def test_representable_saved_animation_timing_is_preserved(field):
    assert getattr(Config(**{field: 2**32 - 1}), field) == 2**32 - 1


@pytest.mark.parametrize("interval", [24 * 10**9, 10**400])
def test_update_checks_accept_large_saved_intervals(interval):
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)
    state = UpdateState(last_checked_at=(now - timedelta(days=1)).isoformat())
    assert not should_check_for_updates(
        enabled=True, interval_hours=interval, state=state, now=now
    )


@pytest.mark.parametrize(
    "offset, due", [(timedelta(microseconds=-1), False), (timedelta(0), True)]
)
def test_update_interval_retains_exact_hour_boundary(offset, due):
    checked = datetime(2026, 10, 15, tzinfo=timezone.utc)
    assert (
        should_check_for_updates(
            enabled=True,
            interval_hours=24,
            state=UpdateState(last_checked_at=checked.isoformat()),
            now=checked + timedelta(hours=24) + offset,
        )
        is due
    )
