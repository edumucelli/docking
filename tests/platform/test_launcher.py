"""Tests for desktop file resolution."""

import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Mock gi before importing launcher only when PyGObject is unavailable.
try:
    import gi  # noqa: F401
except Exception:
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.core.config import MiddleClickAction
from docking.platform import launcher as launcher_mod
from docking.platform.launcher import (
    DesktopInfo,
    Launcher,
    get_actions,
    launch,
    launch_action,
    launch_new_window,
    open_target,
)


class TestGetDesktopDirs:
    def test_uses_xdg_data_dirs(self, tmp_path):
        # Given
        apps_dir = tmp_path / "share" / "applications"
        apps_dir.mkdir(parents=True)
        # When
        with patch.dict(os.environ, {"XDG_DATA_DIRS": str(tmp_path / "share")}):
            launcher = Launcher()
        # Then
        assert apps_dir in launcher._desktop_dirs

    def test_includes_user_local(self, tmp_path):
        # Given
        user_apps = tmp_path / ".local" / "share" / "applications"
        user_apps.mkdir(parents=True)
        # When
        with patch.dict(
            os.environ, {"XDG_DATA_HOME": str(tmp_path / ".local" / "share")}
        ):
            launcher = Launcher()
        # Then
        assert user_apps in launcher._desktop_dirs

    def test_skips_nonexistent_dirs(self):
        # Given / When
        with patch.dict(os.environ, {"XDG_DATA_DIRS": "/nonexistent/path"}):
            launcher = Launcher()
        # Then
        assert Path("/nonexistent/path/applications") not in launcher._desktop_dirs

    def test_includes_flatpak_host_desktop_dirs(self, tmp_path, monkeypatch):
        # Given
        host_share = tmp_path / "run" / "host" / "usr" / "share"
        host_apps = host_share / "applications"
        host_apps.mkdir(parents=True)
        monkeypatch.setattr(launcher_mod, "HOST_XDG_DATA_DIRS", (str(host_share),))

        # When
        with patch.dict(os.environ, {"XDG_DATA_DIRS": "/nonexistent/path"}):
            launcher = Launcher()

        # Then
        assert host_apps in launcher._desktop_dirs

    def test_includes_snap_desktop_export_dir(self, tmp_path, monkeypatch):
        snap_desktop = tmp_path / "var" / "lib" / "snapd" / "desktop"
        snap_apps = snap_desktop / "applications"
        snap_apps.mkdir(parents=True)
        monkeypatch.setattr(launcher_mod, "HOST_XDG_DATA_DIRS", (str(snap_desktop),))

        with patch.dict(os.environ, {"XDG_DATA_DIRS": "/nonexistent/path"}):
            launcher = Launcher()

        assert snap_apps in launcher._desktop_dirs

    def test_includes_flatpak_host_user_desktop_dir(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        host_user_apps = home / ".local" / "share" / "applications"
        host_user_apps.mkdir(parents=True)
        monkeypatch.setattr(launcher_mod, "is_flatpak", lambda: True)

        with patch.dict(
            os.environ,
            {
                "HOME": str(home),
                "XDG_DATA_HOME": str(tmp_path / "sandbox-data"),
                "XDG_DATA_DIRS": "/nonexistent/path",
            },
        ):
            launcher = Launcher()

        assert host_user_apps in launcher._desktop_dirs


class TestIconCache:
    def test_caches_loaded_icons(self):
        # Given
        launcher = Launcher()
        # When
        icon1 = launcher.load_icon("application-x-executable", 48)
        icon2 = launcher.load_icon("application-x-executable", 48)
        # Then
        assert icon1 is icon2

    def test_different_sizes_cached_separately(self):
        # Given
        launcher = Launcher()
        # When
        launcher.load_icon("application-x-executable", 48)
        launcher.load_icon("application-x-executable", 96)
        # Then
        assert ("application-x-executable", 48) in launcher._icon_cache
        assert ("application-x-executable", 96) in launcher._icon_cache

    def test_default_directory_app_name_returns_display_name(self):
        launcher = Launcher()
        app_info = MagicMock()
        app_info.get_display_name.return_value = "Caja"

        with patch(
            "docking.platform.launcher.Gio.AppInfo.get_default_for_type",
            return_value=app_info,
        ):
            name = launcher.default_directory_app_name()

        assert name == "Caja"

    def test_default_directory_app_name_returns_none_without_default(self):
        launcher = Launcher()

        with patch(
            "docking.platform.launcher.Gio.AppInfo.get_default_for_type",
            return_value=None,
        ):
            name = launcher.default_directory_app_name()

        assert name is None

    def test_resolve_parses_desktop_file_when_gio_rejects_it(self, tmp_path):
        apps_dir = tmp_path / "share" / "applications"
        apps_dir.mkdir(parents=True)
        (apps_dir / "pycharm.desktop").write_text(
            "\n".join(
                [
                    "[Desktop Entry]",
                    "Type=Application",
                    "Name=PyCharm",
                    "Exec=/opt/pycharm/bin/pycharm %f",
                    "Icon=/opt/pycharm/bin/pycharm.svg",
                    "StartupWMClass=jetbrains-pycharm",
                ]
            )
        )

        with (
            patch.dict(
                os.environ,
                {
                    "XDG_DATA_DIRS": str(tmp_path / "share"),
                    "XDG_DATA_HOME": str(tmp_path / "data"),
                },
            ),
            patch.object(launcher_mod, "HOST_XDG_DATA_DIRS", ()),
            patch(
                "docking.platform.launcher.Gio.DesktopAppInfo.new",
                return_value=None,
            ),
            patch(
                "docking.platform.launcher.Gio.DesktopAppInfo.new_from_filename",
                side_effect=TypeError("constructor returned NULL"),
            ),
        ):
            launcher = Launcher()
            info = launcher.resolve("pycharm.desktop")

        assert info == DesktopInfo(
            desktop_id="pycharm.desktop",
            name="PyCharm",
            icon_name="/opt/pycharm/bin/pycharm.svg",
            wm_class="jetbrains-pycharm",
            exec_line="/opt/pycharm/bin/pycharm %f",
        )

    def test_resolve_file_icon_caches_image_thumbnail_by_file_stat(self, monkeypatch):
        from docking.platform import launcher as launcher_mod

        launcher = Launcher()
        pixbuf_cls = MagicMock()
        pixbuf_cls.new_from_file_at_scale.return_value = "thumb"
        monkeypatch.setattr(launcher_mod.GdkPixbuf, "Pixbuf", pixbuf_cls, raising=False)
        monkeypatch.setattr(launcher_mod.Path, "exists", lambda self: True)
        monkeypatch.setattr(
            launcher_mod,
            "normalize_file_target",
            lambda _target: "file:///tmp/photo.png",
        )
        monkeypatch.setattr(
            launcher_mod.Path,
            "stat",
            lambda self: SimpleNamespace(st_mtime_ns=10, st_size=20),
        )

        first = launcher.resolve_file_icon(
            target="file:///tmp/photo.png",
            gicon=None,
            content_type="image/png",
            size=48,
            is_dir=False,
        )
        second = launcher.resolve_file_icon(
            target="file:///tmp/photo.png",
            gicon=None,
            content_type="image/png",
            size=48,
            is_dir=False,
        )

        assert first == "thumb"
        assert second == "thumb"
        pixbuf_cls.new_from_file_at_scale.assert_called_once()

    def test_resolve_file_icon_reloads_thumbnail_when_file_stat_changes(
        self, monkeypatch
    ):
        from docking.platform import launcher as launcher_mod

        launcher = Launcher()
        pixbuf_cls = MagicMock()
        pixbuf_cls.new_from_file_at_scale.side_effect = ["thumb-1", "thumb-2"]
        monkeypatch.setattr(launcher_mod.GdkPixbuf, "Pixbuf", pixbuf_cls, raising=False)
        monkeypatch.setattr(launcher_mod.Path, "exists", lambda self: True)
        monkeypatch.setattr(
            launcher_mod,
            "normalize_file_target",
            lambda _target: "file:///tmp/photo.png",
        )
        stats = iter(
            (
                SimpleNamespace(st_mtime_ns=10, st_size=20),
                SimpleNamespace(st_mtime_ns=11, st_size=20),
                SimpleNamespace(st_mtime_ns=12, st_size=20),
                SimpleNamespace(st_mtime_ns=13, st_size=20),
            )
        )
        monkeypatch.setattr(launcher_mod.Path, "stat", lambda self: next(stats))

        first = launcher.resolve_file_icon(
            target="file:///tmp/photo.png",
            gicon=None,
            content_type="image/png",
            size=48,
            is_dir=False,
        )
        second = launcher.resolve_file_icon(
            target="file:///tmp/photo.png",
            gicon=None,
            content_type="image/png",
            size=48,
            is_dir=False,
        )

        assert first == "thumb-1"
        assert second == "thumb-2"
        assert pixbuf_cls.new_from_file_at_scale.call_count == 2


class TestDesktopActions:
    def test_get_actions_returns_pairs(self):
        # Given a mock DesktopAppInfo with actions
        mock_app = MagicMock()
        mock_app.list_actions.return_value = [
            MiddleClickAction.NEW_WINDOW.value,
            "new-private",
        ]
        mock_app.get_action_name.side_effect = lambda a: {
            "new-window": "New Window",
            "new-private": "New Incognito Window",
        }[a]

        # When
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            actions = get_actions(desktop_id="chrome.desktop")

        # Then
        assert actions == [
            ("new-window", "New Window"),
            ("new-private", "New Incognito Window"),
        ]

    def test_get_actions_returns_empty_for_unknown(self):
        # Given an unknown desktop id
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=None
        ):
            actions = get_actions(desktop_id="nonexistent.desktop")
        # Then
        assert actions == []

    def test_get_actions_skips_empty_names(self):
        # Given an action with no display name
        mock_app = MagicMock()
        mock_app.list_actions.return_value = ["good", "empty"]
        mock_app.get_action_name.side_effect = lambda a: "Good" if a == "good" else ""

        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            actions = get_actions(desktop_id="app.desktop")
        # Then
        assert actions == [("good", "Good")]

    def test_launch_action_calls_gio(self):
        # Given
        mock_app = MagicMock()
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            launch_action(desktop_id="chrome.desktop", action_id="new-window")
        # Then
        mock_app.launch_action.assert_called_once_with(
            MiddleClickAction.NEW_WINDOW.value, None
        )

    @patch("subprocess.Popen")
    def test_launch_action_uses_host_exec_for_host_desktop_file(
        self, popen_mock, tmp_path, monkeypatch
    ):
        host_apps = tmp_path / "run" / "host" / "usr" / "share" / "applications"
        host_apps.mkdir(parents=True)
        desktop_file = host_apps / "browser.desktop"
        desktop_file.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Browser\n"
            "Exec=browser\n"
            "Actions=new-window;\n"
            "\n"
            "[Desktop Action new-window]\n"
            "Name=New Window\n"
            "Exec=browser --new-window\n",
            encoding="utf-8",
        )
        mock_app = MagicMock()
        mock_app.list_actions.return_value = ["new-window"]
        monkeypatch.setattr(
            launcher_mod, "HOST_FILESYSTEM_ROOT", tmp_path / "run" / "host"
        )
        monkeypatch.setattr(launcher_mod, "_get_desktop_dirs", lambda: [host_apps])
        monkeypatch.setattr(
            launcher_mod.flatpak,
            "spawn_path",
            lambda **_: "/usr/bin/flatpak-spawn",
        )
        monkeypatch.setattr(
            launcher_mod.Gio.DesktopAppInfo,
            "new",
            lambda _desktop_id: None,
        )
        monkeypatch.setattr(
            launcher_mod.Gio.DesktopAppInfo,
            "new_from_filename",
            lambda _path: mock_app,
        )

        launch_action(desktop_id="browser.desktop", action_id="new-window")

        mock_app.launch_action.assert_not_called()
        args, _kwargs = popen_mock.call_args
        assert args[0][-2:] == ["browser", "--new-window"]

    def test_get_actions_returns_empty_when_gio_raises(self, monkeypatch):
        # Given / When
        from docking.platform import launcher as launcher_mod

        monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new",
            side_effect=TypeError,
        ):
            actions = get_actions(desktop_id="broken.desktop")
        # Then
        assert actions == []

    def test_get_actions_reads_host_desktop_file_when_id_lookup_fails(
        self, tmp_path, monkeypatch
    ):
        host_apps = tmp_path / "run" / "host" / "usr" / "share" / "applications"
        host_apps.mkdir(parents=True)
        desktop_file = host_apps / "org.gnome.FileRoller.desktop"
        desktop_file.write_text("[Desktop Entry]\nType=Application\n")

        mock_app = MagicMock()
        mock_app.list_actions.return_value = ["extract-here"]
        mock_app.get_action_name.return_value = "Extract Here"
        monkeypatch.setattr(launcher_mod, "_get_desktop_dirs", lambda: [host_apps])
        monkeypatch.setattr(
            launcher_mod.Gio.DesktopAppInfo,
            "new",
            lambda _desktop_id: None,
        )
        new_from_filename = MagicMock(return_value=mock_app)
        monkeypatch.setattr(
            launcher_mod.Gio.DesktopAppInfo,
            "new_from_filename",
            new_from_filename,
        )

        actions = get_actions(desktop_id="org.gnome.FileRoller.desktop")

        assert actions == [("extract-here", "Extract Here")]
        new_from_filename.assert_called_once_with(str(desktop_file))

    def test_get_actions_parses_host_desktop_file_when_gio_filename_lookup_fails(
        self, tmp_path, monkeypatch, caplog
    ):
        host_apps = tmp_path / "run" / "host" / "usr" / "share" / "applications"
        host_apps.mkdir(parents=True)
        desktop_file = host_apps / "org.gnome.FileRoller.desktop"
        desktop_file.write_text(
            "\n".join(
                [
                    "[Desktop Entry]",
                    "Type=Application",
                    "Name=Archive Manager",
                    "Exec=file-roller %U",
                    "Actions=extract-here;",
                    "",
                    "[Desktop Action extract-here]",
                    "Name=Extract Here",
                    "Exec=file-roller --extract-here %U",
                ]
            )
        )

        monkeypatch.setattr(launcher_mod, "_get_desktop_dirs", lambda: [host_apps])
        monkeypatch.setattr(launcher_mod.Gio.DesktopAppInfo, "new", lambda _id: None)
        monkeypatch.setattr(
            launcher_mod.Gio.DesktopAppInfo,
            "new_from_filename",
            lambda _path: None,
        )

        with caplog.at_level(logging.WARNING, logger="docking.launcher"):
            actions = get_actions(desktop_id="org.gnome.FileRoller.desktop")

        assert actions == [("extract-here", "Extract Here")]
        assert "constructor returned NULL" not in caplog.text

    def test_launch_action_ignores_gio_errors(self, monkeypatch):
        # Given
        from docking.platform import launcher as launcher_mod

        monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)
        mock_app = MagicMock()
        mock_app.launch_action.side_effect = RuntimeError("gio fail")

        # When
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            launch_action(desktop_id="chrome.desktop", action_id="new-window")

        # Then
        mock_app.launch_action.assert_called_once_with("new-window", None)


