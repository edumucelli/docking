"""Owned Explorer folder windows and independent file-icon geometry."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from pathlib import Path

from .desktop_driver import WindowsDesktop


class ExplorerFolder:
    """Open a fresh folder window and close only that window after the check."""

    def __init__(self, native: WindowsDesktop, directory: Path):
        self.native = native
        self.directory = directory.resolve()
        self.window = None

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
            x1, y1, x2, _y2 = self.window["rect"]
            self.native.click((x1 + x2) // 2, y1 + 20)
            self.native.key(ord("2"), control=True, shift=True)
            time.sleep(1)
        except BaseException:
            self.close()
            raise
        return self

    def file_rectangle(self, path: Path):
        assert path.parent.samefile(self.directory)
        assert self.window is not None
        names = ",".join(
            "'" + name.replace("'", "''") + "'" for name in (path.name, path.stem)
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
   @{{x=$r.Left;y=$r.Top;width=$r.Width;height=$r.Height;name=$item.Current.Name}}
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

    def __exit__(self, *_exception):
        self.close()
