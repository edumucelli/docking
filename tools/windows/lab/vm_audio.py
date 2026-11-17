"""Capture the Windows VM's actual QEMU audio output for desktop acceptance.

The lab must enable AUDIO=Y. Dockur exposes 48 kHz, stereo, signed 16-bit PCM
through its private audio relay. No guest pipeline or audio sink is replaced.
Use --output for one capture, or --share/--name to service an Ambient smoke run.
"""

from __future__ import annotations

import argparse
import array
import json
import math
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

_CAPTURE = """import socket,sys,time
duration,settle=float(sys.argv[1]),float(sys.argv[2])
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
s.settimeout(.2)
s.connect('/run/shm/audio.sock')
start=time.monotonic();parts=[]
while time.monotonic()-start<duration+settle:
 try:data=s.recv(65536)
 except socket.timeout:continue
 if not data:raise RuntimeError('QEMU audio relay disconnected')
 if time.monotonic()-start>=settle:parts.append(data)
s.close()
sys.stdout.buffer.write(b''.join(parts))
"""


def capture(container: str, output: Path, seconds: float) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["docker", "exec", container, "python3", "-c", _CAPTURE, str(seconds), "0.35"],
        capture_output=True,
        timeout=seconds + 15,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    # Socket reads can split a stereo frame; retain only complete frames.
    pcm = result.stdout[: len(result.stdout) // 4 * 4]
    values = array.array("h", pcm)
    if sys.byteorder != "little":
        values.byteswap()
    count = len(values)
    report = {
        "rate": 48000,
        "channels": 2,
        "sample_width": 2,
        "frames": count // 2,
        "seconds": count / 96000,
        "rms": math.sqrt(sum(x * x for x in values) / count) / 32768 if count else 0,
        "peak": max(abs(min(values)), abs(max(values))) / 32768 if count else 0,
        "source": "QEMU audio relay; native Windows output",
    }
    with wave.open(str(output), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(pcm)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(output.with_suffix(".json"))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--share", type=Path)
    parser.add_argument("--name")
    parser.add_argument("--seconds", type=float, default=2.5)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--container", default="docking-windows-lab")
    args = parser.parse_args()
    if not 0.1 <= args.seconds <= 30:
        parser.error("Capture duration must be between 0.1 and 30 seconds")
    if args.output:
        print(json.dumps(capture(args.container, args.output, args.seconds), indent=2))
        return
    if not args.name or not re.fullmatch(r"[a-zA-Z0-9_-]+", args.name):
        parser.error(
            "Watch mode requires a simple --name for the guest output directory"
        )
    root = args.share / "lab" / args.name
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        result = root / "result.json"
        if result.exists():
            report = json.loads(result.read_text(encoding="utf-8"))
            print(json.dumps(report, indent=2))
            if not report.get("ok") or not report.get("normal_shutdown"):
                raise SystemExit(1)
            return
        for request in sorted((root / "requests").glob("*.json")):
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", request.stem):
                raise ValueError("Invalid audio phase name")
            output = root / "audio" / (request.stem + ".wav")
            if output.with_suffix(".json").exists():
                continue
            try:
                report = capture(args.container, output, args.seconds)
                print(json.dumps({"phase": request.stem, **report}), flush=True)
            except Exception as exc:
                output.with_suffix(".json").write_text(json.dumps({"error": repr(exc)}))
                raise
        time.sleep(0.2)
    raise TimeoutError(
        f"Ambient smoke did not finish; inspect its existing job and {root}"
    )


if __name__ == "__main__":
    main()
