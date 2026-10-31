"""Passive catalog evidence preserves failed deadlines and native results."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.windows.visual import catalog_changes_smoke as mod


def application(name="Owned fixture", identifier="Owned.Id"):
    return SimpleNamespace(name=name, desktop_id=identifier)


def timeline():
    return mod._CatalogTrace(lambda: {"metadata_generation": 3}, ["Owned fixture"])


@pytest.mark.parametrize("available", [None, (), (application(),)])
def test_worker_trace_returns_exact_metadata_and_records_request_generations(available):
    generation = {"metadata_generation": 2, "metadata_epoch": 1}
    trace = mod._CatalogTrace(lambda: dict(generation), ["Owned fixture"])
    result = SimpleNamespace(applications=available)

    def read():
        generation["metadata_generation"] = 3
        return result

    assert trace.read("metadata", read) is result
    trace.publication(1, 2, result)
    observed = trace.snapshot()
    row = observed["reads"][0]
    assert row["before"]["metadata_generation"] == 2
    assert row["after"]["metadata_generation"] == 3
    assert row["available"] is (available is not None) and row["finished"]
    assert row["elapsed_ms"] >= 0
    publication = observed["publications"][0]
    assert publication["requested_generation"] == 2
    assert publication["current"]["metadata_generation"] == 3
    assert publication["available"] is (available is not None)


@pytest.mark.parametrize("code", [None, 5, -2147023174, 0x800706BA])
def test_worker_trace_preserves_exact_native_error_without_private_messages(code):
    trace = mod._CatalogTrace(lambda: {"metadata_generation": 8}, ["Owned fixture"])
    original = OSError(87, "private document/path")
    original.winerror = code
    with pytest.raises(OSError) as caught:
        trace.read("shell", Mock(side_effect=original))
    assert caught.value is original
    observed = trace.snapshot()["reads"][0]
    assert observed["error"]["code"] == (87 if code is None else code)
    assert observed["finished"] and "private" not in str(observed)
    if code in (-2147023174, 0x800706BA):
        assert observed["error"]["hresult"] == "0x800706ba"


def test_trace_retains_only_owned_labels_and_detaches_bounded_snapshots():
    trace = mod._CatalogTrace(lambda: {"generation": counter[0]}, ["Owned fixture"])
    counter = [0]
    apps = (application(), application("Private unrelated application", "private"))
    for index in range(100):
        counter[0] = index
        assert trace.read("shell", lambda: apps) is apps
        trace.publish(apps)
    observed = trace.snapshot()
    assert len(observed["reads"]) == len(observed["published"]) == 64
    assert observed["reads_dropped"] == observed["published_dropped"] == 36
    assert [row["before"]["generation"] for row in observed["reads"][:6]] == list(
        range(6)
    )
    assert observed["reads"][-1]["after"]["generation"] == 99
    assert "Private" not in str(observed) and "private" not in str(observed)
    observed["reads"][0]["finished"] = False
    assert trace.snapshot()["reads"][0]["finished"]


@pytest.mark.parametrize("diagnostic_error", [False, True])
def test_fresh_shell_oracle_runs_only_after_irrevocable_failed_wait(diagnostic_error):
    events, observed = [], {}
    original = TimeoutError("Pending catalog did not recover")

    def wait(predicate, _message):
        assert not predicate()
        events.append("failed-original-20-second-wait")
        raise original

    def oracle():
        assert events == ["failed-original-20-second-wait"]
        events.append("independent-shell-read")
        if diagnostic_error:
            raise OSError(5, "private path")
        return (application(),)

    with pytest.raises(TimeoutError) as caught:
        mod._wait_recovery(
            wait,
            lambda: False,
            "pending publication",
            oracle=oracle,
            observe=lambda: {"generation": 3},
            observed=observed,
            labels=["Owned fixture"],
            trace=timeline(),
        )
    assert caught.value is original
    assert events == ["failed-original-20-second-wait", "independent-shell-read"]
    assert observed["published_at_failure"] == {"generation": 3}
    if diagnostic_error:
        assert observed["independent_shell_error"]["code"] == 5
    else:
        assert observed["independent_shell_after_failure"] == [
            {"id": "Owned.Id", "name": "Owned fixture"}
        ]


def test_successful_publication_does_not_prime_an_independent_shell_read():
    oracle = Mock()
    mod._wait_recovery(
        lambda predicate, _message: predicate(),
        lambda: True,
        "ready",
        oracle=oracle,
        observe=Mock(),
        observed={},
        labels=["Owned"],
        trace=timeline(),
    )
    oracle.assert_not_called()


def test_failed_diagnostic_observation_cannot_replace_original_timeout():
    original = TimeoutError("Original pending publication failure")
    with pytest.raises(TimeoutError) as caught:
        mod._wait_recovery(
            Mock(side_effect=original),
            lambda: False,
            "pending",
            oracle=lambda: (),
            observe=Mock(side_effect=RuntimeError()),
            observed={},
            labels=["Owned"],
            trace=timeline(),
        )
    assert caught.value is original


def test_passive_registry_timing_preserves_calls_lazy_source_and_restores_contract():
    calls = []
    source = (value for value in (application(),))
    metadata = SimpleNamespace(
        invalidate=lambda: calls.append("invalidate") or "invalidated"
    )
    registry = SimpleNamespace(
        _metadata=metadata,
        _application_source=lambda: calls.append("source") or source,
        refresh=lambda: calls.append("refresh") or "refreshed",
    )
    discovery = object()
    gio_catalog = SimpleNamespace(
        _read_gio_discovery=lambda: calls.append("worker-source") or discovery
    )
    worker_source = gio_catalog._read_gio_discovery
    originals = registry._application_source, registry.refresh, metadata.invalidate
    trace = timeline()
    wrappers = mod._RegistryTimeline(registry, gio_catalog, trace)
    assert not calls  # Constructing diagnostics must not read or prime Gio.
    try:
        assert registry.refresh() == "refreshed"
        assert registry._application_source() is source
        assert registry._metadata.invalidate() == "invalidated"
        assert gio_catalog._read_gio_discovery() is discovery
        assert calls == ["refresh", "source", "invalidate", "worker-source"]
        assert next(source).name == "Owned fixture"  # No diagnostic iteration.
    finally:
        wrappers.close()
    assert (
        registry._application_source,
        registry.refresh,
        metadata.invalidate,
    ) == originals
    assert gio_catalog._read_gio_discovery is worker_source
    operations = trace.snapshot()["operations"]
    assert [row["kind"] for row in operations] == [
        "gtk-refresh",
        "registry-source",
        "metadata-invalidate",
        "worker-gio-discovery",
    ]
    assert all(row["finished"] and row["elapsed_ms"] >= 0 for row in operations)
    assert all(
        row["thread"] and row["finished_ns"] >= row["started_ns"] for row in operations
    )


def test_registry_timing_preserves_original_error_and_restores_after_cancellation():
    original = KeyboardInterrupt()
    registry = SimpleNamespace(
        _application_source=Mock(),
        refresh=Mock(side_effect=original),
        _metadata=SimpleNamespace(invalidate=Mock()),
    )
    refresh = registry.refresh
    gio_catalog = SimpleNamespace(_read_gio_discovery=Mock())
    worker_source = gio_catalog._read_gio_discovery
    trace = timeline()
    wrappers = mod._RegistryTimeline(registry, gio_catalog, trace)
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            registry.refresh()
        assert caught.value is original
    finally:
        wrappers.close()
    assert registry.refresh is refresh
    assert gio_catalog._read_gio_discovery is worker_source
    refresh.assert_called_once_with()
    observed = trace.snapshot()["operations"][0]
    assert observed["finished"] and observed["error"] == {"class": "KeyboardInterrupt"}


def test_catalog_timeline_separates_worker_completion_publication_and_observer_delivery(
    monkeypatch,
):
    clock = iter(
        (10_000_000, 12_000_000, 22_000_000, 25_000_000, 50_000_000, 75_000_000)
    )
    monkeypatch.setattr(mod.time, "monotonic_ns", lambda: next(clock))
    trace = timeline()
    result = SimpleNamespace(applications=(application(),))
    assert trace.read("metadata", lambda: result) is result
    trace.publication(1, 3, result)
    trace.publish(result.applications)
    trace.publish(result.applications)
    trace.mark("gate-release")
    snapshot = trace.snapshot()
    assert snapshot["reads"][0]["started_ns"] == 10_000_000
    assert snapshot["reads"][0]["finished_ns"] == 12_000_000
    assert snapshot["publications"][0]["monotonic_ns"] == 22_000_000
    assert snapshot["published"][0]["monotonic_ns"] == 25_000_000
    assert len(snapshot["published"]) == 1  # Timestamps do not defeat deduplication.
    assert snapshot["observer"] == {
        "calls": 2,
        "first_ns": 25_000_000,
        "last_ns": 50_000_000,
        "max_gap_ms": 25.0,
    }
    assert snapshot["milestones"][0]["monotonic_ns"] == 75_000_000


def test_timeline_operations_and_milestones_keep_first_and_last_bounded_evidence():
    trace = timeline()
    for index in range(140):
        assert trace.operation(str(index), lambda value=index: value) == index
        trace.mark(str(index))
    observed = trace.snapshot()
    assert len(observed["operations"]) == 128
    assert len(observed["milestones"]) == 64
    assert observed["operations_dropped"] == 12
    assert observed["milestones_dropped"] == 76
    for key in ("operations", "milestones"):
        assert [row["kind"] for row in observed[key][:6]] == list(map(str, range(6)))
        assert observed[key][-1]["kind"] == "139"
    observed["operations"][0]["finished"] = False
    assert trace.snapshot()["operations"][0]["finished"]


def test_recovery_timeline_records_failed_outcome_before_original_oracle_once():
    trace, observed = timeline(), {}
    original = TimeoutError("original wait")

    def oracle():
        snapshot = trace.snapshot()
        assert [row["kind"] for row in snapshot["milestones"]] == [
            "recovery-wait-start",
            "recovery-wait-failed",
        ]
        assert snapshot["operations"][-1]["kind"] == "failed-outcome-oracle"
        return (application(),)

    with pytest.raises(TimeoutError) as caught:
        mod._wait_recovery(
            Mock(side_effect=original),
            lambda: False,
            "pending publication",
            oracle=oracle,
            observe=trace.snapshot,
            observed=observed,
            labels=["Owned fixture"],
            trace=trace,
        )
    assert caught.value is original
    assert not observed["published_at_failure"]["operations"]
    assert [row["kind"] for row in trace.snapshot()["operations"]] == [
        "failed-outcome-oracle"
    ]
