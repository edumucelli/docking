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

    monkeypatch.setattr(
        ffi,
        "bind",
        lambda _library, name, *_types: (
            query if name == "AssocQueryStringW" else lambda *_args: None
        ),
    )
    native = SimpleNamespace(wait_for=lambda callback, _message: callback())
    fixture = SimpleNamespace(root=tmp_path, executables={"Other": executable})
    report, lifecycle = {"checks": {"association-absence": {}}}, {"closed": True}
    if passes:
        assert smoke.association_checks(
            native, fixture, tmp_path, report, mode="absence", lifecycle=lifecycle
        )["passed"]
    else:
        with pytest.raises(AssertionError, match="Native Shell"):
            smoke.association_checks(
                native, fixture, tmp_path, report, mode="absence", lifecycle=lifecycle
            )
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
    selected = SimpleNamespace(desktop_id=program, has_gio_source=True)
    handle = object()
    fake = SimpleNamespace(
        refresh=Mock(),
        default_for_content_type=lambda extension: (
            selected if extension == ".docking-owned" else None
        ),
        get=lambda identifier: selected if identifier == program else None,
        _gio_handle_for=lambda identifier: handle if identifier == program else None,
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

    monkeypatch.setattr(
        ffi,
        "bind",
        lambda _library, name, *_types: (
            query if name == "AssocQueryStringW" else lambda *_args: None
        ),
    )

    def completed(_native, _facts, actual_executable, argv, *, lifecycle):
        assert actual_executable == executable
        assert len(argv) == 1
        lifecycle["closed"] = True
        return {"argv": argv}

    monkeypatch.setattr(smoke, "finish_program", completed)
    native = SimpleNamespace(wait_for=lambda callback, _message: callback())
    fixture = SimpleNamespace(root=tmp_path, executables={"Other": executable})
    report, lifecycle = {"checks": {"association-explicit": {}}}, {"closed": True}
    result = smoke.association_checks(
        native, fixture, tmp_path, report, mode="explicit", lifecycle=lifecycle
    )
    assert result["passed"] and launch.call_args.args[0] == program
    assert len(launch.call_args.args[1]) == 1
    assert fake.refresh.call_count == 2
    assert report["checks"]["association-explicit"]["observed"] == {
        "resolved": True,
        "registered": True,
        "has_gio_source": True,
    }
    assert registry.keys == before and registry.opened == 0
    assert lifecycle["closed"] and report["associations_restored"]
