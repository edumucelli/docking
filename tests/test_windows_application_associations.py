"""Owned association fixtures register complete clients and restore their keys."""

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.platform.applications.associations import (
    AssociationResolution,
    AssociationStatus,
)
from tools.windows.visual import applications_domain_smoke as smoke


@pytest.fixture
def association_main_context(monkeypatch):
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    context = SimpleNamespace(
        pending=Mock(return_value=False),
        iteration=Mock(side_effect=AssertionError("No unrelated main-loop dispatch")),
    )
    monkeypatch.setattr(GLib.MainContext, "default", lambda: context)
    return context


def _wait_for_ready(callback, message):
    for _ in range(3):
        result = callback()
        if result:
            return result
    pytest.fail(message)


class Registry:
    HKEY_CURRENT_USER = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self, *, parent=False, fail_field=None):
        self.keys = {"Software": {}, r"Software\Classes": {}}
        if parent:
            self.keys[r"Software\RegisteredApplications"] = {"Other": ("Existing", 1)}
        self.OpenKey = self.open_key
        self.CreateKeyEx = self.create_key
        self.QueryValueEx = self.query_value
        self.SetValueEx = self.set_value
        self.DeleteValue = self.delete_value
        self.QueryInfoKey = self.query_info
        self.DeleteKey = self.delete_key
        self.opened = 0
        self.fail_field = fail_field

    @contextmanager
    def open_key(self, root, name, _reserved=0, _access=0):
        assert root == self.HKEY_CURRENT_USER
        if name not in self.keys:
            raise FileNotFoundError(name)
        self.opened += 1
        try:
            yield name
        finally:
            self.opened -= 1

    def create_key(self, root, name, _reserved, _access):
        self.keys.setdefault(name, {})
        return self.OpenKey(root, name)

    def query_value(self, key, field):
        if field not in self.keys[key]:
            raise FileNotFoundError(field)
        return self.keys[key][field]

    def set_value(self, key, field, _reserved, kind, value):
        self.keys[key][field] = (value, kind)
        if field == self.fail_field:
            raise OSError(5, "Injected registry write failure")

    def delete_value(self, key, field):
        if field not in self.keys[key]:
            raise FileNotFoundError(field)
        del self.keys[key][field]

    def query_info(self, key):
        children = sum(name.startswith(key + "\\") for name in self.keys)
        return children, len(self.keys[key]), 0

    def delete_key(self, root, key):
        assert root == self.HKEY_CURRENT_USER
        assert not any(name.startswith(key + "\\") for name in self.keys)
        del self.keys[key]


@pytest.mark.parametrize("existing_parent", [False, True])
@pytest.mark.parametrize("failure", [None, "body", "registration-value"])
def test_complete_registration_is_owned_and_cleanup_survives_failed_body_or_write(
    existing_parent, failure
):
    registry = Registry(
        parent=existing_parent,
        fail_field="Docking.Acceptance.Owned"
        if failure == "registration-value"
        else None,
    )
    before, lifecycle = deepcopy(registry.keys), {}
    program = "Docking.Acceptance.Owned"
    capabilities = r"Software\Classes\Docking.Acceptance.Owned\Capabilities"

    def run():
        with smoke.association_registration(
            registry, ".owned", program, "C:/Owned application.exe", lifecycle
        ):
            assert registry.keys[r"Software\RegisteredApplications"][program] == (
                capabilities,
                registry.REG_SZ,
            )
            assert registry.keys[capabilities]["ApplicationName"] == (program, 1)
            assert registry.keys[capabilities + r"\FileAssociations"][".owned"] == (
                program,
                1,
            )
            command = registry.keys[
                r"Software\Classes\Docking.Acceptance.Owned\shell\open\command"
            ][""][0]
            assert command == '"C:\\Owned application.exe" "%1"'
            if failure == "body":
                raise RuntimeError("Observed fixture failure")

    if failure is None:
        run()
    else:
        with pytest.raises(RuntimeError if failure == "body" else OSError):
            run()
    assert registry.keys == before
    assert registry.opened == 0 and lifecycle["association_restored"]


def test_existing_owned_name_is_rejected_before_any_mutation():
    registry = Registry(parent=True)
    registry.keys[r"Software\Classes\.owned"] = {"": ("Previous", 1)}
    before = deepcopy(registry.keys)
    with (
        pytest.raises(RuntimeError, match="unexpectedly exists"),
        smoke.association_registration(
            registry, ".owned", "Docking.Acceptance.Owned", "C:/Owned.exe", {}
        ),
    ):
        pytest.fail("Existing registration cannot be used")
    assert registry.keys == before and registry.opened == 0


