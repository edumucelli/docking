"""Capture the actual Windows VM display before the guest lab agent is ready."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--share", type=Path, required=True)
    parser.add_argument("--container", default="docking-windows-lab")
    parser.add_argument("--name", default="windows-screen")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", args.name):
        parser.error(
            "Capture names may contain letters, digits, dots, dashes and underscores"
        )
    target = f"/shared/lab/{args.name}.ppm"
    # Dockur's monitor.sock speaks QEMU's text monitor, rather than QMP.
    program = (
        "import socket,time\n"
        "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)\n"
        "s.settimeout(3)\n"
        "s.connect('/run/shm/monitor.sock')\n"
        "s.recv(4096)\n"
        f"s.sendall(('info status\\nscreendump {target}\\n').encode())\n"
        "time.sleep(0.5)\n"
        "print(s.recv(65536).decode(errors='replace'))\n"
        "s.close()\n"
    )
    result = subprocess.run(
        ["docker", "exec", args.container, "python3", "-c", program],
        text=True,
        capture_output=True,
        check=True,
    )
    status = re.search(r"VM status: (\w+)", result.stdout)
    source = args.share / "lab" / f"{args.name}.ppm"
    from PIL import Image

    image = Image.open(source)
    output = source.with_suffix(".png")
    image.save(output)
    print(
        json.dumps(
            {
                "vm_status": status.group(1) if status else None,
                "capture": str(output),
                "size": image.size,
            }
        )
    )


if __name__ == "__main__":
    main()
