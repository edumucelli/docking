"""Offline Steam discovery and matching, including the TF2 report in #364."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.platform.applications.identity import (
    LaunchProvenanceStore,
    ProcessIdentityService,
)
from docking.platform.applications.launcher import ApplicationLauncher
from docking.platform.applications.matcher import AppIdMatcher
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.applications.running import RunningAppInfo, RunningWindowInfo
from docking.platform.applications.steam import (
    MAX_VDF_BYTES,
    SteamDiscovery,
    default_steam_roots,
    parse_vdf,
    steam_app_id_from_exec,
)
from docking.platform.applications.types import ApplicationOrigin, MatchMethod
from docking.platform.backends.base import WindowId
from docking.platform.backends.x11.impl.window_tracker import WindowMatcher
from tests.platform.test_model import _make_config, _make_model


def _manifest(library, app_id="440", name="Team Fortress 2", directory=None, extra=""):
    directory = directory or name
    apps = library / "steamapps"
    install = apps / "common" / directory
    install.mkdir(parents=True, exist_ok=True)
    path = apps / f"appmanifest_{app_id}.acf"
    path.write_text(
        f'"AppState" {{ "appid" "{app_id}" "name" "{name}" '
        f'"installdir" "{directory}" {extra} }}'
    )
    return install, path


@pytest.fixture
def steam_fixture(tmp_path):
    root = tmp_path / "Steam"
    install, manifest = _manifest(root)
    executable = install / "tf_linux64"
    executable.write_bytes(b"\x7fELF")
    apps = tmp_path / "applications"
    apps.mkdir()
    proc = tmp_path / "proc"
    (proc / "41").mkdir(parents=True)
    source = SteamDiscovery(((root, False),))
    registry = ApplicationRegistry(
        application_source=lambda: (),
        desktop_directories_source=lambda: (apps,),
        steam_source=source,
    )
    registry._desktop_app_info_for_id = lambda _id: None
    registry._desktop_app_info_from_filename = lambda _path: None
    registry.refresh()
    processes = ProcessIdentityService(
        LaunchProvenanceStore(),
        proc_root=proc,
        executable_resolver=lambda _pid: executable,
    )
    return SimpleNamespace(
        root=root,
        install=install,
        manifest=manifest,
        executable=executable,
        apps=apps,
        proc=proc,
        source=source,
        registry=registry,
        processes=processes,
    )


def test_tf2_report_matches_without_shortcut_or_environment(steam_fixture):
    fixture = steam_fixture
    window = SimpleNamespace(
        get_class_group_name=lambda: "tf_linux64",
        get_class_instance_name=lambda: "tf_linux64",
        get_pid=lambda: 41,
    )
    matcher = WindowMatcher(
        application_registry=fixture.registry,
        process_identity_service=fixture.processes,
    )
    result = matcher.match_result(window)
    assert result.desktop_id == "steam-440.desktop"
    assert result.evidence.method is MatchMethod.STEAM_EXECUTABLE
    assert result.application.name == "Team Fortress 2"
    assert result.application.origin is ApplicationOrigin.STEAM
    assert matcher.last_diagnostic.steam_app_id == "440"
    assert fixture.registry.snapshot() == ()
    assert (
        fixture.registry.diagnostic_snapshot().applications[0].steam_manifest
        == fixture.manifest
    )


@pytest.mark.parametrize("flatpak", [False, True])
def test_game_pin_restart_and_launch_use_steam(steam_fixture, flatpak):
    fixture = steam_fixture
    fixture.registry._steam_source = SteamDiscovery(((fixture.root, flatpak),))
    fixture.registry.refresh()
    popen = MagicMock(return_value=SimpleNamespace(pid=77, poll=lambda: None))
    launcher = ApplicationLauncher(
        fixture.registry, LaunchProvenanceStore(), popen=popen
    )
    icons = MagicMock()
    config = _make_config([])

    def model():
        return _make_model(
            config,
            application_registry=fixture.registry,
            application_launcher=launcher,
            icon_loader=icons,
            target_service=MagicMock(),
        )

    dock = model()
    dock.update_running({"steam-440.desktop": RunningAppInfo(count=2, active=True)})
    item = dock.visible_items()[0]
    assert item.name == "Team Fortress 2"
    assert item.instance_count == 2
    assert item.is_active
    assert item.runtime_executable == ""
    icons.load_desktop_icon.assert_called()
    dock.pin_item(item.desktop_id)
    assert config.pinned == ["steam-440.desktop"]
    dock.update_running({})
    assert dock.visible_items()[0].is_pinned
    restored = model()
    assert restored.visible_items()[0].desktop_id == "steam-440.desktop"
    assert launcher.launch("steam-440.desktop")
    expected = ["flatpak", "run", "com.valvesoftware.Steam"] if flatpak else ["steam"]
    assert popen.call_args.args[0] == [*expected, "steam://rungameid/440"]
    assert not list(fixture.apps.glob("*.desktop"))


def test_proton_and_flatpak_identity_precedence(steam_fixture):
    fixture = steam_fixture
    (fixture.apps / "com.valvesoftware.Steam.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Steam\n"
        "Exec=flatpak run com.valvesoftware.Steam\n"
    )
    fixture.registry.refresh()
    process_dir = fixture.proc / "41"
    (process_dir / "environ").write_bytes(
        b"SECRET=private\0SteamAppId=440\0SteamGameId=440\0"
    )
    (process_dir / "root").mkdir()
    (process_dir / "root/.flatpak-info").write_text(
        "[Application]\nname=com.valvesoftware.Steam\n"
    )
    fixture.processes._executable_resolver = lambda _pid: Path("/runtime/wine64")
    matcher = AppIdMatcher(fixture.registry, fixture.processes)
    result = matcher.match_attempt("game.exe", process_id=41)
    assert result.match.desktop_id == "steam-440.desktop"
    assert result.match.evidence.method is MatchMethod.STEAM_APP_ID
    assert result.steam_app_id == "440"
    assert "private" not in repr(result)
    # With no process metadata a Steam-specific WM_CLASS is still sufficient.
    assert matcher.match_result("steam_app_440").desktop_id == "steam-440.desktop"


def test_conflicting_ids_are_not_used_and_unknown_windows_stay_unmatched(steam_fixture):
    fixture = steam_fixture
    _manifest(fixture.root, "570", "Dota 2")
    fixture.registry.refresh()
    (fixture.proc / "41/environ").write_bytes(b"SteamAppId=570\0")
    matcher = AppIdMatcher(fixture.registry, fixture.processes)
    assert matcher.match_result("tf_linux64", process_id=41) is None
    fixture.processes._executable_resolver = lambda _pid: Path("/opt/unrelated")
    (fixture.proc / "41/environ").unlink()
    assert matcher.match_result("unknown", process_id=41) is None


@pytest.mark.parametrize(
    "environment",
    [
        b"SteamAppId=0\0",
        b"SteamAppId=-1\0",
        b"SteamAppId=4294967296\0",
        b"SteamAppId=440\0SteamGameId=570\0",
        b"SteamAppId=\xff\0",
        b"SteamAppId=440\0" + b"x" * 131072,
    ],
)
def test_invalid_or_unreadable_environment_does_not_escape(steam_fixture, environment):
    fixture = steam_fixture
    path = fixture.proc / "41/environ"
    path.write_bytes(environment)
    assert fixture.processes.identity_for_pid(41).steam_app_id is None
    path.unlink()
    assert fixture.processes.identity_for_pid(41).steam_app_id is None


def test_existing_launcher_wins_and_hidden_override_is_respected(steam_fixture):
    fixture = steam_fixture
    custom = fixture.apps / "my-tf2.desktop"
    custom.write_text(
        "[Desktop Entry]\nType=Application\nName=Custom TF2\n"
        "Exec=steam -applaunch 440\nIcon=custom-icon\n"
    )
    fixture.registry.refresh()
    result = AppIdMatcher(fixture.registry, fixture.processes).match_result(
        "tf_linux64", process_id=41
    )
    assert result.desktop_id == "my-tf2.desktop"
    assert result.application.declared_icon == "custom-icon"
    assert fixture.registry.get("steam-440.desktop") is None
    custom.write_text(custom.read_text() + "Hidden=true\n")
    fixture.registry.refresh()
    assert fixture.registry.resolve_steam_app_id("440") is None
    assert fixture.registry.get("steam-440.desktop") is None


def test_library_folders_symlinks_icons_and_corrupt_files(steam_fixture, tmp_path):
    fixture = steam_fixture
    extra = tmp_path / 'Extra "quoted" Library'
    _manifest(extra, "570", "Dota 2")
    escaped = str(extra).replace('"', '\\"')
    (fixture.root / "steamapps/libraryfolders.vdf").write_text(
        f'"libraryfolders" {{ "0" {{ "path" "{fixture.root}" }} "1" {{ "path" "{escaped}" }} }}'
    )
    (fixture.root / "steamapps/appmanifest_999.acf").write_text('"AppState" {')
    cache = fixture.root / "appcache/librarycache/440"
    cache.mkdir(parents=True)
    icon = cache / ("a" * 40 + ".jpg")
    icon.write_bytes(b"image")
    alias = tmp_path / "alias"
    alias.symlink_to(fixture.root, target_is_directory=True)
    source = SteamDiscovery(((fixture.root, False), (alias, False)))
    result = source()
    assert {game.app_id for game in result.games} == {"440", "570"}
    assert len(result.games) == 2
    assert next(game for game in result.games if game.app_id == "440").icon == str(icon)
    assert len(result.issues) == 1
    assert extra / "steamapps" in result.watch_directories
    # Legacy libraryfolders form and icon-cache layout remain supported.
    (fixture.root / "steamapps/libraryfolders.vdf").write_text(
        f'"libraryfolders" {{ "1" "{escaped}" }}'
    )
    icon.unlink()
    legacy = cache.parent / "440_icon.jpg"
    legacy.write_bytes(b"image")
    assert source().games[0].icon == str(legacy)


def test_missing_uninstalled_tools_and_escaping_install_dirs(steam_fixture):
    fixture = steam_fixture
    _manifest(fixture.root, "570", "Uninstalled", extra='"StateFlags" "1"')
    tool, _ = _manifest(fixture.root, "123", "Runtime")
    (tool / "toolmanifest.vdf").write_text("")
    missing, _ = _manifest(fixture.root, "456", "Missing")
    missing.rmdir()
    bad = fixture.root / "steamapps/appmanifest_789.acf"
    bad.write_text('"AppState" { "appid" "789" "name" "Bad" "installdir" "../escape" }')
    result = fixture.source()
    assert [game.app_id for game in result.games] == ["440"]
    assert len(result.issues) == 1
    assert SteamDiscovery(())().games == ()


def test_install_uninstall_and_monitor_cleanup(steam_fixture):
    fixture = steam_fixture
    registry = fixture.registry
    monitors = []
    registry._app_monitor_factory = MagicMock

    def monitor(_path):
        value = MagicMock()
        monitors.append(value)
        return value

    registry._directory_monitor_factory = monitor
    registry.start()
    generation = registry.generation
    _manifest(fixture.root, "570", "Dota 2")
    assert registry.refresh()
    assert registry.generation > generation
    assert registry.get("steam-570.desktop") is not None
    fixture.manifest.unlink()
    assert registry.refresh()
    assert registry.get("steam-440.desktop") is None
    registry.stop()
    for value in monitors:
        value.cancel.assert_called_once()


def test_distinct_game_windows_aggregate_independently(steam_fixture):
    fixture = steam_fixture
    _manifest(fixture.root, "570", "Dota 2")
    fixture.registry.refresh()
    matcher = AppIdMatcher(fixture.registry, fixture.processes)
    groups = {}
    for index, app_id in enumerate(("440", "440", "570"), 1):
        result = matcher.match_result(f"steam_app_{app_id}")
        groups.setdefault(result.desktop_id, []).append(
            RunningWindowInfo(
                desktop_id=result.desktop_id,
                xid=index,
                window_id=WindowId("x11", str(index)),
                active=index == 3,
                urgent=False,
                window=index,
            )
        )
    running = {key: RunningAppInfo.from_windows(rows) for key, rows in groups.items()}
    assert running["steam-440.desktop"].count == 2
    assert running["steam-570.desktop"].count == 1
    assert running["steam-570.desktop"].active


@pytest.mark.parametrize(
    "command,expected",
    [
        ("steam steam://rungameid/440", "440"),
        ("/usr/bin/steam -applaunch 440 -novid", "440"),
        ("flatpak run com.valvesoftware.Steam steam://rungameid/440", "440"),
        ("other steam://rungameid/440", None),
        ("steam steam://rungameid/0", None),
        ('sh -c "steam -applaunch 440"', None),
    ],
)
def test_game_launcher_recognition(command, expected):
    assert steam_app_id_from_exec(command) == expected


@pytest.mark.parametrize(
    "text",
    [
        '"key"',
        '"key" {',
        "}",
        '"unterminated',
        "{",
        '"a" { "b" }',
        '"x" {' * 20 + "}" * 20,
        "x" * (MAX_VDF_BYTES + 1),
    ],
)
def test_invalid_vdf_is_rejected(text):
    with pytest.raises(ValueError):
        parse_vdf(text)


def test_recent_games_follow_existing_setting(steam_fixture):
    fixture = steam_fixture
    config = _make_config([], show_recent_apps=True)
    dock = _make_model(
        config,
        application_registry=fixture.registry,
        application_launcher=MagicMock(),
        icon_loader=MagicMock(),
        target_service=MagicMock(),
    )
    dock.update_running({"steam-440.desktop": RunningAppInfo(count=1)})
    dock.update_running({})
    assert dock.visible_items()[0].is_recent
    assert dock.visible_items()[0].desktop_id == "steam-440.desktop"
    assert fixture.registry.snapshot() == ()


def test_steam_diagnostic_report_is_cached_and_omits_environment(steam_fixture):
    from docking.platform.backends.diagnostics import WindowTrackingDiagnostic
    from docking.platform.diagnostics import (
        collect_diagnostics,
        format_diagnostics_report,
    )
    from tests.platform.test_diagnostics import _Backend

    fixture = steam_fixture
    (fixture.proc / "41/environ").write_bytes(b"SteamAppId=440\0SECRET=private\0")
    matcher = WindowMatcher(
        application_registry=fixture.registry,
        process_identity_service=fixture.processes,
    )
    matcher.match_result(
        SimpleNamespace(
            get_class_group_name=lambda: "tf_linux64",
            get_class_instance_name=lambda: "tf_linux64",
            get_pid=lambda: 41,
        )
    )
    tracking = WindowTrackingDiagnostic(
        status="available",
        windows=(matcher.last_diagnostic,),
        application_discovery=fixture.registry.diagnostic_snapshot(),
    )
    backend = _Backend()
    backend.windows = SimpleNamespace(diagnostic_snapshot=lambda: tracking)
    fixture.registry._steam_source = lambda: pytest.fail("No Steam rediscovery")
    report = format_diagnostics_report(
        collect_diagnostics(
            backend=backend,
            application_registry=fixture.registry,
        )
    )
    assert "- Steam application ID: 440" in report
    assert "appmanifest\\_440.acf" in report
    assert "Steam metadata directory" in report
    assert "private" not in report


def test_unreadable_manifest_does_not_drop_other_games(steam_fixture, monkeypatch):
    fixture = steam_fixture
    _manifest(fixture.root, "570", "Dota 2")
    open_path = Path.open

    def read(path, *args, **kwargs):
        if path == fixture.manifest:
            raise PermissionError("Unreadable manifest")
        return open_path(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", read)
    result = fixture.source()
    assert [game.app_id for game in result.games] == ["570"]
    assert len(result.issues) == 1


def test_native_installation_precedes_duplicate_flatpak(steam_fixture, tmp_path):
    fixture = steam_fixture
    flatpak = tmp_path / "FlatpakSteam"
    _manifest(flatpak)
    fixture.registry._steam_source = SteamDiscovery(
        ((flatpak, True), (fixture.root, False))
    )
    fixture.registry.refresh()
    app = fixture.registry.get("steam-440.desktop")
    assert app.exec_line == "steam steam://rungameid/440"
    assert (
        fixture.registry.resolve_steam_executable(
            flatpak / "steamapps/common/Team Fortress 2/tf_linux64"
        )
        is app
    )


def test_roots_follow_xdg_and_flatpak_layouts(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    roots = default_steam_roots()
    assert (tmp_path / "data/Steam", False) in roots
    assert (
        tmp_path / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        True,
    ) in roots
