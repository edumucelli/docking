"""Real GTK ownership, secondary input, keyboard routing and chooser lifetime."""

import gc
import json
import os
import subprocess
import time
from contextlib import ExitStack
from ctypes import wintypes
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from gi.repository import Gdk, Gio, GLib, Gtk

from docking.core.items import FOLDER_KIND, DockItem
from docking.platform.desktop.base import OpenWithMode, OpenWithResult
from docking.ui.file_actions import FileActionsController
from docking.ui.folder.browser import FolderRow
from docking.ui.menu import MenuHandler, _DirectoryMenuRow
from tests.ui.preview_support import settle_gtk

pytestmark = pytest.mark.visual


@pytest.fixture
def owner_window():
    if not Gtk.init_check()[0]:
        pytest.skip("GTK display required")
    window = Gtk.Window()
    window.set_default_size(400, 200)
    window.add(Gtk.Label(label="Owned folder menu fixture"))
    window.show_all()
    settle_gtk()
    yield window
    window.destroy()
    settle_gtk()


@pytest.fixture
def browser(tmp_path, owner_window):
    window = owner_window
    targets = MagicMock()
    targets.normalize_file_target.return_value = None
    targets.open_with_mode.return_value = OpenWithMode.GTK
    stack = MagicMock()
    stack.folder_prefs.return_value = {"sort": "name", "show_hidden": False}
    stack.sort_options.return_value = [("Name", "name")]
    stack.icon_px.return_value = 16
    stack.list_directory.return_value = [
        FolderRow(
            (tmp_path / "file.txt").as_uri(),
            "Owned file",
            "file",
            False,
            False,
            0,
            0,
            0,
            None,
        )
    ]
    handler = MenuHandler(
        about=MagicMock(),
        settings=MagicMock(),
        runtime=MagicMock(),
        model=MagicMock(),
        config=MagicMock(lock_icons=True),
        window_tracker=MagicMock(),
        preview_service=MagicMock(),
        folder_stack=stack,
        diagnostics=MagicMock(),
        dock_window=window,
        search=MagicMock(),
        application_registry=MagicMock(),
        application_launcher=MagicMock(),
        target_service=targets,
        file_actions=MagicMock(),
    )
    root = handler._new_popup_menu()
    handler._build_folder_item_menu(
        root,
        DockItem(
            desktop_id=tmp_path.as_uri(), kind=FOLDER_KIND, target=tmp_path.as_uri()
        ),
    )
    root.show_all()
    root.popup_at_widget(window, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, None)
    settle_gtk()
    yield handler, root, targets, window
    for menu in list(handler._open_menus):
        menu.popdown()
        menu.destroy()
    root.destroy()
    settle_gtk()


@pytest.mark.parametrize("keyboard", [False, True])
def test_entry_commands_replace_browsing_menu_without_launching(browser, keyboard):
    handler, root, targets, _window = browser
    gc.collect()
    row = next(
        child for child in root.get_children() if isinstance(child, _DirectoryMenuRow)
    )
    target = row.target
    root.select_item(row)
    if keyboard:
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval, event.state = Gdk.KEY_F10, Gdk.ModifierType.SHIFT_MASK
        event.window = root.get_window()
        assert root.emit("key-press-event", event)
    else:
        press = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        press.button, press.window = 3, row.get_window()
        assert row.emit("button-press-event", press)
        release = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
        release.button, release.window = 3, row.get_window()
        assert row.emit("button-release-event", release)
    settle_gtk()
    targets.open_target.assert_not_called()
    assert not root.get_visible()
    commands = next(iter(handler._open_menus))
    assert commands.get_attach_widget() is handler._dock_window
    assert commands.get_toplevel().get_transient_for() is handler._dock_window
    assert [child.get_label() for child in commands.get_children()] == [
        "Open",
        "Open With…",
    ]
    commands.get_children()[1].activate()
    handler._file_actions.open_with.assert_called_once_with(target)


def test_system_chooser_cancellation_does_not_launch_or_leave_a_dialog(browser):
    _handler, root, targets, window = browser
    root.popdown()
    controller = FileActionsController(parent=window, target_service=targets)
    controller._pending = True
    try:
        controller._choose(("file:///owned/document.txt", "text/plain"))
        assert controller._dialog is not None
        controller._dialog.response(Gtk.ResponseType.CANCEL)
        settle_gtk()
        assert not controller._pending and controller._dialog is None
        targets.launch_with_handler.assert_not_called()
    finally:
        controller.stop()


