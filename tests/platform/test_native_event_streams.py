"""Real idle IPC readers must stop promptly and restart without stale state."""

import socket
import threading

import pytest

from docking.platform.backends.wayland.hyprland_ipc import (
    HyprlandEventStream,
    HyprlandSocketPaths,
)
from docking.platform.backends.wayland.niri_ipc import NiriEventStream


@pytest.mark.parametrize("backend", ["hyprland", "niri"])
def test_idle_stream_stops_and_restarts(tmp_path, backend):
    path = tmp_path / "events.sock"
    received = threading.Event()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.settimeout(2)
    server.bind(str(path))
    server.listen()
    requests = []
    closed = []

    def respond():
        with server:
            for _ in range(2):
                with server.accept()[0] as peer:
                    peer.settimeout(2)
                    if backend == "niri":
                        requests.append(peer.recv(4096))
                        peer.sendall(b'{"WindowFocusChanged":{"id":1}}\n')
                    else:
                        peer.sendall(b"activewindowv2>>0x123\n")
                    closed.append(peer.recv(4096))

    reader = (
        NiriEventStream(socket_path=path, callback=lambda _event: received.set())
        if backend == "niri"
        else HyprlandEventStream(
            paths=HyprlandSocketPaths(command=path, events=path),
            callback=lambda _event: received.set(),
        )
    )
    peer_thread = threading.Thread(target=respond, daemon=True)
    peer_thread.start()
    try:
        for _ in range(2):
            received.clear()
            reader.start()
            assert received.wait(1), "Native event was not delivered"
            worker = reader._thread
            assert worker is not None and worker.is_alive()
            reader.stop()
            assert not worker.is_alive(), "Idle socket reader leaked on shutdown"
        peer_thread.join(timeout=1)
        assert not peer_thread.is_alive()
        assert closed == [b"", b""]
        if backend == "niri":
            assert requests == [b'{"EventStream":null}\n'] * 2
    finally:
        reader.stop()
        server.close()
