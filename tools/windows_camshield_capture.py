"""Consume generated virtual-camera frames in an independent Windows process.

Only an explicitly named SWD virtual camera can be opened. This helper does not
enumerate or select physical cameras. Write OUTPUT/stop to end normally.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
import zlib
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=120)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python")
    if not args.device.casefold().startswith("\\\\?\\swd#vcamdevapi#"):
        parser.error("An explicit SWD virtual-camera symbolic link is required")
    if not 0 < args.seconds <= 600:
        parser.error("Capture lifetime must be at most 600 seconds")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "capture.json").exists() or (output / "stop").exists():
        parser.error("Use a fresh capture directory")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from docking.platform.backends.windows.com import (
        GUID,
        HRESULT,
        apartment,
        method,
        release,
    )
    from docking.platform.backends.windows.ffi import bind

    pointer, u32 = ctypes.c_void_p, ctypes.c_uint32
    source, reader, attributes = pointer(), pointer(), pointer()
    source_type = GUID.parse("c60ac5fe-252a-478f-a0ef-bc8fa5f7cad3")
    video_type = GUID.parse("8ac3587a-4ae7-42d8-99e0-0a6013eef90f")
    symbolic = GUID.parse("58f0aad8-22bf-4f8a-bb3d-d2c4978c6e2f")
    shared = GUID.parse("44d1a9bc-2999-4238-ae43-0730ceb2ab1b")
    report = {
        "pid": os.getpid(),
        "device": args.device,
        "frames": 0,
        "checksums": [],
        "stage": "startup",
    }

    def publish():
        temporary = output / "capture.tmp"
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        for attempt in range(10):
            try:
                temporary.replace(output / "capture.json")
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.02)

    def check(status):
        if status < 0:
            raise OSError(f"Camera capture failed: 0x{status & 0xFFFFFFFF:08x}")

    with apartment(multithreaded=True):
        mf = ctypes.WinDLL("mfplat")
        check(bind(mf, "MFStartup", HRESULT, u32, u32)(0x20070, 0))
        try:
            publish()
            check(
                bind(mf, "MFCreateAttributes", HRESULT, ctypes.POINTER(pointer), u32)(
                    ctypes.byref(attributes), 3
                )
            )
            check(
                method(
                    attributes, 24, HRESULT, ctypes.POINTER(GUID), ctypes.POINTER(GUID)
                )(attributes, ctypes.byref(source_type), ctypes.byref(video_type))
            )
            check(
                method(attributes, 25, HRESULT, ctypes.POINTER(GUID), ctypes.c_wchar_p)(
                    attributes, ctypes.byref(symbolic), args.device
                )
            )
            # Windows 11 24H2+: independent shared readers can coexist.
            check(
                method(attributes, 21, HRESULT, ctypes.POINTER(GUID), u32)(
                    attributes, ctypes.byref(shared), 1
                )
            )
            media = ctypes.WinDLL("mf")
            check(
                bind(
                    media,
                    "MFCreateDeviceSource",
                    HRESULT,
                    pointer,
                    ctypes.POINTER(pointer),
                )(attributes, ctypes.byref(source))
            )
            rw = ctypes.WinDLL("mfreadwrite")
            check(
                bind(
                    rw,
                    "MFCreateSourceReaderFromMediaSource",
                    HRESULT,
                    pointer,
                    pointer,
                    ctypes.POINTER(pointer),
                )(source, None, ctypes.byref(reader))
            )
            report["stage"] = "reading"
            publish()
            deadline = time.monotonic() + args.seconds
            previous = None
            while time.monotonic() < deadline and not (output / "stop").exists():
                stream, flags, stamp = u32(), u32(), ctypes.c_int64()
                sample, buffer = pointer(), pointer()
                try:
                    check(
                        method(
                            reader,
                            9,
                            HRESULT,
                            u32,
                            u32,
                            ctypes.POINTER(u32),
                            ctypes.POINTER(u32),
                            ctypes.POINTER(ctypes.c_int64),
                            ctypes.POINTER(pointer),
                        )(
                            reader,
                            0xFFFFFFFC,
                            0,
                            ctypes.byref(stream),
                            ctypes.byref(flags),
                            ctypes.byref(stamp),
                            ctypes.byref(sample),
                        )
                    )
                    if flags.value & 3:
                        raise OSError(f"Camera stream ended or failed: {flags.value}")
                    if not sample.value:
                        continue
                    check(
                        method(sample, 41, HRESULT, ctypes.POINTER(pointer))(
                            sample, ctypes.byref(buffer)
                        )
                    )
                    data, maximum, length = pointer(), u32(), u32()
                    check(
                        method(
                            buffer,
                            3,
                            HRESULT,
                            ctypes.POINTER(pointer),
                            ctypes.POINTER(u32),
                            ctypes.POINTER(u32),
                        )(
                            buffer,
                            ctypes.byref(data),
                            ctypes.byref(maximum),
                            ctypes.byref(length),
                        )
                    )
                    try:
                        if not data.value or length.value != 320 * 240 * 3 // 2:
                            raise ValueError("Expected generated 320x240 NV12 frames")
                        payload = ctypes.string_at(data, length.value)
                    finally:
                        check(method(buffer, 4, HRESULT)(buffer))
                    if previous is not None and stamp.value <= previous:
                        raise ValueError("Camera timestamps did not advance")
                    previous = stamp.value
                    report["frames"] += 1
                    crc = zlib.crc32(payload)
                    if crc not in report["checksums"] and len(report["checksums"]) < 64:
                        report["checksums"].append(crc)
                    report.update(
                        time=stamp.value,
                        bytes=len(payload),
                        minimum=min(payload),
                        maximum=max(payload),
                    )
                    if report["frames"] == 1:
                        from PIL import Image

                        Image.frombytes("L", (320, 240), payload[: 320 * 240]).save(
                            output / "first-frame.png"
                        )
                    if report["frames"] % 10 == 1:
                        publish()
                finally:
                    release(buffer)
                    release(sample)
            report["ok"] = report["frames"] > 10 and len(report["checksums"]) > 5
            assert report["ok"], "Insufficient changing generated frames"
        except BaseException as exc:
            report.update(ok=False, error=repr(exc))
            raise
        finally:
            release(reader)
            if source.value:
                report["source_shutdown"] = method(source, 12, HRESULT)(source)
            release(source)
            release(attributes)
            report["mf_shutdown"] = bind(mf, "MFShutdown", HRESULT)()
            report["closed"] = True
            publish()
            print(json.dumps(report))


if __name__ == "__main__":
    main()
