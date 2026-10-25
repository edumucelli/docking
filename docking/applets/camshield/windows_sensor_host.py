"""Bounded-lifetime host for native camera callbacks; no capture is opened."""

from __future__ import annotations

import ctypes
import json
import sys
from contextlib import suppress
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from docking.applets.camshield.windows_sensor import SensorMonitor, inventory
from docking.platform.backends.windows.com import HRESULT, apartment
from docking.platform.backends.windows.ffi import bind


def main() -> None:
    if sys.platform != "win32":
        raise OSError("Camera sensor host requires Windows")
    with apartment(multithreaded=True) as ole:
        dll = ctypes.WinDLL("mfplat")
        startup = bind(dll, "MFStartup", HRESULT, ctypes.c_uint32, ctypes.c_uint32)
        status = startup(0x20070, 1)
        if status < 0:
            raise OSError(f"Media Foundation startup failed: {status}")
        monitor = SensorMonitor()
        try:
            # Enumeration can still succeed when sensor reporting is
            # unsupported or denied. Preserve explicit unknown activity.
            with suppress(OSError):
                monitor.start()
            for line in sys.stdin:
                request = json.loads(line)
                if request.get("op") == "stop":
                    break
                sequence = request.get("id")
                try:
                    devices = inventory(ole)
                    known, reports = monitor.snapshot()
                    response = {
                        "id": sequence,
                        "devices": devices,
                        "known": known,
                        "reports": reports,
                    }
                except OSError as exc:
                    response = {"id": sequence, "error": str(exc)}
                print(json.dumps(response), flush=True)
        finally:
            monitor.stop()
            bind(dll, "MFShutdown", HRESULT)()


if __name__ == "__main__":
    main()
