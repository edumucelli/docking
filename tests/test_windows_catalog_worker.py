"""Windows catalog reads must leave GTK responsive and publish whole generations."""

from dataclasses import replace
from threading import Event, current_thread, get_ident, local
from time import monotonic, sleep
from types import SimpleNamespace

import gi
import pytest

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from docking.platform.applications.metadata import ApplicationMetadata
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.applications.windows import catalog as windows_catalog
from tests.platform.application_fakes import GioApplicationFake, application

_ID = "Owned.Worker.App"


class _Handle(GioApplicationFake):
    def __init__(self, version):
        super().__init__(_ID, name="Gio " + version, commandline="owned.exe")

    def get_executable(self):
        return r"C:\Owned\owned.exe"


class _Registrations:
    def create(self, _target, *, startup_wm_class):
        raise AssertionError("No registration action belongs to this catalog test")

    def applications(self):
        return ()

    def directories(self):
        return ()


class _Monitor:
    def connect(self, signal, _callback):
        assert signal == "changed"
        return 1

    def disconnect(self, identifier):
        assert identifier == 1


class _Loop:
    """Dispatch real sources on a private context, excluding unrelated GI state."""

    def __init__(self):
        self.context = GLib.MainContext.new()
        self.sources = {}

    def attach(self, source, callback, arguments):
        # GI dispatch supplies user data on Linux and omits it on Windows.
        source.set_callback(lambda *_data: callback(*arguments))
        identifier = source.attach(self.context)
        self.sources[identifier] = source
        return identifier

    def idle(self, callback, *arguments):
        return self.attach(GLib.idle_source_new(), callback, arguments)

    def timeout(self, milliseconds, callback):
        return self.attach(GLib.timeout_source_new(milliseconds), callback, ())

    def remove(self, identifier):
        source = self.sources.pop(identifier, None)
        if source is None:
            return False
        source.destroy()
        return True

    def until(self, predicate):
        deadline = monotonic() + 3
        while not predicate():
            assert monotonic() < deadline, "Owned catalog test did not settle"
            self.context.iteration(False)
            sleep(0.001)

    def pump(self):
        deadline = monotonic() + 0.03
        while monotonic() < deadline:
            self.context.iteration(False)
            sleep(0.001)

    def close(self):
        for source in self.sources.values():
            source.destroy()
        self.sources.clear()


class _Worker:
    def __init__(self, monkeypatch, *, blocked=(), custom=None):
        self.ui_thread = get_ident()
        self.thread = None
        self.reader_threads, self.native_versions, self.errors = [], [], []
        self.lifecycle = []
        self.mode = "first"
        self.blocked = set(blocked)
        self.entered = {version: Event() for version in blocked}
        self.release = {version: Event() for version in blocked}
        self.selected = local()
        self.handles = {
            version: _Handle(version) for version in ("first", "obsolete", "current")
        }
        self.custom = custom
        self.loop = _Loop()

        def get_all():
            self.lifecycle.append(("gio", get_ident()))
            self.reader_threads.append(get_ident())
            # Fail immediately if GTK calls this known blocking API, so a bad
            # implementation cannot hang the pytest process on our own event.
            assert get_ident() != self.ui_thread, "Gio enumerated on GTK"
            self.thread = current_thread()
            version = self.mode
            if version in self.blocked:
                self.entered[version].set()
                if not self.release[version].wait(5):
                    self.errors.append("Owned Gio blocker was not released")
            self.selected.version = version
            if version == "gio-unavailable":
                raise OSError("Owned Gio provider unavailable")
            return () if version == "empty" else (self.handles[version],)

        def native(ids, *, registrations=(), cancelled, exporter):
            assert get_ident() != self.ui_thread and not cancelled()
            version = self.selected.version if self.custom is None else self.mode
            self.native_versions.append(version)
            return ApplicationMetadata(
                applications=()
                if version == "empty"
                else (replace(application(_ID), name="Shell " + version),)
            )

        monkeypatch.setattr(
            windows_catalog,
            "Gio",
            SimpleNamespace(AppInfo=SimpleNamespace(get_all=get_all)),
        )
        monkeypatch.setattr(
            windows_catalog.catalog, "read_application_metadata", native
        )
        self.backend = windows_catalog.WindowsApplicationCatalogBackend()
        self.registry = ApplicationRegistry(
            catalog_backend=self.backend,
            application_source=(lambda: (custom,)) if custom is not None else None,
            executable_registration=_Registrations(),
            desktop_directories_source=lambda: (),
        )

        def monitor():
            self.lifecycle.append(("monitor", get_ident()))
            return _Monitor()

        self.registry._app_monitor_factory = monitor
        self.registry._change_monitor_factory = lambda _changed: None
        self.registry._schedule_timeout = self.loop.timeout
        self.registry._cancel_timeout = self.loop.remove
        self.registry._debounce_ms = 1
        assert self.registry._metadata is not None
        self.registry._metadata._schedule_idle = self.loop.idle
        self.registry._metadata._remove_source = self.loop.remove

    def ready(self, version):
        self.loop.until(
            lambda: (
                self.registry.get(_ID) is not None
                and self.registry.get(_ID).name == "Shell " + version
            )
        )
        self.loop.pump()

    def changed(self, version):
        self.mode = version
        self.registry._on_registry_changed()

    def close(self):
        self.registry.stop()
        for event in self.release.values():
            event.set()
        if self.thread is not None:
            self.thread.join(2)
            assert not self.thread.is_alive(), "Owned metadata worker leaked"
        self.loop.close()
        assert not self.errors


