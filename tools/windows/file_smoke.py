"""Check Windows drive/UNC file workflows and real Explorer-to-dock drops.

Owns temporary documents, one Explorer window, a Notepad view and an isolated
Docking instance. Run in the native Windows lab with no dock or editors open.
The desktop input coordinates assume the documented 1280x800 / 96 DPI baseline.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo))
    from docking.launcher import prepare_display

    prepare_display()
    from PIL import Image, ImageGrab

    from docking.core.paths import local_path_from_uri_or_path
    from docking.platform.icons import IconLoader
    from docking.platform.targets import TargetService
    from docking.search.coordinator import SearchCancellation, SearchRequest
    from docking.search.intents import QueryIntentKind, parse_query_intent
    from docking.search.preview import preview_local_target
    from docking.search.providers.path import PathSearchProvider
    from docking.search.types import SearchQuery
    from docking.ui.folder._browser import FolderBrowser, FolderPrefs

    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    )
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user.PostMessageW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    user.IsWindow.argtypes = (wintypes.HWND,)
    user.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
    user.OpenClipboard.argtypes = (wintypes.HWND,)
    user.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    user.SetClipboardData.restype = wintypes.HANDLE
    user.GetClipboardData.argtypes = (wintypes.UINT,)
    user.GetClipboardData.restype = wintypes.HANDLE
    kernel.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    kernel.GlobalAlloc.restype = wintypes.HANDLE
    kernel.GlobalLock.argtypes = (wintypes.HANDLE,)
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = (wintypes.HANDLE,)
    kernel.GlobalFree.argtypes = (wintypes.HANDLE,)
    child = None
    owned_windows = []
    report = {}

    def windows():
        rows = []

        @callback_type
        def visit(hwnd, _data):
            if user.IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                title = ctypes.create_unicode_buffer(1024)
                classname = ctypes.create_unicode_buffer(256)
                user.GetWindowTextW(hwnd, title, len(title))
                user.GetClassNameW(hwnd, classname, len(classname))
                rect = wintypes.RECT()
                user.GetWindowRect(hwnd, ctypes.byref(rect))
                rows.append(
                    {
                        "hwnd": hwnd,
                        "pid": pid.value,
                        "title": title.value,
                        "class": classname.value,
                        "rect": [rect.left, rect.top, rect.right, rect.bottom],
                    }
                )
            return True

        user.EnumWindows(visit, 0)
        return rows

    def wait_for(predicate, message, seconds=30):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            if child is not None and child.poll() is not None:
                raise AssertionError(
                    f"Dock exited during {message}: {child.returncode}"
                )
            time.sleep(0.2)
        raise AssertionError(message)

    def key(value, *, control=False, shift=False):
        for modifier, enabled in ((0x11, control), (0x10, shift)):
            if enabled:
                user.keybd_event(modifier, 0, 0, 0)
        user.keybd_event(value, 0, 0, 0)
        user.keybd_event(value, 0, 2, 0)
        for modifier, enabled in ((0x10, shift), (0x11, control)):
            if enabled:
                user.keybd_event(modifier, 0, 2, 0)
        time.sleep(0.15)

    def clipboard(value):
        payload = (value + "\0").encode("utf-16-le")
        memory = kernel.GlobalAlloc(2, len(payload))
        assert memory
        pointer = kernel.GlobalLock(memory)
        assert pointer
        ctypes.memmove(pointer, payload, len(payload))
        kernel.GlobalUnlock(memory)
        wait_for(lambda: user.OpenClipboard(None), "Clipboard is busy", 5)
        try:
            assert user.EmptyClipboard()
            if not user.SetClipboardData(13, memory):
                kernel.GlobalFree(memory)
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            user.CloseClipboard()

    def clipboard_text():
        wait_for(lambda: user.OpenClipboard(None), "Clipboard is busy", 5)
        try:
            memory = user.GetClipboardData(13)
            assert memory
            pointer = kernel.GlobalLock(memory)
            assert pointer
            try:
                return ctypes.wstring_at(pointer)
            finally:
                kernel.GlobalUnlock(memory)
        finally:
            user.CloseClipboard()

    def click(x, y):
        user.SetCursorPos(int(x), int(y))
        time.sleep(0.3)
        user.mouse_event(2, 0, 0, 0, 0)
        user.mouse_event(4, 0, 0, 0, 0)
        time.sleep(0.3)

    def close(window):
        user.PostMessageW(window["hwnd"], 0x10, 0, 0)
        wait_for(
            lambda: not user.IsWindow(window["hwnd"]), "Owned window did not close"
        )

    assert not any(
        w["title"] == "Docking"
        or "notepad" in w["title"].lower()
        or w["class"] == "CabinetWClass"
        for w in windows()
    ), "Close the existing dock/editor/Explorer fixture before running this check"
    local = Path(tempfile.mkdtemp(prefix="docking-files-")).resolve()
    folder = local / "Documents # résumé 100%"
    folder.mkdir()
    document = folder / "Windows # résumé + 100%.txt"
    document.write_text(
        "Docking Windows file acceptance\nDrive paths, Unicode and CRLF work.\n",
        encoding="utf-8",
    )
    dropped = folder / "Dropped # café.txt"
    dropped.write_text("Explorer drop acceptance", encoding="utf-8")
    image = folder / "Colors # 100%.png"
    Image.new("RGB", (80, 40), (40, 170, 80)).save(image)
    shared = output / "UNC # résumé 100%.txt"
    shared.write_text("UNC authority remains host.lan", encoding="utf-8")
    target_service = TargetService(icon_loader=IconLoader())
    copied = []
    provider = PathSearchProvider(
        target_service=target_service, copy_text=copied.append, icon_size=48
    )

    def check_path(path):
        canonical = path.resolve()
        uri = canonical.as_uri()
        for text in (str(path), uri):
            assert local_path_from_uri_or_path(text) == path
            normalized = target_service.normalize_file_target(text)
            assert normalized == uri, (text, normalized, uri)
            assert parse_query_intent(text).kind is QueryIntentKind.PATH
            request = SearchRequest(
                query=SearchQuery(text=text),
                generation=1,
                cancellation=SearchCancellation(1),
            )
            results = next(iter(provider.search(request))).results
            assert len(results) == 1 and results[0].identity.key == uri
            result = results[0]
            assert result.title == path.name
            assert provider.invoke(
                result_identity=result.identity,
                action_identity=result.actions[1].identity,
            )
            assert copied[-1] == str(canonical)
        return {"path": str(path), "uri": uri, "copy_path": copied[-1]}

    try:
        report["drive"] = check_path(document)
        report["unc"] = check_path(shared)
        assert shared.drive.lower().startswith(r"\\host.lan\data")
        preview = preview_local_target(target=document.as_uri(), title=document.name)
        assert (
            preview.body
            == "Docking Windows file acceptance\nDrive paths, Unicode and CRLF work."
        )
        assert (
            preview_local_target(target=shared.as_uri(), title=shared.name).body
            == shared.read_text()
        )
        image_info = target_service.resolve_file(str(image), 48)
        assert (
            image_info is not None
            and image_info.is_thumbnail
            and image_info.icon is not None
        )
        browser = FolderBrowser(target_service=target_service)
        rows = browser.list_directory(
            target=str(folder), prefs=FolderPrefs(), icon_px=32
        )
        assert {r.name for r in rows} == {document.name, dropped.name, image.name}
        report["directory_names"] = [r.name for r in rows]
        shared_rows = browser.list_directory(
            target=output.as_uri(), prefs=FolderPrefs(), icon_px=32
        )
        assert shared.name in {row.name for row in shared_rows}
        report["unc_directory_names"] = [row.name for row in shared_rows]
        report["image_thumbnail"] = True
        for path, key_name, capture in (
            (document, "default_text_handler", "default-text-handler.png"),
            (shared, "unc_text_handler", "unc-text-handler.png"),
        ):
            assert target_service.open_target(str(path))
            editor = wait_for(
                lambda: next(
                    (w for w in windows() if "notepad" in w["title"].lower()), None
                ),
                "The default text handler did not appear",
                40,
            )
            owned_windows.append(editor)
            wait_for(
                lambda hwnd=editor["hwnd"], expected=path.stem: any(
                    w["hwnd"] == hwnd and expected in w["title"] for w in windows()
                ),
                "The text handler launched without the requested document",
                20,
            )
            editor = next(w for w in windows() if w["hwnd"] == editor["hwnd"])
            ex1, ey1, ex2, ey2 = editor["rect"]
            click((ex1 + ex2) // 2, (ey1 + ey2) // 2)
            key(0x41, control=True)
            key(0x43, control=True)
            actual_text = clipboard_text().replace("\r\n", "\n").strip()
            assert actual_text == path.read_text(encoding="utf-8").strip(), actual_text
            key(0x24, control=True)
            ImageGrab.grab(all_screens=True).save(output / capture)
            report[key_name] = {**editor, "document_text": actual_text}
            close(editor)
        assert target_service.open_target(folder.as_uri())
        explorer = wait_for(
            lambda: next((w for w in windows() if w["class"] == "CabinetWClass"), None),
            "The default folder handler did not open",
            40,
        )
        owned_windows.append(explorer)
        time.sleep(2)
        report["default_folder_handler"] = explorer
        ImageGrab.grab(all_screens=True).save(output / "explorer-folder.png")
        config_home = output / "config"
        config = config_home / "docking" / "dock.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            json.dumps(
                {
                    "pinned": [
                        {"kind": "file", "target": document.as_uri()},
                        {"kind": "folder", "target": folder.as_uri()},
                        {"kind": "applet", "target": "applet://search"},
                    ],
                    "icon_size": 48,
                    "hide_mode": "none",
                    "position": "bottom",
                    "show_recent_apps": False,
                    "show_recent_docs_in_menu": False,
                    "startup_tips_enabled": False,
                    "update_check_enabled": False,
                }
            ),
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "XDG_CONFIG_HOME": str(config_home),
            "XDG_CACHE_HOME": str(output / "cache"),
            "DOCKING_LOG_LEVEL": "DEBUG",
        }
        with (output / "docking.log").open("w", encoding="utf-8") as log:
            child = subprocess.Popen(
                [
                    str(Path(sys.executable).with_name("pythonw.exe")),
                    str(repo / "run.py"),
                ],
                cwd=repo,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        dock = wait_for(
            lambda: next(
                (
                    w
                    for w in windows()
                    if w["pid"] == child.pid and w["title"] == "Docking"
                ),
                None,
            ),
            "The file/folder dock did not appear",
            50,
        )
        time.sleep(2)
        dock = next(w for w in windows() if w["hwnd"] == dock["hwnd"])
        report["dock"] = dock
        x1, _y1, x2, y2 = dock["rect"]
        center, y = (x1 + x2) // 2, y2 - 25
        click(center, y)
        stack = wait_for(
            lambda: next(
                (
                    w
                    for w in windows()
                    if w["pid"] == child.pid
                    and w["hwnd"] != dock["hwnd"]
                    and w["rect"][3] - w["rect"][1] > 100
                ),
                None,
            ),
            "Clicking the folder did not show its stack",
        )
        report["folder_stack"] = stack
        ImageGrab.grab(all_screens=True).save(output / "folder-stack.png")
        click(1000, 400)
        click(center + 60, y)
        search = wait_for(
            lambda: next(
                (
                    w
                    for w in windows()
                    if w["pid"] == child.pid and w["title"] == "Docking Search"
                ),
                None,
            ),
            "Search did not open",
        )
        clipboard(str(document))
        key(0x41, control=True)
        key(0x56, control=True)
        time.sleep(1.5)
        key(0x50, control=True)
        time.sleep(1)
        report["search_window"] = search
        ImageGrab.grab(all_screens=True).save(output / "search-drive-preview.png")
        key(0x4A, control=True)
        time.sleep(0.3)
        key(0x28)
        key(0x0D)
        time.sleep(0.5)
        assert clipboard_text() == str(document), "Search Copy Path lost drive/Unicode"
        report["search_copy_path"] = clipboard_text()
        if any(w["hwnd"] == search["hwnd"] for w in windows()):
            key(0x1B)
            key(0x1B)
            key(0x1B)
        # Native UI Automation locates the actual Explorer item; no file drop is
        # injected into GTK or its model. Win32 mouse input exercises native DnD.
        ex1, ey1, ex2, _ey2 = explorer["rect"]
        click((ex1 + ex2) // 2, ey1 + 20)
        key(0x32, control=True, shift=True)
        time.sleep(1)
        name = dropped.name.replace("'", "''")
        stem = dropped.stem.replace("'", "''")
        program = f"""[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
$window = [System.Windows.Automation.AutomationElement]::FromHandle(
 [IntPtr]{explorer["hwnd"]})
$items = $window.FindAll(
 [System.Windows.Automation.TreeScope]::Descendants,
 [System.Windows.Automation.Condition]::TrueCondition)
foreach ($item in $items) {{
 if ($item.Current.Name -eq '{name}' -or $item.Current.Name -eq '{stem}') {{
  $r = $item.Current.BoundingRectangle
  if ($r.Width -gt 20 -and $r.Height -gt 20) {{
   @{{name=$item.Current.Name; x=$r.Left; y=$r.Top;
      width=$r.Width; height=$r.Height}} | ConvertTo-Json -Compress
   break
  }}
 }}
}}"""
        encoded = base64.b64encode(program.encode("utf-16-le")).decode("ascii")
        found = subprocess.run(
            ["powershell.exe", "-NoProfile", "-EncodedCommand", encoded],
            capture_output=True,
            text=True,
            encoding="utf-8-sig",
            timeout=30,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        source = json.loads(found.stdout)
        report["explorer_drag_source"] = source
        sx, sy = source["x"] + source["width"] / 2, source["y"] + 20
        user.SetCursorPos(int(sx), int(sy))
        time.sleep(0.3)
        user.mouse_event(2, 0, 0, 0, 0)
        try:
            for step in range(1, 31):
                user.SetCursorPos(
                    int(sx + (center - sx) * step / 30), int(sy + (y - sy) * step / 30)
                )
                time.sleep(0.05)
            time.sleep(1)
            ImageGrab.grab(all_screens=True).save(output / "explorer-dragging.png")
        finally:
            user.mouse_event(4, 0, 0, 0, 0)
        wait_for(
            lambda: any(
                entry.get("target") == dropped.as_uri()
                for entry in json.loads(config.read_text(encoding="utf-8"))["pinned"]
            ),
            "Explorer file drag did not persist the dropped file",
            20,
        )
        report["explorer_drop_pinned"] = dropped.as_uri()
        ImageGrab.grab(all_screens=True).save(output / "explorer-dropped.png")
        close(explorer)
        user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
        child.wait(timeout=15)
        assert child.returncode == 0
        report["ok"] = True
    except Exception as exc:
        report["ok"] = False
        report["error"] = repr(exc)
        ImageGrab.grab(all_screens=True).save(output / "failure.png")
        raise
    finally:
        for window in owned_windows:
            if user.IsWindow(window["hwnd"]):
                user.PostMessageW(window["hwnd"], 0x10, 0, 0)
        report["shutdown_forced"] = child is not None and child.poll() is None
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        (output / "result.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
