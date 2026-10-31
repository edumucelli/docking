"""Windows application metadata and native launching contracts."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

from gi.repository import Gio

from docking.platform.applications import launcher as launcher_module
from docking.platform.applications import registry as registry_module
from docking.platform.applications.identity import LaunchProvenanceStore
from docking.platform.applications.launcher import ApplicationLauncher
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.applications.windows import catalog as windows_catalog_module
from docking.platform.applications.windows.catalog import (
    WindowsApplicationCatalogBackend,
)
from docking.platform.applications.windows.discovery import (
    discover_windows_applications,
    merge_shell_catalog,
)
from docking.platform.applications.windows.launch import WindowsApplicationLaunchBackend
from tests.platform.application_fakes import application


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


def test_shell_export_file_survives_merge_with_a_retained_gio_handle(tmp_path):
    handle = native_app()
    native = replace(
        application("Example.App"),
        export_file=tmp_path / "Example.lnk",
        launch_target=r"shell:AppsFolder\Example.App",
        document_target=r"C:\cache\source.lnk",
    )
    result = merge_shell_catalog(discover_windows_applications((handle,)), (native,))
    assert result.applications[0].export_file == native.export_file
    assert result.applications[0].document_target == native.document_target
    assert result.handles["Example.App"] is handle


def test_selected_shortcut_discards_old_exports_until_its_generation_is_ready(
    tmp_path, monkeypatch
):
    selected = replace(
        application("Example.App"), launch_target=r"C:\private\original.lnk"
    )
    prepared = replace(
        selected,
        document_target=r"C:\cache\catalog-source.lnk",
        export_file=tmp_path / "Original.lnk",
        export_data=(("Native file data", b"original-pidl"),),
    )
    lookup = SimpleNamespace(
        association_revision=0,
        hidden_ids=frozenset(),
        applications=(prepared,),
        invalidate=MagicMock(),
    )
    backend = SimpleNamespace(
        applications=lambda: (selected,),
        create=MagicMock(return_value=selected.desktop_id),
        directories=lambda: (),
    )
    handle = native_app()
    monkeypatch.setattr(
        registry_module, "create_catalog_backend", WindowsApplicationCatalogBackend
    )
    monkeypatch.setattr(registry_module, "create_metadata_lookup", lambda **_kw: lookup)
    registry = ApplicationRegistry(
        application_source=lambda: (handle,), executable_registration=backend
    )
    registry.refresh()
    assert registry.get(selected.desktop_id).export_file == prepared.export_file

    selected = replace(selected, launch_target=r"C:\private\replacement.lnk")
    updated = registry.register_executable(tmp_path / "Selected.lnk")
    assert updated.launch_target == selected.launch_target
    assert not updated.document_target
    assert updated.export_file is None and updated.export_data == ()
    lookup.invalidate.assert_called_once()
    assert registry._gio_handle_for(selected.desktop_id) is handle

    lookup.applications = (
        replace(
            selected,
            export_file=tmp_path / "Replacement.lnk",
            export_data=(("Native file data", b"replacement-pidl"),),
        ),
    )
    registry.refresh()
    updated = registry.get(selected.desktop_id)
    assert updated.export_file == tmp_path / "Replacement.lnk"
    assert updated.export_data == (("Native file data", b"replacement-pidl"),)


def test_empty_native_catalog_publishes_initial_discovery_completion(monkeypatch):
    lookup = SimpleNamespace(
        association_revision=0, hidden_ids=frozenset(), applications=None
    )
    monkeypatch.setattr(registry_module, "create_metadata_lookup", lambda **_kw: lookup)
    monkeypatch.setattr(
        registry_module, "create_catalog_backend", WindowsApplicationCatalogBackend
    )
    registry = ApplicationRegistry(application_source=lambda: ())
    assert registry.initial_discovery_pending
    assert registry.refresh()
    assert registry.initial_discovery_pending
    published = []
    registry.subscribe(lambda: published.append(registry.initial_discovery_pending))

    lookup.applications = ()
    assert registry.refresh()

    assert not registry.initial_discovery_pending
    assert registry.snapshot() == ()
    assert published == [False]
    assert not registry.refresh()
    assert published == [False]


def test_failed_refresh_preserves_published_discovery_readiness(monkeypatch):
    lookup = SimpleNamespace(
        association_revision=0, hidden_ids=frozenset(), applications=None
    )
    monkeypatch.setattr(registry_module, "create_metadata_lookup", lambda **_kw: lookup)
    monkeypatch.setattr(
        registry_module, "create_catalog_backend", WindowsApplicationCatalogBackend
    )
    source = MagicMock(return_value=())
    registry = ApplicationRegistry(application_source=source)
    assert registry.refresh()
    generation = registry.generation

    lookup.applications = ()
    source.side_effect = OSError("Unavailable fixture")
    assert not registry.refresh()

    assert registry.initial_discovery_pending
    assert registry.generation == generation


def test_windows_registry_works_without_unix_desktop_app_info(monkeypatch):
    handle = native_app()
    monkeypatch.setattr(
        registry_module, "create_catalog_backend", WindowsApplicationCatalogBackend
    )
    monkeypatch.setattr(
        registry_module,
        "Gio",
        SimpleNamespace(
            AppInfo=SimpleNamespace(get_all=lambda: [handle]),
            AppInfoMonitor=SimpleNamespace(get=lambda: None),
        ),
    )
    monkeypatch.setattr(windows_catalog_module, "Gio", registry_module.Gio)
    registry = ApplicationRegistry(application_source=lambda: (handle,))
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
        association_revision=0,
        hidden_ids=frozenset(),
        applications=None,
        start=MagicMock(),
        stop=MagicMock(),
        request=MagicMock(),
        invalidate=MagicMock(),
    )
    wiring = {}

    def create_lookup(**kwargs):
        wiring.update(kwargs)
        return lookup

    monkeypatch.setattr(registry_module, "create_metadata_lookup", create_lookup)
    monkeypatch.setattr(
        registry_module, "create_catalog_backend", WindowsApplicationCatalogBackend
    )
    registry = ApplicationRegistry(
        application_source=lambda: (main, widget), desktop_directories_source=lambda: ()
    )
    registry._app_monitor_factory = lambda: None
    registry._change_monitor_factory = lambda _changed: None
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
    monkeypatch.setattr(
        launcher_module, "create_launch_backend", WindowsApplicationLaunchBackend
    )
    launcher = ApplicationLauncher(registry, LaunchProvenanceStore(), popen=popen)
    assert launcher.launch(app.desktop_id)
    handle.launch.assert_called_once()
    files, context = handle.launch.call_args.args
    assert files == []
    assert isinstance(context, Gio.AppLaunchContext)
    popen.assert_not_called()


def test_shell_catalog_keeps_distinct_ids_and_gio_only_association_handles():
    handle = native_app(app_id="Registered.Client", name="Same name")
    result = discover_windows_applications([handle])
    shell_apps = (
        replace(application("Native.One"), name="Same name"),
        replace(application("Native.Two"), name="Same name"),
    )
    merged = merge_shell_catalog(result, shell_apps)
    assert {app.desktop_id for app in merged.applications if app.visible} == {
        "Native.One",
        "Native.Two",
    }
    assert merged.handles["Registered.Client"] is handle
    assert merged.applications[0].desktop_id == "Registered.Client"
    assert not merged.applications[0].visible


def test_shell_identity_updates_gio_metadata_without_losing_its_handle():
    handle = native_app(app_id="Native.One", name="Old name")
    native = replace(
        application("Native.One"),
        name="Localized name",
        launch_target=r"shell:AppsFolder\Native.One",
    )
    merged = merge_shell_catalog(discover_windows_applications([handle]), (native,))
    assert len(merged.applications) == 1
    app = merged.applications[0]
    assert app.name == "Localized name" and app.has_gio_source
    assert app.launch_target == native.launch_target
    assert merged.handles[app.desktop_id] is handle


def test_native_catalog_launcher_needs_no_gio_handle_or_command_parser(monkeypatch):
    from docking.platform.backends.windows import shell

    app = replace(
        application("Native.One"), launch_target=r"shell:AppsFolder\Native.One"
    )
    registry = SimpleNamespace(get=lambda _id: app)
    launch = MagicMock(return_value=True)
    monkeypatch.setattr(
        launcher_module, "create_launch_backend", WindowsApplicationLaunchBackend
    )
    monkeypatch.setattr(shell, "activate_application_target", launch)
    launcher = ApplicationLauncher(registry, LaunchProvenanceStore())
    assert launcher.launch(app.desktop_id)
    launch.assert_called_once_with(app.launch_target)


def test_native_menu_catalog_keeps_association_only_clients_but_hides_components(
    monkeypatch,
):
    client = native_app(app_id="Registered.Client")
    hidden = native_app(app_id="Package!Hidden")
    main = replace(
        application("Native.One"), launch_target=r"shell:AppsFolder\Native.One"
    )
    lookup = SimpleNamespace(
        association_revision=0,
        hidden_ids=frozenset(("Package!Hidden",)),
        applications=(main,),
    )
    monkeypatch.setattr(
        registry_module, "create_catalog_backend", WindowsApplicationCatalogBackend
    )
    monkeypatch.setattr(registry_module, "create_metadata_lookup", lambda **_kw: lookup)
    registry = ApplicationRegistry(application_source=lambda: (client, hidden))
    registry.refresh()
    assert registry.snapshot() == (main,)
    assert registry.get("Registered.Client").visible is False
    assert registry._listing_for_content_type_result(hidden) is None
    listing = registry._listing_for_content_type_result(client)
    assert listing is not None and listing.desktop_id is None
    assert registry._gio_handle_for_unidentified(listing.listing_key) is client
    monkeypatch.setattr(
        registry_module.Gio.AppInfo,
        "get_all_for_type",
        lambda _type: [client, client, hidden],
    )
    listings = registry._association_listings((client, client, hidden))
    assert len(listings) == 1 and listings[0].desktop_id is None


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
    monkeypatch.setattr(
        launcher_module, "create_launch_backend", WindowsApplicationLaunchBackend
    )
    monkeypatch.setattr(shell, "activate_packaged_application", native)
    launcher = ApplicationLauncher(registry, LaunchProvenanceStore())

    assert not launcher.launch(app.desktop_id)
    native.assert_called_once_with(app.desktop_id)
    handle.launch.assert_not_called()


def test_windows_process_identity_handles_inaccessible_processes(monkeypatch):
    from docking.platform.applications.windows import process as windows_process

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
    from docking.platform.applications.windows import process as windows_process

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