@pytest.fixture
def worker(monkeypatch):
    created = []

    def make(**arguments):
        result = _Worker(monkeypatch, **arguments)
        created.append(result)
        return result

    yield make
    for result in reversed(created):
        result.close()


def test_blocked_gio_read_keeps_real_glib_heartbeat_and_refresh_responsive(worker):
    owned = worker(blocked=("first",))
    beats = []
    owned.loop.timeout(2, lambda: (beats.append(get_ident()), True)[1])
    owned.registry.start()
    owned.loop.until(owned.entered["first"].is_set)
    owned.loop.until(lambda: len(beats) >= 4)
    assert set(beats) == {owned.ui_thread}
    assert owned.reader_threads and owned.ui_thread not in owned.reader_threads
    assert not owned.release["first"].is_set()
    assert owned.backend.default_source() == ()
    for _index in range(4):
        owned.registry.refresh()
    assert owned.registry.initial_discovery_pending
    assert owned.registry.get(_ID) is None
    assert owned.registry._gio_handle_for(_ID) is None
    owned.release["first"].set()
    owned.ready("first")
    assert not owned.registry.initial_discovery_pending
    assert owned.registry._gio_handle_for(_ID) is owned.handles["first"]


def test_superseded_worker_generation_cannot_mix_new_handles_and_old_catalog(worker):
    owned = worker(blocked=("obsolete", "current"))
    observed = []

    def published():
        app = owned.registry.get(_ID)
        if app is not None:
            observed.append((app.name, owned.registry._gio_handle_for(_ID)))

    owned.registry.subscribe(published)
    owned.registry.start()
    owned.ready("first")
    owned.changed("obsolete")
    owned.loop.until(owned.entered["obsolete"].is_set)
    owned.changed("current")
    owned.loop.until(lambda: owned.registry._metadata._pending is not None)
    owned.release["obsolete"].set()
    owned.loop.until(owned.entered["current"].is_set)
    owned.loop.pump()
    assert owned.registry.get(_ID).name == "Shell first"
    assert owned.registry._gio_handle_for(_ID) is owned.handles["first"]
    owned.release["current"].set()
    owned.ready("current")
    assert observed[-1] == ("Shell current", owned.handles["current"])
    assert all(
        (name, handle)
        in (
            ("Shell first", owned.handles["first"]),
            ("Shell current", owned.handles["current"]),
        )
        for name, handle in observed
    )


