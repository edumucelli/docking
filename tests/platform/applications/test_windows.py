"""Windows application metadata and native launching contracts."""

from dataclasses import replace
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


def test_visibility_updates_hide_components_but_keep_canonical_lookup_and_launch_handles(
    monkeypatch,
):
    main = native_app(app_id="Example.Package!Main", name="Same name")
    widget = native_app(app_id="Example.Package!Widget", name="Same name")
    lookup = SimpleNamespace(
        hidden_ids=frozenset(),
        start=MagicMock(),
        stop=MagicMock(),
        request=MagicMock(),
        invalidate=MagicMock(),
    )
    wiring = {}

    def create_lookup(**kwargs):
        wiring.update(kwargs)
        return lookup

    monkeypatch.setattr(registry_module, "create_visibility_lookup", create_lookup)
    monkeypatch.setattr(registry_module, "is_windows", lambda: True)
    registry = ApplicationRegistry(
        application_source=lambda: (main, widget), desktop_directories_source=lambda: ()
    )
    registry._app_monitor_factory = lambda: None
    timers = []
    registry._schedule_timeout = lambda _delay, callback: (
        timers.append(callback) or len(timers)
    )
    try:
        registry.start()
        assert len(registry.snapshot()) == 2
        lookup.hidden_ids = frozenset(("Example.Package!Widget",))
        wiring["on_changed"]()
        assert len(timers) == 1
        timers.pop()()
        assert [app.desktop_id for app in registry.snapshot()] == [
            "Example.Package!Main"
        ]
        assert registry.get("Example.Package!Widget").visible is False
        assert registry._gio_handle_for("Example.Package!Widget") is widget
        assert registry._gio_handle_for("Example.Package!Main") is main
        lookup.invalidate.assert_not_called()
        registry._on_registry_changed()
        lookup.invalidate.assert_called_once()
        timers.pop()()
    finally:
        registry.stop()
    lookup.start.assert_called_once()
    lookup.stop.assert_called_once()


def test_windows_launch_uses_native_handle_instead_of_posix_exec(monkeypatch):
    handle = native_app()
    app = discover_windows_applications([handle]).applications[0]
    registry = SimpleNamespace(get=lambda _id: app, _gio_handle_for=lambda _id: handle)
    popen = MagicMock(side_effect=AssertionError("Windows commands need native launch"))
    launcher = ApplicationLauncher(registry, LaunchProvenanceStore(), popen=popen)
    monkeypatch.setattr(launcher_module, "is_windows", lambda: True)
    assert launcher.launch(app.desktop_id)
    handle.launch.assert_called_once()
    files, context = handle.launch.call_args.args
    assert files == []
    assert isinstance(context, launcher_module.Gio.AppLaunchContext)
    popen.assert_not_called()


def test_packaged_launch_delegates_foreground_permission_without_double_launch(
    monkeypatch,
):
    from docking.platform.backends.windows import shell

    handle = native_app()
    app = replace(
        discover_windows_applications([handle]).applications[0],
        desktop_id="Example.Package!App",
        executable_path=None,
    )
    registry = SimpleNamespace(get=lambda _id: app, _gio_handle_for=lambda _id: handle)
    native = MagicMock(return_value=False)
    monkeypatch.setattr(launcher_module, "is_windows", lambda: True)
    monkeypatch.setattr(shell, "activate_packaged_application", native)
    launcher = ApplicationLauncher(registry, LaunchProvenanceStore())

    assert not launcher.launch(app.desktop_id)
    native.assert_called_once_with(app.desktop_id)
    handle.launch.assert_not_called()


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