class TestResolve:
    def test_resolve_uses_gio_desktop_info_directly(self):
        # Given
        launcher = Launcher()
        icon = MagicMock()
        icon.to_string.return_value = "firefox"
        app = MagicMock()
        app.get_startup_wm_class.return_value = "Firefox"
        app.get_commandline.return_value = "/usr/bin/firefox %U"
        app.get_icon.return_value = icon
        app.get_display_name.return_value = "Firefox"

        # When
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=app
        ):
            info = launcher.resolve("firefox.desktop")

        # Then
        assert info is not None
        assert info.wm_class == "Firefox"
        assert info.icon_name == "firefox"
        assert info.name == "Firefox"

    def test_resolve_falls_back_to_filename_and_executable_name(self, tmp_path):
        # Given
        apps_dir = tmp_path / "applications"
        apps_dir.mkdir()
        desktop_file = apps_dir / "code.desktop"
        desktop_file.write_text("[Desktop Entry]\nName=Code\n")

        launcher = Launcher()
        launcher._desktop_dirs = [apps_dir]

        app = MagicMock()
        app.get_startup_wm_class.return_value = ""
        app.get_commandline.return_value = "/usr/bin/code %F"
        app.get_icon.return_value = None
        app.get_display_name.return_value = ""

        # When
        with (
            patch(
                "docking.platform.launcher.Gio.DesktopAppInfo.new",
                return_value=None,
            ),
            patch(
                "docking.platform.launcher.Gio.DesktopAppInfo.new_from_filename",
                return_value=app,
            ),
        ):
            info = launcher.resolve("code.desktop")

        # Then
        assert info is not None
        assert info.wm_class == "code"
        assert info.icon_name == "application-x-executable"
        assert info.name == "code.desktop"

    def test_resolve_only_falls_back_when_gio_returns_none(self, tmp_path):
        apps_dir = tmp_path / "applications"
        apps_dir.mkdir()
        (apps_dir / "firefox.desktop").write_text("[Desktop Entry]\nName=Fallback\n")
        launcher = Launcher()
        launcher._desktop_dirs = [apps_dir]

        app = MagicMock()
        app.__bool__.return_value = False
        app.get_startup_wm_class.return_value = "Firefox"
        app.get_commandline.return_value = "/usr/bin/firefox %U"
        app.get_icon.return_value = None
        app.get_display_name.return_value = "Firefox"

        with (
            patch(
                "docking.platform.launcher.Gio.DesktopAppInfo.new",
                return_value=app,
            ),
            patch(
                "docking.platform.launcher.Gio.DesktopAppInfo.new_from_filename"
            ) as from_filename,
        ):
            info = launcher.resolve("firefox.desktop")

        assert info is not None
        assert info.name == "Firefox"
        from_filename.assert_not_called()

    def test_resolve_returns_none_when_lookups_fail(self, monkeypatch):
        # Given
        from docking.platform import launcher as launcher_mod

        monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)
        launcher = Launcher()
        launcher._desktop_dirs = []

        # When
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new",
            side_effect=TypeError,
        ):
            info = launcher.resolve("missing.desktop")

        # Then
        assert info is None

    def test_resolve_by_wm_class_indexes_fallback_exec_alias(self, tmp_path):
        apps_dir = tmp_path / "applications"
        apps_dir.mkdir()
        (apps_dir / "org.gnome.Calculator.desktop").write_text("[Desktop Entry]\n")

        launcher = Launcher()
        launcher._desktop_dirs = [apps_dir]
        launcher.resolve = MagicMock(
            side_effect=lambda desktop_id, **_kwargs: (
                DesktopInfo(
                    desktop_id="org.gnome.Calculator.desktop",
                    name="Calculator",
                    icon_name="org.gnome.Calculator",
                    wm_class="gnome-calculator",
                    exec_line="gnome-calculator",
                )
                if desktop_id == "org.gnome.Calculator.desktop"
                else None
            )
        )

        info = launcher.resolve_by_wm_class("gnome-calculator")

        assert info is not None
        assert info.desktop_id == "org.gnome.Calculator.desktop"

    def test_resolve_file_uses_image_thumbnail_for_images(self, monkeypatch):
        from docking.platform import launcher as launcher_mod

        launcher = Launcher()
        gicon = MagicMock()
        info = MagicMock()
        info.get_icon.return_value = gicon
        info.get_file_type.return_value = launcher_mod.Gio.FileType.REGULAR
        info.get_display_name.return_value = "Photo"
        info.get_content_type.return_value = "image/png"
        gfile = MagicMock()
        gfile.query_info.return_value = info
        monkeypatch.setattr(launcher_mod.Gio.File, "new_for_uri", lambda _uri: gfile)

        thumb = object()
        pixbuf_cls = MagicMock()
        pixbuf_cls.new_from_file_at_scale.return_value = thumb
        monkeypatch.setattr(launcher_mod.GdkPixbuf, "Pixbuf", pixbuf_cls, raising=False)
        monkeypatch.setattr(launcher_mod.Path, "exists", lambda self: True)
        monkeypatch.setattr(
            launcher_mod.Path,
            "stat",
            lambda self: SimpleNamespace(st_mtime_ns=10, st_size=20),
        )
        launcher.load_gicon = MagicMock(return_value="gicon-pixbuf")
        launcher.load_icon = MagicMock(return_value="fallback-pixbuf")

        resolved = launcher.resolve_file("/tmp/photo.png", 48)

        assert resolved is not None
        assert resolved.icon is thumb
        launcher.load_gicon.assert_not_called()
        launcher.load_icon.assert_not_called()


