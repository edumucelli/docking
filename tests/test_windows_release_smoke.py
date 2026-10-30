"""Packaged measurements preserve source isolation and owned process lifetimes."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.windows.visual import release_smoke


def packaged_fixture(tmp_path):
    bundle = tmp_path / "Docking"
    files = {
        "Docking.exe": b"native launcher fixture",
        "runtime/share/docking/runtime.json": json.dumps(
            {"version": "1.2.3", "wheel_sha256": "1" * 64}
        ).encode(),
    }
    inventory = json.dumps(
        {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    ).encode()
    files["files.sha256.json"] = inventory
    archive = tmp_path / "portable.zip"
    with zipfile.ZipFile(archive, "w") as target:
        for name, data in files.items():
            target.writestr("Docking/" + name, data)
            path = bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return bundle, archive


def test_measurement_rejects_changed_runtime_with_unchanged_inventory(tmp_path):
    bundle, archive = packaged_fixture(tmp_path)
    identity = release_smoke.verify_payload(bundle, archive)
    assert identity["version"] == "1.2.3"
    (bundle / "Docking.exe").write_bytes(b"different executable")
    with pytest.raises(ValueError, match="changed payload"):
        release_smoke.verify_payload(bundle, archive)


def test_measurement_allows_generated_cache_but_rejects_unlisted_dll(tmp_path):
    bundle, archive = packaged_fixture(tmp_path)
    generated = bundle / "runtime/__pycache__/generated.pyc"
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b"generated")
    release_smoke.verify_payload(bundle, archive)
    (bundle / "runtime/unlisted.dll").write_bytes(b"unaccepted binary")
    with pytest.raises(ValueError, match="unlisted"):
        release_smoke.verify_payload(bundle, archive)


def test_measurement_isolates_runtime_and_user_configuration(tmp_path, monkeypatch):
    for name in (
        "PYTHONPATH",
        "GI_TYPELIB_PATH",
        "GTK_PATH",
        "MSYSTEM",
        "PYGI_DLL_PATH",
    ):
        monkeypatch.setenv(name, "developer-runtime")
    monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "existing-user"))
    environment = release_smoke.runtime_environment(tmp_path / "measurement")
    assert environment["PATH"] == str(tmp_path / "Windows/System32")
    assert environment["XDG_CONFIG_HOME"] == str(tmp_path / "measurement/config")
    assert "PYTHONPATH" not in environment
    assert "GI_TYPELIB_PATH" not in environment
    assert "PYGI_DLL_PATH" not in environment
    assert not (tmp_path / "existing-user").exists()
    config = json.loads((tmp_path / "measurement/config/docking/dock.json").read_text())
    assert config["update_check_enabled"] is False
    assert len(config["pinned"]) == 5


class Child:
    pid = 10

    def __init__(self, *, hung=False):
        self.returncode = None
        self.hung = hung
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        if self.hung and not self.killed:
            raise subprocess.TimeoutExpired("owned launcher", timeout)
        self.returncode = 0 if not self.killed else 1
        return self.returncode

    def kill(self):
        self.killed = True


class ProcessError(Exception):
    pass


class ProcessGoneError(ProcessError):
    pass


class ProcessTimeoutError(ProcessError):
    pass


def session(child):
    value = release_smoke.PackagedSession.__new__(release_smoke.PackagedSession)
    value.child, value.window, value.owned = child, 20, {}
    value._process_api = SimpleNamespace(
        Process=lambda _pid: pytest.fail("Unexpected native process lookup"),
        Error=ProcessError,
        NoSuchProcess=ProcessGoneError,
        TimeoutExpired=ProcessTimeoutError,
    )
    value.desktop = SimpleNamespace(user=SimpleNamespace(PostMessageW=lambda *_: 1))
    value.responsive = lambda: True
    value.remember = list
    return value


def test_clean_shutdown_requires_normal_close_and_zero_exit():
    result = session(Child()).stop()
    assert result == {
        "normal_shutdown": True,
        "close_requested": True,
        "exit_code": 0,
        "forced": False,
        "owned_remaining": [],
        "errors": [],
    }


def test_unresponsive_shutdown_uses_owned_cleanup_and_records_failure():
    value = session(Child(hung=True))
    value.responsive = lambda: False
    result = value.stop()
    assert value.child.killed
    assert result["normal_shutdown"] is False
    assert result["forced"] is True
    assert result["close_requested"] is False


def test_cleanup_does_not_terminate_reused_pid_and_keeps_original_diagnostic(
    monkeypatch,
):
    value = session(Child(hung=True))

    def disappeared():
        raise OSError("query failed")

    class Reused:
        def is_running(self):
            return True

        def create_time(self):
            return 2.0

        def terminate(self):
            pytest.fail("A reused process ID is not owned")

    value.responsive = disappeared
    value.owned = {(99, 1.0): Reused()}
    value.remember = disappeared
    clock = iter((0, 4))
    monkeypatch.setattr(release_smoke.time, "monotonic", lambda: next(clock))
    result = value.stop()
    assert result["normal_shutdown"] is False
    assert "OSError" in result["errors"]
    assert value.child.killed


def test_remember_ignores_exited_and_reused_descendants():
    value = session(None)
    value.owned = {
        (10, 1.0): SimpleNamespace(is_running=lambda: True, create_time=lambda: 1.0),
        (11, 1.0): SimpleNamespace(is_running=lambda: True, create_time=lambda: 2.0),
        (12, 1.0): SimpleNamespace(is_running=lambda: False),
    }
    rows = release_smoke.PackagedSession.remember(value)
    assert [key for key, _process in rows] == [(10, 1.0)]


def test_exited_descendant_during_cleanup_is_benign(monkeypatch):
    value = session(Child())

    def gone():
        raise ProcessGoneError(11)

    process = SimpleNamespace(is_running=gone)
    calls = iter(([((11, 1.0), process)], []))
    value.remember = lambda: next(calls)
    clock = iter((0, 4))
    monkeypatch.setattr(release_smoke.time, "monotonic", lambda: next(clock))
    assert value.stop()["normal_shutdown"] is True


def test_release_tool_import_and_test_collection_need_no_native_process_dependency():
    root = Path(__file__).resolve().parents[1]
    code = f"""
import importlib.abc
import sys
sys.path.insert(0, {str(root)!r})
assert "psutil" not in sys.modules
class RejectNativeDependency(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "psutil" or fullname.startswith("psutil."):
            raise ModuleNotFoundError("Blocked native dependency", name=fullname)
sys.meta_path.insert(0, RejectNativeDependency())
from tools.windows.visual import release_smoke
import pytest
status = pytest.main([
    "--collect-only", "-q", {str(Path(__file__).resolve())!r}, "-o", "addopts=",
])
assert status == 0
assert "psutil" not in sys.modules
print("dependency-isolation-passed")
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "test_measurement_rejects_changed_runtime" in completed.stdout
    assert "test_exited_descendant_during_cleanup" in completed.stdout
    assert "dependency-isolation-passed" in completed.stdout
