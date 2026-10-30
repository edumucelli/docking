"""Owned Windows input editors and physical focus for known GTK entries."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import subprocess
import sys
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


def _editor_points(bounds: list[int]) -> list[tuple[int, int]]:
    left, top, right, bottom = bounds
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise AssertionError("Owned input editor has no client area")
    return list(
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


def _choose_placement(
    native: WindowsDesktop,
    window: dict,
    before: list[int],
    workarea: list[int],
    journal: dict,
    *,
    phase: str,
) -> list[int] | None:
    """Predict one placement using current occluders and actual Z-ordered hits."""
    left, top, right, bottom = workarea
    width, height = before[2] - before[0], before[3] - before[1]
    assert width <= right - left and height <= bottom - top, (
        "Owned editor does not fit the workarea"
    )
    identities = {value["hwnd"]: value["pid"] for value in native.windows()[:256]}
    assert identities.get(window["hwnd"]) == window["pid"], (
        "Owned editor is absent from window order"
    )
    # EnumWindows supplies identities; GW_HWNDNEXT explicitly supplies Z order.
    # Bound this read-only walk and reject cycles/destruction rather than ever
    # treating an unknown hit as exposed space.
    next_window = bind(
        native.user,
        "GetWindow",
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
    )
    below, seen, cursor = {window["hwnd"]}, set(), window["hwnd"]
    for _ in range(256):
        if not cursor or cursor in seen:
            break
        seen.add(cursor)
        if cursor in identities:
            below.add(cursor)
        cursor = int(next_window(cursor, 2) or 0)
    blockers = {
        tuple(value["root"]["bounds"])
        for value in journal["points"]
        if value["phase"] == phase
        and "bounds" in value["root"]
        and value["root"]["hwnd"] != window["hwnd"]
    }
    candidates = []
    journal["placement_candidates"] = candidates
    for x, y in dict.fromkeys(
        (
            (left, top),
            (right - width, top),
            (left, bottom - height),
            (right - width, bottom - height),
        )
    ):
        bounds = [x, y, x + width, y + height]
        if bounds == before:
            continue
        row = {"bounds": bounds, "observations": [], "exposed_points": 0}
        candidates.append(row)
        for px, py in _editor_points(bounds):
            if any(a <= px < c and b <= py < d for a, b, c, d in blockers):
                continue
            facts = _point_facts(native, px, py)
            row["observations"].append(facts)
            root = facts["root"]
            if root["hwnd"] in below and identities[root["hwnd"]] == root.get("pid"):
                row["exposed_points"] += 1
    usable = [row for row in candidates if row["exposed_points"]]
    if not usable:
        return None
    best = min(
        usable,
        key=lambda row: (
            -row["exposed_points"],
            (row["bounds"][0] - before[0]) ** 2 + (row["bounds"][1] - before[1]) ** 2,
        ),
    )
    return best["bounds"]


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
    points = _editor_points(current["rect"])

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
    if point is None:
        if owned is None:
            raise AssertionError("Owned input editor has no exposed input point")
        owned.activate_for_focus(editor, row)
        current = next(
            (value for value in native.windows(pid) if value["hwnd"] == hwnd), None
        )
        assert current is not None and current["pid"] == pid, (
            "Owned editor disappeared after activation"
        )
        points = _editor_points(current["rect"])
        point = exposed("after_activation")
    scope = owned.place_for_focus(editor, row) if point is None else nullcontext()
    with scope:
        if point is None:
            current = next(
                (value for value in native.windows(pid) if value["hwnd"] == hwnd), None
            )
            assert current is not None and current["pid"] == pid, (
                "Owned editor disappeared after placement"
            )
            points = _editor_points(current["rect"])
            point = exposed("placed")
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


def gtk_entry_geometry(entry, window, current: Callable) -> dict:
    """Read one pinned entry's physical screen target on its GTK thread."""
    current_window, current_entry = current()
    assert current_window is window and current_entry is entry, (
        "Owned GTK entry was replaced"
    )
    assert entry.get_toplevel() is window, "Owned GTK entry changed its toplevel"
    assert (
        window.get_visible()
        and window.get_mapped()
        and entry.get_visible()
        and entry.get_mapped()
        and entry.get_realized()
    ), "Owned GTK entry is not visible and mapped"
    gdk_window = window.get_window()
    assert gdk_window is not None, "Owned GTK entry has no native window"
    success, ox, oy = gdk_window.get_origin()
    assert success, "Owned GTK entry screen origin is unavailable"
    offset = entry.translate_coordinates(window, 0, 0)
    allocation, scale = entry.get_allocation(), window.get_scale_factor()
    assert offset is not None and allocation.width > 1 and allocation.height > 1, (
        "Owned GTK entry has no usable allocation"
    )
    assert scale > 0, "Owned GTK entry has invalid scale"
    # Match the existing native GTK button probes: GDK origins and widget
    # offsets are logical coordinates, whereas WindowFromPoint uses pixels.
    left, top = (ox + offset[0]) * scale, (oy + offset[1]) * scale
    width, height = allocation.width * scale, allocation.height * scale
    return {
        "origin": [ox, oy],
        "allocation": [offset[0], offset[1], allocation.width, allocation.height],
        "scale": scale,
        "rect": [left, top, left + width, top + height],
        "point": [int(left + width / 2), int(top + height / 2)],
    }


