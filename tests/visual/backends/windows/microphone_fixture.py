"""Prepare a synthetic ALSA input for the owned Windows lab's optional HDA codec.

The file source contains only a generated 440 Hz tone. It never opens a host
microphone. Enabling/removing compose.microphone.yaml requires a controlled VM
recreation; this generator does not change the running VM.
"""

from __future__ import annotations

import argparse
import array
import json
import math
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--share", type=Path, required=True)
    args = parser.parse_args()
    output = args.share / "lab/microphone-fixture"
    output.mkdir(parents=True, exist_ok=True)
    tone = output / "tone.raw"
    if tone.exists():
        parser.error(
            "Preserve the existing fixture; choose a fresh share or clean it up"
        )
    samples = array.array(
        "h",
        (round(8192 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(48000)),
    )
    if sys.byteorder != "little":
        samples.byteswap()
    with tone.open("xb") as stream:
        for _ in range(600):
            stream.write(samples.tobytes())
    (output / "alsa.conf").write_text(
        "</usr/share/alsa/alsa.conf>\n"
        "pcm.docking_fixture {\n"
        "  type file\n"
        '  slave.pcm "null"\n'
        '  file "/dev/null"\n'
        '  infile "/shared/lab/microphone-fixture/tone.raw"\n'
        '  format "raw"\n'
        "}\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "generated_tone": str(tone),
                "rate": 48000,
                "channels": 1,
                "seconds": 600,
                "physical_input": False,
            }
        )
    )


if __name__ == "__main__":
    main()
