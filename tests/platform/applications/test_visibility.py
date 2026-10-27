"""Visibility IO is coalesced, and only current main-loop results can publish."""

import threading
import time

from docking.platform.applications.visibility import ApplicationVisibilityLookup


class _Idle:
    def __init__(self):
        self.callbacks = {}
        self.sequence = 0
        self.lock = threading.RLock()

    def add(self, callback):
        with self.lock:
            self.sequence += 1
            self.callbacks[self.sequence] = callback
            return self.sequence

    def remove(self, source):
        with self.lock:
            return self.callbacks.pop(source, None) is not None

    def flush(self):
        with self.lock:
            callbacks = tuple(self.callbacks.values())
            self.callbacks.clear()
        for callback in callbacks:
            assert callback() is False


def _wait(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < deadline, "Visibility lookup did not settle"
        time.sleep(0.005)


def _lookup(resolve, idle, changed):
    return ApplicationVisibilityLookup(
        resolve=resolve,
        on_changed=changed,
        schedule_idle=idle.add,
        remove_source=idle.remove,
    )


def test_io_uses_a_worker_and_only_requested_ids_publish_on_the_main_loop():
    main = threading.get_ident()
    idle, threads, changes = _Idle(), [], []

    def resolve(ids):
        assert ids == ("one", "two")
        threads.append(threading.get_ident())
        return frozenset(("one", "unrelated"))

    lookup = _lookup(resolve, idle, lambda: changes.append(threading.get_ident()))
    try:
        lookup.request(("one", "two"))
        assert not idle.callbacks
        lookup.start()
        lookup.request(("two", "one", "one"))
        _wait(lambda: bool(idle.callbacks))
        assert not lookup.hidden_ids
        idle.flush()
        assert lookup.hidden_ids == {"one"}
        assert threads[0] != main
        assert changes == [main]
    finally:
        lookup.stop()


def test_intermediate_requests_and_older_inflight_results_are_discarded():
    idle, started, release = _Idle(), threading.Event(), threading.Event()
    calls = []

    def resolve(ids):
        calls.append(ids)
        if ids == ("old",):
            started.set()
            assert release.wait(2)
        return frozenset(ids)

    lookup = _lookup(resolve, idle, lambda: None)
    try:
        lookup.start()
        lookup.request(("old",))
        assert started.wait(1)
        lookup.request(("intermediate",))
        lookup.request(("current",))
        release.set()
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        assert lookup.hidden_ids == {"current"}
        assert calls == [("old",), ("current",)]
    finally:
        release.set()
        lookup.stop()


def test_invalidation_rereads_unchanged_ids_and_can_reveal_an_updated_entry():
    idle, calls = _Idle(), []

    def resolve(ids):
        calls.append(ids)
        return frozenset(ids) if len(calls) == 1 else frozenset()

    lookup = _lookup(resolve, idle, lambda: None)
    try:
        lookup.start()
        lookup.request(("app",))
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        assert lookup.hidden_ids == {"app"}
        lookup.request(("app",))
        lookup.invalidate()
        lookup.request(("app",))
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        assert not lookup.hidden_ids
        assert len(calls) == 2
    finally:
        lookup.stop()


def test_stop_removes_queued_delivery_and_rejects_a_racing_callback():
    idle, changed = _Idle(), []
    lookup = _lookup(lambda ids: frozenset(ids), idle, lambda: changed.append(True))
    lookup.start()
    lookup.request(("app",))
    _wait(lambda: bool(idle.callbacks))
    callback = next(iter(idle.callbacks.values()))
    lookup.stop()
    assert not idle.callbacks
    callback()
    assert not changed
    assert not lookup.hidden_ids


def test_restarting_cannot_publish_a_stopped_workers_result():
    idle, started, release, done = (
        _Idle(),
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )

    def resolve(ids):
        if ids == ("old",):
            started.set()
            assert release.wait(2)
            done.set()
        return frozenset(ids)

    lookup = _lookup(resolve, idle, lambda: None)
    try:
        lookup.start()
        lookup.request(("old",))
        assert started.wait(1)
        lookup.stop()
        lookup.start()
        lookup.request(("new",))
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        assert lookup.hidden_ids == {"new"}
        release.set()
        assert done.wait(1)
        lookup.stop()
        idle.flush()
        assert lookup.hidden_ids == {"new"}
    finally:
        release.set()
        lookup.stop()