def test_shell_replacement_without_broadcast_discards_held_read_and_publishes_fresh_names(
    worker, monkeypatch
):
    import ctypes

    from docking.platform.backends.windows import catalog_monitor

    owned = worker()
    shell = [(101, 11)]
    subscriptions = []
    held, release = Event(), Event()
    cancelled_reads, observed = [], []
    native_read = windows_catalog.catalog.read_application_metadata

    def native(ids, *, registrations, cancelled, exporter):
        if owned.selected.version == "obsolete":
            held.set()
            assert release.wait(5), "Owned Shell read was not released"
            cancelled_reads.append(cancelled())
            return ApplicationMetadata(
                applications=(replace(application(_ID), name="Shell obsolete"),)
            )
        return native_read(
            ids, registrations=registrations, cancelled=cancelled, exporter=exporter
        )

    def subscribe(callback, *, folder_id, title):
        monitor = SimpleNamespace(
            closed=False,
            reconnects=0,
        )

        def reconnect():
            assert not monitor.closed
            monitor.reconnects += 1
            callback()

        def close():
            monitor.closed = True

        monitor.reconnect, monitor.close = reconnect, close
        subscriptions.append(monitor)
        return monitor

    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_args, **_kwargs: object(), raising=False
    )
    monkeypatch.setattr(
        catalog_monitor, "explorer_tray", lambda _user, *, exclude_pid: shell[0]
    )
    monkeypatch.setattr(catalog_monitor, "ShellChangeMonitor", subscribe)
    monkeypatch.setattr(catalog_monitor.GLib, "idle_add", owned.loop.idle)
    monkeypatch.setattr(catalog_monitor.GLib, "timeout_add", owned.loop.timeout)
    monkeypatch.setattr(catalog_monitor.GLib, "source_remove", owned.loop.remove)
    monkeypatch.setattr(windows_catalog.catalog, "read_application_metadata", native)
    owned.registry._change_monitor_factory = owned.backend.create_change_monitor
    owned.registry.subscribe(
        lambda: (
            observed.append(owned.registry.get(_ID).name)
            if owned.registry.get(_ID) is not None
            else None
        )
    )
    try:
        owned.registry.start()
        owned.ready("first")
        owned.changed("obsolete")
        owned.loop.until(held.is_set)
        held_generation = owned.registry._metadata._generation
        owned.mode = "current"
        shell[0] = (202, 22)
        # No TaskbarCreated or folder notification is delivered. The real
        # private GLib timer must invalidate the read still held on the worker.
        owned.loop.until(lambda: owned.registry._metadata._generation > held_generation)
        owned.loop.until(lambda: owned.registry._metadata._pending is not None)
        assert not release.is_set()
        assert owned.registry.get(_ID).name == "Shell first"
        assert [monitor.reconnects for monitor in subscriptions] == [1, 1, 1]
        release.set()
        owned.ready("current")
        assert cancelled_reads == [True]
        assert "Shell obsolete" not in observed
        assert observed[-1] == "Shell current"
        assert owned.registry._gio_handle_for(_ID) is owned.handles["current"]
    finally:
        release.set()
        owned.close()
    assert all(monitor.closed for monitor in subscriptions)


@pytest.fixture
def reconciled_worker(worker, monkeypatch):
    import ctypes

    from docking.platform.backends.windows import catalog_monitor

    owned = worker()
    now, shell, callbacks, monitors = [0.0], [(101, 11)], [], []
    monitor_type = catalog_monitor.CatalogChangeMonitor

    def subscribe(callback, *, folder_id, title):
        callbacks.append(callback)
        return SimpleNamespace(close=lambda: None, reconnect=callback)

    def monitor(changed):
        result = monitor_type(changed, clock=lambda: now[0])
        monitors.append(result)
        return result

    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_args, **_kwargs: object(), raising=False
    )
    monkeypatch.setattr(
        catalog_monitor, "explorer_tray", lambda _user, *, exclude_pid: shell[0]
    )
    monkeypatch.setattr(catalog_monitor, "ShellChangeMonitor", subscribe)
    monkeypatch.setattr(catalog_monitor.GLib, "idle_add", owned.loop.idle)
    monkeypatch.setattr(catalog_monitor.GLib, "timeout_add", owned.loop.timeout)
    monkeypatch.setattr(catalog_monitor.GLib, "source_remove", owned.loop.remove)
    monkeypatch.setattr(windows_catalog, "CatalogChangeMonitor", monitor)
    owned.registry._change_monitor_factory = owned.backend.create_change_monitor
    return SimpleNamespace(
        owned=owned, now=now, shell=shell, callbacks=callbacks, monitors=monitors
    )


