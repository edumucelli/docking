"""Real picker sockets, responsive selection and fresh termination targets."""

import json
import os
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from gi.repository import GLib

import docking.platform.backends.wayland.niri_picking as picking
from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    WindowId,
    WindowSnapshot,
)
from docking.platform.backends.wayland.niri_ipc import NiriWindowService
from tests.platform.application_fakes import identity_services


@pytest.fixture
def picker_context(monkeypatch):
    context = GLib.MainContext.new()
    context.push_thread_default()

    def timeout_add(seconds, callback):
        source = GLib.timeout_source_new(seconds * 1000)
        source.set_callback(lambda *_: callback())
        return source.attach(context)

    def remove(source_id):
        source = context.find_source_by_id(source_id)
        if source is not None:
            source.destroy()

    monkeypatch.setattr(
        picking,
        "GLib",
        SimpleNamespace(
            MainLoop=lambda: GLib.MainLoop.new(context, False),
            timeout_add_seconds=timeout_add,
            source_remove=remove,
            PRIORITY_DEFAULT=GLib.PRIORITY_DEFAULT,
            Error=GLib.Error,
        ),
    )
    try:
        yield context
    finally:
        while context.pending():
            context.iteration(False)
        context.pop_thread_default()


def _row(pid=1234):
    return WindowSnapshot(
        id=WindowId(DisplayServer.WAYLAND, "8"),
        desktop_id="lab.desktop",
        title="Fixture",
        pid=pid,
    )


def _server(tmp_path, reply, *, delay=0):
    path = tmp_path / "niri.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.settimeout(2)
    server.bind(str(path))
    server.listen()
    requests = []

    def respond():
        with server, server.accept()[0] as peer:
            peer.settimeout(2)
            request = bytearray()
            while b"\n" not in request:
                chunk = peer.recv(4096)
                if not chunk:
                    break
                request.extend(chunk)
            requests.append(json.loads(request))
            time.sleep(delay)
            try:
                peer.sendall(reply[:4])
                peer.sendall(reply[4:])
            except BrokenPipeError:
                pass

    worker = threading.Thread(target=respond, daemon=True)
    worker.start()
    return path, worker, requests


@pytest.mark.parametrize(
    "reply",
    [
        b'{"Ok":{"PickedWindow":{"id":8,"pid":9999}}}\n',
        b'{"Ok":{"PickedWindow":null}}\n',
        b'{"Ok":{"PickedWindow":{"id":true}}}\n',
        b'{"Err":"picker unavailable"}\n',
        b"bad json\n",
        b"",
    ],
)
def test_native_picker_handles_selection_cancellation_and_bad_replies(
    tmp_path,
    picker_context,
    reply,
):
    path, worker, requests = _server(tmp_path, reply)
    row = _row()
    windows = SimpleNamespace(live_window=MagicMock(return_value=row))
    service = picking.NiriWindowPickService(windows=windows, socket_path=str(path))
    selected = service.select_window()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert requests == ["PickWindow"]
    assert (selected is row) == (b'"id":8' in reply)
    if selected:
        assert selected.pid == 1234  # use fresh state, not the picker reply's PID
    else:
        windows.live_window.assert_not_called()


def test_waiting_for_native_click_keeps_the_main_context_responsive(
    tmp_path,
    picker_context,
):
    path, worker, _ = _server(
        tmp_path,
        b'{"Ok":{"PickedWindow":{"id":8}}}\n',
        delay=0.08,
    )
    ticks = []
    timer = GLib.timeout_source_new(10)
    timer.set_callback(lambda *_: ticks.append(True) or True)
    timer.attach(picker_context)
    service = picking.NiriWindowPickService(
        windows=SimpleNamespace(live_window=lambda _: _row()),
        socket_path=str(path),
    )
    try:
        assert service.select_window() is not None
        assert len(ticks) >= 2
    finally:
        timer.destroy()
        worker.join(timeout=1)


def test_stop_cancels_an_outstanding_pick(tmp_path, picker_context):
    path, worker, _ = _server(
        tmp_path,
        b'{"Ok":{"PickedWindow":{"id":8}}}\n',
        delay=0.08,
    )
    service = picking.NiriWindowPickService(
        windows=SimpleNamespace(live_window=MagicMock()),
        socket_path=str(path),
    )
    timer = GLib.timeout_source_new(20)
    timer.set_callback(lambda *_: service.stop() or False)
    timer.attach(picker_context)
    assert service.select_window() is None
    service._windows.live_window.assert_not_called()
    worker.join(timeout=1)
    timer.destroy()


@pytest.mark.parametrize("pid", [None, 0, 1, os.getpid(), 9999, 1234])
def test_kill_revalidates_the_live_pid_and_rejects_changed_or_unsafe_targets(
    monkeypatch,
    pid,
):
    kill = MagicMock(return_value=True)
    monkeypatch.setattr("docking.applets.windowkiller.state.kill_pid", kill)
    windows = SimpleNamespace(live_window=lambda _: _row(pid))
    service = picking.NiriWindowPickService(windows=windows, socket_path="/unused")
    row = _row()
    service._selected_pids[row.id] = row.pid
    assert service.kill(row.id) is (
        ActionResult.OK if pid == 1234 else ActionResult.NOT_FOUND
    )
    assert bool(kill.call_count) is (pid == 1234)
    if pid == 1234:
        kill.assert_called_once_with(1234)


def test_live_window_does_not_reuse_cached_state_after_query_failure():
    client = MagicMock()
    client.ok_data.return_value = [
        {"id": 8, "app_id": "Alacritty", "title": "Fixture", "pid": 1234},
    ]
    service = NiriWindowService(
        model=SimpleNamespace(visible_items=list, update_running=lambda **_: None),
        **identity_services(),
        client=client,
        event_stream_factory=lambda _: None,
    )
    row = service.live_window(WindowId(DisplayServer.WAYLAND, "8"))
    assert row.pid == 1234
    client.ok_data.return_value = None
    assert service.live_window(row.id) is None
    assert service.list_all_windows()[0].pid == 1234  # taskbar keeps its fallback