@pytest.mark.parametrize("select_child", [False, True])
def test_keyboard_event_delivered_to_parent_targets_selected_entry(
    browser, select_child
):
    handler, root, targets, _window = browser
    item = DockItem(
        desktop_id="file:///owned", kind=FOLDER_KIND, target="file:///owned"
    )
    handler._append_directory_row(
        root,
        item,
        FolderRow("file:///owned/nested", "Nested", "dir", True, True, 0, 0, 0, None),
    )
    root.show_all()
    directory = root.get_children()[-1]
    root.select_item(directory)
    nested = directory.get_submenu()
    nested.popup_at_widget(
        directory, Gdk.Gravity.NORTH_EAST, Gdk.Gravity.NORTH_WEST, None
    )
    settle_gtk()
    row = next(
        child for child in nested.get_children() if isinstance(child, _DirectoryMenuRow)
    )
    if select_child:
        nested.select_item(row)
    else:
        nested.deselect()
        assert nested.get_selected_item() is None
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval, event.state, event.window = Gdk.KEY_Menu, 0, root.get_window()
    assert root.emit("key-press-event", event)
    settle_gtk()
    commands = next(iter(handler._open_menus))
    next(
        action
        for action in commands.get_children()
        if action.get_label() == "Open With…"
    ).activate()
    handler._file_actions.open_with.assert_called_once_with(
        row.target if select_child else directory.target
    )
    targets.open_target.assert_not_called()


def _wait(predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        settle_gtk()
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("Native file action did not complete")


@pytest.fixture
def private_portal_bus(monkeypatch):
    from docking.platform.desktop.implementations.unix import file_actions as portal

    with ExitStack() as cleanup:
        daemon = subprocess.Popen(
            ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
            stdout=subprocess.PIPE,
            text=True,
        )
        cleanup.callback(daemon.wait, timeout=5)
        cleanup.callback(daemon.terminate)
        assert daemon.stdout is not None
        cleanup.enter_context(daemon.stdout)
        address = daemon.stdout.readline().strip()
        assert address, "Private portal bus did not start"
        bus = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )
        cleanup.callback(bus.close_sync, None)
        monkeypatch.setattr(portal.Gio, "bus_get_sync", lambda *_: bus)
        yield bus


@pytest.mark.skipif(os.name == "nt", reason="Unix portal FD transport")
@pytest.mark.parametrize("scenario", ["file", "directory", "vanished"])
def test_portal_receives_selected_fd_even_when_response_precedes_method_reply(
    browser, tmp_path, private_portal_bus, scenario
):
    import fcntl

    from docking.platform.desktop.implementations.unix import file_actions as portal

    _handler, root, _targets, window = browser
    root.popdown()
    directory = scenario == "directory"
    path = tmp_path if directory else tmp_path / "owned.txt"
    if not directory:
        path.write_text("owned fixture")
    bus = private_portal_bus
    acquired = []
    name = Gio.bus_own_name_on_connection(
        bus,
        portal.PORTAL_NAME,
        Gio.BusNameOwnerFlags.NONE,
        lambda *a: acquired.append(True),
        lambda *a: acquired.append(False),
    )
    interface = Gio.DBusNodeInfo.new_for_xml("""<node><interface name="org.freedesktop.portal.OpenURI">
      <property name="version" type="u" access="read"/>
      <method name="OpenFile"><arg type="s" direction="in"/><arg type="h" direction="in"/>
      <arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
      </interface></node>""").interfaces[0]
    observed, results = [], []

    def opening(
        connection, sender, object_path, interface_name, method, parameters, invocation
    ):
        _parent, index, options = parameters.unpack()
        fd = invocation.get_message().get_unix_fd_list().get(index)
        try:
            writable = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDWR
            observed.append((os.fstat(fd).st_ino, options["ask"], options["writable"]))
            assert options["writable"] is writable
        finally:
            os.close(fd)
        request = f"/org/freedesktop/portal/desktop/request/{sender[1:].replace('.', '_')}/{options['handle_token']}"
        if scenario != "vanished":
            connection.emit_signal(
                None,
                request,
                portal.REQUEST,
                "Response",
                GLib.Variant("(ua{sv})", (1, {})),
            )
        invocation.return_value(GLib.Variant("(o)", (request,)))

    registered = bus.register_object(
        portal.PORTAL_PATH, interface, opening, lambda *a: GLib.Variant("u", 3), None
    )
    cancel = None
    try:
        _wait(lambda: acquired)
        assert acquired == [True], "Private fake portal could not acquire its name"
        cancel = portal.request_open_with(
            path.as_uri(), parent=window, on_result=results.append
        )
        if scenario == "vanished":
            _wait(lambda: observed)
            Gio.bus_unown_name(name)
            name = 0
        _wait(lambda: results)
        assert results == [
            portal.OpenWithResult.FAILED
            if scenario == "vanished"
            else portal.OpenWithResult.CANCELLED
        ]
        assert observed == [(path.stat().st_ino, True, not directory)]
    finally:
        if cancel is not None:
            cancel()
        bus.unregister_object(registered)
        if name:
            Gio.bus_unown_name(name)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows chooser")
