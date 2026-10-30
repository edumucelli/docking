"""Owned input-request controls and bounded protocol/lifecycle evidence.

Imports of the installed application stay inside the entry points: importing
this tool must not select a source package before --installed-root is applied.
"""

from __future__ import annotations

import ctypes
import struct
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from tools.windows.visual.input_oracle import OracleError

_TRACE_LIMIT = 512
_VALIDATION_PHASES = {
    101: "cancelled",
    102: "not-authorized",
    103: "helper-machine-mismatch",
    104: "deadline-expired",
    105: "target-process-mismatch",
    106: "target-thread-mismatch",
    107: "root-identity-mismatch",
    108: "target-not-visible-or-child",
    109: "input-desktop-inaccessible-or-changed",
    110: "foreground-root-changed",
    111: "gui-thread-query-failed",
    112: "focused-window-changed",
    200: "controller-atom-or-host-setup",
    201: "controller-hook-installation",
    202: "controller-message-registration",
    203: "controller-message-post",
    210: "x86-host-hook-installation",
    211: "x86-host-message-post",
}


class CleanupJournal:
    """Run every independent cleanup step and keep the original failure first."""

    def __init__(
        self, report: dict, errors: list[Exception], publish: Callable
    ) -> None:
        self.report, self.errors, self.publish = report, errors, publish
        report.setdefault("cleanup", [])

    def attempt(self, name: str, operation: Callable):
        row = {"name": name, "ok": False}
        self.report["cleanup"].append(row)
        try:
            value = operation()
            row["ok"] = True
            return value
        except Exception as error:
            self.errors.append(error)
            self.report["ok"] = False
            row["error"] = _error(error)
            return None
        finally:
            try:
                self.publish()
            except Exception as error:
                # A failed diagnostic write must not prevent later cleanup or
                # replace the original application/cleanup failure.
                self.errors.append(error)
                self.report["ok"] = False


def _error(error: Exception) -> dict:
    result = {"type": type(error).__name__}
    if isinstance(error, OSError):
        # WinError is an external exception boundary: its C errno can differ
        # from the original Win32 status (for example22 versus87).
        try:
            result["code"] = error.winerror
        except AttributeError:
            result["code"] = None
        if result["code"] is None:
            result["code"] = error.errno
    elif isinstance(error, OracleError):
        result.update(api=error.api, code=error.code)
    return result


@contextmanager
def trace_requests(journal: dict, publish: Callable[[], None]) -> Iterator[None]:
    """Trace real requests without changing their result, thread or ownership."""
    from docking.platform.backends.windows.input_thread import (
        InputRequestDiagnostic,
        InputThread,
    )

    original, dispatch = InputThread.request, InputThread._dispatch
    lock, local = threading.Lock(), threading.local()
    journal.update(limit=_TRACE_LIMIT, dropped=0, requests=[])

    def request(client, context, cancelled, profile=None, *, layout=None):
        row = {
            "context": asdict(context),
            "operation": 3 if layout is not None else (2 if profile else 1),
            "requested": profile.code if profile else layout,
            "cancelled_before": cancelled.is_set(),
        }
        local.row = row
        started = time.monotonic()
        try:
            result = original(client, context, cancelled, profile, layout=layout)
            row["outcome"] = "returned" if result is not None else "unavailable"
            row["snapshot"] = asdict(result) if result else None
            return result
        except Exception as error:
            row["outcome"], row["error"] = "failed", _error(error)
            raise
        finally:
            local.row = None
            row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            row["cancelled_after"] = cancelled.is_set()
            # Publish only complete immutable rows. Report writers may run on
            # the driver while requests run on applet workers; they must never
            # observe a row whose dictionary is still being populated.
            with lock:
                if len(journal["requests"]) < _TRACE_LIMIT:
                    row["sequence"] = len(journal["requests"]) + 1
                    journal["requests"].append(row)
                else:
                    journal["dropped"] += 1

    def traced_dispatch(
        client,
        context,
        cancelled,
        profile,
        machine,
        bits,
        path,
        name,
        value,
        *,
        layout=None,
    ):
        try:
            return dispatch(
                client,
                context,
                cancelled,
                profile,
                machine,
                bits,
                path,
                name,
                value,
                layout=layout,
            )
        finally:
            row = local.row
            if row is not None:
                diagnostic = InputRequestDiagnostic.read(value)
                row["wire"] = asdict(diagnostic)
                row["validation"] = _VALIDATION_PHASES.get(diagnostic.phase)
                row["helper_bits"] = bits

    InputThread.request, InputThread._dispatch = request, traced_dispatch
    failure = None
    try:
        yield
    except BaseException as error:
        failure = error
        raise
    finally:
        InputThread.request, InputThread._dispatch = original, dispatch
        # Shared SMB publication belongs to the tool driver, never a request
        # worker. Callers publish during their normal stage/cleanup boundaries.
        try:
            publish()
        except Exception:
            if failure is None:
                raise


