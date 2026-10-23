"""Tests for instance-owned process identity and launch provenance."""

from __future__ import annotations

import os
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


def test_service_preserves_an_explicit_empty_store_instance():
    store = LaunchProvenanceStore()
    service = ProcessIdentityService(store)
    store.record_launch(
        process=SimpleNamespace(pid=42, poll=lambda: None),
        desktop_id="tool.desktop",
        executable_path=None,
    )

    identity = service.identity_for_pid(42)

    assert identity is not None
    assert identity.launch is not None
    assert identity.launch.desktop_id == "tool.desktop"


def test_identity_reads_current_process_executable():
    identity = ProcessIdentityService(LaunchProvenanceStore()).identity_for_pid(
        os.getpid()
    )

    assert identity is not None
    assert identity.executable_path == Path("/proc/self/exe").resolve()


def test_process_metadata_survives_unavailable_executable(tmp_path):
    process = tmp_path / "42"
    (process / "root").mkdir(parents=True)
    (process / "root" / ".flatpak-info").write_text(
        "[Application]\nname=app.grayjay.Grayjay\n[Instance]\ninstance-id=123\n"
    )
    (process / "cmdline").write_bytes(
        b"/usr/bin/python3.13\0/usr/share/gufw/gufw.py\0PRIVATE_ARGUMENT\0"
    )
    service = ProcessIdentityService(
        LaunchProvenanceStore(),
        proc_root=tmp_path,
        executable_resolver=lambda _pid: None,
    )
    result = service.identity_for_pid(42)
    assert result.executable_path is None
    assert result.sandbox_app_id == "app.grayjay.Grayjay"
    assert result.script_basename == "gufw.py"
    assert "PRIVATE_ARGUMENT" not in repr(result)


@pytest.mark.parametrize(
    "metadata",
    [
        b"bad",
        b"\xff",
        b"[Runtime]\nname=org.gnome.Platform\n",
        b"[Application]\nname=not-an-id\n",
        b"[Application]\nname=../../app\n",
        b"[Application]\nname=org.example.App\nname=other\n",
        b"x" * 65537,
    ],
)
def test_invalid_flatpak_process_metadata_does_not_create_identity(tmp_path, metadata):
    root = tmp_path / "42" / "root"
    root.mkdir(parents=True)
    (root / ".flatpak-info").write_bytes(metadata)
    service = ProcessIdentityService(
        LaunchProvenanceStore(),
        proc_root=tmp_path,
        executable_resolver=lambda _pid: None,
    )
    assert service.identity_for_pid(42).sandbox_app_id is None


@pytest.mark.parametrize(
    "cmdline",
    [
        b"/usr/bin/python3\0-m\0gufw.py\0",
        b"/usr/bin/python3\0-c\0gufw.py\0",
        b"/usr/bin/bash\0gufw.py\0",
        b"/usr/bin/python3\0gufw\0",
        b"\xff",
        b"x" * 4097,
    ],
)
def test_script_metadata_rejects_unconfirmed_script_names(tmp_path, cmdline):
    process = tmp_path / "42"
    process.mkdir()
    (process / "cmdline").write_bytes(cmdline)
    service = ProcessIdentityService(
        LaunchProvenanceStore(),
        proc_root=tmp_path,
        executable_resolver=lambda _pid: None,
    )
    assert service.identity_for_pid(42).script_basename is None


def test_process_metadata_failure_is_optional(tmp_path, monkeypatch):
    original = Path.open

    def open_path(path, *args, **kwargs):
        if path.is_relative_to(tmp_path):
            raise PermissionError("PRIVATE")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_path)
    service = ProcessIdentityService(
        LaunchProvenanceStore(),
        proc_root=tmp_path,
        executable_resolver=lambda _pid: None,
    )
    identity = service.identity_for_pid(42)
    assert identity.sandbox_app_id is None and identity.script_basename is None


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

    store.record_launch(
        process=SimpleNamespace(pid=5, poll=lambda: None),
        desktop_id="5.desktop",
        executable_path=None,
    )

    assert store.provenance_for_pid(1) is None
    assert store.provenance_for_pid(2) is not None
    assert store.provenance_for_pid(3) is not None
    assert store.provenance_for_pid(4) is not None


def test_concurrent_record_read_and_prune_remain_consistent():
    store = LaunchProvenanceStore(max_records=32)

    def exercise(pid: int) -> None:
        store.record_launch(
            process=SimpleNamespace(pid=pid, poll=lambda: None),
            desktop_id=f"{pid}.desktop",
            executable_path=None,
        )
        store.provenance_for_pid(pid)

    with ThreadPoolExecutor(max_workers=12) as executor:
        tuple(executor.map(exercise, range(1, 257)))

    retained = sum(store.provenance_for_pid(pid) is not None for pid in range(1, 257))
    assert retained <= 32
