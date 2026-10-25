"""Windows camera state, native callback ownership and sensor-process failure."""

from __future__ import annotations

import ctypes
import io
import queue
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from docking.applets.camshield import windows as backend
from docking.applets.camshield import windows_client as client
from docking.applets.camshield import windows_sensor as sensor
from docking.applets.camshield.backend import create_backend
from docking.applets.camshield.state import CamshieldState, build_tooltip
from docking.platform import environment


class CameraAPI:
    def __init__(self):
        self.value = client.CameraSnapshot(
            (
                client.CameraDevice("first", "Front"),
                client.CameraDevice("second", "Rear"),
            ),
            (
                client.CameraReport("FIRST", (42,)),
                client.CameraReport("second", (42, 43)),
            ),
            True,
        )
        self.stop = Mock()

    def snapshot(self):
        return self.value


@pytest.fixture
def camera(monkeypatch):
    api = CameraAPI()
    instance = backend.WindowsCamshieldBackend(api=api)
    monkeypatch.setattr(
        instance._processes, "executable", lambda _: Path("Recorder.exe")
    )
    return instance, api


def test_windows_factory_is_lazy_and_does_not_import_unix(monkeypatch):
    monkeypatch.setattr(environment, "is_windows", lambda: True)
    monkeypatch.setitem(sys.modules, "docking.applets.camshield.linux", None)
    launch = Mock(side_effect=AssertionError("Constructor must not launch a process"))
    monkeypatch.setattr(client.subprocess, "Popen", launch)
    instance = create_backend()
    assert isinstance(instance, backend.WindowsCamshieldBackend)
    assert not instance.controls_available()
    assert not instance.lock() and not instance.unlock()
    instance.stop()
    launch.assert_not_called()


def test_multiple_devices_and_processes_are_grouped_by_pid(camera):
    instance, _ = camera
    state = instance.get_state()
    assert state.available and state.active and not state.locked
    assert [(h.pid, h.command, h.devices) for h in state.holders] == [
        (42, "Recorder.exe", ("Front", "Rear")),
        (43, "Recorder.exe", ("Rear",)),
    ]


def test_omitted_device_is_unknown_but_known_usage_still_proves_active(camera):
    instance, api = camera
    api.value = replace(api.value, reports=(client.CameraReport("first", ()),))
    state = instance.get_state()
    assert state.active is None
    assert "Camera activity unknown" in build_tooltip(state)
    assert "Camera idle" not in build_tooltip(state)
    api.value = replace(api.value, reports=(client.CameraReport("first", (42,)),))
    assert instance.get_state().active is True
    api.value = replace(api.value, activity_known=False)
    assert instance.get_state().active is None


def test_absence_explicit_idle_and_removed_camera_reports(camera):
    instance, api = camera
    api.value = replace(
        api.value,
        reports=tuple(client.CameraReport(d.identifier, ()) for d in api.value.devices),
    )
    assert instance.get_state().active is False
    api.value = replace(api.value, devices=())
    assert instance.get_state() == CamshieldState(False, False)


def test_missing_process_name_does_not_hide_camera_holder(camera, monkeypatch):
    instance, _ = camera
    monkeypatch.setattr(instance._processes, "executable", lambda _: None)
    state = instance.get_state()
    assert state.active and all(h.command == "Unknown" for h in state.holders)


def test_failure_and_stop_cannot_claim_idle(camera, monkeypatch):
    instance, api = camera
    monkeypatch.setattr(
        api, "snapshot", Mock(side_effect=OSError("Service unavailable"))
    )
    assert instance.get_state() == CamshieldState(False, None, error=True)
    assert "Camera state unavailable" in build_tooltip(instance.get_state())
    instance.stop()
    instance.stop()
    api.stop.assert_called_once()
    before = api.snapshot.call_count
    assert instance.get_state() == CamshieldState(False, False)
    assert not instance.open_settings()
    assert api.snapshot.call_count == before


def test_settings_handoff_without_hardware(camera, monkeypatch):
    instance, api = camera
    api.value = replace(api.value, devices=())
    opened = []
    monkeypatch.setattr(backend.os, "startfile", opened.append, raising=False)
    assert instance.settings_available() and instance.open_settings()
    assert opened == ["ms-settings:privacy-webcam"]
    monkeypatch.setattr(backend.os, "startfile", Mock(side_effect=OSError("denied")))
    assert not instance.open_settings()


def raw_snapshot():
    return {
        "known": True,
        "devices": [{"id": "camera", "name": "Synthetic"}],
        "reports": [
            {
                "id": "CAMERA",
                "processes": [
                    {"pid": 42, "streaming": False},
                    {"pid": 42, "streaming": True},
                ],
            }
        ],
    }