@pytest.mark.parametrize(
    "native_status,passes", [(-2147023741, True), (-2147024891, False)]
)
def test_absence_is_independent_of_known_client_lookup_and_rejects_access_errors(
    tmp_path, monkeypatch, association_main_context, native_status, passes
):
    import sys

    from docking.platform.applications import registry as registry_module
    from docking.platform.native.windows import ffi

    registry = Registry(parent=True)
    before = deepcopy(registry.keys)
    executable = tmp_path / "Owned.exe"
    executable.write_bytes(b"owned fixture")
    fake = SimpleNamespace(
        start=Mock(), stop=Mock(), refresh=Mock(), initial_discovery_pending=False
    )
    lookups = []

    def no_handler(content_type):
        assert content_type.startswith(".docking-absent-")
        lookups.append(content_type)
        return AssociationResolution(
            AssociationStatus.PENDING if len(lookups) == 1 else AssociationStatus.READY
        )

    fake.default_for_content_type = Mock(side_effect=no_handler)
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(registry_module, "ApplicationRegistry", lambda **_kwargs: fake)
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: SimpleNamespace(hex="owned"))
    monkeypatch.setattr(
        smoke, "finish_program", Mock(side_effect=AssertionError("No launch"))
    )
    import ctypes

    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_args, **_kwargs: object(), raising=False
    )

    def query(_flags, _kind, extension, _verb, output, size):
        if extension.startswith(".docking-absent-"):
            return native_status
        if output is None:
            size._obj.value = len(str(executable)) + 1
            return 1
        output.value = str(executable)
        return 0

    def bind(_library, name, *_types):
        if name == "AssocQueryStringW":
            return query
        if name == "SleepEx":
            return lambda _milliseconds, _alertable: 0
        assert name == "SHChangeNotify"
        return lambda *_args: None

    monkeypatch.setattr(ffi, "bind", bind)
    native = SimpleNamespace(wait_for=_wait_for_ready)
    fixture = SimpleNamespace(root=tmp_path, executables={"Other": executable})
    report, lifecycle = {"checks": {"association-absence": {}}}, {"closed": True}
    if passes:
        assert smoke._registered_association_checks(
            native, fixture, tmp_path, report, mode="absence", lifecycle=lifecycle
        )["passed"]
    else:
        with pytest.raises(OSError) as failure:
            smoke._registered_association_checks(
                native, fixture, tmp_path, report, mode="absence", lifecycle=lifecycle
            )
        assert failure.value.errno == native_status & 0xFFFFFFFF
    fake.start.assert_called_once_with()
    fake.stop.assert_called_once_with()
    assert fake.refresh.call_count == (3 if passes else 1)
    assert fake.default_for_content_type.call_count == (2 if passes else 0)
    assert association_main_context.pending.call_count == (3 if passes else 1)
    if passes:
        observed = report["checks"]["association-absence"]["observed"]
        assert observed["lookup_status"] == "ready" and observed["absent"]
    assert registry.keys == before and registry.opened == 0
    assert report["associations_restored"] and lifecycle["closed"]


def test_explicit_association_uses_enumerated_exact_client_and_retained_handle(
    tmp_path, monkeypatch, association_main_context
):
    import ctypes
    import sys

    from docking.platform.applications import launcher as launcher_module
    from docking.platform.applications import registry as registry_module
    from docking.platform.native.windows import ffi

    registry = Registry(parent=True)
    before = deepcopy(registry.keys)
    executable = tmp_path / "Owned.exe"
    executable.write_bytes(b"owned fixture")
    program = "Docking.Acceptance.owned"
    identifier = "HKEY_CURRENT_USER\\Software\\Classes\\" + program
    selected = SimpleNamespace(desktop_id=identifier, has_gio_source=True)
    dispatched = []
    handle = object()
    fake = SimpleNamespace(
        start=Mock(),
        stop=Mock(),
        refresh=Mock(),
        initial_discovery_pending=False,
        get=lambda selected_id: selected if selected_id == identifier else None,
        _gio_handle_for=lambda selected_id: (
            handle if selected_id == identifier else None
        ),
    )
    resolutions = iter(
        (
            AssociationResolution(AssociationStatus.PENDING),
            AssociationResolution(AssociationStatus.READY, selected),
        )
    )

    def lookup(extension):
        assert dispatched and extension == ".docking-owned"
        return next(resolutions)

    fake.default_for_content_type = Mock(side_effect=lookup)
    launch = Mock(return_value=True)
    monkeypatch.setattr(registry_module, "ApplicationRegistry", lambda **_kwargs: fake)
    monkeypatch.setattr(
        launcher_module,
        "ApplicationLauncher",
        lambda *_args, **_kwargs: SimpleNamespace(launch_app_uris=launch),
    )
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: SimpleNamespace(hex="owned"))
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_args, **_kwargs: object(), raising=False
    )

    def query(_flags, _kind, extension, _verb, output, size):
        assert extension == ".docking-owned"
        assert registry.keys[r"Software\RegisteredApplications"][program][0].endswith(
            r"\Capabilities"
        )
        if output is None:
            size._obj.value = len(str(executable)) + 1
            return 1
        output.value = str(executable)
        return 0

    def bind(_library, name, *_types):
        if name == "AssocQueryStringW":
            return query
        if name == "SleepEx":

            def alertable(milliseconds, enabled):
                assert milliseconds == 0 and enabled is True
                dispatched.append(True)
                return 0xC0

            return alertable
        assert name == "SHChangeNotify"
        return lambda *_args: None

    monkeypatch.setattr(ffi, "bind", bind)

    def completed(
        _native,
        _facts,
        actual_executable,
        argv,
        *,
        lifecycle,
        observed,
        file_arguments,
        focus_before_close,
    ):
        assert actual_executable == executable
        assert len(argv) == 1 and file_arguments == (0,)
        assert observed == {} and focus_before_close is True
        lifecycle["closed"] = True
        return {"argv": argv}

    monkeypatch.setattr(smoke, "finish_program", completed)
    native = SimpleNamespace(wait_for=_wait_for_ready)
    fixture = SimpleNamespace(root=tmp_path, executables={"Other": executable})
    report, lifecycle = {"checks": {"association-explicit": {}}}, {"closed": True}
    result = smoke._registered_association_checks(
        native, fixture, tmp_path, report, mode="explicit", lifecycle=lifecycle
    )
    assert result["passed"] and launch.call_args.args[0] == identifier
    assert len(launch.call_args.args[1]) == 1
    fake.start.assert_called_once_with()
    fake.stop.assert_called_once_with()
    assert fake.refresh.call_count == 3
    assert fake.default_for_content_type.call_count == 2
    assert association_main_context.pending.call_count == 3
    observed = report["checks"]["association-explicit"]["observed"]
    assert {
        key: observed[key]
        for key in ("resolved", "registered", "has_gio_source", "id_matches")
    } == {
        "resolved": True,
        "registered": True,
        "has_gio_source": True,
        "id_matches": True,
    }
    assert observed["alertable_status"] == 0xC0 and len(dispatched) == 3
    assert observed["lookup_status"] == "ready"
    assert observed["native_query"]["flags"] == 0x400
    assert registry.keys == before and registry.opened == 0
    assert lifecycle["closed"] and report["associations_restored"]