class TestTryLoadIcon:
    def test_loads_icon_from_absolute_path(self, monkeypatch):
        # Given
        from docking.platform import launcher as launcher_mod

        launcher = Launcher()
        theme = MagicMock()
        monkeypatch.setattr(
            launcher_mod.Gtk.IconTheme, "get_default", lambda: theme, raising=False
        )
        monkeypatch.setattr(launcher_mod.Path, "is_absolute", lambda self: True)
        monkeypatch.setattr(launcher_mod.Path, "exists", lambda self: True)

        pix = object()
        pixbuf_cls = MagicMock()
        pixbuf_cls.new_from_file_at_scale.return_value = pix
        monkeypatch.setattr(launcher_mod.GdkPixbuf, "Pixbuf", pixbuf_cls, raising=False)

        # When
        out = launcher._try_load_icon("/tmp/icon.png", 48)

        # Then
        assert out is pix
        theme.lookup_icon.assert_not_called()

    def test_loads_flatpak_host_icon_for_host_desktop_absolute_path(self, monkeypatch):
        # Given
        from docking.platform import launcher as launcher_mod

        launcher = Launcher()
        theme = MagicMock()
        theme.get_search_path.return_value = []
        monkeypatch.setattr(
            launcher_mod.Gtk.IconTheme, "get_default", lambda: theme, raising=False
        )
        monkeypatch.setattr(
            launcher_mod.Path,
            "exists",
            lambda self: str(self) == "/run/host/opt/app/icon.png",
        )

        pix = object()
        pixbuf_cls = MagicMock()
        pixbuf_cls.new_from_file_at_scale.return_value = pix
        monkeypatch.setattr(launcher_mod.GdkPixbuf, "Pixbuf", pixbuf_cls, raising=False)

        # When
        out = launcher._try_load_icon("/opt/app/icon.png", 48)

        # Then
        assert out is pix
        pixbuf_cls.new_from_file_at_scale.assert_called_once_with(
            "/run/host/opt/app/icon.png",
            48,
            48,
            True,
        )
        theme.lookup_icon.assert_not_called()

    def test_loads_host_pixmap_icon_when_desktop_icon_includes_extension(
        self, tmp_path, monkeypatch
    ):
        from docking.platform import launcher as launcher_mod

        host_share = tmp_path / "run" / "host" / "usr" / "share"
        pixmaps = host_share / "pixmaps"
        pixmaps.mkdir(parents=True)
        icon_file = pixmaps / "acvc-64.png"
        icon_file.write_bytes(b"fake")
        monkeypatch.setattr(launcher_mod, "HOST_PIXMAP_DIRS", (str(pixmaps),))
        monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)

        launcher = Launcher()
        theme = MagicMock()
        theme.get_search_path.return_value = []
        icon_info = MagicMock()
        icon_info.load_icon.return_value = "pixbuf"
        theme.lookup_icon.side_effect = [None, icon_info]
        monkeypatch.setattr(
            launcher_mod.Gtk.IconTheme, "get_default", lambda: theme, raising=False
        )

        out = launcher._try_load_icon("acvc-64.png", 48)

        assert out == "pixbuf"
        theme.append_search_path.assert_called_once_with(str(pixmaps))
        assert [call.args[0] for call in theme.lookup_icon.call_args_list] == [
            "acvc-64.png",
            "acvc-64",
        ]

    def test_loads_host_pixmap_icon_for_extensionless_name(self, tmp_path, monkeypatch):
        from docking.platform import launcher as launcher_mod

        host_share = tmp_path / "run" / "host" / "usr" / "share"
        pixmaps = host_share / "pixmaps"
        pixmaps.mkdir(parents=True)
        icon_file = pixmaps / "mongodb-compass.png"
        icon_file.write_bytes(b"fake")
        monkeypatch.setattr(launcher_mod, "HOST_PIXMAP_DIRS", (str(pixmaps),))
        monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)

        launcher = Launcher()
        theme = MagicMock()
        theme.get_search_path.return_value = []
        icon_info = MagicMock()
        icon_info.load_icon.return_value = "pixbuf"
        theme.lookup_icon.return_value = icon_info
        monkeypatch.setattr(
            launcher_mod.Gtk.IconTheme, "get_default", lambda: theme, raising=False
        )

        out = launcher._try_load_icon("mongodb-compass", 48)

        assert out == "pixbuf"
        theme.append_search_path.assert_called_once_with(str(pixmaps))
        theme.lookup_icon.assert_called_once_with(
            "mongodb-compass",
            48,
            launcher_mod.Gtk.IconLookupFlags.FORCE_SIZE,
        )

    def test_uses_gnome_legacy_icon_alias_before_fallback(self, monkeypatch):
        # Given
        from docking.platform import launcher as launcher_mod

        launcher = Launcher()
        monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)
        monkeypatch.setattr(launcher_mod.Path, "exists", lambda self: False)

        theme = MagicMock()
        theme.get_search_path.return_value = []
        mock_info = MagicMock()
        mock_info.load_icon.return_value = "calculator-pixbuf"
        theme.lookup_icon.side_effect = [None, mock_info]
        monkeypatch.setattr(
            launcher_mod.Gtk.IconTheme, "get_default", lambda: theme, raising=False
        )

        # When
        out = launcher._try_load_icon("org.gnome.Calculator", 48)

        # Then
        assert out == "calculator-pixbuf"
        assert theme.lookup_icon.call_args_list[0].args[0] == "org.gnome.Calculator"
        assert theme.lookup_icon.call_args_list[1].args[0] == "gnome-calculator"

    def test_load_desktop_icon_uses_exec_basename_before_fallback(self, monkeypatch):
        from docking.platform import launcher as launcher_mod

        launcher = Launcher()
        monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)
        monkeypatch.setattr(launcher_mod.Path, "exists", lambda self: False)

        theme = MagicMock()
        theme.get_search_path.return_value = []
        mock_info = MagicMock()
        mock_info.load_icon.return_value = "file-roller-pixbuf"
        theme.lookup_icon.side_effect = [
            None,
            None,
            None,
            mock_info,
        ]
        monkeypatch.setattr(
            launcher_mod.Gtk.IconTheme, "get_default", lambda: theme, raising=False
        )
        info = DesktopInfo(
            desktop_id="org.gnome.FileRoller.desktop",
            name="Archive Manager",
            icon_name="org.gnome.ArchiveManager",
            wm_class="file-roller",
            exec_line="file-roller %U",
        )

        out = launcher.load_desktop_icon(info, 48)

        assert out == "file-roller-pixbuf"
        assert [call.args[0] for call in theme.lookup_icon.call_args_list] == [
            "org.gnome.ArchiveManager",
            "gnome-archivemanager",
            "archivemanager",
            "file-roller",
        ]

    def test_uses_theme_fallback_when_primary_icon_lookup_fails(self, monkeypatch):
        # Given
        from docking.platform import launcher as launcher_mod

        launcher = Launcher()
        monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)

        theme = MagicMock()
        mock_info = MagicMock()
        mock_info.load_icon.return_value = "fallback-pixbuf"
        theme.lookup_icon.side_effect = [None, mock_info]
        monkeypatch.setattr(
            launcher_mod.Gtk.IconTheme, "get_default", lambda: theme, raising=False
        )
        monkeypatch.setattr(launcher_mod.os.path, "isabs", lambda p: False)

        # When
        out = launcher._try_load_icon("missing-icon", 48)

        # Then
        assert out == "fallback-pixbuf"
        assert theme.lookup_icon.call_count == 2

    def test_returns_none_when_all_icon_lookups_fail(self, monkeypatch):
        # Given
        from docking.platform import launcher as launcher_mod

        launcher = Launcher()
        monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)

        theme = MagicMock()
        theme.lookup_icon.return_value = None
        monkeypatch.setattr(
            launcher_mod.Gtk.IconTheme, "get_default", lambda: theme, raising=False
        )
        monkeypatch.setattr(launcher_mod.os.path, "isabs", lambda p: False)

        # When
        out = launcher._try_load_icon("missing-icon", 48)

        # Then
        assert out is None

    def test_load_gicon_uses_lookup_by_gicon_when_available(self, monkeypatch):
        from docking.platform import launcher as launcher_mod

        launcher = Launcher()
        icon_info = MagicMock()
        icon_info.load_icon.return_value = "pixbuf"
        theme = MagicMock()
        theme.lookup_by_gicon.return_value = icon_info
        monkeypatch.setattr(
            launcher_mod.Gtk.IconTheme, "get_default", lambda: theme, raising=False
        )
        gicon = MagicMock()
        gicon.to_string.return_value = "folder"

        out = launcher.load_gicon(gicon, 48)

        assert out == "pixbuf"
        theme.lookup_by_gicon.assert_called_once()

    def test_resolve_file_prefers_gicon_then_falls_back(self, monkeypatch):
        from docking.platform import launcher as launcher_mod

        launcher = Launcher()
        gicon = MagicMock()
        gicon.to_string.return_value = "folder"
        info = MagicMock()
        info.get_icon.return_value = gicon
        info.get_file_type.return_value = launcher_mod.Gio.FileType.DIRECTORY
        info.get_display_name.return_value = "Docs"
        gfile = MagicMock()
        gfile.query_info.return_value = info
        monkeypatch.setattr(launcher_mod.Gio.File, "new_for_uri", lambda _uri: gfile)
        launcher.load_gicon = MagicMock(return_value="gicon-pixbuf")
        launcher.load_icon = MagicMock(return_value="fallback-pixbuf")

        resolved = launcher.resolve_file("file:///tmp/docs", 48)

        assert resolved is not None
        assert resolved.icon == "gicon-pixbuf"
        launcher.load_gicon.assert_called_once_with(gicon=gicon, size=48)


