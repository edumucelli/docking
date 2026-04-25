"""Lightweight smoke coverage for Python 3.14 jobs without GI bindings."""

from __future__ import annotations

import importlib
import json
import signal
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.core.config import Config


def _install_fake_gi(monkeypatch):
    fake_glib = SimpleNamespace(
        PRIORITY_HIGH=100,
        Error=RuntimeError,
        markup_escape_text=lambda text: text,
        unix_signal_add=MagicMock(),
        idle_add=MagicMock(),
    )
    fake_gio = SimpleNamespace(
        AppInfo=SimpleNamespace(launch_default_for_uri=MagicMock())
    )
    fake_gtk = SimpleNamespace(main=MagicMock(), main_quit=MagicMock())
    fake_repo = SimpleNamespace(Gio=fake_gio, GLib=fake_glib, Gtk=fake_gtk)
    fake_gi = SimpleNamespace(require_version=MagicMock(), repository=fake_repo)
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repo)
    return fake_glib, fake_gtk


def _import_about_module(monkeypatch):
    _install_fake_gi(monkeypatch)
    sys.modules.pop("docking.ui.about", None)
    return importlib.import_module("docking.ui.about")


def _load_app_module(monkeypatch, *, vendor_exists: bool = False):
    fake_glib, fake_gtk = _install_fake_gi(monkeypatch)

    platform_pkg = types.ModuleType("docking.platform")
    platform_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "docking.platform", platform_pkg)

    ui_pkg = types.ModuleType("docking.ui")
    ui_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "docking.ui", ui_pkg)

    stub_modules = {
        "docking.platform.environment": {
            "apply_tweaks": lambda **_kwargs: None,
            "detect_desktop": lambda: "test",
        },
        "docking.platform.launcher": {
            "Launcher": type("Launcher", (), {}),
        },
        "docking.platform.model": {
            "DockModel": type("DockModel", (), {}),
        },
        "docking.platform.window_tracker": {
            "WindowTracker": type("WindowTracker", (), {}),
        },
        "docking.platform.unity": {
            "UnityLauncherListener": type("UnityLauncherListener", (), {}),
        },
        "docking.ui.factory": {
            "build_dock_window": lambda **_kwargs: None,
        },
        "docking.ui.new_year": {
            "NewYearGreetingController": type("NewYearGreetingController", (), {}),
        },
        "docking.ui.renderer": {
            "DockRenderer": type("DockRenderer", (), {}),
        },
        "docking.ipc": {
            "DockItemsService": type("DockItemsService", (), {}),
        },
    }
    for module_name, members in stub_modules.items():
        stub_mod = types.ModuleType(module_name)
        for name, value in members.items():
            setattr(stub_mod, name, value)
        monkeypatch.setitem(sys.modules, module_name, stub_mod)

    vendor_dir = "/usr/lib/docking/vendor"
    path_is_dir = Path.is_dir

    def _fake_is_dir(path: Path) -> bool:
        if str(path) == vendor_dir:
            return vendor_exists
        return path_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", _fake_is_dir)

    sys.modules.pop("docking.app", None)
    return importlib.import_module("docking.app"), fake_glib, fake_gtk


class FakeButton:
    def __init__(self, label: str) -> None:
        self._label = label

    def get_label(self) -> str:
        return self._label

    def hide(self) -> None:
        return


class FakeActionArea:
    def __init__(self, children: list[object]) -> None:
        self._children = children

    def get_children(self) -> list[object]:
        return self._children


class FakeAboutDialog:
    def __init__(self, **_kwargs) -> None:
        self.show_count = 0
        self.version = None
        self.buttons: list[object] = [
            FakeButton(label="Credits"),
            FakeButton(label="License"),
            FakeButton(label="Close"),
        ]
        self._action_area = FakeActionArea(children=self.buttons)

    def set_program_name(self, _value: str) -> None:
        return

    def set_version(self, value: str) -> None:
        self.version = value

    def set_comments(self, _value: str) -> None:
        return

    def set_website(self, _value: str) -> None:
        return

    def set_website_label(self, _value: str) -> None:
        return

    def set_logo_icon_name(self, _value: str) -> None:
        return

    def set_authors(self, _value: list[str]) -> None:
        return

    def set_license_type(self, _value) -> None:
        return

    def set_license(self, _value: str) -> None:
        return

    def set_wrap_license(self, _value: bool) -> None:
        return

    def add_button(self, label: str, _response) -> FakeButton:
        button = FakeButton(label=label)
        self.buttons.append(button)
        return button

    def connect(self, _signal: str, _callback) -> None:
        return

    def get_action_area(self) -> FakeActionArea:
        return self._action_area

    def show_all(self) -> None:
        self.show_count += 1


