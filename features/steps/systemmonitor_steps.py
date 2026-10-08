"""Deterministic lifecycle scenarios using the production guarded worker."""

from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from behave import given, then, when

import docking.applets.systemmonitor.applet as applet_mod
from docking.applets.systemmonitor.applet import SystemMonitorApplet
from docking.applets.systemmonitor.gpu import GpuStats
from docking.core.config import Config
from tests.applets.systemmonitor_support import DeferredSensors


@given("a system monitor with deferred sensor sampling")
def deferred_monitor(context):
    # These scenarios own timer mocks rather than the generic dock scheduler.
    context.harness.stop()
    temporary = TemporaryDirectory(prefix="docking-bdd-sensors-")
    context.add_cleanup(temporary.cleanup)
    patches = ExitStack()
    context.add_cleanup(patches.close)
    folder = Path(temporary.name)
    (folder / "stat").write_text("cpu  200 0 100 100 0 0 0\n")
    (folder / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 750 kB\n")
    patches.enter_context(patch.object(applet_mod, "_PROC_STAT", folder / "stat"))
    patches.enter_context(patch.object(applet_mod, "_PROC_MEMINFO", folder / "meminfo"))
    patches.enter_context(
        patch.object(applet_mod.GLib, "timeout_add_seconds", return_value=7)
    )
    patches.enter_context(
        patch.object(applet_mod.GLib, "source_remove", return_value=True)
    )
    applet = SystemMonitorApplet(48, config=Config())
    applet._show_disk = False
    applet._temperature_reader.read = MagicMock(return_value=55.2)
    applet._gpu_reader.read = MagicMock(return_value=GpuStats("GPU", 0.4))
    context.sensor_queue = DeferredSensors()
    applet._worker = context.sensor_queue.worker
    applet.start(lambda: None)
    context.add_cleanup(applet.stop)
    context.sensor_applet = applet


@when("the system monitor is polled ten times")
def poll_monitor(context):
    for _ in range(10):
        assert context.sensor_applet._tick()


@then("one sensor request remains and no sensor was read on GTK")
def guarded_sampling(context):
    assert len(context.sensor_queue.tasks) == 1
    context.sensor_applet._temperature_reader.read.assert_not_called()
    context.sensor_applet._gpu_reader.read.assert_not_called()


@when("the sensor worker completes and GTK receives the result")
def publish_sensor_result(context):
    context.sensor_queue.read()
    context.sensor_queue.publish()


@when("the system monitor is stopped and restarted")
def restart_monitor(context):
    context.sensor_applet.stop()
    context.sensor_applet.start(lambda: None)


@then("the old system monitor sensor result is ignored")
def ignored_result(context):
    assert context.sensor_applet._temperature_c is None
    assert context.sensor_applet._gpu is None


@then("the system monitor tooltip contains the new sensor values")
def sensor_tooltip(context):
    assert context.sensor_applet._temperature_c == 55.2
    assert "GPU: 40%" in context.sensor_applet.item.name
