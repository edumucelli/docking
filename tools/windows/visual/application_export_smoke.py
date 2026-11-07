"""Export a real menu row to Explorer, exit Docking, and open the copied link."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def _wait_for_app_content(hwnd: int, *, classic: bool) -> dict:
    """Wait for visible application content, not the first unpainted HWND."""
    program = f"""$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$scope=[System.Windows.Automation.TreeScope]::Descendants
$condition=[System.Windows.Automation.OrCondition]::new(
 [System.Windows.Automation.PropertyCondition]::new(
  [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
  [System.Windows.Automation.ControlType]::Edit),
 [System.Windows.Automation.PropertyCondition]::new(
  [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
  [System.Windows.Automation.ControlType]::Document))
if(${str(classic).lower()}) {{
 $condition=[System.Windows.Automation.PropertyCondition]::new(
  [System.Windows.Automation.AutomationElement]::ClassNameProperty,
  'CharGridWClass')
}}
$deadline=[DateTime]::UtcNow.AddSeconds(15)
do {{
 $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{hwnd})
 foreach($control in $root.FindAll($scope,$condition)) {{
  $bounds=$control.Current.BoundingRectangle
  if(-not $control.Current.IsOffscreen -and
     $bounds.Width -gt 0 -and $bounds.Height -gt 0) {{
   @{{class=$control.Current.ClassName;name=$control.Current.Name;
      type=$control.Current.ControlType.ProgrammaticName;
      bounds=@($bounds.Left,$bounds.Top,$bounds.Width,$bounds.Height)}}|
    ConvertTo-Json -Compress
   exit 0
  }}
 }}
 Start-Sleep -Milliseconds 50
}} while([DateTime]::UtcNow -lt $deadline)
throw 'Launched application content did not become ready'
"""
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            base64.b64encode(program.encode("utf-16-le")).decode("ascii"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--classic", action="store_true")
    parser.add_argument("--trace-surfaces", action="store_true")
    finish = parser.add_mutually_exclusive_group()
    finish.add_argument("--cancel-export", action="store_true")
    finish.add_argument("--close-during-export", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires a native Windows desktop")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Choose a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    from PIL import ImageGrab

    from docking.platform.native.windows.appresolver import shortcut_application_id
    from tools.windows.visual.desktop_driver import WindowsDesktop, read_shared_json
    from tools.windows.visual.explorer import ExplorerFolder

    native = WindowsDesktop()
    baseline, explorer = native.workarea(), native.explorer()
    with tempfile.TemporaryDirectory(prefix="docking-explorer-export-") as temporary:
        directory = Path(temporary) / "Export résumé 日本語"
        directory.mkdir()
        command = [
            sys.executable,
            "-m",
            "tools.windows.visual.applications_smoke",
            "--output",
            str(output / "dock"),
            "--explorer-export",
            "--export-directory",
            str(directory),
        ]
        if args.classic:
            command.append("--classic")
        if args.trace_surfaces:
            command.append("--trace-surfaces")
        if args.cancel_export:
            command.append("--cancel-export")
        if args.close_during_export:
            command.append("--close-during-export")
        with (output / "dock.log").open("wb") as stream:
            subprocess.run(
                command,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=240,
            )
        report = read_shared_json(output / "dock/result.json")
        assert report["ok"] and report["normal_shutdown"]
        exported = report["export"]
        path = Path(exported["path"])
        assert path.parent.samefile(directory)
        assert not Path(exported["source"]).exists(), (
            "Child profile cache was not removed"
        )
        cancelled = args.cancel_export or args.close_during_export
        if cancelled:
            assert not path.exists()
            assert not any(directory.iterdir())
            assert not any(
                w["title"] == "Character Map"
                if args.classic
                else w["class"] == "Notepad"
                for w in native.windows()
            ), "Cancelled drag activated its application"
        else:
            assert shortcut_application_id(path) == exported["id"]
        assert not any(w["title"] == "Docking" for w in native.windows())
        assert native.workarea() == baseline and native.explorer() == explorer
        if cancelled:
            assert report["native_drag_active"]
            key = (
                "native_drag_cancelled"
                if args.cancel_export
                else "native_close_during_drag"
            )
            assert report[key]
            result = {
                "ok": True,
                "id": exported["id"],
                "source_cache_removed": True,
                "dock_process_exited": True,
                "destination_absent": True,
                "normal_shutdown": True,
                key: True,
            }
        else:
            launched = None
            try:
                with ExplorerFolder(native, directory) as folder:
                    rect = folder.file_rectangle(path)
                    point = (rect["x"] + rect["width"] / 2, rect["y"] + 20)
                    native.double_click(*point)
                    launched = native.wait_for(
                        lambda: next(
                            (
                                w
                                for w in native.windows()
                                if (
                                    w["title"] == "Character Map"
                                    if args.classic
                                    else w["class"] == "Notepad"
                                )
                            ),
                            None,
                        ),
                        "Copied shortcut did not launch after Docking exited",
                        45,
                    )
                    native.wait_for(
                        lambda: native.user.GetForegroundWindow() == launched["hwnd"],
                        "Copied shortcut did not activate its application",
                    )
                    content = _wait_for_app_content(
                        launched["hwnd"], classic=args.classic
                    )
                    launched = next(
                        w for w in native.windows() if w["hwnd"] == launched["hwnd"]
                    )
                    ImageGrab.grab(all_screens=True).save(
                        output / "copied-shortcut-launched.png"
                    )
                    result = {
                        "ok": True,
                        "id": exported["id"],
                        "actual_explorer_copy": True,
                        "source_cache_removed": True,
                        "dock_process_exited": True,
                        "actual_explorer_launch": True,
                        "native_content_ready": True,
                        "content": content,
                        "window": launched,
                        "normal_shutdown": True,
                    }
            finally:
                if launched is not None and native.user.IsWindow(launched["hwnd"]):
                    assert native.user.PostMessageW(launched["hwnd"], 0x10, 0, 0)
                    native.wait_for(
                        lambda: not native.user.IsWindow(launched["hwnd"]),
                        "Owned application did not close",
                    )
    assert native.workarea() == baseline and native.explorer() == explorer
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("Native application export and lifecycle acceptance passed")


if __name__ == "__main__":
    main()