@pytest.mark.parametrize("first_result", ["unchanged", "unavailable", "error"])
def test_native_event_retries_completed_stale_or_failed_read_without_another_notification(
    reconciled_worker, monkeypatch, first_result
):
    signals = reconciled_worker
    owned = signals.owned
    changed, reads = Event(), []
    original_read = windows_catalog.catalog.read_application_metadata

    def native(ids, *, registrations, cancelled, exporter):
        if not changed.is_set():
            return original_read(
                ids, registrations=registrations, cancelled=cancelled, exporter=exporter
            )
        assert get_ident() != owned.ui_thread and not cancelled()
        reads.append(len(reads) + 1)
        if len(reads) == 1:
            if first_result == "error":
                raise OSError("Owned native catalog temporarily unavailable")
            if first_result == "unavailable":
                return ApplicationMetadata()
            return ApplicationMetadata(
                applications=(replace(application(_ID), name="Shell first"),)
            )
        return ApplicationMetadata(
            applications=(replace(application(_ID), name="Shell current"),)
        )

    monkeypatch.setattr(windows_catalog.catalog, "read_application_metadata", native)
    owned.registry.start()
    owned.ready("first")
    monitor = signals.monitors[0]
    changed.set()
    signals.callbacks[0]()  # The only native event, before namespace convergence.
    owned.loop.until(lambda: reads == [1] and not monitor._reads)
    owned.loop.pump()
    assert owned.registry.get(_ID).name == "Shell first"
    signals.now[0] = 0.9
    monitor._check_shell()
    owned.loop.pump()
    assert reads == [1]
    signals.now[0] = 1.0
    monitor._check_shell()
    owned.ready("current")
    assert reads == [1, 2]
    assert owned.registry._gio_handle_for(_ID) is owned.handles["first"]


def test_reconciliation_waits_for_slow_full_resolver_without_cancelling_its_generation(
    reconciled_worker, monkeypatch
):
    signals = reconciled_worker
    owned = signals.owned
    changed, held, release = Event(), Event(), Event()
    reads, cancellations = [], []
    original_read = windows_catalog.catalog.read_application_metadata

    def native(ids, *, registrations, cancelled, exporter):
        if not changed.is_set():
            return original_read(
                ids, registrations=registrations, cancelled=cancelled, exporter=exporter
            )
        reads.append(get_ident())
        if len(reads) == 1:
            held.set()
            assert release.wait(5), "Owned settling read was not released"
            cancellations.append(cancelled())
            return ApplicationMetadata(
                applications=(replace(application(_ID), name="Shell first"),)
            )
        return ApplicationMetadata(
            applications=(replace(application(_ID), name="Shell current"),)
        )

    monkeypatch.setattr(windows_catalog.catalog, "read_application_metadata", native)
    try:
        owned.registry.start()
        owned.ready("first")
        monitor = signals.monitors[0]
        changed.set()
        signals.callbacks[0]()
        owned.loop.until(held.is_set)
        generation = owned.registry._metadata._generation
        for instant in (1.0, 2.0, 5.0):
            signals.now[0] = instant
            assert monitor._check_shell() is True
            owned.loop.pump()
        assert owned.registry._metadata._generation == generation
        assert len(reads) == 1 and not release.is_set()
        release.set()
        owned.loop.until(lambda: not monitor._reads)
        owned.loop.pump()
        signals.now[0] = 5.9
        monitor._check_shell()
        assert len(reads) == 1
        signals.now[0] = 6.0
        monitor._check_shell()
        owned.ready("current")
        assert len(reads) == 2 and cancellations == [False]
        assert set(reads) == {owned.thread.ident}
    finally:
        release.set()