@pytest.mark.parametrize("failure", ("start", "body", "cleanup-only"))
def test_running_registry_stops_once_and_preserves_the_primary_failure(failure):
    primary, secondary = OSError(5, "Owned primary"), OSError(6, "Owned cleanup")
    fake = SimpleNamespace(
        start=Mock(side_effect=primary if failure == "start" else None),
        stop=Mock(side_effect=secondary),
    )
    with pytest.raises(OSError) as raised, smoke.running_association_registry(fake):
        if failure == "body":
            raise primary
    fake.start.assert_called_once_with()
    fake.stop.assert_called_once_with()
    assert raised.value is (secondary if failure == "cleanup-only" else primary)
    if failure != "cleanup-only":
        assert raised.value.__cause__ is secondary


@pytest.mark.parametrize(
    "second_status", [0, 0x80070002, 0x80070003, 0x80070483, 0x80070005, 1]
)
def test_native_association_uses_sizing_then_value_and_never_calls_unknown_absence(
    second_status,
):
    value, calls = r"C:\Windows\UnknownFallback.exe", []

    def query(flags, kind, extension, verb, output, size):
        assert (flags, kind, extension, verb) == (0x400, 2, ".owned-absent", "open")
        calls.append(output is None)
        if output is None:
            size._obj.value = len(value) + 1
            return 1  # S_FALSE is required buffer sizing, never absence.
        assert size._obj.value == len(value) + 1
        if second_status == 0:
            output.value = value
        return second_status

    observed = {}
    if second_status in (0, 0x80070002, 0x80070003, 0x80070483):
        result = smoke.association_executable(query, ".owned-absent", observed)
        assert result == (value if second_status == 0 else None)
    else:
        with pytest.raises(OSError) as failure:
            smoke.association_executable(query, ".owned-absent", observed)
        assert failure.value.errno == second_status
    assert calls == [True, False]
    assert [call["hresult"] for call in observed["calls"]] == [1, second_status]


@pytest.mark.parametrize("size", [0, 32769])
def test_native_association_rejects_invalid_sizing_without_claiming_no_handler(size):
    def query(_flags, _kind, _extension, _verb, output, received_size):
        assert output is None
        received_size._obj.value = size
        return 1

    observed = {}
    with pytest.raises(ValueError, match="buffer size"):
        smoke.association_executable(query, ".owned", observed)
    assert len(observed["calls"]) == 1


@pytest.mark.parametrize("existing_parent", [False, True])
@pytest.mark.parametrize("mode", ["explicit", "absence"])
def test_fresh_lookup_finishes_before_parent_registration_cleanup(
    tmp_path, monkeypatch, existing_parent, mode
):
    run_association_child_fixture(
        tmp_path, monkeypatch, existing_parent=existing_parent, mode=mode
    )


@pytest.mark.parametrize(
    "failure", ["assign", "communicate", "close", "child", "cancel", "assign-close"]
)
def test_fresh_lookup_failure_stops_owned_child_before_unregistering(
    tmp_path, monkeypatch, failure
):
    run_association_child_fixture(tmp_path, monkeypatch, failure=failure)


