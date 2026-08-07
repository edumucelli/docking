"""Tests for instance-owned process identity and launch provenance."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from docking.platform.applications.identity import (
    APP_URI_PREFIX,
    LaunchProvenanceStore,
    ProcessIdentityService,
    parse_application_uri,
)


@pytest.mark.parametrize(
    ("app_uri", "expected"),
    [
        ("application://firefox.desktop", "firefox.desktop"),
        ("application://  firefox.desktop  ", "firefox.desktop"),
        ("firefox.desktop", None),
        (" application://firefox.desktop", None),
        ("application://", None),
        ("application://nested/path.desktop", None),
        ("application://firefox", None),
        ("application://firefox.Desktop", None),
    ],
)
def test_application_uri_parser_preserves_unity_validation(app_uri, expected):
    assert APP_URI_PREFIX == "application://"
    assert parse_application_uri(app_uri) == expected


@pytest.mark.parametrize("pid", [None, 0, -1, True, False, 1.5, "1"])
def test_process_identity_rejects_non_positive_exact_int_pids(pid):
    resolver_calls: list[object] = []
    service = ProcessIdentityService(
        LaunchProvenanceStore(),
        executable_resolver=lambda value: resolver_calls.append(value) or None,
    )

    assert service.identity_for_pid(pid) is None
    assert resolver_calls == []


def test_service_uses_injected_executable_resolver_and_shared_store():
    executable = Path("/opt/example/bin/tool")
    store = LaunchProvenanceStore()
    store.record_launch(
        process=SimpleNamespace(pid=42, poll=lambda: None),
        desktop_id="tool.desktop",
        executable_path=executable,
    )
    service = ProcessIdentityService(
        store,
        executable_resolver=lambda pid: executable if pid == 42 else None,
    )

    identity = service.identity_for_pid(42)

    assert identity is not None
    assert identity.executable_path == executable
    assert identity.launch is not None
    assert identity.launch.desktop_id == "tool.desktop"
    assert service.provenance_store is store


def test_service_preserves_an_explicit_empty_store_instance():
    store = LaunchProvenanceStore()

    service = ProcessIdentityService(store)

    assert service.provenance_store is store


def test_store_is_bounded_and_live_reads_refresh_order():
    store = LaunchProvenanceStore(max_records=2)
    for pid in (1, 2):
        store.record_launch(
            process=SimpleNamespace(pid=pid, poll=lambda: None),
            desktop_id=f"{pid}.desktop",
            executable_path=None,
        )

    assert store.provenance_for_pid(1) is not None
    store.record_launch(
        process=SimpleNamespace(pid=3, poll=lambda: None),
        desktop_id="3.desktop",
        executable_path=None,
    )

    assert store.provenance_for_pid(1) is not None
    assert store.provenance_for_pid(2) is None
    assert store.provenance_for_pid(3) is not None
    assert len(store) == 2


def test_finished_pruning_preserves_legacy_poll_semantics():
    statuses = {
        1: 0,
        2: False,
        3: None,
    }
    store = LaunchProvenanceStore()
    for pid in statuses:
        store.record_launch(
            process=SimpleNamespace(
                pid=pid,
                poll=lambda pid=pid: statuses[pid],
            ),
            desktop_id=f"{pid}.desktop",
            executable_path=None,
        )
    store.record_launch(
        process=SimpleNamespace(
            pid=4,
            poll=lambda: (_ for _ in ()).throw(ValueError("not ready")),
        ),
        desktop_id="4.desktop",
        executable_path=None,
    )

    store.prune_finished()

    assert store.provenance_for_pid(1) is None
    assert store.provenance_for_pid(2) is not None
    assert store.provenance_for_pid(3) is not None
    assert store.provenance_for_pid(4) is not None


def test_concurrent_record_read_prune_and_clear_remain_consistent():
    store = LaunchProvenanceStore(max_records=32)

    def exercise(pid: int) -> None:
        store.record_launch(
            process=SimpleNamespace(pid=pid, poll=lambda: None),
            desktop_id=f"{pid}.desktop",
            executable_path=None,
        )
        store.provenance_for_pid(pid)
        store.prune_finished()

    with ThreadPoolExecutor(max_workers=12) as executor:
        tuple(executor.map(exercise, range(1, 257)))

    assert len(store) <= 32
    store.clear()
    assert len(store) == 0