def pe_machine(path: Path) -> int:
    """Read the exact owned compiler output's COFF machine, with bounded seeks."""
    with path.open("rb") as source:
        header = source.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise ValueError("Owned editor lacks a DOS header")
        offset = struct.unpack_from("<I", header, 60)[0]
        if not 64 <= offset <= 16 * 1024 * 1024:
            raise ValueError("Owned editor has an invalid PE offset")
        source.seek(offset)
        value = source.read(6)
    if len(value) != 6 or value[:4] != b"PE\0\0":
        raise ValueError("Owned editor lacks a PE signature")
    return struct.unpack_from("<H", value, 4)[0]


def collect_controls(
    *,
    row: dict,
    operations: tuple,
    validate: Callable,
    close: Callable,
    publish: Callable[[], None] | None = None,
) -> None:
    """Collect typed request failures only after safe ownership and close proof.

    Known requests may fail while all owned resources remain valid. Unexpected
    setup/identity/readback/close errors remain fatal and stop the matrix.
    """
    from docking.platform.backends.windows.input_thread import InputRequestError

    row["operations"] = []
    failure = None
    try:
        for name, operation in operations:
            validate()
            result = {"name": name, "ok": False}
            row["operations"].append(result)
            try:
                snapshot = operation()
                result["snapshot"] = asdict(snapshot) if snapshot else None
                result["ok"] = snapshot is not None
            except InputRequestError as error:
                result["error"] = _error(error)
                result["wire"] = asdict(error.diagnostic)
            validate()
            if publish is not None:
                publish()
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            close()
            row["closed"] = True
        except Exception as error:
            row["close_error"] = _error(error)
            if failure is None:
                raise
    row["ok"] = all(result["ok"] for result in row["operations"])


def request_controls(
    native, output: Path, *, french: int, journal: dict, publish: Callable[[], None]
) -> None:
    """Exercise real x64/x86 read, classic and TSF requests before applet input."""
    from docking.applets.keyboardlayout.windows import WindowsKeyboardAPI
    from docking.platform.backends.windows.input_service import WindowsInputService
    from tools.windows.visual.input_editor import OwnedInputEditor, focus_editor
    from tools.windows.visual.input_environment import bind, process_architecture

    api, service = WindowsKeyboardAPI(), WindowsInputService()
    caller = api.active(0)
    expected = (0x04090409, french & 0xFFFFFFFF)
    keyboards = service.catalog(api.layouts()).keyboards
    records = {p.layout & 0xFFFFFFFF: p for p in keyboards if p.enabled}
    assert all(layout in records for layout in expected), (
        "Enabled US/French keyboard profiles are required for direct controls"
    )
    journal["controls"] = []
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    controller = bind(kernel, "GetCurrentProcess", ctypes.c_void_p)()
    journal["controller_abi"] = process_architecture(kernel, controller, details=True)
    for bits in (64, 32):
        directory = output / f"request-control-{bits}"
        directory.mkdir(parents=True, exist_ok=True)
        document = directory / "input.txt"
        document.write_text("", encoding="utf-8")
        editor = OwnedInputEditor(
            native, directory, document, bits=bits, machine=service._thread._machine
        )
        row = {"architecture": bits, "editor": editor.journal}
        journal["controls"].append(row)
        publish()
        failure = None
        try:
            window = editor.start()
            row["pe_machine"] = f"0x{pe_machine(directory / f'editor{bits}.exe'):04x}"
            assert row["pe_machine"] == ("0x8664" if bits == 64 else "0x014c")
            assert editor.process is not None
            row["process_abi"] = process_architecture(
                kernel, int(editor.process._handle), details=True
            )
            focus_editor(
                native, window, owned=editor, journal=row.setdefault("focus", {})
            )
            context = api.foreground()
            assert (
                context
                and context.root == window["hwnd"]
                and context.pid == window["pid"]
            )
            row["context"] = asdict(context)

            def validate(context=context):
                assert api.valid(context) and api.foreground() == context, (
                    "Direct input control lost the exact owned typing context"
                )
                assert api.active(0) & 0xFFFFFFFF == caller & 0xFFFFFFFF, (
                    "Direct input control changed caller layout"
                )

            def read(context=context):
                snapshot = service.read(context, threading.Event())
                if snapshot is not None:
                    assert (
                        snapshot.layout & 0xFFFFFFFF
                        == api.active(context.thread) & 0xFFFFFFFF
                    )
                return snapshot

            def activate(layout, *, tsf, context=context):
                snapshot = (
                    service.activate(context, records[layout], threading.Event())
                    if tsf
                    else service._thread.request(
                        context, threading.Event(), layout=layout
                    )
                )
                if snapshot is not None:
                    assert snapshot.layout & 0xFFFFFFFF == layout, (
                        "Direct control readback mismatch"
                    )
                    assert api.active(context.thread) & 0xFFFFFFFF == layout
                return snapshot

            operations = (
                ("initial-read", read),
                ("classic-fr", lambda: activate(expected[1], tsf=False)),
                ("classic-us", lambda: activate(expected[0], tsf=False)),
                ("tsf-keyboard-fr", lambda: activate(expected[1], tsf=True)),
                ("tsf-keyboard-us", lambda: activate(expected[0], tsf=True)),
                ("final-read", read),
            )
            collect_controls(
                row=row,
                operations=operations,
                validate=validate,
                close=editor.close,
                publish=publish,
            )
        except BaseException as error:
            failure = error
            raise
        finally:
            try:
                editor.close()
            except Exception as error:
                row["close_error"] = _error(error)
                if failure is None:
                    raise
            finally:
                try:
                    publish()
                except Exception:
                    if failure is None:
                        raise
    journal["controls_passed"] = all(row["ok"] for row in journal["controls"])
    publish()