def test_non_streaming_process_still_holds_camera():
    value = client._snapshot(raw_snapshot())
    assert value.reports == (client.CameraReport("CAMERA", (42,)),)


@pytest.mark.parametrize(
    "alter",
    [
        lambda r: r.update(known=1),
        lambda r: r.update(devices=None),
        lambda r: r.update(reports={}),
        lambda r: r["devices"].append(r["devices"][0]),
        lambda r: r["devices"][0].update(id=""),
        lambda r: r["devices"][0].update(name=4),
        lambda r: r["reports"].append(r["reports"][0]),
        lambda r: r["reports"][0].update(processes=None),
        lambda r: r["reports"][0]["processes"][0].update(pid=True),
        lambda r: r["reports"][0]["processes"][0].update(pid=0),
        lambda r: r["reports"][0]["processes"][0].update(streaming="false"),
    ],
)
def test_malformed_child_data_is_rejected(alter):
    value = raw_snapshot()
    alter(value)
    with pytest.raises(ValueError):
        client._snapshot(value)


class Child:
    def __init__(self, *, stuck=False):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.waits = []
        self.killed = False
        self.stuck = stuck

    def wait(self, *, timeout):
        self.waits.append(timeout)
        if self.stuck and not self.killed:
            raise subprocess.TimeoutExpired("owned-sensor", timeout)
        return 0

    def kill(self):
        self.killed = True


@pytest.mark.parametrize(
    "reply", [None, {"id": 99}, {"id": 1, "error": "denied"}, {"id": 1}]
)
def test_failed_child_is_reaped_before_next_request(reply, monkeypatch):
    api = client.WindowsCameraAPI()
    children = []

    def start():
        api._process = child = Child(stuck=True)
        children.append(child)
        api._responses = queue.Queue()
        api._responses.put(reply)

    monkeypatch.setattr(api, "_start", start)
    with pytest.raises(OSError):
        api.snapshot()
    assert len(children) == 1 and children[0].killed
    assert children[0].waits == [2, 2]
    assert api._process is None and children[0].stdout.closed


def test_sensor_request_deadline_reaps_only_owned_child(monkeypatch):
    api = client.WindowsCameraAPI()
    api._process = child = Child(stuck=True)
    durations = []

    def timeout(*, timeout):
        durations.append(timeout)
        raise queue.Empty

    monkeypatch.setattr(api._responses, "get", timeout)
    with pytest.raises(OSError):
        api.snapshot()
    assert durations == [4] and child.killed and child.waits == [2, 2]


def test_client_serializes_queries_and_stop_rejects_late_results(monkeypatch):
    api = client.WindowsCameraAPI()
    api._process = child = Child()
    reading, resume = threading.Event(), threading.Event()

    def get(*, timeout):
        reading.set()
        assert resume.wait(5)
        return {"id": 1, **raw_snapshot()}

    monkeypatch.setattr(api._responses, "get", get)
    values = []
    worker = threading.Thread(target=lambda: values.append(api.snapshot()))
    worker.start()
    assert reading.wait(5)
    api.stop()  # Must return without waiting on the native request.
    resume.set()
    worker.join(5)
    assert not worker.is_alive()
    with pytest.raises(OSError):
        api.snapshot()
    # The process reaper owns shutdown even while a query is returning.
    for thread in threading.enumerate():
        if thread.name == "camera-sensor-close":
            thread.join(5)
    assert api._process is None and child.waits == [2]


class Reports:
    def __init__(self, monkeypatch):
        self.count = 1
        self.pids = [42, 43]
        self.failure = None
        self.acquired, self.released = [], []
        monkeypatch.setattr(
            sensor.ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False
        )
        monkeypatch.setattr(sensor, "method", self.method)
        monkeypatch.setattr(
            sensor,
            "release",
            lambda p: self.released.append(p.value) if p.value else None,
        )
        self.monitor = sensor.SensorMonitor()

    def method(self, pointer, index, result, *kinds):
        handle = getattr(pointer, "value", pointer)

        def invoke(this, *args):
            if (handle, index) == self.failure:
                return -1
            if (handle, index) == (10, 3):
                ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_uint32))[0] = self.count
            elif (handle, index) == (10, 4):
                ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_void_p))[0] = 20
                self.acquired.append(20)
            elif handle == 20 and index in (3, 4):
                args[0].value = "Synthetic" if index == 3 else "sensor-id"
                ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_uint32))[0] = (
                    len(args[0].value) + 1
                )
            elif (handle, index) == (20, 5):
                ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_uint32))[0] = len(
                    self.pids
                )
            elif (handle, index) == (20, 6):
                value = 30 + args[0]
                ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_void_p))[0] = value
                self.acquired.append(value)
            elif handle in (30, 31) and index in (3, 4):
                value = self.pids[handle - 30] if index == 3 else 1
                ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_uint32))[0] = value
            else:
                raise AssertionError((handle, index))
            return 0

        return invoke

    def notify(self):
        # Invoke the live native callback thunk, not just its Python method.
        assert (
            self.monitor._functions[3](
                ctypes.byref(self.monitor._instance), ctypes.c_void_p(10)
            )
            == 0
        )
        assert sorted(self.acquired) == sorted(self.released)
        return self.monitor.snapshot()


