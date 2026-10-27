"""Send keyboard input to the Windows VM when its guest runner is unavailable."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path


def text_key(character: str) -> str:
    if character.isascii() and (character.islower() or character.isdigit()):
        return character
    if character.isascii() and character.isupper():
        return "shift-" + character.lower()
    return {
        " ": "spc",
        "\\": "backslash",
        "/": "slash",
        ".": "dot",
        "-": "minus",
        "_": "shift-minus",
        ":": "shift-semicolon",
        "'": "apostrophe",
        '"': "shift-apostrophe",
        "=": "equal",
    }[character]


def send_keys(commands: list[tuple[str, float]], *, container: str) -> None:
    """Send real virtual-keyboard events through the existing QEMU monitor."""
    program = (
        "import json,socket,time\n"
        "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)\n"
        "s.settimeout(3);s.connect('/run/shm/monitor.sock');s.recv(4096)\n"
        "s.settimeout(0.01)\n"
        f"commands=json.loads({json.dumps(json.dumps(commands))})\n"
        "for key,delay in commands:\n"
        " s.sendall(('sendkey '+key+' 20\\n').encode());time.sleep(delay)\n"
        " try:\n"
        "  answer=s.recv(65536).decode(errors='replace')\n"
        "  if 'unknown' in answer.lower() or 'invalid' in answer.lower():\n"
        "   raise RuntimeError(answer)\n"
        " except socket.timeout:pass\n"
        "s.close()\n"
    )
    subprocess.run(
        ["docker", "exec", container, "python3", "-c", program],
        check=True,
    )
    print(json.dumps({"sent_key_commands": len(commands)}))


def focus_commands(request: dict) -> list[tuple[str, float]]:
    """Translate the focus probe's two stages into a bounded keyboard sequence."""
    origin = request["origin"]
    if origin not in {"desktop", "taskbar", "start"}:
        raise ValueError("Unknown focus origin")
    delay = float(request["delay"])
    if not math.isfinite(delay) or not 0 <= delay <= 10:
        raise ValueError("Focus delay must be between zero and ten seconds")
    if request["stage"] == "origin":
        commands = [
            ("esc", 0.3),
            ("meta_l-d" if origin == "desktop" else "meta_l", 0.4),
        ]
        if origin == "taskbar":
            commands.append(("esc", 0.3))
        return commands
    if request["stage"] != "activate":
        raise ValueError("Unknown focus stage")
    if origin == "start":
        # A QEMU key remains down for 20 ms; release Escape before the chord.
        return [("esc", 0.04 + delay), ("ctrl-alt-spc", 0.25)]
    return [("ctrl-alt-spc", 0.25)]


def drive_focus_keyboard(output: Path, *, container: str) -> None:
    """Acknowledge each fresh request only after its hardware input was sent."""
    from tools.windows.desktop_driver import read_shared_json

    for stage in ("origin", "activate"):
        request_path = output / f"keyboard-{stage}.request.json"
        response_path = output / f"keyboard-{stage}.response.json"
        deadline = time.monotonic() + 90
        while True:
            if request_path.exists():
                request = read_shared_json(request_path)
                response = (
                    read_shared_json(response_path) if response_path.exists() else None
                )
                if response is None or response["token"] != request["token"]:
                    break
            if time.monotonic() >= deadline:
                raise TimeoutError("No fresh keyboard request: " + stage)
            time.sleep(0.1)
        if request["stage"] != stage:
            raise ValueError("Focus request stage does not match its filename")
        commands = focus_commands(request)
        if stage == "activate" and request["origin"] != "start":
            time.sleep(float(request["delay"]))
        send_keys(commands, container=container)
        response = {"token": request["token"], "commands": commands}
        temporary = response_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(response), encoding="utf-8")
        temporary.replace(response_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="docking-windows-lab")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", help="Open Windows Run and type this command")
    mode.add_argument("--key", help="A QEMU key chord, such as esc or meta_l-r")
    mode.add_argument(
        "--focus-handshake", type=Path, help="Drive focus_smoke --external-keyboard"
    )
    args = parser.parse_args()
    if args.focus_handshake is not None:
        drive_focus_keyboard(args.focus_handshake, container=args.container)
        return
    commands: list[tuple[str, float]] = []
    if args.run:
        commands.append(("meta_l-r", 1.0))
        commands.extend((text_key(c), 0.05) for c in args.run)
        commands.append(("ret", 0.5))
    elif args.key:
        if not re.fullmatch(r"[a-z0-9_]+(?:-[a-z0-9_]+)*", args.key):
            parser.error("Invalid QEMU key chord")
        commands.append((args.key, 0.5))
    send_keys(commands, container=args.container)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    main()
