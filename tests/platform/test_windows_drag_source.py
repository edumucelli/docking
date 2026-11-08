"""GTK/OLE handoff ordering, shutdown races and owned modal-loop resources."""

import ctypes
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.platform.applications.implementations.windows import drag_source as native


class Widget:
    def __init__(self):
        self.handlers = {}
        self.sequence = 0
        self.get_window = MagicMock(return_value=object())

    def connect(self, signal, callback, *args):
        self.sequence += 1
        self.handlers[self.sequence] = (signal, callback, args)
        return self.sequence

    def disconnect(self, identifier):
        del self.handlers[identifier]

    def handler_is_connected(self, identifier):
        return identifier in self.handlers

    def emit(self, signal, *args):
        for name, callback, extra in tuple(self.handlers.values()):
            if name == signal:
                callback(self, *args, *extra)

    def drag_source_set(self, *_args):
        pass


@pytest.fixture
def setup_source(monkeypatch):
    callbacks, removed, canceled = {}, [], []
    sequence = [0]

    def add(callback):
        sequence[0] += 1
        callbacks[sequence[0]] = callback
        return sequence[0]

    def remove(identifier):
        removed.append(identifier)
        return callbacks.pop(identifier, None) is not None

    def flush():
        queued = list(callbacks.items())
        callbacks.clear()
        for _identifier, callback in queued:
            assert callback() is False

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: object(), raising=False)
    monkeypatch.setattr(native.GLib, "idle_add", add)
    monkeypatch.setattr(native.GLib, "source_remove", remove)
    monkeypatch.setattr(native.Gtk, "drag_cancel", canceled.append)
    host, row, context = Widget(), Widget(), Widget()
    source = native.WindowsFileDragSource(host)
    data = (("CF_HDROP", b"prepared path bytes"),)
    source.prepare(row, application_id="Native.App", data=data)
    return SimpleNamespace(
        source=source,
        host=host,
        row=row,
        context=context,
        data=data,
        callbacks=callbacks,
        removed=removed,
        canceled=canceled,
        flush=flush,
    )


@pytest.mark.parametrize("order", [("gtk", "native"), ("native", "gtk")])
def test_native_drag_waits_for_both_gtk_end_and_original_ole_loop_exit(
    setup_source, monkeypatch, order
):
    state = setup_source
    run = MagicMock()
    monkeypatch.setattr(state.source, "_run", run)
    state.row.emit("drag-begin", state.context)
    assert state.canceled == []
    state.flush()
    assert state.canceled == [state.context]
    for index, event in enumerate(order):
        if event == "gtk":
            state.row.emit("drag-end", state.context)
        else:
            state.context.emit("dnd-finished")
        run.assert_not_called()
        if index == 0:
            assert not state.callbacks
    state.flush()
    run.assert_called_once_with(
        (*state.data, ("application/x-docking-application-id", b"Native.App"))
    )
    assert not state.context.handlers
    state.source.stop()


def test_stop_cancels_pending_source_and_rejects_an_already_queued_callback(
    setup_source, monkeypatch
):
    state = setup_source
    run = MagicMock()
    monkeypatch.setattr(state.source, "_run", run)
    state.row.emit("drag-begin", state.context)
    racing = next(iter(state.callbacks.values()))
    state.source.stop()
    assert state.canceled == [state.context]
    assert not state.callbacks and state.removed
    assert (
        not state.context.handlers
        and not state.row.handlers
        and not state.host.handlers
    )
    assert racing() is False
    run.assert_not_called()


def test_releasing_before_the_handoff_does_not_create_a_data_object(
    setup_source, monkeypatch
):
    state = setup_source
    object_factory = MagicMock()
    monkeypatch.setattr(native, "cached_data_object", object_factory)
    monkeypatch.setattr(native, "bind", lambda *_args: lambda _key: 0)
    state.source._run(state.data)
    object_factory.assert_not_called()
    state.host.get_window.assert_not_called()
    state.source.stop()