class TestLaunchNewWindow:
    def test_launch_new_window_prefers_desktop_action(self):
        mock_app = MagicMock()
        mock_app.list_actions.return_value = ["new-window", "new-private"]
        with (
            patch(
                "docking.platform.launcher.Gio.DesktopAppInfo.new",
                return_value=mock_app,
            ),
            patch("docking.platform.launcher.launch") as launch_mock,
        ):
            launch_new_window(desktop_id="sublime_text.desktop")

        mock_app.launch_action.assert_called_once_with("new-window", None)
        launch_mock.assert_not_called()

    def test_launch_new_window_falls_back_without_action(self):
        mock_app = MagicMock()
        mock_app.list_actions.return_value = ["new-private"]
        with (
            patch(
                "docking.platform.launcher.Gio.DesktopAppInfo.new",
                return_value=mock_app,
            ),
            patch("docking.platform.launcher.launch") as launch_mock,
        ):
            launch_new_window(desktop_id="app.desktop")

        mock_app.launch_action.assert_not_called()
        launch_mock.assert_called_once_with(desktop_id="app.desktop")

    def test_launch_new_window_falls_back_when_desktop_missing(self):
        with (
            patch(
                "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=None
            ),
            patch("docking.platform.launcher.launch") as launch_mock,
        ):
            launch_new_window(desktop_id="missing.desktop")

        launch_mock.assert_called_once_with(desktop_id="missing.desktop")


