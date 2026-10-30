"""Owned x86/AMD64 Forms editors for independent Windows input acceptance."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import subprocess
import uuid
from collections.abc import Callable
from contextlib import ExitStack, contextmanager, nullcontext
from ctypes import wintypes
from pathlib import Path

from tools.windows.bundle import command_path
from tools.windows.visual.desktop_driver import WindowsDesktop, write_shared_json
from tools.windows.visual.input_environment import bind

_EDITOR_LOG_LIMIT = 16_384


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


def _creation(kernel, process: int) -> int:
    times = [_FileTime() for _ in range(4)]
    if not bind(
        kernel,
        "GetProcessTimes",
        ctypes.c_int32,
        ctypes.c_void_p,
        *([ctypes.POINTER(_FileTime)] * 4),
    )(process, *(ctypes.byref(value) for value in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    return times[0].low | times[0].high << 32


def _window_facts(user, hwnd: int) -> dict:
    """Bounded physical-target facts, excluding arbitrary window titles."""
    row = {"hwnd": int(hwnd or 0)}
    if not hwnd:
        return row
    # Match WindowsDesktop.user's existing DWORD pointer declaration. Rebinding
    # this shared function to another same-width ctypes scalar would reject its
    # callers' byref(wintypes.DWORD()) on Windows.
    pid = wintypes.DWORD()
    row["thread"] = int(
        bind(
            user,
            "GetWindowThreadProcessId",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
        )(hwnd, ctypes.byref(pid))
    )
    row["pid"] = pid.value
    cls, bounds = ctypes.create_unicode_buffer(256), wintypes.RECT()
    if bind(
        user,
        "GetClassNameW",
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
    )(hwnd, cls, len(cls)):
        row["class"] = cls.value
    if bind(
        user,
        "GetWindowRect",
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.RECT),
    )(hwnd, ctypes.byref(bounds)):
        row["bounds"] = [bounds.left, bounds.top, bounds.right, bounds.bottom]
    return row


def _point_facts(native: WindowsDesktop, x: int, y: int) -> dict:
    raw = bind(native.user, "WindowFromPoint", ctypes.c_void_p, wintypes.POINT)(
        wintypes.POINT(x, y)
    )
    root = (
        bind(
            native.user,
            "GetAncestor",
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )(raw, 2)
        if raw
        else 0
    )
    return {
        "point": [x, y],
        "hit": _window_facts(native.user, int(raw or 0)),
        "root": _window_facts(native.user, int(root or 0)),
    }


def _topmost(user, hwnd: int) -> bool:
    ctypes.set_last_error(0)
    value = bind(user, "GetWindowLongW", ctypes.c_int32, ctypes.c_void_p, ctypes.c_int)(
        hwnd, -20
    )
    error = ctypes.get_last_error()
    if not value and error:
        raise ctypes.WinError(error)
    return bool(value & 8)  # WS_EX_TOPMOST; never change this bit.


def focus_editor(
    native: WindowsDesktop,
    editor: dict,
    *,
    owned: OwnedInputEditor | None = None,
    journal: dict | None = None,
) -> tuple[int, int]:
    """Expose only a verified Popen-owned editor, then send one hit-tested click."""
    journal = journal if journal is not None else {}
    attempts = journal.setdefault("attempts", [])
    row = {"points": []}
    if len(attempts) < 128:
        attempts.append(row)
    else:
        journal["dropped"] = journal.get("dropped", 0) + 1
    hwnd, pid = editor["hwnd"], editor["pid"]
    current = next(
        (value for value in native.windows(pid) if value["hwnd"] == hwnd), None
    )
    if current is None or current["pid"] != pid:
        raise AssertionError("Owned input editor is no longer visible")
    row["editor"] = {key: current[key] for key in ("hwnd", "pid", "rect")}
    left, top, right, bottom = current["rect"]
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise AssertionError("Owned input editor has no client area")
    points = list(
        dict.fromkeys(
            [
                (int(left + min(150, width / 2)), int(top + min(200, height * 0.6))),
                *(
                    (int(left + width * x), int(top + height * y))
                    for y in (0.6, 0.8, 0.4)
                    for x in (0.1, 0.25, 0.5, 0.75, 0.9)
                ),
            ]
        )
    )

    def exposed(phase):
        for x, y in points:
            facts = _point_facts(native, x, y)
            row["points"].append({"phase": phase, **facts})
            if (
                facts["root"]["hwnd"] == hwnd
                and facts["root"].get("pid") == pid
                and (owned is None or facts["hit"].get("pid") == pid)
            ):
                return x, y
        return None

    point = exposed("initial")
    if point is None and owned is None:
        raise AssertionError("Owned input editor has no exposed input point")
    scope = owned.raise_for_focus(editor, row) if point is None else nullcontext()
    with scope:
        if point is None:
            point = exposed("raised")
        if point is None:
            raise AssertionError("Owned input editor has no exposed input point")
        x, y = point

        def verify():
            position = wintypes.POINT()
            if not bind(
                native.user,
                "GetCursorPos",
                ctypes.c_int32,
                ctypes.POINTER(wintypes.POINT),
            )(ctypes.byref(position)) or (position.x, position.y) != (x, y):
                raise AssertionError("Pointer moved away from the owned input editor")
            current = next(
                (value for value in native.windows(pid) if value["hwnd"] == hwnd), None
            )
            if current is None or current["pid"] != pid:
                raise AssertionError("Owned input editor identity changed before click")
            if owned is not None:
                row["identity_before_press"] = owned.verify_identity(editor)
            facts = row["before_press"] = _point_facts(native, x, y)
            if (
                facts["root"]["hwnd"] != hwnd
                or facts["root"].get("pid") != pid
                or (owned is not None and facts["hit"].get("pid") != pid)
            ):
                raise AssertionError("Owned input editor was covered before click")

        native.click(x, y, before_press=verify)
        native.wait_for(
            lambda: native.user.GetForegroundWindow() == hwnd,
            "Owned input editor did not receive foreground",
        )
        if owned is not None:
            row["identity_after_click"] = owned.verify_identity(editor)
        row["activated"] = True
        return x, y


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
        self._created: int | None = None
        self._executable: Path | None = None
        self._pe: int | None = None
        self._image_hash: str | None = None
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
            self._executable = executable
            self._created = _creation(self._kernel, int(process._handle))
            assert self._created > 0, "Owned editor creation time is invalid"
            from tools.windows.visual.input_requests import pe_machine

            self._pe = pe_machine(executable)
            self._image_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
            self.journal.update(
                created=self._created,
                pe_machine=f"0x{self._pe:04x}",
                image_sha256=self._image_hash,
            )
            assert self._pe == (0x8664 if self.bits == 64 else 0x14C), (
                "Owned input editor PE architecture is wrong"
            )
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

    def verify_identity(self, window: dict) -> dict:
        from tools.windows.visual.input_requests import pe_machine

        process = self.process
        assert process is not None and process.poll() is None, (
            "Owned editor process exited"
        )
        assert self.window is not None and window["hwnd"] == self.window["hwnd"], (
            "Owned editor HWND changed"
        )
        assert window["pid"] == process.pid, "Owned editor PID changed"
        handle = int(process._handle)
        pid = bind(self._kernel, "GetProcessId", ctypes.c_uint32, ctypes.c_void_p)(
            handle
        )
        assert pid == process.pid, "Owned editor process handle changed"
        created = _creation(self._kernel, handle)
        assert created == self._created, "Owned editor creation time changed"
        assert (
            self._executable is not None and pe_machine(self._executable) == self._pe
        ), "Owned editor PE changed"
        assert (
            hashlib.sha256(self._executable.read_bytes()).hexdigest()
            == self._image_hash
        ), "Owned editor executable changed"
        facts = _window_facts(self.native.user, window["hwnd"])
        assert facts["pid"] == pid and facts["thread"], (
            "Owned editor window identity changed"
        )
        return {
            "pid": pid,
            "created": created,
            "window": window["hwnd"],
            "thread": facts["thread"],
            "pe_machine": f"0x{self._pe:04x}",
            "image_sha256": self._image_hash,
        }

    @contextmanager
    def raise_for_focus(self, window: dict, journal: dict):
        """Raise only the exact owned window, preserving topmost and activation."""
        hwnd, user = window["hwnd"], self.native.user
        journal["identity_before_raise"] = self.verify_identity(window)
        topmost = _topmost(user, hwnd)
        previous = int(
            bind(user, "GetWindow", ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)(
                hwnd, 3
            )
            or 0
        )
        prior = _window_facts(user, previous)
        prior_topmost = _topmost(user, previous) if previous else topmost
        foreground = int(user.GetForegroundWindow() or 0)
        position = bind(
            user,
            "SetWindowPos",
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint32,
        )
        flags = 0x0213  # NOMOVE | NOSIZE | NOACTIVATE | NOOWNERZORDER.
        journal["raise"] = {
            "previous": prior,
            "topmost": topmost,
            "foreground_before": foreground,
            "flags": flags,
        }
        failure = None
        try:
            # Recheck immediately at the only owned mutation boundary.
            self.verify_identity(window)
            assert position(hwnd, 0, 0, 0, 0, 0, flags), "Owned editor raise failed"
            journal["raise"]["accepted"] = True
            self.verify_identity(window)
            assert _topmost(user, hwnd) == topmost, "Owned editor topmost state changed"
            journal["raise"]["foreground_after"] = int(user.GetForegroundWindow() or 0)
            assert journal["raise"]["foreground_after"] == foreground, (
                "Nonactivating editor raise changed foreground"
            )
            yield
        except BaseException as error:
            failure = error
            raise
        finally:
            if not journal.get("activated", False):
                try:
                    self.verify_identity(window)
                    # A predecessor in another z-order band means this editor
                    # was already first in its own band. HWND_TOP preserves it;
                    # passing a topmost predecessor could change its style.
                    after = 0
                    if previous and prior_topmost == topmost:
                        current = _window_facts(user, previous)
                        assert (current.get("pid"), current.get("thread")) == (
                            prior.get("pid"),
                            prior.get("thread"),
                        ), "Prior editor z-order anchor changed"
                        assert _topmost(user, previous) == topmost, (
                            "Prior editor z-order band changed"
                        )
                        after = previous
                    assert position(hwnd, after, 0, 0, 0, 0, flags), (
                        "Owned editor z-order did not restore"
                    )
                    assert _topmost(user, hwnd) == topmost, (
                        "Owned editor topmost state did not restore"
                    )
                    restored_previous = int(
                        bind(
                            user,
                            "GetWindow",
                            ctypes.c_void_p,
                            ctypes.c_void_p,
                            ctypes.c_uint32,
                        )(hwnd, 3)
                        or 0
                    )
                    journal["raise"]["previous_after_restore"] = restored_previous
                    assert restored_previous == previous, (
                        "Owned editor z-order readback differs"
                    )
                    journal["raise"]["z_order_restored"] = True
                except Exception as error:
                    journal["raise"]["restore_error"] = type(error).__name__
                    if failure is None:
                        raise

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
