"""Reproduce input races with production workers and delayed GTK replies."""

from unittest.mock import MagicMock

import pytest

from docking.applets.brightness.state import BrightnessState
from docking.applets.powerprofiles.state import PowerProfilesState, profile_label
from docking.applets.volume.state import VolumeState
from tests.applets.deferred_worker import DeferredWorker
from tests.applets.test_brightness import _make_applet as brightness_applet
from tests.applets.test_powerprofiles import _make_applet as profiles_applet
from tests.applets.test_volume import _make_applet as volume_applet


def test_rapid_brightness_scroll_applies_the_last_requested_value(monkeypatch):
    applet = brightness_applet(brightness=0.5)
    queue = DeferredWorker()
    applet._worker = queue.worker
    written = []
    monkeypatch.setattr(
        applet._backend,
        "set_brightness",
        lambda *, target, value: (
            written.append(value) or BrightnessState(value, target)
        ),
    )

    for _ in range(5):
        applet.on_scroll(direction_up=True)
    queue.drain()

    assert written[-1] == pytest.approx(0.6)
    assert applet._brightness == pytest.approx(written[-1])
    assert applet.item.name == "Brightness: 60%"


def test_brightness_reset_is_ordered_after_pending_scroll(monkeypatch):
    applet = brightness_applet(brightness=0.5)
    queue = DeferredWorker()
    applet._worker = queue.worker
    written = []
    monkeypatch.setattr(
        applet._backend,
        "set_brightness",
        lambda *, target, value: (
            written.append(value) or BrightnessState(value, target)
        ),
    )
    applet.on_scroll(direction_up=True)
    applet.on_clicked()
    # An unguarded reset must not start concurrently with an older write.
    assert len(queue.tasks) == 1
    queue.drain()
    assert written[-1] == 1.0


@pytest.mark.parametrize("action", ["scroll", "mute"])
def test_old_volume_poll_does_not_undo_user_input(action):
    applet = volume_applet(VolumeState(volume=45, muted=False))
    queue = DeferredWorker()
    applet._worker = queue.worker
    applet._tick()
    queue.read()  # The old backend state is already waiting for GTK.
    if action == "scroll":
        updated = VolumeState(volume=50, muted=False)
        applet._backend.controls.get_state.return_value = updated
        applet.on_scroll(direction_up=True)
    else:
        updated = VolumeState(volume=45, muted=True)
        applet._backend.controls.get_state.return_value = updated
        applet.on_clicked()
    queue.publish()
    assert (applet._volume, applet._muted) == (updated.volume, updated.muted)
    assert applet.item.name == ("Volume: 50%" if action == "scroll" else "Muted")


def test_profile_menu_keeps_the_last_choice_during_pending_write(monkeypatch):
    applet, backend = profiles_applet(
        monkeypatch,
        PowerProfilesState(
            available=True,
            active_profile="balanced",
            profiles=("balanced", "performance", "power-saver"),
        ),
    )
    queue = DeferredWorker()
    applet._worker = queue.worker
    applet._set_profile_async(profile="performance")
    applet._set_profile_async(profile="power-saver")
    queue.drain()
    assert backend.set_calls == ["performance", "power-saver"]
    assert applet._state.active_profile == "power-saver"
    assert profile_label("power-saver") in applet.item.name


def test_profile_menu_can_cancel_pending_change_by_reselecting_original(monkeypatch):
    applet, backend = profiles_applet(
        monkeypatch,
        PowerProfilesState(
            True, "balanced", ("balanced", "performance", "power-saver")
        ),
    )
    queue = DeferredWorker()
    applet._worker = queue.worker
    selected = MagicMock()
    selected.get_active.return_value = True
    applet._on_profile_toggled(selected, "performance")
    applet._on_profile_toggled(selected, "balanced")
    queue.drain()
    assert backend.set_calls == ["performance", "balanced"]
    assert applet._state.active_profile == "balanced"


def test_old_brightness_poll_cannot_restore_a_value_before_the_last_write(monkeypatch):
    applet = brightness_applet(brightness=0.5)
    queue = DeferredWorker()
    applet._worker = queue.worker
    monkeypatch.setattr(
        applet._backend, "get_state", lambda: BrightnessState(0.5, "test-panel")
    )
    monkeypatch.setattr(
        applet._backend,
        "set_brightness",
        lambda *, target, value: BrightnessState(value, target),
    )
    applet._tick()
    queue.read()
    applet.on_scroll(direction_up=True)
    queue.read()
    queue.publish()  # Old poll arrives while a write reply is pending.
    queue.publish()
    assert applet._brightness == pytest.approx(0.52)


def test_brightness_write_failure_does_not_drop_pending_target(monkeypatch):
    applet = brightness_applet(brightness=0.5)
    queue = DeferredWorker()
    applet._worker = queue.worker
    values = []

    def write(*, target, value):
        values.append(value)
        if len(values) == 1:
            raise OSError("temporary backend failure")
        return BrightnessState(value, target)

    monkeypatch.setattr(applet._backend, "set_brightness", write)
    applet.on_scroll(direction_up=True)
    applet.on_scroll(direction_up=True)
    queue.drain()
    assert values == pytest.approx([0.52, 0.54])
    assert not applet._set_in_progress


