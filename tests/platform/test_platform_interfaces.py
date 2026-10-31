"""Regression guards for the Phase 1 dependency and lifetime boundaries."""

from __future__ import annotations

import ast
import inspect
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from docking.applets.worker import SerialWorker
from docking.platform.applications.associations import AssociationKey, AssociationStatus
from docking.platform.applications.identity import (
    LaunchProvenanceStore,
    ProcessIdentityService,
)
from docking.platform.applications.implementations.unix.associations import (
    UnixApplicationAssociationBackend,
)
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.targets import TargetService

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("keep", [False, True])
def test_quit_handler_cleanup_removes_only_live_signal_sources(monkeypatch, keep):
    from docking.platform.desktop.implementations.unix import lifecycle

    installed = {}
    removed = []

    def install(_priority, signum, callback, *args):
        source = 77 + len(installed)
        installed[signum] = (source, callback, args)
        return source

    monkeypatch.setattr(lifecycle.GLib, "unix_signal_add", install)
    monkeypatch.setattr(lifecycle.GLib, "source_remove", removed.append)
    cleanup = lifecycle.install_quit_handlers(lambda: keep)
    _source, callback, args = installed[signal.SIGTERM]

    assert callback(*args) is keep
    cleanup()
    cleanup()

    assert removed == ([77, 78] if keep else [77])


def test_construction_requires_platform_dependencies():
    for cls, names in (
        (
            ApplicationRegistry,
            ("catalog_backend", "association_backend", "registration_backend"),
        ),
        (ProcessIdentityService, ("backend",)),
        (TargetService, ("application_registry",)),
    ):
        signature = inspect.signature(cls)
        for name in names:
            assert signature.parameters[name].default is inspect.Parameter.empty


