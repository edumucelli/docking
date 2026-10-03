"""Exercise Docking services and record independent compositor observations."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from app_probe import call

ROOT = Path(os.environ["LAB_SCRIPTS"])
OUT = Path(os.environ["LAB_DIR"])
SOCKET = os.environ["LAB_APP_SOCKET"]
COMPOSITOR = os.environ["LAB_NATIVE_OBSERVER"]


def native(kind):
    probe = "gnome_probe.py" if COMPOSITOR == "gnome" else "toplevel_probe.py"
    return json.loads(
        subprocess.check_output(
            [sys.executable, str(ROOT / "probes" / probe), kind], timeout=6, text=True
        )
    )


def poll(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    raise RuntimeError(
        "native state did not reach the expected value within the budget"
    )


def extension(enabled):
    result = subprocess.check_output(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.gnome.Shell",
            "--object-path",
            "/org/gnome/Shell",
            "--method",
            "org.gnome.Shell.Extensions."
            + ("EnableExtension" if enabled else "DisableExtension"),
            "docking-bridge@docking.org",
        ],
        text=True,
        timeout=8,
    )
    if result.strip() != "(true,)":
        raise RuntimeError(f"extension operation failed: {result}")


def run(action, name):
    events = []

    def record(phase):
        entry = {
            "phase": phase,
            "app": call(SOCKET, {}),
            "native_windows": native("windows"),
            "native_workspaces": native("workspaces"),
        }
        if COMPOSITOR == "gnome":
            entry["geometry"] = native("geometry")
            entry["bridge_available"] = (
                subprocess.check_output(
                    [
                        "gdbus",
                        "call",
                        "--session",
                        "--dest",
                        "org.freedesktop.DBus",
                        "--object-path",
                        "/org/freedesktop/DBus",
                        "--method",
                        "org.freedesktop.DBus.NameHasOwner",
                        "org.docking.Docking.GnomeShellBridge",
                    ],
                    text=True,
                    timeout=5,
                ).strip()
                == "(true,)"
            )
        events.append(entry)
        (OUT / f"{name}.events.json").write_text(json.dumps(events, indent=2))
        return entry

    if action == "bridge-recovery":
        initial = record("ready")
        extension(False)
        record("disabled")
        call(SOCKET, {"action": "position", "edge": "left"})
        # Exceed the existing initial-position retry window (1.5 seconds).
        time.sleep(3)
        record("unavailable")
        extension(True)
        poll(
            lambda: (
                native("geometry").get("dock_rect", {}).get("x") == 0
                and native("geometry").get("dock_rect", {}).get("y") == 0
                and native("geometry").get("dock_rect", {}).get("height")
                == initial["geometry"]["outputs"][0]["height"]
            )
        )
        recovered = record("recovered")
        if recovered["app"]["pid"] != initial["app"]["pid"]:
            raise RuntimeError("Docking restarted during bridge recovery")
    client = subprocess.Popen(
        [sys.executable, str(ROOT / "probes/client_probe.py"), "overlap", "/tmp/unused"]
    )
    try:
        poll(lambda: call(SOCKET, {})["windows"])
        record("opened")
        if action == "window-actions":
            for command, phase, minimized in [
                ("minimize", "minimized", True),
                ("activate", "restored", False),
            ]:
                result = call(SOCKET, {"action": command})
                if result["result"] != "ok":
                    raise RuntimeError(f"Docking {command} returned {result['result']}")
                poll(
                    lambda minimized=minimized: any(
                        w.get("title") == "Lab probe"
                        and w.get("minimized") is minimized
                        and (minimized or w.get("active") is True)
                        for w in native("windows")
                    )
                )
                poll(
                    lambda minimized=minimized: any(
                        w.get("minimized") is minimized
                        for w in call(SOCKET, {})["windows"]
                    )
                )
                record(phase)
        elif action == "workspace-switch":
            spaces = call(SOCKET, {})["workspaces"]
            original = next(w for w in spaces if w["active"])
            target = next(w for w in spaces if not w["active"])
            for workspace, phase in [(target, "switched"), (original, "returned")]:
                result = call(SOCKET, {"action": "workspace", "id": workspace["id"]})
                if result["result"] != "ok":
                    raise RuntimeError(f"workspace action returned {result['result']}")
                poll(
                    lambda workspace=workspace: any(
                        w["active"] and w["id"] == workspace["id"]
                        for w in native("workspaces")
                    )
                )
                poll(
                    lambda workspace=workspace: any(
                        w["active"] and w["id"] == workspace["id"]
                        for w in call(SOCKET, {})["workspaces"]
                    )
                )
                record(phase)
        result = call(SOCKET, {"action": "close"})
        if result["result"] != "ok":
            raise RuntimeError(f"Docking close returned {result['result']}")
        poll(lambda: not any(w.get("title") == "Lab probe" for w in native("windows")))
        poll(lambda: not call(SOCKET, {})["windows"])
        client.wait(timeout=8)
        record("closed")
    finally:
        if client.poll() is None:
            client.terminate()
            client.wait(timeout=8)


if __name__ == "__main__":
    run(*sys.argv[1:3])
