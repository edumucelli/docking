"""Sensor worker lifecycle, single-flight, and GTK publication contracts."""

import threading
from unittest.mock import MagicMock

import pytest

import docking.applets.systemmonitor.applet as applet_mod
from docking.applets.systemmonitor.applet import SystemMonitorApplet
from docking.applets.systemmonitor.gpu import GpuStats
from docking.applets.worker import BackgroundWorker
from docking.core.config import Config
from tests.applets.systemmonitor_support import DeferredSensors


@pytest.fixture
def sampling(tmp_path, monkeypatch):
    stat = tmp_path / "stat"
    stat.write_text("cpu  200 0 100 100 0 0 0\n")
    memory = tmp_path / "meminfo"
    memory.write_text("MemTotal: 1000 kB\nMemAvailable: 750 kB\n")
    monkeypatch.setattr(applet_mod, "_PROC_STAT", stat)
    monkeypatch.setattr(applet_mod, "_PROC_MEMINFO", memory)
    monkeypatch.setattr(applet_mod.GLib, "timeout_add_seconds", lambda *_: 7)
    monkeypatch.setattr(applet_mod.GLib, "source_remove", lambda *_: True)
    applet = SystemMonitorApplet(48, config=Config())
    applet._show_disk = False
    applet._temperature_reader.read = MagicMock(return_value=55.2)
    applet._gpu_reader.read = MagicMock(return_value=GpuStats("GPU", 0.4))
    queue = DeferredSensors()
    applet._worker = queue.worker
    applet.start(MagicMock())
    yield applet, queue
    applet.stop()


def test_tick_does_not_read_sensors_and_single_flight_includes_queued_delivery(
    sampling,
):
    applet, queue = sampling
    for _ in range(10):
        assert applet._tick()
    assert len(queue.tasks) == 1
    applet._temperature_reader.read.assert_not_called()
    applet._gpu_reader.read.assert_not_called()
    queue.read()
    assert applet._temperature_c is None and applet._gpu is None
    applet._tick()
    assert queue.tasks == []
    queue.publish()
    assert applet._temperature_c == 55.2
    assert applet._gpu.utilization == 0.4
    assert "GPU: 40%" in applet.item.name
    applet._tick()
    assert len(queue.tasks) == 1


@pytest.mark.parametrize("restart", [False, True])
def test_old_result_is_discarded_after_stop_or_restart(sampling, restart):
    applet, queue = sampling
    applet._tick()
    queue.read()
    applet.stop()
    notified = MagicMock()
    if restart:
        applet.start(notified)
        applet._tick()
        assert queue.tasks == []
    queue.publish()
    assert applet._temperature_c is None and applet._gpu is None
    notified.assert_not_called()
    if restart:
        applet._tick()
        queue.read()
        queue.publish()
        assert applet._temperature_c == 55.2
        notified.assert_called_once()
    else:
        assert applet._tick() is False


def test_worker_exception_releases_guard_and_polling_retries(sampling):
    applet, queue = sampling
    applet._temperature_reader.read.side_effect = [OSError("unavailable"), 60.0]
    applet._tick()
    queue.read()
    queue.publish()
    assert applet._temperature_c is None
    applet._tick()
    queue.read()
    queue.publish()
    assert applet._temperature_c == 60.0


def test_sensor_only_changes_do_not_render_icon_and_rounded_values_do_not_notify(
    sampling,
):
    applet, queue = sampling
    applet._tick()
    queue.read()
    queue.publish()
    applet.present = MagicMock()
    applet._notify.reset_mock()
    applet._sample_sensors()
    queue.read()
    queue.publish()
    applet.present.assert_not_called()
    applet._notify.assert_not_called()
    applet._temperature_reader.read.return_value = 55.21
    applet._sample_sensors()
    queue.read()
    queue.publish()
    applet._notify.assert_not_called()
    applet._temperature_reader.read.return_value = 56.2
    applet._sample_sensors()
    queue.read()
    queue.publish()
    applet._notify.assert_called_once()
    applet.present.assert_not_called()


def test_repeated_start_does_not_register_duplicate_timer(sampling, monkeypatch):
    applet, _queue = sampling
    timer = MagicMock(return_value=8)
    monkeypatch.setattr(applet_mod.GLib, "timeout_add_seconds", timer)
    applet.start(MagicMock())
    timer.assert_not_called()
    assert applet._timer_id == 7


def test_real_thread_reads_without_mutating_gtk_and_delivers_on_main(sampling):
    applet, queue = sampling
    entered, release, delivered = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    main_thread = threading.get_ident()
    reader_threads = []

    def read():
        reader_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        return 61.0

    def idle(callback, *args):
        queue.callbacks.append((callback, args))
        delivered.set()
        return 1

    applet._temperature_reader.read = read
    applet._worker = BackgroundWorker(idle_add=idle)
    notifications = []
    applet._notify = lambda: notifications.append(threading.get_ident())
    try:
        assert applet._tick()
        assert entered.wait(5)
        assert reader_threads[0] != main_thread
        assert applet._temperature_c is None
        release.set()
        assert delivered.wait(5)
        assert applet._temperature_c is None
        notifications.clear()
        queue.publish()
        assert applet._temperature_c == 61.0
        assert notifications == [main_thread]
    finally:
        release.set()