def _fake_about_gtk():
    return type(
        "FakeGtk",
        (),
        {
            "AboutDialog": FakeAboutDialog,
            "Button": FakeButton,
            "License": type("FakeLicense", (), {"GPL_3_0": "gpl3"}),
            "ResponseType": type("FakeResponseType", (), {"HELP": 1}),
            "Window": object,
        },
    )


def test_config_load_existing_file_smoke(tmp_path):
    path = tmp_path / "dock.json"
    path.write_text(
        json.dumps(
            {
                "icon_size": 72,
                "zoom_percent": 1.25,
                "pinned": ["firefox.desktop"],
            }
        )
    )

    config = Config.load(path)

    assert config.icon_size == 72
    assert config.zoom_percent == 1.25
    assert config._path == path


def test_about_dialog_controller_smoke(monkeypatch):
    about_mod = _import_about_module(monkeypatch)
    monkeypatch.setattr(about_mod, "Gtk", _fake_about_gtk())
    monkeypatch.setattr(about_mod, "pkg_version", lambda _name: "1.2.3")
    controller = about_mod.AboutDialogController(parent=object())

    controller.show()
    first = controller._dialog
    controller.show()

    assert first is not None
    assert controller._dialog is first
    assert first.show_count == 2


def test_app_main_smoke(monkeypatch):
    app_mod, fake_glib, fake_gtk = _load_app_module(monkeypatch)

    config = SimpleNamespace(theme="default", icon_size=48, transparency=1.0)
    theme = MagicMock()
    theme.with_opacity.return_value = object()
    launcher = MagicMock()
    model = MagicMock()
    renderer = MagicMock()
    tracker = MagicMock()
    unity = MagicMock()
    new_year = MagicMock()
    window = MagicMock()
    items_service = MagicMock()

    config_cls = MagicMock()
    config_cls.load.return_value = config
    monkeypatch.setattr(app_mod, "Config", config_cls)

    theme_cls = MagicMock()
    theme_cls.load.return_value = theme
    monkeypatch.setattr(app_mod, "Theme", theme_cls)

    monkeypatch.setattr(app_mod, "Launcher", MagicMock(return_value=launcher))
    monkeypatch.setattr(app_mod, "DockModel", MagicMock(return_value=model))
    monkeypatch.setattr(app_mod, "DockRenderer", MagicMock(return_value=renderer))
    monkeypatch.setattr(app_mod, "WindowTracker", MagicMock(return_value=tracker))
    monkeypatch.setattr(app_mod, "UnityLauncherListener", MagicMock(return_value=unity))
    monkeypatch.setattr(
        app_mod,
        "NewYearGreetingController",
        MagicMock(return_value=new_year),
    )
    monkeypatch.setattr(app_mod, "build_dock_window", MagicMock(return_value=window))
    monkeypatch.setattr(
        app_mod, "DockItemsService", MagicMock(return_value=items_service)
    )

    def idle_add(callback, *args):
        return callback(*args)

    fake_glib.idle_add.side_effect = idle_add

    app_mod.main()

    new_year.start.assert_called_once()
    new_year.stop.assert_called_once()

    fake_gtk.main.assert_called_once()
    assert fake_glib.unix_signal_add.call_count == 2
    unity.start.assert_called_once()
    unity.stop.assert_called_once()
    sig_calls = [call.args[1] for call in fake_glib.unix_signal_add.call_args_list]
    assert signal.SIGINT in sig_calls
    assert signal.SIGTERM in sig_calls


def test_app_quit_smoke(monkeypatch):
    app_mod, _fake_glib, fake_gtk = _load_app_module(monkeypatch)

    result = app_mod._quit()

    assert result is False
    fake_gtk.main_quit.assert_called_once()