def run_association_child_fixture(
    tmp_path, monkeypatch, *, existing_parent=False, mode="explicit", failure=None
):
    import io
    import json
    import subprocess
    import sys

    from docking.platform.native.windows import process_job

    registry = Registry(parent=existing_parent)
    before, events = deepcopy(registry.keys), []
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: SimpleNamespace(hex="owned"))
    executable = tmp_path / "Owned.exe"
    executable.write_bytes(b"owned")
    fixture = SimpleNamespace(root=tmp_path, executables={"Other": executable})
    report = {
        "checks": {"association-" + mode: {}},
        "runtime": {"wheel_sha256": "a" * 64},
    }
    lifecycle = {"closed": True}
    registered = r"Software\RegisteredApplications"
    program = "Docking.Acceptance.owned"

    class Process:
        pid, _handle, returncode = 4321, 123, None

        def __init__(self):
            self.stdin = io.BytesIO()

        def communicate(self, permission, *, timeout):
            assert permission == b"1" and timeout == 65
            assert events == ["spawn", "assigned"]
            assert program in registry.keys[registered]
            events.append("permitted")
            if failure == "communicate":
                raise subprocess.TimeoutExpired("owned child", timeout)
            if failure == "cancel":
                raise KeyboardInterrupt
            request = json.loads(
                (tmp_path / ("association-" + mode + "-request.json")).read_text()
            )
            assert request["wheel"] == str((tmp_path / "candidate.whl").resolve())
            assert request["installed_root"] == str((tmp_path / "installed").resolve())
            child = {
                "pid": self.pid,
                "ok": failure != "child",
                "normal_shutdown": True,
                "runtime": {"wheel_sha256": "a" * 64},
                "registration_parent_before_gio": {"exists": True},
                "checks": {
                    "association-" + mode: {
                        "observed": {
                            "canonical_id": "HKEY_CURRENT_USER\\Software\\Classes\\"
                            + program
                        }
                    }
                },
                "facts": {"argv": ["owned document"]},
            }
            (tmp_path / ("association-" + mode + "-result.json")).write_text(
                json.dumps(child)
            )
            events.extend(["application-closed", "child-exited"])
            self.returncode = 1 if failure == "child" else 0
            return None, None

        def wait(self, *, timeout):
            assert timeout == 5
            assert self.stdin.closed and "job-closed" in events
            if self.returncode is None:
                # EOF before job closure releases an unassigned child without
                # permitting its imports or fixture application to start.
                self.returncode = 1 if failure in ("assign", "assign-close") else -1
                events.append("child-exited")
            events.append("joined")
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            pytest.fail("All owned processes in this scenario stopped normally")

    process = Process()

    def spawn(command, **options):
        assert command[0] == sys.executable and command[1:3] == ["-I", "-c"]
        assert command[3].index("read(1)") < command[3].index("from tools")
        assert options["stdin"] == subprocess.PIPE
        assert options["stdout"].writable() and options["stderr"] == subprocess.STDOUT
        assert program in registry.keys[registered]
        events.append("spawn")
        return process

    class Job:
        def assign(self, handle):
            assert handle == 123 and program in registry.keys[registered]
            if failure in ("assign", "assign-close"):
                raise OSError(5, "Owned assignment failure")
            events.append("assigned")

        def close(self):
            assert process.stdin.closed
            assert program in registry.keys[registered]
            events.append("job-closed")
            if failure in ("close", "assign-close"):
                raise OSError(6, "Owned job close failure")

    delete_value = registry.DeleteValue

    def unregister(key, field):
        assert events[-1] == "joined" and process.poll() is not None
        events.append("unregistered")
        delete_value(key, field)

    monkeypatch.setattr(registry, "DeleteValue", unregister)
    monkeypatch.setattr(process_job, "ProcessJob", Job)
    monkeypatch.setattr(smoke.subprocess, "Popen", spawn)

    def notified():
        assert registry.keys == before and events[-1] == "unregistered"
        events.append("notified")

    monkeypatch.setattr(smoke, "association_changed", notified)
    kwargs = {
        "mode": mode,
        "lifecycle": lifecycle,
        "installed_root": tmp_path / "installed",
        "wheel": tmp_path / "candidate.whl",
    }
    if failure:
        expected = {
            "assign": OSError,
            "communicate": subprocess.TimeoutExpired,
            "close": OSError,
            "child": AssertionError,
            "cancel": KeyboardInterrupt,
            "assign-close": OSError,
        }[failure]
        with pytest.raises(expected) as raised:
            smoke.association_checks(None, fixture, tmp_path, report, **kwargs)
        if failure == "assign-close":
            assert raised.value.errno == 5
    else:
        result = smoke.association_checks(None, fixture, tmp_path, report, **kwargs)
        assert result["passed"] and lifecycle["closed"]
    observed = report["checks"]["association-" + mode]["observed"]
    parents = observed["registration_parent"]
    assert parents["before"]["exists"] is existing_parent
    assert parents["registered"] == {
        "exists": True,
        "subkeys": 0,
        "values": 2 if existing_parent else 1,
    }
    assert parents["after"] == parents["before"]
    assert observed["child_stopped"] and report["associations_restored"]
    assert registry.keys == before and registry.opened == 0
    assert events[-2:] == ["unregistered", "notified"]
    if failure in ("close", "assign-close"):
        assert observed["child_cleanup_error"]["errno"] == 6