def test_host_destroy_releases_capture_before_modal_and_data_resources_exit(
    setup_source, monkeypatch
):
    state = setup_source
    events = []

    @contextmanager
    def data_object(data):
        assert data == state.data
        events.append("data-enter")
        try:
            yield ctypes.c_void_p(11)
        finally:
            events.append("data-exit")

    @contextmanager
    def modal(_user, _on_error):
        events.append("modal-enter")
        try:
            yield 31
        finally:
            events.append("modal-exit")

    def bind(_library, name, *_signature):
        if name == "PostMessageW":

            def post(hwnd, message, key, _data):
                assert (hwnd, message, key) == (31, 0x100, 0x1B)
                events.append("native-cancel-posted")
                return True

            return post
        if name == "GetAsyncKeyState":
            return lambda _key: 0x8000
        if name == "ReleaseCapture":
            return lambda: events.append("capture-released")
        assert name == "SHDoDragDrop"

        def drag(_window, _data, _source, allowed, _result):
            assert allowed == 1
            state.host.emit("destroy")
            assert events[-1] == "capture-released"
            return 0x40101

        return drag

    monkeypatch.setattr(native, "cached_data_object", data_object)
    monkeypatch.setattr(native, "_gtk_events", modal)
    monkeypatch.setattr(native, "window_handle", lambda _window: 22)
    monkeypatch.setattr(native, "bind", bind)
    state.source._run(state.data)
    assert events == [
        "data-enter",
        "modal-enter",
        "native-cancel-posted",
        "capture-released",
        "modal-exit",
        "data-exit",
    ]
    assert (
        not state.source._active
        and state.source._native_window == 0
        and not state.row.handlers
        and not state.host.handlers
    )


@pytest.mark.parametrize("timer_fails", [False, True])
def test_modal_timer_is_bounded_and_drains_only_its_owned_window(
    monkeypatch, timer_fails
):
    events, iterations = [], []
    context = SimpleNamespace(
        pending=lambda: True, iteration=lambda _block: iterations.append(1)
    )
    monkeypatch.setattr(
        native.GLib, "MainContext", SimpleNamespace(default=lambda: context)
    )
    monkeypatch.setattr(
        ctypes, "WINFUNCTYPE", lambda *_args: lambda fn: fn, raising=False
    )
    monkeypatch.setattr(
        ctypes, "WinError", lambda _code: OSError("No timer"), raising=False
    )
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, "CDLL", lambda _name: object())
    queued = [True, False]

    def bind(_library, name, *_signature):
        if name == "gdk_win32_set_modal_dialog_libgtk_only":
            return lambda hwnd: events.append(("modal", hwnd))
        if name == "CreateWindowExW":
            return lambda *_args: 31
        if name == "SetTimer":

            def create(hwnd, _id, interval, callback):
                assert hwnd == 31 and interval == 16
                callback(None, 0, 0, 0)
                return 0 if timer_fails else 73

            return create
        if name == "PeekMessageW":

            def peek(_message, hwnd, first, last, flags):
                assert (hwnd, first, last, flags) == (31, 0x113, 0x113, 1)
                events.append("drain")
                return queued.pop(0)

            return peek
        if name == "KillTimer":

            def kill(hwnd, identifier):
                assert (hwnd, identifier) == (31, 1)
                events.append(name)

            return kill
        assert name == "DestroyWindow"
        return lambda *_args: events.append(name)

    monkeypatch.setattr(native, "bind", bind)
    if timer_fails:
        with pytest.raises(OSError), native._gtk_events(object(), lambda: None):
            pytest.fail("Missing timer must reject the modal operation")
        assert events == ["DestroyWindow"]
    else:
        with native._gtk_events(object(), lambda: None):
            assert events == [("modal", 31)]
        assert events == [
            ("modal", 31),
            ("modal", None),
            "KillTimer",
            "drain",
            "drain",
            "DestroyWindow",
        ]
    assert len(iterations) == 32
