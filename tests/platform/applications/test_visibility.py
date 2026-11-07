"""Visibility IO is coalesced, and only current main-loop results can publish."""

import threading
import time

from docking.platform.applications.metadata import (
    ApplicationMetadata,
    ApplicationMetadataLookup,
)


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
    return ApplicationMetadataLookup(
        resolve_factory=lambda: (
            lambda ids, _cancelled, *, association_keys=(): ApplicationMetadata(
                hidden_ids=resolve(ids)
            )
        ),
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


def test_catalog_io_cancel_callback_belongs_to_its_original_lifetime():
    idle, entered, release, finished = (
        _Idle(),
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    observations = []

    def resolve(ids, cancelled, *, association_keys=()):
        if ids == ("old",):
            entered.set()
            assert release.wait(2)
            observations.append(cancelled())
            finished.set()
        return ApplicationMetadata(hidden_ids=frozenset(ids))

    lookup = ApplicationMetadataLookup(
        resolve_factory=lambda: resolve,
        on_changed=lambda: None,
        schedule_idle=idle.add,
        remove_source=idle.remove,
    )
    try:
        lookup.start()
        lookup.request(("old",))
        assert entered.wait(1)
        lookup.stop()
        lookup.start()
        lookup.request(("new",))
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        release.set()
        assert finished.wait(1)
        assert observations == [True]
        assert lookup.hidden_ids == {"new"}
    finally:
        release.set()
        lookup.stop()


def test_failed_catalog_read_preserves_the_last_complete_native_listing():
    from tests.platform.application_fakes import application

    idle, calls = _Idle(), []
    apps = (application("Native"),)

    def resolve(_ids):
        calls.append(True)
        return ApplicationMetadata(applications=apps if len(calls) == 1 else None)

    lookup = ApplicationMetadataLookup(
        resolve_factory=lambda: (
            lambda ids, _cancelled, *, association_keys=(): resolve(ids)
        ),
        on_changed=lambda: None,
        schedule_idle=idle.add,
        remove_source=idle.remove,
    )
    try:
        lookup.start()
        lookup.request(())
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        lookup.invalidate()
        lookup.request(())
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        assert lookup.applications == apps
    finally:
        lookup.stop()


def test_periodic_worker_refresh_discovers_changes_without_gio_notifications():
    from tests.platform.application_fakes import application

    idle, called = _Idle(), threading.Event()
    apps = (application("Added"),)
    lookup = ApplicationMetadataLookup(
        resolve_factory=lambda: (
            lambda _ids, _cancelled, *, association_keys=(): (
                called.set() or ApplicationMetadata(applications=apps)
            )
        ),
        on_changed=lambda: None,
        schedule_idle=idle.add,
        remove_source=idle.remove,
        refresh_interval=0.02,
    )
    try:
        lookup.start()
        lookup.request(())
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        called.clear()
        assert called.wait(1)
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        assert lookup.applications == apps
    finally:
        lookup.stop()


def test_factory_reads_selected_registrations_and_prepares_exports_on_the_worker(
    monkeypatch, tmp_path
):
    from dataclasses import replace
    from types import SimpleNamespace

    from docking.platform.applications.implementations.windows import (
        native_catalog as catalog,
    )
    from docking.platform.applications.implementations.windows.catalog import (
        WindowsApplicationCatalogBackend,
    )
    from tests.platform.application_fakes import application

    main = threading.get_ident()
    idle, calls = _Idle(), []
    selected = application("Native.App")
    exporters = []

    def registrations():
        assert threading.get_ident() != main
        return (selected,)

    def prepare(ids, *, registrations, cancelled, exporter):
        assert threading.get_ident() != main
        assert not cancelled()
        assert exporter is not None
        exporters.append(exporter)
        calls.append((ids, registrations))
        return ApplicationMetadata(applications=registrations)

    monkeypatch.setattr(catalog, "read_application_metadata", prepare)
    lookup = ApplicationMetadataLookup(
        on_changed=lambda: None,
        schedule_idle=idle.add,
        remove_source=idle.remove,
        resolve_factory=WindowsApplicationCatalogBackend(
            SimpleNamespace(applications=registrations)
        ).metadata_resolver_factory(
            SimpleNamespace(applications=registrations), read_default_source=False
        ),
    )
    assert lookup is not None
    try:
        lookup.start()
        lookup.request(("Native.App",))
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        assert lookup.applications == (selected,)
        selected = replace(selected, export_file=tmp_path / "Selected.lnk")
        lookup.invalidate()
        lookup.request(("Native.App",))
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        assert lookup.applications == (selected,)
        assert len(calls) == 2
        assert exporters[0] is exporters[1]
        lookup.stop()
        lookup.start()
        lookup.request(("Native.App",))
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        assert exporters[2] is not exporters[0]
        assert calls[1] == (("Native.App",), (selected,))
    finally:
        lookup.stop()


def test_default_source_visibility_includes_new_worker_ids_and_retains_launch_handles(
    monkeypatch,
):
    from types import SimpleNamespace

    from docking.platform.applications.implementations.windows import (
        catalog as windows_catalog,
    )
    from tests.platform.application_fakes import GioApplicationFake

    main = threading.get_ident()
    idle, calls = _Idle(), []

    class Handle(GioApplicationFake):
        def get_id(self):
            assert threading.get_ident() != main
            return super().get_id()

    handle = Handle("New.Hidden.App", name="Owned")

    def source():
        assert threading.get_ident() != main
        return (handle,)

    def native(ids, *, registrations, cancelled, exporter):
        assert threading.get_ident() != main
        assert not cancelled() and registrations == () and exporter is not None
        calls.append(ids)
        return ApplicationMetadata(
            applications=(), hidden_ids=frozenset(("New.Hidden.App", "Unrelated"))
        )

    monkeypatch.setattr(
        windows_catalog, "Gio", SimpleNamespace(AppInfo=SimpleNamespace(get_all=source))
    )
    monkeypatch.setattr(windows_catalog.catalog, "read_application_metadata", native)
    lookup = ApplicationMetadataLookup(
        resolve_factory=windows_catalog.WindowsApplicationCatalogBackend(
            SimpleNamespace(applications=lambda: ())
        ).metadata_resolver_factory(
            SimpleNamespace(applications=lambda: ()), read_default_source=True
        ),
        on_changed=lambda: None,
        schedule_idle=idle.add,
        remove_source=idle.remove,
    )
    try:
        lookup.start()
        lookup.request(())
        _wait(lambda: bool(idle.callbacks))
        assert lookup.source_discovery is None
        idle.flush()
        assert calls == [("New.Hidden.App",)]
        assert lookup.hidden_ids == {"New.Hidden.App"}
        assert lookup.source_discovery.handles["New.Hidden.App"] is handle
    finally:
        lookup.stop()


def test_association_queries_publish_handles_atomically_on_idle_and_filter_known_ids():
    from docking.platform.applications.associations import (
        AssociationKey,
        AssociationRead,
        AssociationStatus,
    )

    idle, changes = _Idle(), []
    key, handle = AssociationKey("default", "owned/type"), object()

    def resolve(_ids, _cancelled, *, association_keys=()):
        assert association_keys == (key,)
        return ApplicationMetadata(
            associations=((key, AssociationRead(AssociationStatus.READY, (handle,))),),
            association_ids=frozenset(("Owned.Association",)),
            hidden_ids=frozenset(("Owned.Association", "Unrelated")),
        )

    lookup = ApplicationMetadataLookup(
        resolve_factory=lambda: resolve,
        on_changed=lambda: changes.append(threading.get_ident()),
        schedule_idle=idle.add,
        remove_source=idle.remove,
    )
    assert (
        lookup.association_reads((key,))[0][1].status is AssociationStatus.UNAVAILABLE
    )
    lookup.start()
    try:
        lookup.request(())
        _wait(lambda: bool(idle.callbacks))
        assert (
            lookup.association_reads((key,))[0][1].status is AssociationStatus.PENDING
        )
        idle.flush()
        result = lookup.association_reads((key,))[0][1]
        assert result.status is AssociationStatus.READY and result.handles == (handle,)
        assert changes == [threading.get_ident()]
        assert lookup.hidden_ids == {"Owned.Association"}
    finally:
        lookup.stop()
    assert (
        lookup.association_reads((key,))[0][1].status is AssociationStatus.UNAVAILABLE
    )


def test_association_keys_coalesce_and_older_inflight_answers_cannot_publish():
    from docking.platform.applications.associations import (
        AssociationKey,
        AssociationRead,
        AssociationStatus,
    )

    idle, entered, release = _Idle(), threading.Event(), threading.Event()
    one, two = (
        AssociationKey("default", "owned/one"),
        AssociationKey("all", "owned/two"),
    )
    calls, obsolete, current = [], object(), object()

    def resolve(_ids, _cancelled, *, association_keys=()):
        calls.append(association_keys)
        if association_keys == (one,):
            entered.set()
            assert release.wait(2)
        return ApplicationMetadata(
            associations=tuple(
                (
                    key,
                    AssociationRead(
                        AssociationStatus.READY,
                        (obsolete if len(association_keys) == 1 else current,),
                    ),
                )
                for key in association_keys
            )
        )

    lookup = ApplicationMetadataLookup(
        resolve_factory=lambda: resolve,
        on_changed=lambda: None,
        schedule_idle=idle.add,
        remove_source=idle.remove,
    )
    lookup.start()
    try:
        lookup.association_reads((one,))
        assert entered.wait(1)
        generation = lookup._generation
        lookup.association_reads((one, one))
        assert lookup._generation == generation
        lookup.association_reads((two, one, two))
        release.set()
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        assert calls == [(one,), (one, two)]
        assert all(
            read.handles == (current,)
            for _key, read in lookup.association_reads((one, two))
        )
    finally:
        release.set()
        lookup.stop()


def test_invalidated_association_failure_is_unavailable_but_retains_last_catalog():
    from docking.platform.applications.associations import (
        AssociationKey,
        AssociationRead,
        AssociationStatus,
    )
    from tests.platform.application_fakes import application

    idle, key, calls = _Idle(), AssociationKey("default", "owned/type"), []
    apps = (application("Owned"),)

    def resolve(_ids, _cancelled, *, association_keys=()):
        calls.append(True)
        if len(calls) > 1:
            raise OSError(5, "Owned failure")
        return ApplicationMetadata(
            applications=apps,
            associations=((key, AssociationRead(AssociationStatus.READY)),),
        )

    lookup = ApplicationMetadataLookup(
        resolve_factory=lambda: resolve,
        on_changed=lambda: None,
        schedule_idle=idle.add,
        remove_source=idle.remove,
    )
    lookup.start()
    try:
        lookup.association_reads((key,))
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        assert lookup.association_reads((key,))[0][1].status is AssociationStatus.READY
        lookup.invalidate()
        assert (
            lookup.association_reads((key,))[0][1].status is AssociationStatus.PENDING
        )
        lookup.request(())
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        assert (
            lookup.association_reads((key,))[0][1].status
            is AssociationStatus.UNAVAILABLE
        )
        assert lookup.applications == apps
    finally:
        lookup.stop()


def test_association_stop_restart_rejects_old_handle_and_requeries_known_keys():
    from docking.platform.applications.associations import (
        AssociationKey,
        AssociationRead,
        AssociationStatus,
    )

    idle, entered, release = _Idle(), threading.Event(), threading.Event()
    key, old, new, calls = (
        AssociationKey("default", "owned/type"),
        object(),
        object(),
        [],
    )

    def resolve(_ids, cancelled, *, association_keys=()):
        calls.append(True)
        first = len(calls) == 1
        if first:
            entered.set()
            assert release.wait(2)
            assert cancelled()
        return ApplicationMetadata(
            associations=(
                (
                    key,
                    AssociationRead(AssociationStatus.READY, (old if first else new,)),
                ),
            )
        )

    lookup = ApplicationMetadataLookup(
        resolve_factory=lambda: resolve,
        on_changed=lambda: None,
        schedule_idle=idle.add,
        remove_source=idle.remove,
    )
    lookup.start()
    try:
        lookup.association_reads((key,))
        assert entered.wait(1)
        lookup.stop()
        lookup.start()
        lookup.association_reads((key,))
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        release.set()
        assert lookup.association_reads((key,))[0][1].handles == (new,)
    finally:
        release.set()
        lookup.stop()


def test_association_key_budget_is_bounded_and_never_implies_no_handler():
    from docking.platform.applications.associations import (
        AssociationKey,
        AssociationStatus,
    )

    lookup = _lookup(lambda _ids: frozenset(), _Idle(), lambda: None)
    keys = tuple(AssociationKey("default", f"owned/{index}") for index in range(257))
    assert all(
        read.status is AssociationStatus.UNAVAILABLE
        for _key, read in lookup.association_reads(keys)
    )
    assert lookup._association_keys == ()


def test_query_only_lookup_refreshes_periodically_without_an_inventory_request():
    from docking.platform.applications.associations import (
        AssociationKey,
        AssociationRead,
        AssociationStatus,
    )

    idle, key, calls = _Idle(), AssociationKey("default", "owned/type"), []

    def resolve(_ids, _cancelled, *, association_keys=()):
        assert _ids == () and association_keys == (key,)
        calls.append(True)
        return ApplicationMetadata(
            associations=((key, AssociationRead(AssociationStatus.READY)),)
        )

    lookup = ApplicationMetadataLookup(
        resolve_factory=lambda: resolve,
        on_changed=lambda: None,
        schedule_idle=idle.add,
        remove_source=idle.remove,
        refresh_interval=0.02,
    )
    lookup.start()
    try:
        lookup.association_reads((key,))
        _wait(lambda: len(calls) >= 2)
        _wait(lambda: bool(idle.callbacks))
        idle.flush()
        assert lookup.association_reads((key,))[0][1].status is AssociationStatus.READY
    finally:
        lookup.stop()
