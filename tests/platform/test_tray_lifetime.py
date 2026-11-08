"""Successor tray workers cannot acquire an exclusive host before old cleanup."""

from queue import Queue
from threading import Event, Thread

from docking.applets.worker import SerialWorker
from docking.platform.tray import service as module
from docking.platform.tray.service import TrayService
from docking.platform.tray.state import TrayState
from tests.platform_factories import EmptyTrayBackend


def test_restart_waits_for_native_close_on_worker_and_rejects_obsolete_results(
    monkeypatch,
):
    replies = Queue()
    release = Event()
    closing, first_polled, second_started, second_polled, second_closed = (
        Event() for _ in range(5)
    )
    threads = []
    closes = []

    class Backend(EmptyTrayBackend):
        def __init__(self, identifier):
            self.identifier = identifier

        def get_state(self):
            (first_polled if self.identifier == "first" else second_polled).set()
            return TrayState(True, self.identifier, ())

        def close(self):
            if self.identifier == "first":
                closing.set()
                assert release.wait(3), "Test must release the owned native host"
            closes.append(self.identifier)
            if self.identifier == "second":
                second_closed.set()

    def idle_add(callback, *args):
        replies.put((callback, args))
        return replies.qsize()

    def thread_factory(*, target, daemon):
        successor = bool(threads)

        def run():
            if successor:
                second_started.set()
            target()

        thread = Thread(target=run, daemon=daemon)
        threads.append(thread)
        return thread

    backends = iter((Backend("first"), Backend("second")))
    monkeypatch.setattr(module.GLib, "timeout_add_seconds", lambda *_args: 1)
    monkeypatch.setattr(module.GLib, "source_remove", lambda _source: True)
    service = TrayService(
        backend_factory=lambda: next(backends),
        worker_factory=lambda **kwargs: SerialWorker(
            idle_add=idle_add, thread_factory=thread_factory, **kwargs
        ),
    )
    try:
        service.start()
        assert first_polled.wait(2)
        generation = service.generation
        service.stop()
        assert closing.wait(2)
        service.start()
        assert second_started.wait(2)
        assert not second_polled.is_set()
        assert not service.activate("owned", generation=generation)
        assert not service.activate("owned", generation=service.generation)
        callback, args = replies.get(timeout=2)
        callback(*args)
        assert not service.state.available
        release.set()
        assert second_polled.wait(2)
        callback, args = replies.get(timeout=2)
        callback(*args)
        assert service.state.mode == "second"
    finally:
        release.set()
        service.stop()
        for thread in threads:
            thread.join(timeout=3)
            assert not thread.is_alive()
    assert second_closed.is_set() and closes == ["first", "second"]