class TestOpenTarget:
    def test_open_target_accepts_https_url(self):
        with patch(
            "docking.platform.launcher.Gio.AppInfo.launch_default_for_uri"
        ) as launch_mock:
            assert open_target("https://github.com/edumucelli/docking/issues") is True

        launch_mock.assert_called_once_with(
            "https://github.com/edumucelli/docking/issues", None
        )

    def test_open_target_normalizes_local_path(self, tmp_path):
        target = tmp_path / "example.txt"
        target.write_text("hello")

        with patch(
            "docking.platform.launcher.Gio.AppInfo.launch_default_for_uri"
        ) as launch_mock:
            assert open_target(str(target)) is True

        launch_mock.assert_called_once_with(target.resolve().as_uri(), None)

    def test_open_target_uses_host_gio_for_local_file_in_flatpak(
        self, tmp_path, monkeypatch
    ):
        target = tmp_path / "example.txt"
        target.write_text("hello")
        monkeypatch.setattr(launcher_mod, "is_flatpak", lambda: True)
        monkeypatch.setattr(
            launcher_mod.flatpak,
            "spawn_path",
            lambda **_: "/usr/bin/flatpak-spawn",
        )

        with (
            patch("docking.platform.launcher.subprocess.Popen") as popen_mock,
            patch(
                "docking.platform.launcher.Gio.AppInfo.launch_default_for_uri"
            ) as launch_mock,
        ):
            assert open_target(str(target)) is True

        popen_mock.assert_called_once()
        args, kwargs = popen_mock.call_args
        assert args[0] == [
            "/usr/bin/flatpak-spawn",
            "--host",
            "env",
            "-u",
            "GIO_USE_VFS",
            "-u",
            "GI_TYPELIB_PATH",
            "-u",
            "GSETTINGS_SCHEMA_DIR",
            "-u",
            "XDG_DATA_DIRS",
            "gio",
            "open",
            target.resolve().as_uri(),
        ]
        assert kwargs["start_new_session"] is True
        launch_mock.assert_not_called()

    def test_open_target_returns_false_for_unsupported_scheme(self):
        with patch(
            "docking.platform.launcher.Gio.AppInfo.launch_default_for_uri"
        ) as launch_mock:
            assert open_target("mailto:test@example.com") is False

        launch_mock.assert_not_called()


