"""Independent, read-only Win32/CIM brightness capability probe."""

import argparse
import base64
import ctypes as c
import json
import os
import subprocess
import sys
from pathlib import Path


class Point(c.Structure):
    _fields_ = [("x", c.c_int32), ("y", c.c_int32)]


class Rect(c.Structure):
    _fields_ = [(x, c.c_int32) for x in ("l", "t", "r", "b")]


class Info(c.Structure):
    _fields_ = [
        ("size", c.c_uint32),
        ("monitor", Rect),
        ("work", Rect),
        ("flags", c.c_uint32),
        ("device", c.c_wchar * 32),
    ]


class Device(c.Structure):
    _fields_ = [
        ("size", c.c_uint32),
        ("name", c.c_wchar * 32),
        ("description", c.c_wchar * 128),
        ("flags", c.c_uint32),
        ("id", c.c_wchar * 128),
        ("key", c.c_wchar * 128),
    ]


class Physical(c.Structure):
    _fields_ = [("handle", c.c_void_p), ("description", c.c_wchar * 128)]


def bind(dll, name, result, *args):
    f = getattr(dll, name)
    f.restype = result
    f.argtypes = args
    return f


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python")
    if args.output.exists():
        parser.error("Use a fresh output path")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    u = c.WinDLL("user32", use_last_error=True)
    d = c.WinDLL("dxva2", use_last_error=True)
    h = bind(u, "MonitorFromPoint", c.c_void_p, Point, c.c_uint32)(Point(0, 0), 1)
    i = Info()
    i.size = c.sizeof(i)
    assert bind(u, "GetMonitorInfoW", c.c_int32, c.c_void_p, c.POINTER(Info))(
        h, c.byref(i)
    )
    devices = []
    for n in range(32):
        v = Device()
        v.size = c.sizeof(v)
        if not bind(
            u,
            "EnumDisplayDevicesW",
            c.c_int32,
            c.c_wchar_p,
            c.c_uint32,
            c.POINTER(Device),
            c.c_uint32,
        )(i.device, n, c.byref(v), 1):
            break
        devices.append(
            {"name": v.name, "description": v.description, "flags": v.flags, "id": v.id}
        )
    count = c.c_uint32()
    ok = bind(
        d,
        "GetNumberOfPhysicalMonitorsFromHMONITOR",
        c.c_int32,
        c.c_void_p,
        c.POINTER(c.c_uint32),
    )(h, c.byref(count))
    rows = []
    if ok and count.value:
        handles = (Physical * count.value)()
        assert bind(
            d,
            "GetPhysicalMonitorsFromHMONITOR",
            c.c_int32,
            c.c_void_p,
            c.c_uint32,
            c.POINTER(Physical),
        )(h, count, handles)
        try:
            for p in handles:
                caps = c.c_uint32()
                temp = c.c_uint32()
                capok = bind(
                    d,
                    "GetMonitorCapabilities",
                    c.c_int32,
                    c.c_void_p,
                    c.POINTER(c.c_uint32),
                    c.POINTER(c.c_uint32),
                )(p.handle, c.byref(caps), c.byref(temp))
                lo = c.c_uint32()
                cur = c.c_uint32()
                hi = c.c_uint32()
                readok = bind(
                    d,
                    "GetMonitorBrightness",
                    c.c_int32,
                    c.c_void_p,
                    c.POINTER(c.c_uint32),
                    c.POINTER(c.c_uint32),
                    c.POINTER(c.c_uint32),
                )(p.handle, c.byref(lo), c.byref(cur), c.byref(hi))
                rows.append(
                    {
                        "description": p.description,
                        "caps_ok": bool(capok),
                        "caps": caps.value,
                        "read_ok": bool(readok),
                        "error": c.get_last_error(),
                        "range": [lo.value, cur.value, hi.value],
                    }
                )
        finally:
            bind(
                d, "DestroyPhysicalMonitors", c.c_int32, c.c_uint32, c.POINTER(Physical)
            )(count, handles)
    ps = r"""
$ErrorActionPreference='Stop';
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);
$rows=@();$errorText=$null;
try {
 $rows=@(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness |
  Select-Object Active,InstanceName,CurrentBrightness,Level)
} catch {$errorText=$_.Exception.Message};
[ordered]@{rows=$rows;error=$errorText}|ConvertTo-Json -Depth 5 -Compress
"""
    r = subprocess.run(
        [
            str(
                Path(os.environ["SYSTEMROOT"])
                / "System32/WindowsPowerShell/v1.0/powershell.exe"
            ),
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            base64.b64encode(ps.encode("utf-16-le")).decode(),
        ],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=True,
        encoding="utf-8",
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    result = {
        "primary": i.device,
        "flags": i.flags,
        "devices": devices,
        "ddc_count_ok": bool(ok),
        "ddc_count": count.value,
        "ddc": rows,
        "wmi": json.loads(r.stdout),
        "read_only": True,
    }
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
