"""Owned sensor process, with serialized requests and bounded shutdown."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CameraDevice:
    identifier: str
    name: str


@dataclass(frozen=True)
class CameraReport:
    identifier: str
    pids: tuple[int, ...]


@dataclass(frozen=True)
class CameraSnapshot:
    devices: tuple[CameraDevice, ...]
    reports: tuple[CameraReport, ...]
    activity_known: bool


def _snapshot(response: dict[str, Any]) -> CameraSnapshot:
    """Validate the process boundary before exposing native data to the dock."""
    known = response.get("known")
    devices, reports = response.get("devices"), response.get("reports")
    if (
        type(known) is not bool
        or not isinstance(devices, list)
        or not isinstance(reports, list)
        or len(devices) > 256
        or len(reports) > 256
    ):
        raise ValueError("Invalid camera snapshot")

    def identity(row):
        if not isinstance(row, dict):
            raise ValueError("Invalid camera row")
        value = row.get("id")
        if not isinstance(value, str) or not value or len(value) > 32768:
            raise ValueError("Invalid camera identity")
        return value

    inventory, activities = {}, {}
    for row in devices:
        key = identity(row)
        name = row.get("name")
        if not isinstance(name, str) or len(name) > 32768:
            raise ValueError("Invalid camera name")
        if key.casefold() in inventory:
            raise ValueError("Duplicate camera identity")
        inventory[key.casefold()] = CameraDevice(key, name or key)
    for row in reports:
        key = identity(row)
        processes = row.get("processes")
        if not isinstance(processes, list) or len(processes) > 4096:
            raise ValueError("Invalid camera process list")
        pids = set()
        for process in processes:
            if not isinstance(process, dict):
                raise ValueError("Invalid camera process")
            pid = process.get("pid")
            if (
                type(pid) is not int
                or not 0 < pid <= 0xFFFFFFFF
                or type(process.get("streaming")) is not bool
            ):
                raise ValueError("Invalid camera process identity")
            # A paused process still holds the camera, just as an open Linux
            # video file descriptor does. Streaming is not a mute switch.
            pids.add(pid)
        if key.casefold() in activities:
            raise ValueError("Duplicate camera activity report")
        activities[key.casefold()] = CameraReport(key, tuple(sorted(pids)))
    return CameraSnapshot(tuple(inventory.values()), tuple(activities.values()), known)


class WindowsCameraAPI:
    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._sequence = 0
        self._responses: queue.Queue[Any] = queue.Queue()

    def _start(self) -> None:
        executable = Path(sys.executable)
        if executable.name.casefold() == "pythonw.exe":
            executable = executable.with_name("python.exe")
        self._responses = queue.Queue()
        self._process = subprocess.Popen(
            [str(executable), str(Path(__file__).with_name("windows_sensor_host.py"))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        process, responses = self._process, self._responses

        def read():
            try:
                assert process.stdout is not None
                while line := process.stdout.readline(2_000_001):
                    if len(line) > 2_000_000:
                        raise ValueError("Camera response too large")
                    responses.put(json.loads(line))
            except (OSError, ValueError):
                pass
            finally:
                responses.put(None)

        threading.Thread(target=read, name="camera-sensor-replies", daemon=True).start()

    def _destroy(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.write('{"op":"stop"}\n')
                process.stdin.flush()
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        if process.stdout is not None:
            process.stdout.close()

    def snapshot(self) -> CameraSnapshot:
        with self._lock:
            if self._closed.is_set():
                raise OSError("Camera backend stopped")
            if self._process is None:
                self._start()
            process = self._process
            assert process is not None
            self._sequence += 1
            try:
                assert process.stdin is not None
                process.stdin.write(
                    json.dumps({"op": "query", "id": self._sequence}) + "\n"
                )
                process.stdin.flush()
                response = self._responses.get(timeout=4)
                if (
                    not isinstance(response, dict)
                    or response.get("id") != self._sequence
                ):
                    raise OSError("Camera sensor process stopped")
                if response.get("error"):
                    raise OSError(str(response["error"]))
                return _snapshot(response)
            except (OSError, ValueError, queue.Empty) as exc:
                # This is an actual request deadline. Reap this owned process
                # before allowing a later query to create a replacement.
                self._destroy()
                raise OSError("Camera sensor query failed") from exc

    def stop(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()

        def finish():
            with self._lock:
                self._destroy()

        threading.Thread(
            target=finish, name="camera-sensor-close", daemon=False
        ).start()
