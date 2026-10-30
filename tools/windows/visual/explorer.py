"""Owned Explorer folder windows and independent file-icon geometry."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

from .desktop_driver import WindowsDesktop


class ExplorerFolder:
    """Open a fresh folder window and close only that window after the check."""

    def __init__(self, native: WindowsDesktop, directory: Path):
        self.native = native
        self.directory = directory.resolve()
        self.window = None
        self.cleanup_failure = None
        self.foreground_observations = []

    def __enter__(self):
        initial = {window["hwnd"] for window in self.native.windows()}
        os.startfile(str(self.directory), "open")
        try:
            self.window = self.native.wait_for(
                lambda: next(
                    (
                        window
                        for window in self.native.windows()
                        if window["class"] == "CabinetWClass"
                        and window["title"].startswith(self.directory.name)
                        and window["hwnd"] not in initial
                    ),
                    None,
                ),
                "Owned Explorer folder did not open",
            )
            self.focus()
            current = self._current_window()
            hwnd, bounds = current["hwnd"], current["rect"]
            x1, y1, x2, _y2 = bounds
            point = ((x1 + x2) // 2, y1 + 20)

            def before_caption_press():
                assert self._current_window()["rect"] == bounds, (
                    "Owned Explorer caption moved before input"
                )
                assert self.native.window_at(*point) == hwnd, (
                    "Owned Explorer caption is covered"
                )

            self.native.click(*point, before_press=before_caption_press)
            self.native.wait_for(
                lambda: self.native.user.GetForegroundWindow() == hwnd,
                "Owned Explorer caption did not receive foreground",
            )
            self._current_window()
            self.native.key(ord("2"), control=True, shift=True)
            time.sleep(1)
        except BaseException as error:
            self._cleanup(error)
            raise
        return self

    def _current_window(self):
        assert self.window is not None
        current = next(
            (
                row
                for row in self.native.windows(self.window["pid"])
                if row["hwnd"] == self.window["hwnd"]
            ),
            None,
        )
        assert current is not None and all(
            current[key] == self.window[key]
            for key in ("hwnd", "pid", "class", "title")
        ), "Owned Explorer window identity changed"
        return current

    def focus(self):
        """Prepare only this verified Cabinet before physical fixture input."""
        current = self._current_window()
        observed = {
            "before": self.native.user.GetForegroundWindow(),
            "hwnd": current["hwnd"],
            "pid": current["pid"],
        }
        self.foreground_observations.append(observed)
        if observed["before"] != current["hwnd"]:
            from docking.platform.backends.base import ActionResult
            from docking.platform.backends.windows.windows import Win32WindowAPI

            api = Win32WindowAPI()
            snapshot = next(
                (
                    row
                    for row in api.windows()
                    if row.id.value == current["hwnd"] and row.pid == current["pid"]
                ),
                None,
            )
            assert snapshot is not None, "Owned Explorer activation target disappeared"
            self._current_window()
            status = api.perform(snapshot, "activate")
            observed["result"] = status.value
            assert status is ActionResult.OK, "Owned Explorer foreground request failed"
        self.native.wait_for(
            lambda: self.native.user.GetForegroundWindow() == current["hwnd"],
            "Owned Explorer did not receive foreground",
        )
        self._current_window()
        observed["after"] = self.native.user.GetForegroundWindow()

    def file_rectangle(self, path: Path, *, selection=False):
        assert path.parent.samefile(self.directory)
        assert self.window is not None
        names = ",".join(
            "'" + name.replace("'", "''") + "'" for name in (path.name, path.stem)
        )
        selected = (
            "$selected=$item.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)"
            if selection
            else ""
        )
        fields = (
            ";selected=[bool]$selected.Current.IsSelected;focused=[bool]$item.Current.HasKeyboardFocus"
            if selection
            else ""
        )
        program = f"""[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
$window=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{self.window["hwnd"]})
$scope=[System.Windows.Automation.TreeScope]::Descendants
$condition=[System.Windows.Automation.PropertyCondition]::new(
 [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
 [System.Windows.Automation.ControlType]::ListItem)
$rows=@(foreach($item in $window.FindAll($scope,$condition)) {{
 if($item.Current.Name -in @({names})) {{
  $r=$item.Current.BoundingRectangle
  if($r.Width -gt 20 -and $r.Height -gt 20) {{
   {selected}
   @{{x=$r.Left;y=$r.Top;width=$r.Width;height=$r.Height;name=$item.Current.Name{fields}}}
  }}
 }}
}})
if($rows.Count -ne 1) {{throw 'Expected exactly one owned file icon'}}
$rows[0]|ConvertTo-Json -Compress
"""
        encoded = base64.b64encode(program.encode("utf-16-le")).decode("ascii")
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8-sig",
            check=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return json.loads(result.stdout)

    def activate(self, path: Path, observed):
        """Select the exact owned Explorer item physically, then press Enter."""
        assert self.window is not None
        source = self.window
        observed["foreground_preparation"] = self.foreground_observations
        self.focus()

        def observe(stage):
            item = self.file_rectangle(path, selection=True)
            current = next(
                (
                    row
                    for row in self.native.windows(source["pid"])
                    if row["hwnd"] == source["hwnd"]
                ),
                None,
            )
            assert current is not None and all(
                current[key] == source[key] for key in ("pid", "hwnd", "class", "title")
            ), "Owned Explorer activation window changed"
            cursor = wintypes.POINT()
            query = self.native.user.GetCursorPos
            query.argtypes = (ctypes.POINTER(wintypes.POINT),)
            query.restype = wintypes.BOOL
            assert query(ctypes.byref(cursor)), "Explorer activation cursor unavailable"
            state = {
                "item": item,
                "window": {
                    key: current[key] for key in ("hwnd", "pid", "class", "rect")
                },
                "cursor": [cursor.x, cursor.y],
                "hit_window": self.native.window_at(cursor.x, cursor.y),
                "foreground": self.native.user.GetForegroundWindow(),
            }
            observed[stage] = state
            return state

        rectangle = self.file_rectangle(path, selection=True)
        point = [
            int(rectangle["x"] + rectangle["width"] / 2),
            int(rectangle["y"] + rectangle["height"] / 2),
        ]
        observed["point"] = point

        def before_click():
            state = observe("before_click")
            current = state["item"]
            assert point == [
                int(current["x"] + current["width"] / 2),
                int(current["y"] + current["height"] / 2),
            ], "Explorer item moved before physical selection"
            assert state["cursor"] == point and state["hit_window"] == source["hwnd"], (
                "Explorer activation item is covered"
            )

        self.native.click(*point, before_press=before_click)

        def selected():
            state = observe("selection")
            return (
                state
                if state["item"]["selected"]
                and state["item"]["focused"]
                and state["foreground"] == source["hwnd"]
                else None
            )

        self.native.wait_for(
            selected, "Exact Explorer item did not receive selection and keyboard focus"
        )
        state = observe("before_enter")
        assert (
            state["item"]["selected"]
            and state["item"]["focused"]
            and state["foreground"] == source["hwnd"]
        ), "Explorer activation context changed before Enter"
        observed["physical_enter_started"] = True
        self.native.key(0x0D)
        observed["physical_enter"] = True

    def close(self):
        if self.window is not None:
            current = next(
                (
                    window
                    for window in self.native.windows()
                    if window["hwnd"] == self.window["hwnd"]
                ),
                None,
            )
            if current is not None:
                assert current["pid"] == self.window["pid"]
                assert current["class"] == "CabinetWClass"
                assert current["title"].startswith(self.directory.name)
                assert self.native.user.PostMessageW(current["hwnd"], 0x10, 0, 0)
                self.native.wait_for(
                    lambda: not self.native.user.IsWindow(current["hwnd"]),
                    "Owned Explorer folder did not close normally",
                )
            self.window = None

    def _cleanup(self, primary):
        try:
            self.close()
        except (AssertionError, OSError) as error:
            self.cleanup_failure = {
                "class": type(error).__name__,
                "errno": error.errno if isinstance(error, OSError) else None,
            }
            if primary is None:
                raise

    def __exit__(self, _kind, error, _traceback):
        self._cleanup(error)
