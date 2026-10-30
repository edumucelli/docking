"""Owned x86/AMD64 Forms editors for independent Windows input acceptance."""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from ctypes import wintypes
from pathlib import Path

from tools.windows.bundle import command_path
from tools.windows.visual.desktop_driver import WindowsDesktop, write_shared_json
from tools.windows.visual.input_environment import bind

_EDITOR_LOG_LIMIT = 16_384


def focus_editor(native: WindowsDesktop, editor: dict) -> tuple[int, int]:
    """Click an exposed part of the exact editor, then verify actual foreground.

    A fixed client coordinate can belong to a shell popup covering the editor.
    Inspect alternative client points before sending one physical click; never
    dismiss an unidentified window or force foreground activation.
    """
    hwnd, pid = editor["hwnd"], editor["pid"]
    current = next((row for row in native.windows(pid) if row["hwnd"] == hwnd), None)
    if current is None or current["pid"] != pid:
        raise AssertionError("Owned input editor is no longer visible")
    left, top, right, bottom = current["rect"]
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise AssertionError("Owned input editor has no client area")
    points = [
        (int(left + min(150, width / 2)), int(top + min(200, height * 0.6))),
        *(
            (int(left + width * x), int(top + height * y))
            for y in (0.6, 0.8, 0.4)
            for x in (0.1, 0.25, 0.5, 0.75, 0.9)
        ),
    ]
    for x, y in dict.fromkeys(points):
        if native.window_at(x, y) != hwnd:
            continue

        def verify(x=x, y=y):
            # Check after movement settles, immediately before button injection.
            position = wintypes.POINT()
            if not bind(
                native.user,
                "GetCursorPos",
                ctypes.c_int32,
                ctypes.POINTER(wintypes.POINT),
            )(ctypes.byref(position)) or (position.x, position.y) != (x, y):
                raise AssertionError("Pointer moved away from the owned input editor")
            current = next(
                (row for row in native.windows(pid) if row["hwnd"] == hwnd), None
            )
            if current is None or current["pid"] != pid:
                raise AssertionError("Owned input editor identity changed before click")
            if native.window_at(x, y) != hwnd:
                raise AssertionError("Owned input editor was covered before click")

        native.click(x, y, before_press=verify)
        native.wait_for(
            lambda: native.user.GetForegroundWindow() == hwnd,
            "Owned input editor did not receive foreground",
        )
        return x, y
    raise AssertionError("Owned input editor has no exposed input point")


_EDITOR_SOURCE = r"""
using System;
using System.IO;
using System.Text;
using System.Windows.Forms;
using System.Threading;
using System.Runtime.InteropServices;
public sealed class PauseForm : Form {
 [DllImport("user32.dll", CharSet=CharSet.Unicode)]
 static extern int RegisterWindowMessage(string name);
 readonly int message=RegisterWindowMessage("Docking.OwnedInputFixture.Pause");
 readonly EventWaitHandle gate;
 readonly string journal;
 public PauseForm(string name,string path) {
  gate=EventWaitHandle.OpenExisting(name);journal=path;
 }
 protected override void WndProc(ref Message m) {
  if(m.Msg==message) {
   File.WriteAllText(journal,"paused");gate.WaitOne(30000);return;
  }
  base.WndProc(ref m);
 }
 protected override void Dispose(bool disposing) {
  if(disposing)gate.Dispose();base.Dispose(disposing);
 }
}
public sealed class InputEntry : TextBox {
 readonly string document;
 public InputEntry(string path) { document=path;Multiline=true;Dock=DockStyle.Fill; }
 public void Save() { File.WriteAllText(document,Text,new UTF8Encoding(false)); }
 protected override void OnTextChanged(EventArgs e) {
  base.OnTextChanged(e);Save();
 }
 protected override bool ProcessCmdKey(ref Message m,Keys keys) {
  if(keys==(Keys.Control|Keys.A)) { SelectAll();return true; }
  if(keys==(Keys.Control|Keys.S)) { Save();return true; }
  return base.ProcessCmdKey(ref m,keys);
 }
}
public static class InputFixture {
 [STAThread] public static void Main(string[] args) {
  var form=new PauseForm(args[2],args[3]);
  form.Text=args[0];form.Width=520;form.Height=300;
  var entry=new InputEntry(args[1]);
  form.Controls.Add(entry);form.Shown += delegate { entry.Focus(); };
  form.FormClosing += delegate { entry.Save(); };
  Application.Run(form);
 }
}
"""