def test_native_callback_releases_owned_reports_and_forgets_old_usage(monkeypatch):
    reports = Reports(monkeypatch)
    known, rows = reports.notify()
    assert known and rows[0]["processes"][1]["pid"] == 43
    reports.pids = []
    assert reports.notify() == (
        True,
        [{"id": "sensor-id", "name": "Synthetic", "processes": []}],
    )
    reports.count = 0
    assert reports.notify() == (True, [])


@pytest.mark.parametrize(
    "failure", [(10, 3), (10, 4), (20, 3), (20, 5), (20, 6), (30, 3), (30, 4)]
)
def test_native_callback_failures_are_unknown_and_release_partial_results(
    monkeypatch, failure
):
    reports = Reports(monkeypatch)
    reports.notify()
    reports.failure = failure
    assert reports.notify() == (False, [])


def test_callback_iunknown_identity_and_closed_monitor(monkeypatch):
    reports = Reports(monkeypatch)
    monitor = reports.monitor
    output = ctypes.c_void_p()
    query = monitor._functions[0]
    for iid in (sensor._UNKNOWN, sensor._CALLBACK):
        assert (
            query(None, ctypes.byref(sensor.GUID.parse(iid)), ctypes.byref(output)) == 0
        )
        assert output.value == ctypes.addressof(monitor._instance)
        monitor._functions[2](None)
    assert monitor._references == 1
    assert (
        query(
            None,
            ctypes.byref(sensor.GUID.parse("00000000-0000-0000-0000-000000000000")),
            ctypes.byref(output),
        )
        < 0
    )
    assert not output.value
    monitor.stop()
    assert reports.notify() == (False, [])


@pytest.mark.parametrize("fail", [False, True])
def test_inventory_is_passive_and_releases_partial_results(monkeypatch, fail):
    pointers, freed, released = [], [], []
    array = (ctypes.c_void_p * 2)(20, 30)
    array_address = ctypes.cast(array, ctypes.c_void_p).value
    monkeypatch.setattr(sensor.ctypes, "WinDLL", lambda _: object(), raising=False)
    monkeypatch.setattr(
        sensor, "release", lambda p: released.append(p.value) if p.value else None
    )

    def u32(output, value):
        ctypes.cast(output, ctypes.POINTER(ctypes.c_uint32))[0] = value

    def ptr(output, value):
        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = value

    def bind(dll, name, result, *kinds):
        if name == "MFCreateAttributes":
            return lambda output, count: (ptr(output, 10), 0)[1]
        if name == "MFEnumDeviceSources":

            def enumerate_(attributes, output, count):
                ptr(output, array_address)
                u32(count, 2)
                return 0

            return enumerate_
        assert name == "CoTaskMemFree"
        return lambda value: freed.append(ctypes.cast(value, ctypes.c_void_p).value)

    def method(pointer, index, result, *kinds):
        # Calling IMFActivate.ActivateObject would fail this allowlist.
        if pointer.value == 10:
            assert index == 24
            return lambda *args: 0
        assert index == 13

        def text(this, key, output, length):
            symbolic = bytes(
                ctypes.cast(key, ctypes.POINTER(sensor.GUID)).contents
            ) == bytes(sensor._SYMBOLIC_LINK)
            value = f"camera-{pointer.value}" if symbolic else "Synthetic"
            allocation = ctypes.create_unicode_buffer(value)
            pointers.append(allocation)
            ptr(output, ctypes.addressof(allocation))
            u32(length, len(value))
            return -1 if fail and pointer.value == 30 and not symbolic else 0

        return text

    monkeypatch.setattr(sensor, "bind", bind)
    monkeypatch.setattr(sensor, "method", method)
    if fail:
        with pytest.raises(OSError):
            sensor.inventory(object())
    else:
        assert sensor.inventory(object()) == [
            {"id": "camera-20", "name": "Synthetic"},
            {"id": "camera-30", "name": "Synthetic"},
        ]
    assert sorted(released) == [10, 20, 30]
    assert sorted(freed) == sorted(
        [array_address, *(ctypes.addressof(p) for p in pointers)]
    )