def focus_gtk_entry(
    native: WindowsDesktop,
    mapped: dict,
    *,
    entry,
    window,
    current: Callable,
    dispatch: Callable,
    context,
    api,
    journal: dict,
) -> None:
    """Click only the pinned entry, with no fallback to palette/body coordinates."""
    assert mapped["hwnd"] == context.root and mapped["pid"] == context.pid, (
        "Owned GTK entry context differs from its native window"
    )

    def validate():
        assert api.valid(context) and api.input_desktop_active(), (
            "Owned GTK entry context is no longer valid"
        )

    def geometry():
        return dispatch(lambda: gtk_entry_geometry(entry, window, current))

    validate()
    before = journal["geometry"] = geometry()
    x, y = before["point"]

    def hit(phase):
        facts = journal[phase] = _point_facts(native, x, y)
        root = facts["root"]
        assert (
            root["hwnd"] == context.root
            and root.get("pid") == (context.root_pid or context.pid)
            and root.get("thread") == (context.root_thread or context.thread)
            and facts["hit"].get("pid") == context.pid
        ), "Owned GTK entry is covered or its native identity changed"
        left, top, right, bottom = root["bounds"]
        assert left <= x < right and top <= y < bottom, (
            "Owned GTK entry target is outside its native window"
        )

    hit("initial_hit")

    def before_press():
        validate()
        live = journal["before_press_geometry"] = geometry()
        assert live == before, "Owned GTK entry geometry changed before click"
        position = wintypes.POINT()
        assert bind(
            native.user,
            "GetCursorPos",
            ctypes.c_int32,
            ctypes.POINTER(wintypes.POINT),
        )(ctypes.byref(position)) and (position.x, position.y) == (x, y), (
            "Pointer moved away from the owned GTK entry"
        )
        hit("before_press_hit")

    native.click(x, y, before_press=before_press)

    def focused():
        validate()
        # Recheck the captured widget identity/allocation after event delivery,
        # too: a replacement must never satisfy focus through the old closure.
        assert geometry() == before, "Owned GTK entry geometry changed after click"
        return api.foreground() == context and dispatch(entry.has_focus)

    native.wait_for(focused, "Owned GTK entry did not receive native and GTK focus")
    journal["focused"] = True


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
            creationflags=0x08000000 if sys.platform == "win32" else 0,
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

    def activate_for_focus(self, window, journal) -> None:
        """Normally activate only this pinned child before physical test input."""
        from docking.platform.backends.base import ActionResult
        from docking.platform.backends.windows.windows import Win32WindowAPI

        hwnd, user = window["hwnd"], self.native.user
        row = journal["owned_activation"] = {
            "identity_before": self.verify_identity(window),
            "foreground_before": int(user.GetForegroundWindow() or 0),
            "bounds_before": _window_facts(user, hwnd)["bounds"],
            "topmost_before": _topmost(user, hwnd),
        }
        api = Win32WindowAPI()
        snapshot = api.capture_snapshot(hwnd, expected_pid=window["pid"])
        assert (
            snapshot is not None
            and snapshot.id.value == hwnd
            and snapshot.pid == window["pid"]
        ), "Owned editor activation snapshot changed"
        row["identity_before_request"] = self.verify_identity(window)
        status = api.perform(snapshot, "activate")
        row["result"] = status.value
        row["foreground_immediate"] = int(user.GetForegroundWindow() or 0)
        assert status is ActionResult.OK, "Owned editor normal activation failed"

        def foreground():
            self.verify_identity(window)
            row["foreground_after"] = int(user.GetForegroundWindow() or 0)
            return row["foreground_after"] == hwnd

        # Cross-input-queue SetForegroundWindow activation is asynchronous.
        # Wait for the one accepted request; never attach queues or retry it.
        self.native.wait_for(
            foreground, "Owned editor normal activation did not receive foreground"
        )
        row["identity_after"] = self.verify_identity(window)
        row["bounds_after"] = _window_facts(user, hwnd)["bounds"]
        row["topmost_after"] = _topmost(user, hwnd)
        assert row["bounds_after"] == row["bounds_before"], (
            "Owned editor normal activation changed geometry"
        )
        assert row["topmost_after"] == row["topmost_before"], (
            "Owned editor normal activation changed topmost state"
        )

    @contextmanager
    def place_for_focus(self, window: dict, journal: dict):
        """Move one owned window into observed free space without changing Z order."""
        hwnd, user = window["hwnd"], self.native.user
        journal["identity_before_placement"] = self.verify_identity(window)
        before = _window_facts(user, hwnd)["bounds"]
        workarea = self.native.workarea()
        chosen = _choose_placement(
            self.native, window, before, workarea, journal, phase="after_activation"
        )
        assert chosen is not None, "No observed exposed placement for owned editor"
        topmost = _topmost(user, hwnd)
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
        flags = 0x0215  # NOSIZE | NOZORDER | NOACTIVATE | NOOWNERZORDER.
        journal["placement"] = {
            "before": before,
            "requested": chosen,
            "workarea": workarea,
            "topmost": topmost,
            "foreground_before": foreground,
            "flags": flags,
        }
        failure = None
        try:
            self.verify_identity(window)
            assert _window_facts(user, hwnd)["bounds"] == before, (
                "Owned editor moved before placement"
            )
            assert position(hwnd, 0, chosen[0], chosen[1], 0, 0, flags), (
                "Owned editor placement failed"
            )
            journal["placement"]["accepted"] = True
            self.verify_identity(window)
            after = journal["placement"]["after"] = _window_facts(user, hwnd)["bounds"]
            assert after == chosen, "Owned editor placement readback differs"
            assert _topmost(user, hwnd) == topmost, "Owned editor topmost state changed"
            journal["placement"]["foreground_after"] = int(
                user.GetForegroundWindow() or 0
            )
            assert journal["placement"]["foreground_after"] == foreground, (
                "Nonactivating editor placement changed foreground"
            )
            yield
        except BaseException as error:
            failure = error
            raise
        finally:
            if not journal.get("activated", False):
                try:
                    self.verify_identity(window)
                    assert position(hwnd, 0, before[0], before[1], 0, 0, flags), (
                        "Owned editor geometry did not restore"
                    )
                    assert _window_facts(user, hwnd)["bounds"] == before, (
                        "Owned editor restored geometry differs"
                    )
                    assert _topmost(user, hwnd) == topmost, (
                        "Owned editor topmost state did not restore"
                    )
                    journal["placement"]["geometry_restored"] = True
                except Exception as error:
                    journal["placement"]["restore_error"] = type(error).__name__
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