def release_fixture_layouts(
    *,
    owned: set[int],
    baseline: set[int],
    loaded: Callable,
    unload: Callable,
    targets_closed: bool,
    profiles_restored: bool,
    journal: dict,
) -> None:
    """Release only positively owned Vietnamese compatibility layouts.

    Ownership is established when the fixture enabled both initially disabled
    profiles. Callers must release their TSF references before invoking this.
    """
    journal["fixture_layout_releases"] = []
    if not targets_closed or not profiles_restored:
        raise AssertionError(
            "Owned input contexts/profiles must close before unloading"
        )
    if any(handle in baseline or handle != 0x042A042A for handle in owned):
        raise AssertionError("Refusing to unload an unowned compatibility layout")
    for handle in sorted(owned):
        current = next((h for h in loaded() if h & 0xFFFFFFFF == handle), None)
        row = {"layout": f"{handle:08x}", "already_absent": current is None}
        journal["fixture_layout_releases"].append(row)
        if current is not None:
            row["unloaded"] = bool(unload(current))
            if not row["unloaded"]:
                raise ctypes.WinError(ctypes.get_last_error())


def select_input_runtime(
    installed_root: Path | None, journal: dict, *, source: Path | None = None
) -> Path:
    """Select one complete runtime before any application module is imported."""
    import hashlib
    import sys

    source = (source or Path(__file__).resolve().parents[3]).resolve()
    root = installed_root.resolve() if installed_root is not None else source
    journal["implementation"] = (
        "installed-wheel" if installed_root is not None else "source-built"
    )
    journal["helpers"] = []
    preloaded = sorted(
        name for name in sys.modules if name == "docking" or name.startswith("docking.")
    )
    journal["preloaded_application_modules"] = preloaded
    assert not preloaded, "Select the input runtime before importing Docking"
    if installed_root is not None and root == source:
        raise ValueError("Installed input runtime must be outside the source checkout")
    assert (root / "docking/__init__.py").is_file(), (
        "Selected input runtime has no Docking package"
    )
    for bits, machine in ((64, 0x8664), (32, 0x14C)):
        relative = Path(f"docking/platform/backends/windows/input-thread-{bits}.dll")
        path = root / relative
        assert path.resolve().is_relative_to(root), (
            "Input helper escapes selected runtime"
        )
        row = {"file": relative.as_posix(), "present": path.is_file()}
        journal["helpers"].append(row)
        if row["present"]:
            row["machine"] = f"0x{pe_machine(path):04x}"
            row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            assert row["machine"] == f"0x{machine:04x}", (
                "Input helper has the wrong PE architecture"
            )
    assert all(row["present"] for row in journal["helpers"]), (
        "Build both native input helpers before source acceptance"
        if installed_root is None
        else "Installed input runtime is missing native helpers"
    )
    sys.path.insert(0, str(root))
    return root


def record_input_origins(root: Path, journal: dict, names: tuple[str, ...]) -> None:
    """Verify actual imports against the selected root without logging its prefix."""
    import sys

    from tools.windows.visual.tray_acceptance import runtime_origins

    journal.setdefault("module_origins", {}).update(
        runtime_origins(
            root,
            {name: sys.modules[name].__file__ for name in names},
        )
    )
