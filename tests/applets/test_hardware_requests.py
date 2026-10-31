"""Reproduce input races with production workers and delayed GTK replies."""

from unittest.mock import MagicMock

import pytest

import docking.applets.brightness.implementations.unix.backend as brightness_native
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
        brightness_native,
        "set_brightness",
        lambda *, backend, value, backlight: written.append(value),
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
        brightness_native,
        "set_brightness",
        lambda *, backend, value, backlight: written.append(value),
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
        applet._backend.get_state.return_value = updated
        applet.on_scroll(direction_up=True)
    else:
        updated = VolumeState(volume=45, muted=True)
        applet._backend.get_state.return_value = updated
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
        brightness_native,
        "get_brightness",
        lambda **_kwargs: 0.5,
    )
    monkeypatch.setattr(
        brightness_native,
        "set_brightness",
        lambda **_kwargs: None,
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

    def write(*, backend, value, backlight):
        values.append(value)
        if len(values) == 1:
            raise OSError("temporary backend failure")

    monkeypatch.setattr(
        brightness_native,
        "set_brightness",
        write,
    )
    applet.on_scroll(direction_up=True)
    applet.on_scroll(direction_up=True)
    queue.drain()
    assert values == pytest.approx([0.52, 0.54])
    assert not applet._set_in_progress


def test_stopped_volume_applet_ignores_queued_poll_reply():
    applet = volume_applet(VolumeState(45, False))
    queue = DeferredWorker()
    applet._worker = queue.worker
    applet._backend.get_state.return_value = VolumeState(75, True)
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

    (tmp_path / "brightness").write_text("500")
    (tmp_path / "max_brightness").write_text("1000")
    applet = brightness_applet(brightness=0.5)
    applet._native._output = brightness_native.Backend("eDP-1", sysfs=tmp_path)
    queue = DeferredWorker()
    applet._worker = queue.worker
    monkeypatch.setattr(
        brightness_native,
        "is_wayland_session",
        lambda: True,
    )
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