def _editor_command(
    compiler: Path, source: Path, executable: Path, *, bits: int = 32
) -> list[str]:
    if bits not in (32, 64):
        raise ValueError("Input editor architecture must be 32 or 64")
    return [
        command_path(compiler),
        "/nologo",
        "/target:winexe",
        "/platform:" + ("x86" if bits == 32 else "x64"),
        "/reference:System.Windows.Forms.dll",
        "/out:" + command_path(executable),
        command_path(source),
    ]


def build_editor(output: Path, *, bits: int = 32) -> Path:
    """Build an explicit x86/AMD64 editor with bounded, redacted diagnostics."""
    if bits not in (32, 64):
        raise ValueError("Input editor architecture must be 32 or 64")
    output.mkdir(parents=True, exist_ok=True)
    source, executable = output / "editor.cs", output / f"editor{bits}.exe"
    source.write_text(_EDITOR_SOURCE, encoding="utf-8")
    compiler = (
        Path(os.environ["SYSTEMROOT"]) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
    )
    metadata: dict[str, object] = {
        "status": "running",
        "platform": "x86" if bits == 32 else "x64",
        "source": source.name,
        "executable": executable.name,
        "log": "editor-build.log",
        "timeout_s": 30,
    }
    metadata_path = output / "editor-build.json"
    write_shared_json(metadata_path, metadata)
    roots = {
        output: "<owned-build>",
        compiler.parent: "<framework>",
        Path.cwd(): "<working-directory>",
    }
    for variable in ("SYSTEMROOT", "USERPROFILE", "TEMP", "TMP"):
        value = os.environ.get(variable)
        if value:
            roots[Path(value)] = "<" + variable.lower() + ">"
    spellings = {
        spelling: replacement
        for root, replacement in roots.items()
        for spelling in (
            str(root),
            command_path(root),
            str(root).replace("\\", "/"),
            command_path(root).replace("\\", "/"),
        )
        if spelling not in ("", ".", "/", "\\")
    }

    def retain(stdout, stderr, *, status, returncode=None, errno=None):
        def text(value):
            return (
                value.decode("utf-8", errors="replace")
                if isinstance(value, bytes)
                else value or ""
            )

        diagnostic = "[stdout]\n" + text(stdout) + "\n[stderr]\n" + text(stderr)
        for spelling in sorted(spellings, key=len, reverse=True):
            diagnostic = re.sub(
                re.escape(spelling),
                lambda _match, replacement=spellings[spelling]: replacement,
                diagnostic,
                flags=re.IGNORECASE,
            )
        encoded = diagnostic.encode("utf-8")
        metadata.update(
            status=status,
            returncode=returncode,
            output_truncated=len(encoded) > _EDITOR_LOG_LIMIT,
        )
        if errno is not None:
            metadata["errno"] = errno
        (output / "editor-build.log").write_text(
            encoded[:_EDITOR_LOG_LIMIT].decode("utf-8", errors="ignore"),
            encoding="utf-8",
        )
        write_shared_json(metadata_path, metadata)

    try:
        result = subprocess.run(
            _editor_command(compiler, source, executable, bits=bits),
            check=True,
            capture_output=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as error:
        retain(error.stdout, error.stderr, status="failed", returncode=error.returncode)
        raise RuntimeError(
            f"Independent {bits}-bit editor compilation failed "
            f"(exit {error.returncode}); "
            "see editor-build.log"
        ) from None
    except subprocess.TimeoutExpired as error:
        retain(error.stdout, error.stderr, status="timeout")
        raise subprocess.TimeoutExpired("<framework-csc>", error.timeout) from None
    except OSError as error:
        retain(None, None, status="launch-error", errno=error.errno)
        raise OSError(
            error.errno, "Independent input editor compiler did not start"
        ) from None
    retain(result.stdout, result.stderr, status="success", returncode=result.returncode)
    return executable


class OwnedInputEditor:
    """Own one explicit-architecture editor, its pause event and normal shutdown."""

    def __init__(
        self,
        native: WindowsDesktop,
        directory: Path,
        document: Path,
        *,
        bits: int,
        machine: Callable[[int], int],
    ) -> None:
        if bits not in (32, 64):
            raise ValueError("Input editor architecture must be 32 or 64")
        self.native, self.directory, self.document = native, directory, document
        self.bits, self.machine = bits, machine
        self.title = f"Docking owned {'x64' if bits == 64 else 'x86'} input fixture"
        self.marker = directory / "editor-paused.txt"
        self.process: subprocess.Popen[bytes] | None = None
        self.window: dict[str, object] | None = None
        self.gate: int | None = None
        self._kernel = None
        self._started = False
        self.journal: dict[str, object] = {
            "architecture": bits,
            "platform": "x64" if bits == 64 else "x86",
            "build": directory.name + "/editor-build.json",
            "started": False,
            "closed": False,
            "event_closed": False,
        }

    def start(self) -> dict[str, object]:
        if self.process is not None or self._started or self.journal["closed"]:
            raise RuntimeError("Owned input editor cannot be started twice")
        with ExitStack() as rollback:
            rollback.callback(self.close)
            executable = build_editor(self.directory, bits=self.bits)
            self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            gate_name = "Local\\DockingInputFixture." + uuid.uuid4().hex
            gate = bind(
                self._kernel,
                "CreateEventW",
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int32,
                ctypes.c_int32,
                ctypes.c_wchar_p,
            )(None, True, False, gate_name)
            if not gate:
                raise OSError("Owned input editor pause event did not open")
            self.gate = int(gate)
            self.process = subprocess.Popen(
                [
                    command_path(executable),
                    self.title,
                    command_path(self.document),
                    gate_name,
                    command_path(self.marker),
                ]
            )
            process = self.process
            actual = self.machine(process.pid)
            self.journal["machine"] = f"0x{actual:04x}"
            if actual != (0x8664 if self.bits == 64 else 0x14C):
                raise RuntimeError(
                    "Owned input editor has the wrong process architecture"
                )

            def mapped():
                if process.poll() is not None:
                    raise RuntimeError("Owned input editor exited before mapping")
                return next(
                    (
                        row
                        for row in self.native.windows(process.pid)
                        if row["pid"] == process.pid and row["title"] == self.title
                    ),
                    None,
                )

            observed = self.native.wait_for(
                mapped, "Owned input editor did not map", 30
            )
            if not isinstance(observed, dict):
                raise RuntimeError("Owned input editor has no verified window")
            window = dict[str, object](observed)
            self.window = window
            self.journal.update(started=True, pid=process.pid, window=window["hwnd"])
            self._started = True
            rollback.pop_all()
        return window

    def close(self) -> None:
        if self.journal["closed"] and self.journal["event_closed"]:
            return
        process = self.process
        try:
            if process is not None:
                if process.poll() is None:
                    if self.gate is not None and not bind(
                        self._kernel, "SetEvent", ctypes.c_int32, ctypes.c_void_p
                    )(self.gate):
                        raise OSError("Owned input editor pause event did not release")
                    current = next(
                        (
                            row
                            for row in self.native.windows(process.pid)
                            if row["pid"] == process.pid
                            and row["title"] == self.title
                            and (
                                self.window is None
                                or row["hwnd"] == self.window["hwnd"]
                            )
                        ),
                        None,
                    )
                    if current is not None:
                        if not self.native.user.PostMessageW(
                            current["hwnd"], 0x10, 0, 0
                        ):
                            raise OSError("Owned input editor close request failed")
                    elif not self._started:
                        # Failed startup can precede the first owned HWND.
                        process.terminate()
                        self.journal["startup_terminated"] = True
                code = process.wait(timeout=10)
                self.journal["returncode"] = code
                if self._started and code != 0:
                    raise RuntimeError("Owned input editor did not exit normally")
            self.journal["closed"] = True
        finally:
            try:
                # Cancellation or a failed native close must still release the
                # exact Popen-owned process; never terminate a discovered PID.
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
                    self.journal["forced_termination"] = True
            finally:
                if self.gate is not None:
                    if not bind(
                        self._kernel, "CloseHandle", ctypes.c_int32, ctypes.c_void_p
                    )(self.gate):
                        raise OSError("Owned input editor pause event did not close")
                    self.gate = None
                self.journal["event_closed"] = True
