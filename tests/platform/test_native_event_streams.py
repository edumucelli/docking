"""Real idle IPC readers must stop promptly and restart without stale state."""

import json
import socket
import struct
import threading
from types import SimpleNamespace

import pytest

from docking.platform.backends.wayland.hyprland_ipc import (
    HyprlandEventStream,
    HyprlandSocketPaths,
)
from docking.platform.backends.wayland.niri_ipc import NiriEventStream
from docking.platform.backends.wayland.wayfire_ipc import WayfireEventWatcher


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


def test_wayfire_idle_watcher_does_not_spin_the_main_loop(tmp_path, monkeypatch):
    from gi.repository import GLib

    import docking.platform.backends.wayland.wayfire_ipc as wayfire_ipc

    # Other GTK tests can leave callbacks on the default context. Dispatch only
    # this watcher's real GLib sources, so unrelated native callbacks cannot run
    # against fixtures that have already been torn down.
    context = GLib.MainContext.new()

    def timeout_add(interval, callback):
        source = GLib.timeout_source_new(interval)
        source.set_callback(lambda *_data: callback())
        return source.attach(context)

    def source_remove(source_id):
        source = context.find_source_by_id(source_id)
        if source is not None:
            source.destroy()

    monkeypatch.setattr(
        wayfire_ipc,
        "GLib",
        SimpleNamespace(timeout_add=timeout_add, source_remove=source_remove),
    )

    path = tmp_path / "wayfire.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.settimeout(2)
    server.bind(str(path))
    server.listen()

    def respond():
        with server, server.accept()[0] as peer:
            peer.settimeout(2)
            size = struct.unpack("I", peer.recv(4))[0]
            request = bytearray()
            while len(request) < size:
                request.extend(peer.recv(size - len(request)))
            assert json.loads(request)["method"] == "window-rules/events/watch"
            for message in (
                {"result": "ok"},
                {"event": "view-focused", "view": {"id": 7}},
            ):
                data = json.dumps(message).encode()
                peer.sendall(struct.pack("I", len(data)) + data)
            assert peer.recv(1) == b""

    events = []
    watcher = WayfireEventWatcher(
        socket_path=str(path), on_event=lambda name, view: events.append((name, view))
    )
    drains = []
    original = watcher._drain_queue

    def drain():
        drains.append(True)
        return original()

    monkeypatch.setattr(watcher, "_drain_queue", drain)
    worker = threading.Thread(target=respond, daemon=True)
    worker.start()
    loop = GLib.MainLoop.new(context, False)
    timer = 0

    def finish():
        nonlocal timer
        timer = 0
        loop.quit()
        return False

    timer = timeout_add(250, finish)
    try:
        assert watcher.start()
        loop.run()
        assert events == [("view-focused", {"id": 7})]
        assert 1 <= len(drains) < 20, "Idle watcher is continuously runnable"
    finally:
        watcher.stop()
        if timer:
            source_remove(timer)
        worker.join(timeout=1)
        server.close()
    assert not worker.is_alive()


def test_wayfire_idle_watcher_restarts_and_discards_previous_session_queue(
    tmp_path, monkeypatch
):
    import docking.platform.backends.wayland.wayfire_ipc as wayfire_ipc

    monkeypatch.setattr(wayfire_ipc, "GLib", None)
    path = tmp_path / "wayfire-restart.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.settimeout(2)
    server.bind(str(path))
    server.listen()
    closed = []

    def respond():
        with server:
            for _ in range(2):
                with server.accept()[0] as peer:
                    peer.settimeout(2)
                    size = struct.unpack("I", wayfire_ipc._read_exact(peer, 4))[0]
                    request = json.loads(wayfire_ipc._read_exact(peer, size))
                    assert request["method"] == "window-rules/events/watch"
                    data = b'{"result":"ok"}'
                    peer.sendall(struct.pack("I", len(data)) + data)
                    closed.append(peer.recv(1))

    thread = threading.Thread(target=respond, daemon=True)
    thread.start()
    events = []
    watcher = WayfireEventWatcher(
        socket_path=str(path), on_event=lambda *args: events.append(args)
    )
    try:
        for _ in range(2):
            assert watcher.start()
            assert watcher._queue.empty()
            watcher._queue.put({"event": "view-focused", "view": {"id": 99}})
            worker = watcher._thread
            assert worker is not None and worker.is_alive()
            watcher.stop()
            assert not worker.is_alive()
            assert watcher._queue.empty()
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert closed == [b"", b""]
        assert events == []
    finally:
        watcher.stop()
        server.close()