def test_fresh_child_selects_exact_wheel_after_parent_exists_before_first_import(
    tmp_path,
):
    import hashlib
    import json
    import struct
    import subprocess
    import sys
    import zipfile
    from pathlib import Path

    installed = tmp_path / "installed"
    files = {
        "docking/__init__.py": "import winreg\nassert winreg.parent_exists\nwinreg.first_import_parent_present = True\n",
        "docking/platform/__init__.py": "",
        "docking/platform/applications/__init__.py": "",
        "docking/platform/applications/launcher.py": "",
        "docking/platform/applications/registry.py": "",
    }
    wheel = tmp_path / "owned.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, text in files.items():
            path = installed / name
            path.parent.mkdir(parents=True, exist_ok=True)
            data = text.encode("utf-8")
            path.write_bytes(data)
            archive.writestr(name, data)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    request = {
        "mode": "explicit",
        "token": "owned",
        "fixture_root": str(tmp_path),
        "executable": str(tmp_path / "Owned.exe"),
        "output": str(tmp_path),
        "result": str(tmp_path / "result.json"),
        "installed_root": str(installed),
        "wheel": str(wheel),
        "wheel_sha256": digest,
        "pointer_bits": struct.calcsize("P") * 8,
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request))
    code = r"""
import sys
from contextlib import contextmanager
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
@contextmanager
def opened(root, key):
    assert winreg.parent_exists and key == r"Software\RegisteredApplications"
    yield key
winreg = SimpleNamespace(HKEY_CURRENT_USER=1, OpenKey=opened,
    QueryInfoKey=lambda key: (0, 1, 0), parent_exists=True,
    first_import_parent_present=False)
sys.modules['winreg'] = winreg
from tools.windows.visual import applications_domain_smoke as smoke, desktop_driver
assert 'docking' not in sys.modules and 'gi' not in sys.modules
desktop_driver.WindowsDesktop = lambda: object()
def check(native, fixture, output, report, *, mode, lifecycle, token, borrowed_registration):
    assert winreg.first_import_parent_present and borrowed_registration
    assert mode == 'explicit' and token == 'owned'
    report['checks']['association-explicit']['observed'] = {'canonical_id': r'HKEY_CURRENT_USER\Software\Classes\Docking.Acceptance.owned'}
    lifecycle['closed'] = True
    return {'passed': True, 'facts': {'argv': ['owned document']}}
smoke._registered_association_checks = check
smoke._association_child(sys.argv[2])
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            code,
            str(Path(smoke.__file__).resolve().parents[3]),
            str(request_path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    result_path = tmp_path / "result.json"
    result = json.loads(result_path.read_text()) if result_path.is_file() else None
    assert completed.returncode == 0, {
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "result": result,
    }
    assert result is not None
    assert result["ok"] and result["normal_shutdown"]
    assert result["registration_parent_before_gio"] == {
        "exists": True,
        "subkeys": 0,
        "values": 1,
    }
    assert result["runtime"]["wheel_sha256"] == digest
    assert (
        result["runtime"]["origins"]["registry"]
        == "docking/platform/applications/registry.py"
    )
    assert result["checks"]["association-explicit"]["observed"][
        "canonical_id"
    ].endswith("Docking.Acceptance.owned")


@pytest.mark.parametrize(
    "actual,file_arguments,passes",
    [
        (["C:/Owned/résumé 日本語 #100%.txt", "literal /argument", ""], (0,), True),
        (["C:/Owned/résumé 日本語 #100%.txt", "literal /argument", ""], (), False),
        (["C:/Owned/résumé 日本語 #100%.txt", "literal \\argument", ""], (0,), False),
        (["C:/Other/résumé 日本語 #100%.txt", "literal /argument", ""], (0,), False),
        (["C:/Owned/RÉSUMÉ 日本語 #100%.txt", "literal /argument", ""], (0,), False),
        (["literal /argument", "C:/Owned/résumé 日本語 #100%.txt", ""], (0,), False),
        (["C:/Owned/résumé 日本語 #100%.txt", "literal /argument"], (0,), False),
    ],
)
def test_gio_file_argument_accepts_only_separator_variants_and_always_closes(
    tmp_path, monkeypatch, actual, file_arguments, passes
):
    import json
    import sys

    executable = tmp_path / "Owned.exe"
    executable.write_bytes(b"owned fixture")
    path = tmp_path / "facts.json"
    facts = {"executable": str(executable), "pid": 123, "argv": actual}
    path.write_text(json.dumps(facts))
    process = SimpleNamespace(
        exe=lambda: str(executable),
        pid=123,
        is_running=lambda: True,
        wait=Mock(return_value=0),
    )
    monkeypatch.setitem(
        sys.modules, "psutil", SimpleNamespace(Process=lambda pid: process)
    )
    native = SimpleNamespace(
        wait_for=lambda predicate, _message, *_timeout: predicate(),
        windows=lambda pid: [{"hwnd": 456}],
        user=SimpleNamespace(PostMessageW=Mock(return_value=True)),
    )
    lifecycle = {"closed": False}
    expected = [r"C:\Owned\résumé 日本語 #100%.txt", "literal /argument", ""]
    if passes:
        assert (
            smoke.finish_program(
                native,
                path,
                executable,
                expected,
                lifecycle=lifecycle,
                file_arguments=file_arguments,
            )
            == facts
        )
    else:
        with pytest.raises(AssertionError):
            smoke.finish_program(
                native,
                path,
                executable,
                expected,
                lifecycle=lifecycle,
                file_arguments=file_arguments,
            )
    assert lifecycle["closed"]
    native.user.PostMessageW.assert_called_once_with(456, 0x10, 0, 0)
    process.wait.assert_called_once_with(20)


@pytest.mark.parametrize(
    "mode",
    [
        "foreground",
        "normal",
        "async",
        "async-focus",
        "caption",
        "caption-guard",
        "missing",
        "not-found",
        "stale",
        "replaced-process",
        "replaced-window",
        "changed-bounds",
        "changed-topmost",
        "no-foreground",
        "foreign-focus",
        "lost-during-focus",
        "cancel-focus",
    ],
)
def test_association_focus_targets_only_live_owned_window(monkeypatch, tmp_path, mode):
    from docking.platform.backends.base import ActionResult
    from docking.platform.backends.windows import windows
    from tools.windows.visual import input_editor, window_focus

    executable = tmp_path / "Owned.exe"
    executable.write_bytes(b"owned")
    window = {
        "hwnd": 456,
        "pid": 123,
        "class": "#32770",
        "title": "Owned association",
        "rect": [100, 100, 400, 300],
    }
    state = {
        "foreground": 456 if mode in ("foreground", "async-focus") else 999,
        "topmost": False,
        "focus_ready": mode != "async-focus",
    }
    process = SimpleNamespace(
        pid=123,
        is_running=lambda: mode != "replaced-process",
        create_time=lambda: 20.5,
        exe=lambda: str(executable),
    )
    snapshot = SimpleNamespace(id=SimpleNamespace(value=456), pid=123)
    current = {**window, "pid": 88} if mode == "replaced-window" else window

    def wait(predicate, message):
        value = predicate()
        if mode in ("async", "async-focus") and not value:
            state["foreground"] = 456
            state["focus_ready"] = True
            value = predicate()
        if mode == "lost-during-focus":
            current["pid"] = 77
            value = predicate()
        if mode == "cancel-focus":
            raise KeyboardInterrupt
        assert value, message

    native = SimpleNamespace(
        user=SimpleNamespace(GetForegroundWindow=lambda: state["foreground"]),
        windows=lambda pid: [current],
        wait_for=wait,
    )

    def activate(target, action):
        assert target is snapshot and action == "activate"
        if mode == "not-found":
            return ActionResult.NOT_FOUND
        if mode in ("caption", "caption-guard", "stale"):
            return ActionResult.FAILED
        if mode not in ("async", "no-foreground"):
            state["foreground"] = 456
        if mode == "changed-bounds":
            current["rect"] = [100, 100, 401, 300]
        if mode == "changed-topmost":
            state["topmost"] = True
        return ActionResult.OK

    api = SimpleNamespace(
        capture_snapshot=Mock(return_value=None if mode == "missing" else snapshot),
        perform=Mock(side_effect=activate),
        matches=Mock(return_value=mode != "stale"),
    )
    monkeypatch.setattr(windows, "Win32WindowAPI", lambda: api)
    monkeypatch.setattr(input_editor, "_topmost", lambda *_args: state["topmost"])
    monkeypatch.setattr(
        smoke,
        "_foreground_snapshot",
        lambda _native: {
            "foreground": {"hwnd": state["foreground"]},
            "gui_available": True,
            "active": {"hwnd": 456},
            "focus": {
                "pid": 123 if state["focus_ready"] and mode != "foreign-focus" else 999
            },
        },
    )

    def caption(target, current_window, evidence):
        assert target is native and current_window() is window
        assert evidence["result"] in ("failed", "ok", "already-foreground")
        assert evidence["completion_interaction"] == "physical-owned-caption"
        if evidence["result"] == "ok":
            assert evidence["normal_foreground_verified"] is True
        if mode == "caption-guard":
            raise AssertionError("Owned caption hit changed before input")
        state["foreground"] = 456

    physical = Mock(side_effect=caption)
    monkeypatch.setattr(window_focus, "focus_caption", physical)
    journal = {}
    if mode in ("foreground", "normal", "async", "async-focus", "caption"):
        smoke._focus_program(native, process, executable, window, journal)
        assert state["foreground"] == 456
    else:
        with pytest.raises(
            KeyboardInterrupt if mode == "cancel-focus" else AssertionError
        ):
            smoke._focus_program(native, process, executable, window, journal)
    assert physical.call_count == (
        1
        if mode
        in (
            "foreground",
            "normal",
            "async",
            "async-focus",
            "caption",
            "caption-guard",
            "foreign-focus",
        )
        else 0
    )
    assert api.perform.call_count == (
        0
        if mode
        in (
            "foreground",
            "async-focus",
            "missing",
            "replaced-process",
            "replaced-window",
        )
        else 1
    )
    if api.capture_snapshot.called:
        api.capture_snapshot.assert_called_once_with(456, expected_pid=123)


@pytest.mark.parametrize(
    "mode",
    ["normal", "focus", "partial", "restore", "focus-race", "close", "dual", "trace"],
)
def test_association_completion_journals_focus_and_preserves_strict_cleanup(
    monkeypatch, tmp_path, mode
):
    import json
    import sys

    executable = tmp_path / "Owned.exe"
    executable.write_bytes(b"owned")
    path = tmp_path / "facts.json"
    path.write_text(
        json.dumps({"executable": str(executable), "pid": 123, "argv": ["owned"]})
    )
    events = []
    process = SimpleNamespace(
        pid=123,
        exe=lambda: str(executable),
        is_running=lambda: True,
        wait=Mock(side_effect=lambda _timeout: events.append("closed") or 0),
    )
    monkeypatch.setitem(
        sys.modules, "psutil", SimpleNamespace(Process=lambda _pid: process)
    )
    primary = AssertionError("Owned focus failure")
    secondary = OSError(6, "Owned close failure")
    window = {"hwnd": 456}

    def close(*args):
        assert args == (456, 0x10, 0, 0)
        events.append("close")
        if mode in ("close", "dual"):
            raise secondary
        return True

    native = SimpleNamespace(
        wait_for=lambda predicate, *_args: predicate(),
        windows=lambda _pid: [window],
        user=SimpleNamespace(PostMessageW=Mock(side_effect=close)),
    )

    def focus(_native, selected, actual_executable, target, evidence):
        assert _native is native and selected is process
        assert actual_executable == executable and target is window
        events.append("focus")
        if mode == "partial":
            evidence["caption_press_started"] = True
        if mode == "restore":
            evidence["geometry_restored"] = False
        if mode in ("focus", "partial", "restore", "focus-race", "dual", "trace"):
            raise primary
        return {
            "foreground": {"hwnd": 456},
            "gui_available": True,
            "active": {"hwnd": 456},
            "focus": {"pid": 123},
        }

    def snapshot(_native):
        assert _native is native
        events.append("observe")
        if mode == "trace" and "closed" in events:
            raise OSError(5, "Postclose query failed")
        return {
            "foreground": {"hwnd": 999 if mode == "focus-race" else 456},
            "gui_available": True,
            "active": {"hwnd": 456},
            "focus": {"pid": 123},
        }

    monkeypatch.setattr(smoke, "_focus_program", focus)
    monkeypatch.setattr(smoke, "_foreground_snapshot", snapshot)
    observed, lifecycle = {}, {"closed": False}
    if mode == "normal":
        smoke.finish_program(
            native,
            path,
            executable,
            ["owned"],
            lifecycle=lifecycle,
            observed=observed,
            focus_before_close=True,
        )
        assert events == ["observe", "focus", "close", "closed", "observe"]
        assert observed["preclose_focus"]["focus"]["pid"] == 123
    else:
        with pytest.raises((AssertionError, OSError)) as caught:
            smoke.finish_program(
                native,
                path,
                executable,
                ["owned"],
                lifecycle=lifecycle,
                observed=observed,
                focus_before_close=True,
            )
        if mode in ("focus", "partial", "restore", "dual", "trace"):
            assert caught.value is primary
        if mode == "dual":
            assert caught.value.__cause__ is secondary
        if mode == "trace":
            assert observed["postclose_focus_error"]["class"] == "OSError"
    assert lifecycle["closed"] is (mode not in ("partial", "restore", "close", "dual"))
    assert "mapped_focus" in observed
    if mode != "trace":
        assert "postclose_focus" in observed
    if mode in ("partial", "restore", "close", "dual"):
        assert "close_error" in observed


@pytest.mark.parametrize("state", ["focus", "no-foreground", "thread-exited"])
def test_association_focus_snapshot_is_passive_and_keeps_native_error(
    monkeypatch, state
):
    import ctypes

    from tools.windows.visual import input_editor, input_environment
    from tools.windows.visual.input_oracle import GUIThreadInfo

    hwnd = 0 if state == "no-foreground" else 0x10000200
    user = SimpleNamespace(GetForegroundWindow=lambda: hwnd)
    calls = []
    monkeypatch.setattr(ctypes, "set_last_error", lambda _value: None, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1400, raising=False)

    def facts(library, window):
        assert library is user
        calls.append(window)
        return {"hwnd": window, "thread": 88, "pid": 123} if window else {"hwnd": 0}

    monkeypatch.setattr(input_editor, "_window_facts", facts)

    def bind(library, name, result, *arguments):
        assert library is user and name == "GetGUIThreadInfo"
        assert result is ctypes.c_int32
        assert arguments == (ctypes.c_uint32, ctypes.POINTER(GUIThreadInfo))

        def get(thread, value):
            assert thread == 88 and value._obj.size == ctypes.sizeof(GUIThreadInfo)
            assert value._obj.active is None and value._obj.focus is None
            value._obj.active, value._obj.focus, value._obj.flags = hwnd, hwnd + 1, 1
            return state != "thread-exited"

        return get

    monkeypatch.setattr(input_environment, "bind", bind)
    observed = smoke._foreground_snapshot(SimpleNamespace(user=user))
    assert observed["gui_available"] is (state == "focus")
    assert calls == ([hwnd, hwnd, hwnd + 1] if state == "focus" else [hwnd])
    if state == "focus":
        assert observed["active"]["hwnd"] == hwnd
        assert observed["focus"]["hwnd"] == hwnd + 1
    elif state == "thread-exited":
        assert observed["gui_error"] == 1400 and "focus" not in observed


def test_whole_applications_anchor_queries_only_its_borrowed_process_abi(
    monkeypatch, tmp_path
):
    import ctypes

    from docking.platform.native.windows import ffi
    from tools.windows.visual import input_editor

    kernel = object()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kw: kernel, raising=False)
    process = SimpleNamespace(pid=123, _handle=0x12345678)
    editor = SimpleNamespace(process=process, journal={})
    captured = {}

    def create(native, directory, document, *, bits, machine):
        assert native is marker and bits == 64
        assert directory.name == "owned-focus-context"
        assert document.read_text() == ""
        captured["machine"] = machine
        return editor

    marker = object()
    monkeypatch.setattr(input_editor, "OwnedInputEditor", create)
    query = Mock(return_value=0x8664)
    monkeypatch.setattr(ffi, "process_machine", query)
    journal = {}
    anchor = smoke._ApplicationsFocus(marker, tmp_path, journal)
    assert anchor.editor is editor and journal["editor"] is editor.journal
    assert captured["machine"](123) == 0x8664
    query.assert_called_once_with(kernel, process._handle)
    with pytest.raises(AssertionError, match="process handle changed"):
        captured["machine"](999)
    assert query.call_count == 1


@pytest.mark.parametrize(
    "mode",
    ["normal", "replaced", "lost-after-click", "foreign-focus", "partial", "cancel"],
)
def test_whole_domain_anchor_requires_exact_identity_and_native_focus_at_boundaries(
    monkeypatch, mode
):
    from tools.windows.visual import input_editor

    window = {
        "hwnd": 456,
        "pid": 123,
        "class": "OwnedForm",
        "title": "Owned",
        "rect": [0, 0, 520, 300],
    }
    state = {"lost": mode == "replaced"}
    editor = SimpleNamespace(journal={"input_cleanup_safe": True})

    def identity(observed):
        assert observed is window
        assert not state["lost"], "Owned process identity changed"
        return {"window": 456, "pid": 123, "created": 42, "pe_machine": "0x8664"}

    editor.verify_identity = identity
    anchor = smoke._ApplicationsFocus.__new__(smoke._ApplicationsFocus)
    anchor.window, anchor.editor = window, editor
    anchor.journal = {"boundaries": []}
    anchor.native = SimpleNamespace(windows=lambda _pid: [window])

    def wait(predicate, message):
        assert predicate(), message

    anchor.native.wait_for = wait
    primary = (
        KeyboardInterrupt()
        if mode == "cancel"
        else AssertionError("Owned click was incomplete")
    )
    if mode == "partial":
        primary.__dict__["desktop_input_cleanup"] = {
            "attempted": True,
            "completed": True,
        }

    def focus(native, observed, *, owned, journal):
        assert native is anchor.native and observed is window and owned is editor
        assert journal["before"]["created"] == 42
        if mode in ("partial", "cancel"):
            raise primary
        if mode == "lost-after-click":
            state["lost"] = True

    click = Mock(side_effect=focus)
    monkeypatch.setattr(input_editor, "focus_editor", click)
    monkeypatch.setattr(
        smoke,
        "_foreground_snapshot",
        lambda _native: {
            "foreground": {"hwnd": 999 if mode == "foreign-focus" else 456},
            "gui_available": True,
            "active": {"hwnd": 456},
            "focus": {"pid": 123},
        },
    )
    if mode == "normal":
        anchor.focus("association-absence-cleanup")
        assert anchor.journal["boundaries"][0]["after"]["created"] == 42
    else:
        with pytest.raises(KeyboardInterrupt if mode == "cancel" else AssertionError):
            anchor.focus("owned-boundary")
    assert click.call_count == (0 if mode == "replaced" else 1)
    assert editor.journal["input_cleanup_safe"] is (mode != "partial")


@pytest.mark.parametrize(
    "failure", [None, "start", "operation", "cancel", "close", "dual"]
)
def test_whole_domain_anchor_closes_on_failure_and_preserves_original(
    monkeypatch, tmp_path, failure
):
    events = []
    primary = (
        KeyboardInterrupt()
        if failure == "cancel"
        else AssertionError("Owned operation failed")
    )
    secondary = OSError(6, "Owned anchor close failed")

    class Focus:
        def __init__(self, native, output, journal):
            assert native is marker and output == tmp_path

        def start(self):
            events.append("start")
            if failure == "start":
                raise primary

        def close(self):
            events.append("close")
            if failure in ("close", "dual"):
                raise secondary

    monkeypatch.setattr(smoke, "_ApplicationsFocus", Focus)
    marker, journal = object(), {}

    def run():
        with smoke.applications_focus(marker, tmp_path, journal):
            events.append("whole-domain")
            if failure in ("operation", "cancel", "dual"):
                raise primary

    if failure is None:
        run()
    else:
        with pytest.raises(BaseException) as caught:
            run()
        assert caught.value is (secondary if failure == "close" else primary)
        if failure == "dual":
            assert caught.value.__cause__ is secondary
        if failure in ("close", "dual"):
            assert journal["close_error"]["class"] == "OSError"
    assert events == (
        ["start", "close"] if failure == "start" else ["start", "whole-domain", "close"]
    )