@pytest.mark.parametrize("close_owner", [False, True], ids=["escape", "owner-close"])
def test_windows_native_chooser_can_be_dismissed_without_choosing_an_app(
    owner_window, tmp_path, close_owner, monkeypatch
):
    from docking.platform.native.windows import shell
    from docking.platform.native.windows.ffi import bind, window_handle
    from docking.platform.native.windows.process import read_process_metadata
    from docking.platform.targets import TargetService
    from tests.visual.backends.windows.desktop_driver import WindowsDesktop
    from tests.visual.backends.windows.input_editor import window_facts

    window = owner_window
    native = WindowsDesktop()
    owner = window_handle(window.get_window())
    get_owner = bind(
        native.user, "GetWindow", wintypes.HWND, wintypes.HWND, wintypes.UINT
    )

    def foreground():
        # EnumWindows omits some immersive Windows shell surfaces.
        hwnd = native.user.GetForegroundWindow()
        row = window_facts(native.user, hwnd)
        if row.get("pid"):
            process = read_process_metadata(row["pid"], include_aumid=False)
            if process is not None and process.executable_path is not None:
                row["process"] = process.executable_path.name.casefold()
        if hwnd:
            row["owner"] = get_owner(hwnd, 4)
        return row

    def active_chooser():
        row = foreground()
        if (
            row.get("process") == "openwith.exe"
            or (row.get("class") == "#32770" and row.get("owner") == owner)
        ) and row.get("bounds"):
            left, top, right, bottom = row["bounds"]
            if (
                native.window_at((left + right) // 2, (top + bottom) // 2)
                == row["hwnd"]
            ):
                return row
        return None

    assert active_chooser() is None, "Close the existing Open With dialog first"
    target = tmp_path / "owned file.txt"
    target.write_text("owned fixture")
    results = []
    show = shell.open_with_dialog

    def observe(path, hwnd):
        assert Path(path) == target and hwnd == owner
        result = show(path, hwnd)
        results.append(result)
        return result

    monkeypatch.setattr(shell, "open_with_dialog", observe)
    service = TargetService(icon_loader=MagicMock(), application_registry=MagicMock())
    controller = FileActionsController(parent=window, target_service=service)
    report = Path("artifacts/windows/file-actions")
    report.mkdir(parents=True, exist_ok=True)
    name = "chooser-owner" if close_owner else "chooser-escape"
    chooser = None

    def capture():
        facts = {
            "owner": owner,
            "foreground": foreground(),
            "chooser": chooser,
            "pending": controller._pending,
            "result": [result.name for result in results],
            "error_dialog": controller._error_dialog is not None,
        }
        (report / (name + ".json")).write_text(
            json.dumps(facts, indent=2), encoding="utf-8"
        )
        print(json.dumps(facts))

    try:
        window.set_keep_above(True)
        window.present()
        settle_gtk()
        left, top, right, bottom = window_facts(native.user, owner)["bounds"]
        native.click((left + right) // 2, (top + bottom) // 2)
        _wait(lambda: native.user.GetForegroundWindow() == owner)
        window.set_keep_above(False)
        settle_gtk()
        controller.open_with(target.as_uri())
        chooser = _wait(active_chooser)
        assert controller._pending
        if close_owner:
            window.destroy()
            assert not controller._pending
        if native.user.IsWindowVisible(chooser["hwnd"]):
            _wait(lambda: native.user.GetForegroundWindow() == chooser["hwnd"])
            native.key(0x1B)
        _wait(lambda: results)
        _wait(lambda: not controller._pending)
        _wait(lambda: not native.user.IsWindowVisible(chooser["hwnd"]))
        assert results[0] in {OpenWithResult.CANCELLED, OpenWithResult.COMPLETED}
        assert controller._error_dialog is None
    finally:
        capture()
        controller.stop()
        if chooser is not None and native.user.IsWindowVisible(chooser["hwnd"]):
            remaining = window_facts(native.user, chooser["hwnd"])
            if remaining.get("pid") == chooser["pid"]:
                native.user.PostMessageW(chooser["hwnd"], 0x10, 0, 0)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows console")
def test_windows_terminal_starts_in_selected_directory(
    owner_window, tmp_path, monkeypatch
):
    from docking.platform.desktop.implementations.windows import file_actions as windows
    from tests.visual.backends.windows.desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    before = {row["hwnd"] for row in native.windows()}
    started = []
    popen = windows.subprocess.Popen

    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(windows.subprocess, "Popen", launch)
    output = tmp_path / "working-directory.txt"
    console = None
    try:
        assert windows.open_in_terminal(tmp_path)
        console = _wait(
            lambda: next(
                (
                    row
                    for row in native.windows()
                    if row["hwnd"] not in before
                    and row["class"]
                    in {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}
                ),
                None,
            )
        )
        left, top, right, _bottom = console["rect"]
        native.click((left + right) // 2, top + 15)
        destination = str(output).replace("'", "''")
        native.type_text(
            f"(Get-Location).Path | Out-File -LiteralPath '{destination}' -Encoding utf8; exit"
        )
        native.key(0x0D)
        assert started[0].wait(timeout=15) == 0
        assert os.path.normcase(
            output.read_text(encoding="utf-8-sig").strip()
        ) == os.path.normcase(str(tmp_path))
    finally:
        for process in started:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
        if console is not None and native.user.IsWindow(console["hwnd"]):
            native.user.PostMessageW(console["hwnd"], 0x10, 0, 0)
