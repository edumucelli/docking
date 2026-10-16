"""Exercise production request ordering and alarm actions with controlled I/O."""

import datetime as dt
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from behave import given, then, when
from pytest import MonkeyPatch

import docking.applets.brightness.applet as brightness_mod
from docking.applets.alarm.applet import AlarmApplet
from docking.applets.alarm.state import AlarmPreset, AlarmState, next_alarm
from docking.applets.powerprofiles.state import PowerProfilesState, profile_label
from docking.applets.volume.state import VolumeState
from docking.core.config import Config
from tests.applets.deferred_worker import DeferredWorker
from tests.applets.test_brightness import _make_applet as brightness_applet
from tests.applets.test_powerprofiles import _make_applet as profiles_applet
from tests.applets.test_volume import _make_applet as volume_applet


def patches(context):
    monkeypatch = MonkeyPatch()
    context.add_cleanup(monkeypatch.undo)
    return monkeypatch


@given("brightness is 50 percent with deferred hardware writes")
def deferred_brightness(context):
    context.hardware_queue = DeferredWorker()
    context.brightness = brightness_applet(brightness=0.5)
    context.brightness._worker = context.hardware_queue.worker
    context.hardware_values = []
    patches(context).setattr(
        brightness_mod,
        "set_brightness",
        lambda *, backend, value: context.hardware_values.append(value),
    )


@when("I scroll brightness up five times before the hardware replies")
def scroll_brightness(context):
    for _ in range(5):
        context.brightness.on_scroll(direction_up=True)


@when("all deferred hardware work finishes")
def drain_hardware(context):
    context.hardware_queue.drain()


@then("the hardware and brightness tooltip show 60 percent")
def final_brightness(context):
    assert round(context.hardware_values[-1] * 100) == 60
    assert context.brightness.item.name == "Brightness: 60%"


@given("the balanced power profile with deferred hardware writes")
def deferred_profile(context):
    context.profiles, context.profile_backend = profiles_applet(
        patches(context),
        PowerProfilesState(
            True, "balanced", ("balanced", "performance", "power-saver")
        ),
    )
    context.hardware_queue = DeferredWorker()
    context.profiles._worker = context.hardware_queue.worker


@when("I choose performance and then power-saver before the hardware replies")
def select_profiles(context):
    context.profiles._set_profile_async(profile="performance")
    context.profiles._set_profile_async(profile="power-saver")


@then("the selected power profile and tooltip show power-saver")
def final_profile(context):
    assert context.profile_backend.set_calls[-1] == "power-saver"
    assert context.profiles._state.active_profile == "power-saver"
    assert profile_label("power-saver") in context.profiles.item.name


@given("an old volume poll is waiting to be displayed")
def old_volume_poll(context):
    context.volume = volume_applet(VolumeState(45, False))
    context.hardware_queue = DeferredWorker()
    context.volume._worker = context.hardware_queue.worker
    context.volume._tick()
    context.hardware_queue.read()


@when('I change the volume control with "{action}"')
def new_volume_input(context, action):
    state = VolumeState(50, False) if action == "scroll" else VolumeState(45, True)
    context.volume._backend.get_state.return_value = state
    if action == "scroll":
        context.volume.on_scroll(direction_up=True)
    else:
        context.volume.on_clicked()


@when("the old volume poll is displayed")
def publish_old_volume(context):
    context.hardware_queue.publish()


@then('the volume control retains the new "{action}" state')
def final_volume(context, action):
    expected = "Volume: 50%" if action == "scroll" else "Muted"
    assert context.volume.item.name == expected


def alarm(context, preset):
    folder = TemporaryDirectory(prefix="docking-bdd-alarm-")
    context.add_cleanup(folder.cleanup)
    config = Config()
    config._path = Path(folder.name) / "dock.json"
    context.alarm_now = dt.datetime(2026, 5, 18, 9, 0, tzinfo=dt.timezone.utc)
    context.alarm = AlarmApplet(48, config=config)
    patches(context).setattr(context.alarm, "_now", lambda: context.alarm_now)
    context.alarm._state = AlarmState(presets=(preset,))


@given("a disabled 07:00 alarm at 09:00 today")
def disabled_alarm(context):
    alarm(context, AlarmPreset(hour=7, enabled=False))


@when("I enable the alarm and its scheduler runs")
def enable_alarm(context):
    context.alarm._toggle_alarm(index=0, enabled=True)
    context.alarm._tick()


@then("the alarm stays quiet and is scheduled for 07:00 tomorrow")
def next_day_alarm(context):
    assert not context.alarm.item.is_urgent
    expected = context.alarm_now.replace(day=19, hour=7)
    assert next_alarm(context.alarm._state, now=context.alarm_now).when == expected


@given("a 07:00 alarm that already rang today at 09:00")
def consumed_alarm(context):
    alarm(context, AlarmPreset(hour=7, last_triggered="2026-05-18"))


@when("I change the alarm to 10:00 today")
def edit_alarm(context):
    context.alarm._upsert_preset(
        index=0, preset=replace(context.alarm._state.presets[0], hour=10)
    )


@then("it is scheduled for 10:00 today")
def later_today_alarm(context):
    assert next_alarm(
        context.alarm._state, now=context.alarm_now
    ).when == context.alarm_now.replace(hour=10)


@when("the alarm clock reaches 10:00 today")
def due_alarm(context):
    context.alarm_now = context.alarm_now.replace(hour=10)
    context.alarm._tick()


@then("the edited alarm rings")
def ringing_edited_alarm(context):
    assert context.alarm.item.is_urgent
    assert context.alarm._state.ringing_index == 0