def test_stopped_volume_applet_ignores_queued_poll_reply():
    applet = volume_applet(VolumeState(45, False))
    queue = DeferredWorker()
    applet._worker = queue.worker
    applet._backend.controls.get_state.return_value = VolumeState(75, True)
    applet._tick()
    queue.read()
    applet.stop()
    queue.publish()
    assert (applet._volume, applet._muted) == (45, False)


def test_slow_volume_polls_do_not_overlap_or_arrive_out_of_order():
    applet = volume_applet(VolumeState(45, False))
    queue = DeferredWorker()
    applet._worker = queue.worker
    applet._tick()
    applet._tick()
    assert len(queue.tasks) == 1
    queue.drain()
    applet._tick()
    assert len(queue.tasks) == 1


def test_denied_sysfs_write_restores_the_actual_brightness(monkeypatch, tmp_path):
    from pathlib import Path

    from docking.applets.brightness.implementations.unix import (
        backend as brightness_state,
    )

    (tmp_path / "brightness").write_text("500")
    (tmp_path / "max_brightness").write_text("1000")
    applet = brightness_applet(brightness=0.5)
    from docking.applets.brightness.implementations.linux.backlight import (
        LinuxBacklightSource,
    )

    applet._backend = brightness_state.UnixBrightnessBackend(LinuxBacklightSource())
    monkeypatch.setattr(
        brightness_state,
        "detect_output",
        lambda _backlight: brightness_state.Backend("eDP-1", sysfs=tmp_path),
    )
    queue = DeferredWorker()
    applet._worker = queue.worker
    monkeypatch.setattr(brightness_state, "is_wayland_session", lambda: True)
    applet._state = applet._backend.get_state()
    write_text = Path.write_text

    def denied(path, *args, **kwargs):
        if path == tmp_path / "brightness":
            raise PermissionError("read-only backlight")
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", denied)
    for _ in range(5):
        applet.on_scroll(direction_up=True)
    queue.drain()

    assert (tmp_path / "brightness").read_text() == "500"
    assert applet._brightness == pytest.approx(0.5)
    assert applet.item.name == "Brightness: 50%"


def test_slow_brightness_polls_do_not_overlap():
    applet = brightness_applet(0.5)
    queue = DeferredWorker()
    applet._worker = queue.worker
    applet._tick()
    applet._tick()
    assert len(queue.tasks) == 1
    queue.drain()
    applet._tick()
    assert len(queue.tasks) == 1


def test_stopped_brightness_ignores_poll_reply_and_queued_writes():
    applet = brightness_applet(0.5)
    queue = DeferredWorker()
    applet._worker = queue.worker
    applet._tick()
    queue.read()
    applet.on_scroll(direction_up=True)
    applet.on_scroll(direction_up=True)
    applet.stop()
    queue.drain()
    assert applet._backend.writes == []
    assert applet._brightness == pytest.approx(0.54)
    assert applet._pending_brightness is None


def test_changed_primary_cancels_coalesced_brightness_requests(monkeypatch):
    applet = brightness_applet(0.5)
    queue = DeferredWorker()
    applet._worker = queue.worker
    writes = []

    def replaced(*, value, target):
        writes.append(target)
        return BrightnessState(0.3, "replacement-panel")

    monkeypatch.setattr(applet._backend, "set_brightness", replaced)
    applet.on_scroll(direction_up=True)
    applet.on_scroll(direction_up=True)
    queue.drain()
    assert writes == ["test-panel"]
    assert applet._pending_brightness is None
    assert applet.item.name == "Brightness: 30%"


def test_discrete_panel_scrolls_advance_and_coalesce_in_the_requested_direction(
    monkeypatch,
):
    applet = brightness_applet(0.4)
    levels = (0.2, 0.4, 0.6, 1.0)
    applet._state = BrightnessState(0.4, "test-panel", levels)
    queue = DeferredWorker()
    applet._worker = queue.worker
    written = []

    def write(*, value, target):
        written.append(value)
        return BrightnessState(value, target, levels)

    monkeypatch.setattr(applet._backend, "set_brightness", write)
    applet.on_scroll(direction_up=True)
    applet.on_scroll(direction_up=True)
    queue.drain()
    assert written == [0.6, 1.0]
    applet.on_scroll(direction_up=False)
    queue.drain()
    assert applet._brightness == 0.6


@pytest.mark.parametrize("already_read", [False, True])
def test_old_brightness_write_cannot_change_restarted_backend(
    monkeypatch, already_read
):
    from tests.applets.test_brightness import FakeBrightnessBackend, _ImmediateWorker
    from tests.platform_factories import TestAppletBackendFactories

    applet = brightness_applet(0.5)
    old = applet._backend
    queue = DeferredWorker()
    applet._worker = queue.worker
    applet.on_scroll(direction_up=True)
    if already_read:
        queue.read()
    applet.stop()
    replacement = FakeBrightnessBackend(0.8)
    monkeypatch.setattr(
        TestAppletBackendFactories, "brightness", lambda self: replacement
    )
    applet._worker = _ImmediateWorker()
    applet.start(notify=lambda: None)
    try:
        queue.drain()
        assert replacement.writes == []
        assert applet.item.name == "Brightness: 80%"
        assert len(old.writes) == int(already_read)
    finally:
        applet.stop()