def test_contracts_and_host_import_without_gi():
    # Backend contracts must not pull in their presentation or native runtime.
    script = """
import importlib
import importlib.abc
import sys
class NoGi(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "gi" or fullname.startswith("gi."):
            raise AssertionError("Headless contract imported GI: " + fullname)
sys.meta_path.insert(0, NoGi())
for name in (
    "docking.platform.host.base", "docking.platform.host.selection",
    "docking.platform.desktop.base", "docking.platform.composition.selection",
    "docking.platform.applications.types", "docking.platform.applications.backend",
    "docking.platform.applications.associations",
    "docking.platform.tray.backend", "docking.platform.tray.menu", "docking.applets.platform",
    "docking.applets.systemtray.presentation",
    "docking.search.services.platform", "docking.search.services.global_shortcuts",
    "docking.search.services.implementations.unix.x11_shortcuts",
    "docking.applets.brightness.backend", "docking.applets.bluetooth.operations",
    "docking.applets.camshield.backend", "docking.applets.capslock.backend",
    "docking.applets.keyboardlayout.backend", "docking.applets.micshield.backend",
    "docking.applets.music.backend", "docking.applets.network.operations",
    "docking.applets.notifications.backend", "docking.applets.notifications.state",
    "docking.applets.powerprofiles.backend", "docking.applets.powerprofiles.state",
    "docking.applets.screenshot.backend", "docking.applets.volume.operations",
    "docking.applets.trash.backend", "docking.applets.webapp.backend",
    "docking.applets.whatsapp.state", "docking.applets.telegram.state",
    "docking.applets.systemmonitor.gpu", "docking.applets.caffeine.inhibit",
    "docking.applets.session.implementations.unix.actions",
    "docking.applets.thermals.backend",
    "docking.applets.aiusage.store", "docking.applets.aiusage.backends.claude",
    "docking.applets.aiusage.backends.codex",
):
    importlib.import_module(name)
from docking.platform.host.selection import host_services
assert host_services() is host_services()
assert not any(name.startswith("gi.") for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_shared_consumers_do_not_select_or_import_native_implementations():
    files = list((ROOT / "docking/platform/applications").glob("*.py"))
    files += list((ROOT / "docking/platform/tray").glob("*.py"))
    files += [ROOT / "docking/search/controller.py"]
    for domain in (ROOT / "docking/applets").iterdir():
        if domain.is_dir():
            files += [
                domain / name
                for name in ("applet.py", "backend.py", "state.py", "render.py")
                if (domain / name).exists()
            ]
    for path in files:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
            else:
                imports = []
            assert all("implementations" not in name.split(".") for name in imports), (
                path
            )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"is_bsd", "is_windows", "is_linux"}, path
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"is_bsd", "is_windows", "is_linux"}, path
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert (node.value.id, node.attr) not in {
                    ("sys", "platform"),
                    ("os", "name"),
                }, path
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert (
                    not node.value.startswith("docking.")
                    or ".implementations." not in node.value
                ), path


def test_implementation_package_initializers_have_no_side_effects():
    for path in (ROOT / "docking").rglob("__init__.py"):
        if "implementations" not in path.parts:
            continue
        tree = ast.parse(path.read_text())
        assert all(
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            for node in tree.body
        ), path


def test_process_evidence_uses_the_injected_backend_for_every_query(tmp_path):
    class Backend:
        def __init__(self):
            self.calls = []

        def executable(self, pid):
            self.calls.append(("executable", pid))
            return tmp_path / "editor"

        def script(self, pid):
            self.calls.append(("script", pid))
            return "editor.py"

        def sandbox_app_id(self, pid):
            self.calls.append(("sandbox", pid))
            return

        def steam_app_id(self, pid):
            self.calls.append(("steam", pid))
            return

    backend = Backend()
    identity = ProcessIdentityService(LaunchProvenanceStore(), backend=backend)
    assert identity.identity_for_pid(42).executable_path == tmp_path / "editor"
    assert sorted(backend.calls) == sorted(
        (name, 42) for name in ("executable", "script", "sandbox", "steam")
    )


@pytest.mark.parametrize("lookup", ["default", "recommended", "all", "name"])
def test_unix_associations_distinguish_empty_and_unavailable(monkeypatch, lookup):
    from gi.repository import Gio

    def empty(*_args):
        return None

    backend = UnixApplicationAssociationBackend()
    method = {
        "default": "get_default_for_type",
        "name": "get_default_for_type",
        "recommended": "get_recommended_for_type",
        "all": "get_all_for_type",
    }[lookup]
    monkeypatch.setattr(Gio.AppInfo, method, empty)
    assert (
        backend.read(AssociationKey(lookup, "application/example")).status
        is AssociationStatus.READY
    )

    def failed(*_args):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(Gio.AppInfo, method, failed)
    assert (
        backend.read(AssociationKey(lookup, "application/example")).status
        is AssociationStatus.UNAVAILABLE
    )


def test_serial_worker_queues_guarded_jobs_and_finalizes_on_its_owner():
    calls = []
    idles = []
    threads = []
    first_started = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    def thread_factory(**kwargs):
        thread = threading.Thread(**kwargs)
        threads.append(thread)
        return thread

    def close():
        calls.append(("close", threading.get_ident()))
        closed.set()

    worker = SerialWorker(
        on_close=close,
        thread_factory=thread_factory,
        idle_add=lambda fn, *args: idles.append((fn, args)),
    )
    assert not threads

    def first():
        first_started.set()
        assert release.wait(2)
        calls.append(("first", threading.get_ident()))
        return 1

    try:
        assert worker.run_guarded(key="first", name="first", fn=first)
        assert first_started.wait(2)
        assert not worker.run_guarded(key="first", name="duplicate", fn=lambda: None)
        assert worker.run_guarded(
            key="second",
            name="second",
            fn=lambda: calls.append(("second", threading.get_ident())),
        )
        worker.stop()
    finally:
        release.set()
        for thread in threads:
            thread.join(3)
    assert closed.is_set()
    assert [name for name, _thread in calls] == ["first", "second", "close"]
    assert len({thread for _name, thread in calls}) == 1
    assert calls[0][1] != threading.get_ident()
    worker.stop()
    assert len(calls) == 3


def test_serial_worker_stop_before_first_job_finalizes_once():
    closed = threading.Event()
    calls = []

    def close():
        calls.append(threading.get_ident())
        closed.set()

    worker = SerialWorker(on_close=close)
    worker.stop()
    assert closed.wait(2)
    worker.stop()
    assert len(calls) == 1
    assert calls[0] != threading.get_ident()
