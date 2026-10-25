"""Windows application metadata and native launching contracts."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.platform.applications import launcher as launcher_module
from docking.platform.applications import registry as registry_module
from docking.platform.applications.identity import LaunchProvenanceStore
from docking.platform.applications.launcher import ApplicationLauncher
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.applications.windows import discover_windows_applications


def native_app(*, app_id="Example.App", name="Example", visible=True):
    return SimpleNamespace(
        get_id=lambda: app_id,
        get_display_name=lambda: name,
        get_executable=lambda: r"C:\Program Files\Example\example.exe",
        get_commandline=lambda: r'"C:\Program Files\Example\example.exe" "%1"',
        should_show=lambda: visible,
        launch=MagicMock(return_value=True),
    )


def test_native_metadata_keeps_windows_paths_and_launch_handles():
    handle = native_app()
    result = discover_windows_applications([handle, handle])
    assert len(result.applications) == 1
    app = result.applications[0]
    assert app.desktop_id == "Example.App"
    assert app.wm_class == "example"
    assert "example" in app.aliases
    assert app.exec_line == r'"C:\Program Files\Example\example.exe" "%1"'
    assert result.handles[app.desktop_id] is handle


def test_idless_applications_get_distinct_stable_identities():
    source = [native_app(app_id="", name="One"), native_app(app_id="", name="Two")]
    first = discover_windows_applications(source)
    second = discover_windows_applications(source)
    assert first.presentation_order == second.presentation_order
    assert len(set(first.presentation_order)) == 2


def test_windows_registry_works_without_unix_desktop_app_info(monkeypatch):
    handle = native_app()
    monkeypatch.setattr(registry_module, "is_windows", lambda: True)
    monkeypatch.setattr(
        registry_module,
        "Gio",
        SimpleNamespace(
            AppInfo=SimpleNamespace(get_all=lambda: [handle]),
            AppInfoMonitor=SimpleNamespace(get=lambda: None),
        ),
    )
    registry = ApplicationRegistry()
    assert registry.refresh()
    assert registry.generation == 1
    assert registry.get("Example.App").name == "Example"
    assert registry._gio_handle_for("Example.App") is handle


def test_windows_launch_uses_native_handle_instead_of_posix_exec(monkeypatch):
    handle = native_app()
    app = discover_windows_applications([handle]).applications[0]
    registry = SimpleNamespace(get=lambda _id: app, _gio_handle_for=lambda _id: handle)
    popen = MagicMock(side_effect=AssertionError("Windows commands need native launch"))
    launcher = ApplicationLauncher(registry, LaunchProvenanceStore(), popen=popen)
    monkeypatch.setattr(launcher_module, "is_windows", lambda: True)
    assert launcher.launch(app.desktop_id)
    handle.launch.assert_called_once_with([], None)
    popen.assert_not_called()


def test_windows_process_identity_handles_inaccessible_processes(monkeypatch):
    from docking.platform.applications import windows_process

    def inaccessible(pid):
        raise PermissionError(pid)

    monkeypatch.setattr(
        windows_process,
        "_psutil",
        lambda: SimpleNamespace(
            Process=inaccessible,
            Error=PermissionError,
        ),
    )
    backend = windows_process.WindowsProcessBackend()
    assert backend.executable(123) is None
    assert backend.script(123) is None
    assert backend.steam_app_id(123) is None


def test_windows_script_identity_uses_windows_path_rules(monkeypatch):
    from docking.platform.applications import windows_process

    process = SimpleNamespace(
        cmdline=lambda: [
            r"C:\Program Files\Python\python.exe",
            r"C:\Users\Example\sample.py",
        ]
    )
    monkeypatch.setattr(
        windows_process,
        "_psutil",
        lambda: SimpleNamespace(
            Process=lambda _pid: process,
            Error=PermissionError,
        ),
    )
    assert windows_process.WindowsProcessBackend().script(123) == "sample.py"
