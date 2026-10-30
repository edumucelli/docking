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


def test_relative_measurement_output_remains_external_after_launcher_changes_cwd(
    tmp_path, monkeypatch
):
    bundle, archive = packaged_fixture(tmp_path)
    checkout = tmp_path / "controller"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
    output = Path("reports/startup-1")
    environment = release_smoke.runtime_environment(output)
    before = {}
    release_smoke.verify_payload(bundle, archive, journal=before)
    script = """
import json, os
from pathlib import Path
config = Path(os.environ['XDG_CONFIG_HOME']) / 'docking/dock.json'
assert config.is_absolute()
assert len(json.loads(config.read_text())['pinned']) == 5
for name in ('XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME'):
    target = Path(os.environ[name]) / 'docking/owned-probe.bin'
    assert target.is_absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'owned mutable fixture output')
"""
    subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=bundle,
        env=environment,
        check=True,
        timeout=10,
    )
    after = {}
    release_smoke.verify_payload(bundle, archive, journal=after)
    assert before["observed_inventory_sha256"] == after["observed_inventory_sha256"]
    assert not (bundle / "reports").exists()
    assert (
        checkout / output / "cache/docking/owned-probe.bin"
    ).read_bytes() == b"owned mutable fixture output"


@pytest.mark.parametrize(
    "mutation",
    ["unlisted-cache", "missing", "missing-metadata", "changed", "linked-bytecode"],
)
def test_payload_mutations_retain_relative_shape_hash_and_strict_failure(
    tmp_path, mutation
):
    bundle, archive = packaged_fixture(tmp_path)
    name = "runtime/share/docking/runtime.json"
    if mutation == "unlisted-cache":
        name = "artifacts/windows/release-readiness/session/cache/docking/generated.bin"
        path = bundle / name
        path.parent.mkdir(parents=True)
        path.write_bytes(b"misplaced generated cache")
    elif mutation == "missing":
        name = "Docking.exe"
        (bundle / name).unlink()
    elif mutation == "missing-metadata":
        (bundle / name).unlink()
    elif mutation == "changed":
        (bundle / name).write_bytes(b"changed bytes")
    else:
        name = "runtime/__pycache__/generated.pyc"
        path = bundle / name
        path.parent.mkdir(parents=True)
        outside = tmp_path / "outside.pyc"
        outside.write_bytes(b"linked cache must remain untrusted")
        try:
            path.symlink_to(outside)
        except OSError:
            pytest.skip("Symbolic links require native permission")
    journal = {}
    with pytest.raises(ValueError):
        release_smoke.verify_payload(bundle, archive, journal=journal)
    assert journal["ok"] is False and journal["complete"] is True
    issue = next(row for row in journal["issues"] if row["path"] == name)
    assert issue["kind"] == (
        "missing"
        if mutation in ("missing", "missing-metadata")
        else "symlink"
        if mutation == "linked-bytecode"
        else "file"
    )
    assert not Path(issue["path"]).is_absolute()
    if mutation == "unlisted-cache":
        assert (
            issue["sha256"] == hashlib.sha256(b"misplaced generated cache").hexdigest()
        )
    elif mutation == "changed":
        assert issue["sha256"] == hashlib.sha256(b"changed bytes").hexdigest()
        assert issue["expected_sha256"] != issue["sha256"]
    elif mutation == "linked-bytecode":
        assert "sha256" not in issue