class TestLaunch:
    @patch("subprocess.Popen")
    def test_launch_uses_shell_false_and_new_session(self, popen_mock):
        mock_app = MagicMock()
        mock_app.get_commandline.return_value = 'firefox --new-window "%u"'
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            launch(desktop_id="firefox.desktop")

        popen_mock.assert_called_once()
        args, kwargs = popen_mock.call_args
        assert args[0] == ["firefox", "--new-window"]
        assert kwargs["shell"] is False
        assert kwargs["start_new_session"] is True

    @patch("subprocess.Popen")
    def test_launch_returns_when_desktop_missing(self, popen_mock):
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=None
        ):
            launch(desktop_id="missing.desktop")
        popen_mock.assert_not_called()

    @patch("subprocess.Popen")
    def test_launch_uses_host_spawn_for_host_desktop_file(
        self, popen_mock, tmp_path, monkeypatch
    ):
        host_apps = tmp_path / "run" / "host" / "usr" / "share" / "applications"
        host_apps.mkdir(parents=True)
        desktop_file = host_apps / "org.gnome.FileRoller.desktop"
        desktop_file.write_text("[Desktop Entry]\nType=Application\n")

        mock_app = MagicMock()
        mock_app.get_commandline.return_value = "file-roller %U"
        monkeypatch.setattr(
            launcher_mod, "HOST_FILESYSTEM_ROOT", tmp_path / "run" / "host"
        )
        monkeypatch.setattr(
            launcher_mod.flatpak,
            "spawn_path",
            lambda **_: "/usr/bin/flatpak-spawn",
        )
        monkeypatch.setattr(launcher_mod, "_get_desktop_dirs", lambda: [host_apps])
        monkeypatch.setattr(
            launcher_mod.Gio.DesktopAppInfo,
            "new",
            lambda _desktop_id: None,
        )
        monkeypatch.setattr(
            launcher_mod.Gio.DesktopAppInfo,
            "new_from_filename",
            lambda _path: mock_app,
        )

        launch(desktop_id="org.gnome.FileRoller.desktop")

        popen_mock.assert_called_once()
        args, kwargs = popen_mock.call_args
        assert args[0] == [
            "/usr/bin/flatpak-spawn",
            "--host",
            "env",
            "-u",
            "GIO_USE_VFS",
            "-u",
            "GI_TYPELIB_PATH",
            "-u",
            "GSETTINGS_SCHEMA_DIR",
            "-u",
            "XDG_DATA_DIRS",
            "file-roller",
        ]
        assert kwargs["shell"] is False

    @patch("subprocess.Popen")
    def test_launch_uses_host_spawn_for_flatpak_host_user_desktop_file(
        self, popen_mock, tmp_path, monkeypatch
    ):
        home = tmp_path / "home"
        host_apps = home / ".local" / "share" / "applications"
        host_apps.mkdir(parents=True)
        (host_apps / "org.example.UserApp.desktop").write_text(
            "[Desktop Entry]\nType=Application\n"
        )

        mock_app = MagicMock()
        mock_app.get_commandline.return_value = "user-app %U"
        monkeypatch.setattr(launcher_mod, "is_flatpak", lambda: True)
        monkeypatch.setattr(
            launcher_mod.flatpak,
            "spawn_path",
            lambda **_: "/usr/bin/flatpak-spawn",
        )
        monkeypatch.setattr(
            launcher_mod.Gio.DesktopAppInfo,
            "new",
            lambda _desktop_id: mock_app,
        )

        with patch.dict(
            os.environ,
            {
                "HOME": str(home),
                "XDG_DATA_HOME": str(tmp_path / "sandbox-data"),
                "XDG_DATA_DIRS": "/nonexistent/path",
            },
        ):
            launch(desktop_id="org.example.UserApp.desktop")

        popen_mock.assert_called_once()
        args, kwargs = popen_mock.call_args
        assert args[0] == [
            "/usr/bin/flatpak-spawn",
            "--host",
            "env",
            "-u",
            "GIO_USE_VFS",
            "-u",
            "GI_TYPELIB_PATH",
            "-u",
            "GSETTINGS_SCHEMA_DIR",
            "-u",
            "XDG_DATA_DIRS",
            "user-app",
        ]
        assert kwargs["shell"] is False

    @patch("subprocess.Popen")
    def test_launch_parses_host_desktop_file_when_gio_filename_lookup_fails(
        self, popen_mock, tmp_path, monkeypatch, caplog
    ):
        host_apps = tmp_path / "run" / "host" / "usr" / "share" / "applications"
        host_apps.mkdir(parents=True)
        desktop_file = host_apps / "org.gnome.FileRoller.desktop"
        desktop_file.write_text(
            "\n".join(
                [
                    "[Desktop Entry]",
                    "Type=Application",
                    "Name=Archive Manager",
                    "Exec=file-roller %U",
                ]
            )
        )

        monkeypatch.setattr(
            launcher_mod, "HOST_FILESYSTEM_ROOT", tmp_path / "run" / "host"
        )
        monkeypatch.setattr(
            launcher_mod.flatpak,
            "spawn_path",
            lambda **_: "/usr/bin/flatpak-spawn",
        )
        monkeypatch.setattr(launcher_mod, "_get_desktop_dirs", lambda: [host_apps])
        monkeypatch.setattr(launcher_mod.Gio.DesktopAppInfo, "new", lambda _id: None)
        monkeypatch.setattr(
            launcher_mod.Gio.DesktopAppInfo,
            "new_from_filename",
            lambda _path: None,
        )

        with caplog.at_level(logging.WARNING, logger="docking.launcher"):
            launch(desktop_id="org.gnome.FileRoller.desktop")

        popen_mock.assert_called_once()
        args, kwargs = popen_mock.call_args
        assert args[0] == [
            "/usr/bin/flatpak-spawn",
            "--host",
            "env",
            "-u",
            "GIO_USE_VFS",
            "-u",
            "GI_TYPELIB_PATH",
            "-u",
            "GSETTINGS_SCHEMA_DIR",
            "-u",
            "XDG_DATA_DIRS",
            "file-roller",
        ]
        assert kwargs["shell"] is False
        assert "constructor returned NULL" not in caplog.text

    @patch("subprocess.Popen")
    def test_launch_returns_when_desktop_constructor_raises(
        self, popen_mock, monkeypatch
    ):
        monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)
        monkeypatch.setattr(
            launcher_mod.Gio.DesktopAppInfo,
            "new",
            MagicMock(side_effect=TypeError("constructor returned NULL")),
        )
        monkeypatch.setattr(launcher_mod, "_get_desktop_dirs", list)

        launch(desktop_id="missing.desktop")

        popen_mock.assert_not_called()

    @patch("subprocess.Popen")
    def test_launch_returns_when_commandline_missing(self, popen_mock):
        mock_app = MagicMock()
        mock_app.get_commandline.return_value = ""
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            launch(desktop_id="foo.desktop")
        popen_mock.assert_not_called()

    @patch("subprocess.Popen")
    def test_launch_handles_bad_exec_parse(self, popen_mock, caplog):
        mock_app = MagicMock()
        mock_app.get_commandline.return_value = 'foo "unterminated'
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            launch(desktop_id="bad.desktop")
        popen_mock.assert_not_called()
        assert "Failed to parse launch command for bad.desktop" in caplog.text

    @patch("subprocess.Popen")
    def test_launch_returns_when_command_becomes_empty_after_field_codes(
        self, popen_mock
    ):
        # Given
        mock_app = MagicMock()
        mock_app.get_commandline.return_value = "%U"
        # When
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            launch(desktop_id="empty.desktop")
        # Then
        popen_mock.assert_not_called()

    @patch("subprocess.Popen", side_effect=OSError("boom"))
    def test_launch_prints_when_spawn_fails(self, _popen_mock, caplog):
        # Given
        mock_app = MagicMock()
        mock_app.get_commandline.return_value = "firefox"
        # When
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new",
            return_value=mock_app,
        ):
            launch(desktop_id="firefox.desktop")
        # Then
        assert "Failed to launch firefox.desktop" in caplog.text
