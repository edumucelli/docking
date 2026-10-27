"""Capture one explicitly named lab endpoint through native WASAPI.

Used by windows_micshield_smoke with the owned synthetic HDA input. The caller
supplies its exact endpoint ID; the helper never falls back to another input.
Only sample statistics are published, and all native audio resources are closed
when the stop file appears or the bounded duration expires.
"""

from __future__ import annotations

import argparse
import array
import json
import math
import os
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--seconds", type=float, default=120)
    args = parser.parse_args()
    if sys.platform != "win32" or not 1 <= args.seconds <= 300:
        parser.error("Requires native Windows and a duration between 1 and 300 seconds")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "capture.json").exists() or (output / "stop").exists():
        parser.error("Use a fresh output directory")
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    source = Gst.ElementFactory.make("wasapisrc", "source")
    convert = Gst.ElementFactory.make("audioconvert", "convert")
    resample = Gst.ElementFactory.make("audioresample", "resample")
    caps = Gst.ElementFactory.make("capsfilter", "caps")
    sink = Gst.ElementFactory.make("appsink", "samples")
    assert all((source, convert, resample, caps, sink)), (
        "Missing WASAPI/GStreamer runtime"
    )
    source.set_property("device", args.device)
    sink.set_property("sync", False)
    sink.set_property("max-buffers", 32)
    sink.set_property("drop", True)
    caps.set_property(
        "caps", Gst.Caps.from_string("audio/x-raw,format=S16LE,rate=48000,channels=1")
    )
    pipeline = Gst.Pipeline.new("owned-microphone-capture")
    for element in (source, convert, resample, caps, sink):
        pipeline.add(element)
    assert source.link(convert) and convert.link(resample)
    assert resample.link(caps) and caps.link(sink)
    report = {"pid": os.getpid(), "device": args.device, "buffers": 0, "samples": 0}

    def publish():
        temporary = output / "capture.tmp"
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        for attempt in range(50):
            try:
                temporary.replace(output / "capture.json")
                return
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.02)

    start, last = time.monotonic(), 0.0
    try:
        assert pipeline.set_state(Gst.State.PLAYING) != Gst.StateChangeReturn.FAILURE
        bus = pipeline.get_bus()
        while (
            time.monotonic() - start < args.seconds and not (output / "stop").exists()
        ):
            message = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if message is not None:
                if message.type == Gst.MessageType.ERROR:
                    error, detail = message.parse_error()
                    raise RuntimeError(f"WASAPI capture failed: {error}: {detail}")
                break
            sample = sink.emit("try-pull-sample", 250 * Gst.MSECOND)
            if sample is None:
                assert report["buffers"] or time.monotonic() - start < 20, (
                    "No capture samples"
                )
                continue
            buffer = sample.get_buffer()
            values = array.array("h", buffer.extract_dup(0, buffer.get_size()))
            if sys.byteorder != "little":
                values.byteswap()
            if values:
                report["buffers"] += 1
                report["samples"] += len(values)
                report["rms"] = math.sqrt(sum(v * v for v in values) / len(values))
                report["peak"] = max(abs(v) for v in values)
                report["elapsed"] = time.monotonic() - start
                if time.monotonic() - last > 0.25:
                    publish()
                    last = time.monotonic()
        report["ok"] = report["samples"] > 0
    except Exception as exc:
        report.update(ok=False, error=repr(exc))
        raise
    finally:
        pipeline.set_state(Gst.State.NULL)
        pipeline.get_state(5 * Gst.SECOND)
        report["closed"] = True
        publish()
        print(json.dumps(report))


if __name__ == "__main__":
    main()
