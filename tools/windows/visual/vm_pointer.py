"""Supply a requested drag through the owned VM's loopback VNC virtual mouse.

This bypasses guest SendInput restrictions without changing Windows privileges.
The guest acceptance tool independently verifies the resulting native geometry.
Only a VNC endpoint offering the None security type is supported; the lab binds
it to loopback. A shared connection preserves an existing viewer.
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import time
from pathlib import Path


def read_exact(connection: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        chunk = connection.recv(count - len(chunks))
        if not chunk:
            raise ConnectionError("VNC closed the input connection")
        chunks.extend(chunk)
    return bytes(chunks)


def drag(*, port: int, start: tuple[int, int], end: tuple[int, int]) -> tuple[int, int]:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        version = read_exact(connection, 12)
        if version != b"RFB 003.008\n":
            raise ValueError("Expected an RFB 3.8 lab endpoint")
        connection.sendall(b"RFB 003.008\n")
        count = read_exact(connection, 1)[0]
        security = read_exact(connection, count)
        if 1 not in security:
            raise ValueError("The loopback lab does not offer None VNC security")
        connection.sendall(b"\x01")
        if read_exact(connection, 4) != b"\x00" * 4:
            raise ConnectionError("VNC rejected the input connection")
        connection.sendall(b"\x01")  # ClientInit: shared desktop.
        header = read_exact(connection, 24)
        width, height = struct.unpack(">HH", header[:4])
        name_size = struct.unpack(">I", header[20:])[0]
        if name_size > 4096:
            raise ValueError("Unexpected VNC desktop name length")
        read_exact(connection, name_size)
        # QEMU selects the active mouse mode during SetEncodings, not during
        # ServerInit. Request its PointerTypeChange pseudo-encoding and require
        # the absolute tablet used by this lab before sending coordinates.
        connection.sendall(struct.pack(">BBHii", 2, 0, 2, 0, -257))
        if read_exact(connection, 4) != b"\x00\x00\x00\x01":
            raise ValueError("Expected QEMU's mouse-mode notification")
        mode, _y, _width, _height, encoding = struct.unpack(
            ">HHHHi", read_exact(connection, 12)
        )
        if encoding != -257 or mode != 1:
            raise ValueError("The lab must expose an absolute virtual tablet")
        if any(not (0 <= x < width and 0 <= y < height) for x, y in (start, end)):
            raise ValueError("Requested pointer coordinates exceed the VM display")

        def pointer(buttons: int, x: int, y: int) -> None:
            connection.sendall(struct.pack(">BBHH", 5, buttons, x, y))

        pointer(0, *start)
        time.sleep(0.2)
        pointer(1, *start)
        try:
            for step in range(1, 13):
                pointer(
                    1,
                    int(start[0] + (end[0] - start[0]) * step / 12),
                    int(start[1] + (end[1] - start[1]) * step / 12),
                )
                time.sleep(0.05)
        finally:
            pointer(0, *end)
        time.sleep(0.3)
        return width, height


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--port", type=int, default=15900)
    args = parser.parse_args()
    if not args.request.name.endswith("-request.json"):
        parser.error("Use a stage name ending in -request.json")
    request = json.loads(args.request.read_text())
    start, end = tuple(request["start"]), tuple(request["end"])
    width, height = drag(port=args.port, start=start, end=end)
    result = {"ok": True, "size": [width, height], "start": start, "end": end}
    complete = args.request.with_name(
        args.request.name.removesuffix("-request.json") + "-complete.json"
    )
    temporary = complete.with_suffix(".tmp")
    temporary.write_text(json.dumps(result))
    temporary.replace(complete)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