def test_unexpected_payload_diagnostics_bound_rows_paths_and_hashed_bytes(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(release_smoke, "_ISSUE_PATH_LIMIT", 8)
    bundle, archive = packaged_fixture(tmp_path)
    for index in range(20):
        (bundle / f"unlisted-{index:02}.bin").write_bytes(
            b"x" * (release_smoke._ISSUE_HASH_LIMIT + 1)
        )
    journal = {}
    with pytest.raises(ValueError, match="unlisted"):
        release_smoke.verify_payload(bundle, archive, journal=journal)
    assert journal["issue_count"] == 20
    assert len(journal["issues"]) == 16 and journal["issues_omitted"] == 4
    assert all(
        len(row["path"]) == 8 and row["path_truncated"] for row in journal["issues"]
    )
    assert all(
        row["hashed_bytes"] == release_smoke._ISSUE_HASH_LIMIT
        for row in journal["issues"]
    )
    assert all(
        "sha256_prefix" in row and "sha256" not in row for row in journal["issues"]
    )


@pytest.mark.parametrize("change", ["missing", "changed"])
def test_inventory_marker_failures_are_retained_before_runtime_start(tmp_path, change):
    bundle, archive = packaged_fixture(tmp_path)
    marker = bundle / "files.sha256.json"
    if change == "missing":
        marker.unlink()
    else:
        marker.write_bytes(b"changed inventory")
    journal = {}
    with pytest.raises(ValueError):
        release_smoke.verify_payload(bundle, archive, journal=journal)
    assert not journal["ok"] and not journal["complete"]
    assert journal["issue_count"] == 1
    assert journal["issues"][0]["path"] == "files.sha256.json"


def test_final_payload_failure_keeps_measurements_and_before_after_evidence(
    tmp_path, monkeypatch
):
    bundle, archive = packaged_fixture(tmp_path)
    output = tmp_path / "report"
    stops = []

    class Packaged:
        def __init__(self, *args):
            pass

        def start(self):
            return {"visible_seconds": 0.01, "responsive_seconds": 0.02}

        def stop(self):
            stops.append(True)
            if len(stops) == 2:
                (bundle / "unexpected.cache").write_bytes(b"unaccepted output")
            return {
                "normal_shutdown": True,
                "close_requested": True,
                "exit_code": 0,
                "forced": False,
                "owned_remaining": [],
                "errors": [],
            }

        def sample(self, elapsed):
            process = {
                "pid": 1,
                "created": 1.0,
                "cpu_seconds": elapsed / 100,
                "rss_bytes": 100,
                "private_bytes": 50,
                "threads": 1,
                "handles": 3,
                "gdi": 1,
                "user": 1,
            }
            return {
                "elapsed_seconds": elapsed,
                "responsive": True,
                "processes": [process],
                **{
                    key: process[key]
                    for key in (
                        "rss_bytes",
                        "private_bytes",
                        "threads",
                        "handles",
                        "gdi",
                        "user",
                    )
                },
            }

    monkeypatch.setattr(release_smoke, "PackagedSession", Packaged)
    monkeypatch.setattr(
        release_smoke, "WindowsDesktop", lambda: SimpleNamespace(windows=list)
    )
    monkeypatch.setattr(release_smoke.sys, "platform", "win32")
    monkeypatch.setattr(
        release_smoke.sys,
        "argv",
        [
            "release_smoke",
            "--bundle",
            str(bundle),
            "--archive",
            str(archive),
            "--output",
            str(output),
            "--cycles",
            "1",
            "--seconds",
            "5",
            "--interval",
            "1",
        ],
    )
    clock = iter([0.0, 0.0, 2.5, 5.0])
    monkeypatch.setattr(release_smoke.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(release_smoke.time, "sleep", lambda _: None)
    with pytest.raises(ValueError, match="unlisted"):
        release_smoke.main()
    report = json.loads((output / "result.json").read_text())
    assert not report["ok"] and report["error"] == "ValueError"
    assert len(report["startups"]) == 1 and len(report["session"]["samples"]) == 3
    assert report["payload_verification"]["before"]["ok"]
    assert not report["payload_verification"]["after"]["ok"]
    assert (
        report["payload_verification"]["after"]["issues"][0]["path"]
        == "unexpected.cache"
    )


@pytest.mark.parametrize(
    "name",
    [
        "runtime/stray.pyc",
        "runtime/__pycache__/unlisted.dll",
        "runtime/__pycache__/data.json",
    ],
)
def test_bytecode_policy_never_exempts_other_generated_payload_files(tmp_path, name):
    bundle, archive = packaged_fixture(tmp_path)
    extra = bundle / name
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_bytes(b"unlisted output")
    with pytest.raises(ValueError, match="unlisted"):
        release_smoke.verify_payload(bundle, archive)


def test_output_inside_bundle_is_rejected_before_any_directory_or_process(
    tmp_path, monkeypatch
):
    bundle, archive = packaged_fixture(tmp_path)
    output = bundle / "measurements"
    monkeypatch.setattr(release_smoke.sys, "platform", "win32")
    monkeypatch.setattr(
        release_smoke.sys,
        "argv",
        [
            "release_smoke",
            "--bundle",
            str(bundle),
            "--archive",
            str(archive),
            "--output",
            str(output),
        ],
    )
    monkeypatch.setattr(
        release_smoke,
        "WindowsDesktop",
        lambda: pytest.fail("Rejected output reached native setup"),
    )
    with pytest.raises(SystemExit) as error:
        release_smoke.main()
    assert error.value.code == 2 and not output.exists()
    release_smoke.verify_payload(bundle, archive)
