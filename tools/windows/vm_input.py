"""Send keyboard input to the Windows VM when its guest runner is unavailable."""

from __future__ import annotations

import argparse
import json
import re
import subprocess


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="docking-windows-lab")
    parser.add_argument("--run", help="Open Windows Run and type this command")
    parser.add_argument("--key", help="A QEMU key chord, such as esc or meta_l-r")
    args = parser.parse_args()
    commands: list[tuple[str, float]] = []
    if args.run:
        commands.append(("meta_l-r", 1.0))
        commands.extend((text_key(c), 0.05) for c in args.run)
        commands.append(("ret", 0.5))
    elif args.key:
        if not re.fullmatch(r"[a-z0-9_]+(?:-[a-z0-9_]+)*", args.key):
            parser.error("Invalid QEMU key chord")
        commands.append((args.key, 0.5))
    else:
        parser.error("Specify --run or --key")
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
        ["docker", "exec", args.container, "python3", "-c", program],
        check=True,
    )
    print(json.dumps({"sent_key_commands": len(commands)}))


if __name__ == "__main__":
    main()