def test_resolver_completion_uses_captured_monitor_after_stop_and_restart(
    reconciled_worker, monkeypatch
):
    signals = reconciled_worker
    owned = signals.owned
    held, release = Event(), Event()
    old_worker = None
    original_read = windows_catalog.catalog.read_application_metadata

    def native(ids, *, registrations, cancelled, exporter):
        if owned.selected.version == "obsolete":
            held.set()
            assert release.wait(5), "Owned stopped resolver was not released"
            assert cancelled()
            return ApplicationMetadata()
        return original_read(
            ids, registrations=registrations, cancelled=cancelled, exporter=exporter
        )

    monkeypatch.setattr(windows_catalog.catalog, "read_application_metadata", native)
    try:
        owned.registry.start()
        owned.ready("first")
        owned.changed("obsolete")
        owned.loop.until(held.is_set)
        old_worker = owned.thread
        old_monitor = signals.monitors[0]
        owned.registry.stop()
        owned.mode = "current"
        owned.registry.start()
        owned.ready("current")
        successor = signals.monitors[1]
        assert not successor._reads
        signals.now[0] = 1.0
        release.set()
        old_worker.join(2)
        assert not old_worker.is_alive()
        owned.loop.pump()
        generation = owned.registry._metadata._generation
        signals.now[0] = 2.0
        assert old_monitor._check_shell() is False
        successor._check_shell()
        owned.loop.pump()
        assert owned.registry._metadata._generation == generation
        assert owned.registry.get(_ID).name == "Shell current"
    finally:
        release.set()
        if old_worker is not None:
            old_worker.join(2)
            assert not old_worker.is_alive()


def test_resolver_reserves_and_finishes_activity_even_when_already_cancelled(
    monkeypatch,
):
    events = []

    def started():
        events.append("started")
        return 17

    def finished(ticket):
        events.append(("finished", ticket))

    def cancelled():
        events.append("cancelled")
        return True

    monitor = SimpleNamespace(read_started=started, read_finished=finished)
    monkeypatch.setattr(
        windows_catalog, "CatalogChangeMonitor", lambda _changed: monitor
    )
    backend = windows_catalog.WindowsApplicationCatalogBackend()
    backend.create_change_monitor(lambda: None)
    resolve = backend.metadata_resolver_factory(
        _Registrations(), read_default_source=True
    )()
    assert resolve((_ID,), cancelled) == ApplicationMetadata()
    assert events == ["started", "cancelled", ("finished", 17)]


@pytest.mark.parametrize("already_published", (False, True))
def test_stop_during_gio_read_cancels_native_work_and_late_handle_delivery(
    worker, already_published
):
    version = "current" if already_published else "first"
    owned = worker(blocked=(version,))
    owned.registry.start()
    if already_published:
        owned.ready("first")
        owned.changed("current")
    owned.loop.until(owned.entered[version].is_set)
    before = owned.registry.snapshot(), owned.registry._gio_handle_for(_ID)
    native_before = list(owned.native_versions)
    owned.registry.stop()
    owned.release[version].set()
    owned.thread.join(2)
    assert not owned.thread.is_alive()
    owned.loop.pump()
    assert owned.native_versions == native_before
    assert (owned.registry.snapshot(), owned.registry._gio_handle_for(_ID)) == before
    assert not owned.registry._metadata._sources


def test_unavailable_gio_retains_accepted_handle_while_native_catalog_continues(worker):
    owned = worker()
    owned.registry.start()
    owned.ready("first")
    owned.changed("gio-unavailable")
    owned.ready("gio-unavailable")
    assert owned.registry._gio_handle_for(_ID) is owned.handles["first"]


def test_successful_empty_worker_catalog_completes_initial_discovery(worker):
    owned = worker()
    owned.mode = "empty"
    assert owned.registry.initial_discovery_pending
    owned.registry.start()
    owned.loop.until(lambda: not owned.registry.initial_discovery_pending)
    assert owned.registry.snapshot() == ()
    assert owned.registry._gio_handle_for(_ID) is None
    assert owned.reader_threads and owned.ui_thread not in owned.reader_threads


def test_explicit_source_preserves_its_handle_and_never_enumerates_default_gio(worker):
    custom = _Handle("custom")
    owned = worker(custom=custom)
    owned.registry.start()
    owned.ready("first")
    assert not owned.reader_threads
    assert owned.registry._gio_handle_for(_ID) is custom


