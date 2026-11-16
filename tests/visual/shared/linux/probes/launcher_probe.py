"""Require visible dock pixels and physical launcher input on every backend."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from tests.visual.shared.linux.probes import (
    input_probe,
)
from tests.visual.shared.linux.probes import (
    native_services_probe as native,
)


class LauncherProbe(native.Probe):
    async def run(self):
        assert self.backend.name == os.environ["LAB_EXPECTED_BACKEND"]
        assert os.environ["LAB_INPUT_SUPPORTED"] == "true", (
            "Native input delivery unverified"
        )
        image = await self.capture("launcher-visible")
        width, height = image.get_width(), image.get_height()
        points = native.dock_marker_points(image, (0, 0, width, height))
        assert len(points) >= 47 * 47, "Visible launcher marker absent or incomplete"
        x = (min(p[0] for p in points) + max(p[0] for p in points)) // 2
        y = (min(p[1] for p in points) + max(p[1] for p in points)) // 2
        for button in (0, 272):
            subprocess.run(
                [
                    sys.executable,
                    input_probe.__file__,
                    os.environ["LAB_INPUT_SOCKET"],
                    "--send",
                    str(x),
                    str(y),
                    str(width),
                    str(height),
                    str(button),
                ],
                check=True,
            )
            await self.settle(0.4)
        launched = self.evidence / "launched.json"
        await self.wait(
            launched.exists, "Physical click did not launch the owned fixture"
        )
        record = json.loads(launched.read_text())
        assert record["nonce"] == os.environ["LAB_LAUNCH_NONCE"]
        assert record["pid"] > 0
        try:
            await self.capture("launcher-launched")
        finally:
            os.kill(record["pid"], signal.SIGTERM)
        (self.evidence / "launcher-result.json").write_text(
            json.dumps(
                {
                    "backend": self.backend.name,
                    "visible_pixels": len(points),
                    "input_verified": True,
                    "launched": True,
                }
            )
        )


if __name__ == "__main__":
    if "--launched" in sys.argv:
        (Path(os.environ["LAB_DIR"]) / "launched.json").write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "nonce": os.environ["LAB_LAUNCH_NONCE"],
                }
            )
        )
        native.fixture()
    else:
        native.Probe = LauncherProbe
        native.main()
