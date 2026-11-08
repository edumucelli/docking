#!/usr/bin/env python3
"""Small Wayfire IPC client for development smoke tests.

Wayfire's ipc plugin exposes a Unix socket whose path is exported as
WAYFIRE_SOCKET inside the compositor session. Messages are JSON payloads with a
4-byte native-endian length prefix. The ipc-rules plugin registers the
window-rules/* methods that are useful for Docking integration work.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
from pathlib import Path
from typing import Any


def _read_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RuntimeError("Wayfire IPC socket closed before response completed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _candidate_sockets() -> list[str]:
    candidates: list[str] = []
    if env_socket := os.environ.get("WAYFIRE_SOCKET"):
        candidates.append(env_socket)
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        candidates.extend(
            str(path) for path in Path(runtime_dir).glob("wayfire-*.socket")
        )
    return candidates


def _resolve_socket(path: str | None) -> str:
    if path:
        return path
    for candidate in _candidate_sockets():
        if Path(candidate).is_socket():
            return candidate
    raise SystemExit(
        "Could not find a Wayfire IPC socket. Run this inside Wayfire, pass "
        "--socket, or export WAYFIRE_SOCKET."
    )


def call_wayfire(socket_path: str, method: str, data: dict[str, Any]) -> Any:
    request = json.dumps({"method": method, "data": data}).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(socket_path)
        sock.sendall(struct.pack("I", len(request)) + request)
        response_len = struct.unpack("I", _read_exact(sock, 4))[0]
        response = _read_exact(sock, response_len)
    return json.loads(response.decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Call a Wayfire IPC method")
    parser.add_argument(
        "method",
        nargs="?",
        default="window-rules/list-views",
        help="IPC method to call",
    )
    parser.add_argument(
        "--data",
        default="{}",
        help="JSON object sent as the method data payload",
    )
    parser.add_argument("--socket", help="Path to WAYFIRE_SOCKET")
    args = parser.parse_args()

    data = json.loads(args.data)
    if not isinstance(data, dict):
        raise SystemExit("--data must be a JSON object")

    socket_path = _resolve_socket(args.socket)
    response = call_wayfire(socket_path=socket_path, method=args.method, data=data)
    print(json.dumps(response, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
