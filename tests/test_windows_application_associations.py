"""Owned association fixtures register complete clients and restore their keys."""

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.windows.visual import applications_domain_smoke as smoke


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
    tmp_path, monkeypatch, native_status, passes
):
    import sys

    from docking.platform.applications import registry as registry_module
    from docking.platform.backends.windows import ffi

    registry = Registry(parent=True)
    before = deepcopy(registry.keys)
    executable = tmp_path / "Owned.exe"
    executable.write_bytes(b"owned fixture")
    fake = SimpleNamespace(refresh=Mock())

    def no_handler(content_type):
        assert content_type.startswith(".docking-absent-")
        return

    fake.default_for_content_type = no_handler
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(registry_module, "ApplicationRegistry", lambda: fake)
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
    native = SimpleNamespace(wait_for=lambda callback, _message: callback())
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
    assert registry.keys == before and registry.opened == 0
    assert report["associations_restored"] and lifecycle["closed"]


def test_explicit_association_uses_enumerated_exact_client_and_retained_handle(
    tmp_path, monkeypatch
):
    import ctypes
    import sys

    from docking.platform.applications import launcher as launcher_module
    from docking.platform.applications import registry as registry_module
    from docking.platform.backends.windows import ffi

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
        refresh=Mock(),
        default_for_content_type=lambda extension: (
            selected if dispatched and extension == ".docking-owned" else None
        ),
        get=lambda selected_id: selected if selected_id == identifier else None,
        _gio_handle_for=lambda selected_id: (
            handle if selected_id == identifier else None
        ),
    )
    launch = Mock(return_value=True)
    monkeypatch.setattr(registry_module, "ApplicationRegistry", lambda: fake)
    monkeypatch.setattr(
        launcher_module,
        "ApplicationLauncher",
        lambda *_args: SimpleNamespace(launch_app_uris=launch),
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

    def completed(_native, _facts, actual_executable, argv, *, lifecycle):
        assert actual_executable == executable
        assert len(argv) == 1
        lifecycle["closed"] = True
        return {"argv": argv}

    monkeypatch.setattr(smoke, "finish_program", completed)
    native = SimpleNamespace(wait_for=lambda callback, _message: callback())
    fixture = SimpleNamespace(root=tmp_path, executables={"Other": executable})
    report, lifecycle = {"checks": {"association-explicit": {}}}, {"closed": True}
    result = smoke._registered_association_checks(
        native, fixture, tmp_path, report, mode="explicit", lifecycle=lifecycle
    )
    assert result["passed"] and launch.call_args.args[0] == identifier
    assert len(launch.call_args.args[1]) == 1
    assert fake.refresh.call_count == 2
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
    assert observed["alertable_status"] == 0xC0 and len(dispatched) == 2
    assert observed["native_query"]["flags"] == 0x400
    assert registry.keys == before and registry.opened == 0
    assert lifecycle["closed"] and report["associations_restored"]


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

    from docking.platform.backends.windows import process_job

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
            path.write_text(text)
            archive.writestr(name, text)
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
    assert completed.returncode == 0, completed.stderr
    result = json.loads((tmp_path / "result.json").read_text())
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