def test_main_context_monitor_initializes_before_gio_worker_on_start_and_restart(
    worker,
):
    owned = worker()
    owned.registry.start()
    owned.ready("first")
    owned.registry.stop()
    owned.thread.join(2)
    assert not owned.thread.is_alive()
    first_lifecycle = list(owned.lifecycle)
    assert first_lifecycle[0] == ("monitor", owned.ui_thread)
    assert all(
        kind == "gio" and thread != owned.ui_thread
        for kind, thread in first_lifecycle[1:]
    )
    owned.mode = "current"
    owned.registry.start()
    owned.ready("current")
    second_lifecycle = owned.lifecycle[len(first_lifecycle) :]
    assert second_lifecycle[0] == ("monitor", owned.ui_thread)
    assert all(
        kind == "gio" and thread != owned.ui_thread
        for kind, thread in second_lifecycle[1:]
    )


def test_association_getters_use_existing_worker_keep_gtk_alive_and_publish_retained_handles(
    monkeypatch,
):
    from docking.platform.applications.associations import AssociationStatus
    from docking.platform.applications.windows import associations

    worker = _Worker(monkeypatch)
    entered, release = Event(), Event()
    handler = GioApplicationFake(
        "Owned.Association", name="Owned Handler", commandline="owned-handler"
    )
    threads, publications, ticks = [], [], []

    def default(_kind, _uri):
        threads.append(get_ident())
        assert get_ident() != worker.ui_thread, "Association queried on GTK"
        entered.set()
        assert release.wait(3)
        return handler

    monkeypatch.setattr(associations.Gio.AppInfo, "get_default_for_type", default)
    monkeypatch.setattr(
        associations.Gio.AppInfo, "get_recommended_for_type", lambda _type: []
    )
    monkeypatch.setattr(associations.Gio.AppInfo, "get_all_for_type", lambda _type: [])
    worker.registry.start()
    try:
        worker.ready("first")
        selected = worker.registry.preferred_listing_for_content_types((".owned",))
        assert selected.status is AssociationStatus.PENDING
        assert entered.wait(1)
        worker.loop.timeout(5, lambda: ticks.append(True) or True)
        started = monotonic()
        for _index in range(5):
            worker.registry.refresh()
            assert (
                worker.registry.preferred_listing_for_content_types((".owned",)).status
                is AssociationStatus.PENDING
            )
        assert monotonic() - started < 0.2
        worker.loop.pump()
        assert len(ticks) > 2

        def published():
            selected = worker.registry.preferred_listing_for_content_types((".owned",))
            if selected.status is AssociationStatus.READY:
                publications.append((get_ident(), selected.listing))

        unsubscribe = worker.registry.subscribe(published)
        release.set()
        worker.loop.until(lambda: bool(publications))
        assert all(thread != worker.ui_thread for thread in threads)
        assert publications[0][0] == worker.ui_thread
        listing = publications[0][1]
        assert listing.name == "Owned Handler" and listing.desktop_id is None
        assert (
            worker.registry._gio_handle_for_unidentified(listing.listing_key) is handler
        )
        default_result = worker.registry.default_for_content_type(".owned")
        assert default_result.status is AssociationStatus.READY
        assert default_result.listing.desktop_id is None
        assert (
            worker.registry._gio_handle_for_unidentified(
                default_result.listing.listing_key
            )
            is handler
        )
        unsubscribe()
        worker.registry._metadata.invalidate()
        worker.registry.refresh()
        assert worker.registry._gio_handle_for_unidentified(listing.listing_key) is None
        assert (
            worker.registry.preferred_listing_for_content_types((".owned",)).status
            is AssociationStatus.PENDING
        )
    finally:
        release.set()
        worker.close()


@pytest.mark.parametrize("userdata", [(), (None,)])
def test_private_loop_accepts_both_gi_source_callback_conventions(userdata):
    from unittest.mock import Mock

    loop, source, callback = _Loop(), Mock(), Mock(return_value=False)
    source.attach.return_value = 37
    try:
        assert loop.attach(source, callback, ("owned", 17)) == 37
        dispatch = source.set_callback.call_args.args[0]
        assert dispatch(*userdata) is False
        callback.assert_called_once_with("owned", 17)
        source.attach.assert_called_once_with(loop.context)
    finally:
        loop.close()
    source.destroy.assert_called_once_with()
