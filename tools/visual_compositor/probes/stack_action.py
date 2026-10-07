"""Native input and independent screen containment checks for folder stacks."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path

from stack_probe import ICON_COLOR, locate, popup_bounds


def main() -> None:
    root = Path(os.environ["LAB_DIR"])
    name = os.environ["LAB_STACK_CASE"]
    state_path = Path(os.environ["LAB_STACK_STATE"])
    input_path = Path(os.environ["XDG_RUNTIME_DIR"]) / "lab-input.sock"
    output = json.loads((root / f"{name}.before.json").read_text())["outputs"][0]
    size = [output["width"], output["height"]]
    scale = int(output["scale"])
    results = []

    def snapshot():
        return json.loads(state_path.read_text())

    def capture(phase):
        path = root / f"{name}.{phase}.png"
        display = os.environ.get("LAB_PARENT_DISPLAY", os.environ["WAYLAND_DISPLAY"])
        subprocess.run(
            ["grim", "-s", str(scale), str(path)],
            env={**os.environ, "WAYLAND_DISPLAY": display},
            check=True,
            timeout=15,
        )
        return path

    def send(x, y, *, button=0, scroll=0):
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            client.connect(str(input_path))
            client.sendall(
                json.dumps(
                    {
                        "x": x,
                        "y": y,
                        "width": size[0],
                        "height": size[1],
                        "button": button,
                        "scroll": scroll,
                    }
                ).encode()
            )
            assert json.loads(client.recv(4096))["ok"]

    def popup_rect(path, state):
        x, y, width, height = popup_bounds(path, state["size"], scale)
        assert 0 <= x <= size[0] - width and 0 <= y <= size[1] - height
        return x, y, width, height

    try:
        time.sleep(1)
        for index, last, phase in (
            (-1, True, "last"),
            (1, False, "first"),
            (0, False, "action"),
        ):
            path = capture("resting")
            icon = locate(path, ICON_COLOR)
            assert icon is not None, "dock icon stimulus not visible"
            x, y = icon[0] // scale + 2, icon[1] // scale + 2
            send(x - 2, y)
            time.sleep(0.15)
            send(x, y)
            time.sleep(0.25)
            send(x, y, button=272)
            time.sleep(0.9)
            state = snapshot()
            assert state["visible"], "native click did not open stack"
            path = capture("opened")
            ox, oy, width, height = popup_rect(path, state)
            for _ in range(18):
                send(ox + width // 2, oy + height // 2, scroll=1 if last else -1)
                time.sleep(0.035)
            time.sleep(0.4)
            state = snapshot()
            path = capture(phase)
            popup_rect(path, state)
            if "scroll" in state:
                value, upper, page = state["scroll"]
                assert abs(value - (upper - page if last else 0)) < 1, state["scroll"]
            card = state["cards"][index]
            cx, cy = card["point"]
            assert 0 <= cx < width and 0 <= cy < height, card
            send(ox + cx, oy + cy)
            time.sleep(0.15)
            previous = len(state["activations"])
            send(ox + cx, oy + cy, button=272)
            time.sleep(0.4)
            state = snapshot()
            assert not state["visible"], "activation did not close stack"
            assert len(state["activations"]) == previous + 1
            assert state["activations"][-1] == card["key"], state
            results.append(
                {"phase": phase, "target": card["key"], "rect": [ox, oy, width, height]}
            )
        report = {
            "passed": True,
            "native_clicks": 3,
            "native_scroll": True,
            "capture_scale": scale,
            "results": results,
        }
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as exc:
        report = {"passed": False, "reason": str(exc), "results": results}
    (root / f"{name}.stack-result.json").write_text(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(report["reason"])


if __name__ == "__main__":
    main()
