"""Independent native tray providers, valid ABI cases and protocol assertions.

The compiled clients use the public Shell API directly. Protocol reports do not
establish physical input or visual acceptance; tray_smoke supplies that evidence.
"""

from __future__ import annotations

import os
import struct
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from tools.windows.bundle import command_path
from tools.windows.visual.desktop_driver import read_shared_json, write_shared_json

_SHAPES = (
    ("hwnd", 0, "full"),
    ("hwnd", 3, "full"),
    ("hwnd", 4, "full"),
    ("guid", 0, "full"),
    ("guid", 3, "full"),
    ("guid", 4, "full"),
    ("hwnd", 0, "v1"),
    ("hwnd", 3, "v2"),
    ("hwnd", 4, "v2"),
    ("guid", 4, "v3"),
)
_ACTIONS = frozenset(
    (
        "add",
        "delete",
        "showtip",
        "tip",
        "focus",
        "balloon",
        "balloon-clear",
        "rect",
        "menu",
        "snapshot",
        "quit",
        "hide",
        "show",
        "owner-window",
    )
)


@dataclass(frozen=True)
class ProviderCase:
    bits: int = 64
    encoding: str = "unicode"
    identity: str = "guid"
    version: int = 4
    size: str = "full"

    def __post_init__(self):
        if self.bits not in (32, 64) or self.encoding not in ("unicode", "ansi"):
            raise ValueError("Invalid native tray architecture/encoding")
        if self.identity not in ("guid", "hwnd") or self.version not in (0, 3, 4):
            raise ValueError("Invalid tray identity/protocol version")
        if self.size not in ("v1", "v2", "v3", "full"):
            raise ValueError("Invalid NOTIFYICONDATA size")
        if self.size == "v1" and self.version != 0:
            raise ValueError("V1 does not contain uVersion")
        if self.identity == "guid" and self.size in ("v1", "v2"):
            raise ValueError("GUID identity requires a V3/full structure")

    @property
    def name(self) -> str:
        return (
            f"{self.bits}-{self.encoding}-{self.identity}-v{self.version}-{self.size}"
        )

    @property
    def native_size(self) -> int:
        # SDK FIELD_OFFSET macros describe native structures, not the shell's
        # canonical 32-bit-handle Unicode wire format decoded by Docking.
        wide = self.encoding == "unicode"
        if self.size == "v1":
            return (40 if self.bits == 64 else 24) + (128 if wide else 64)
        base = (
            (952 if self.bits == 64 else 936)
            if wide
            else (504 if self.bits == 64 else 488)
        )
        return (
            base + {"v2": 0, "v3": 16, "full": 24 if self.bits == 64 else 20}[self.size]
        )

    def configuration(self, label: str, guid: str, *, seconds: int = 120) -> dict:
        if not 1 <= seconds <= 600:
            raise ValueError("Provider lifetime must be between 1 and 600 seconds")
        return {
            "bits": self.bits,
            "label": label,
            "guid": guid,
            "identity": self.identity,
            "uid": 731 if self.identity == "hwnd" or label == "A" else 732,
            "version": self.version,
            "encoding": self.encoding,
            "size": self.size,
            "show_tip": False,
            "rich_popup": True,
            "seconds": seconds,
        }


def provider_cases() -> tuple[ProviderCase, ...]:
    return tuple(
        ProviderCase(bits, encoding, identity, version, size)
        for bits in (64, 32)
        for encoding in ("unicode", "ansi")
        for identity, version, size in _SHAPES
    )


def build_provider(directory: Path, bits: int) -> Path:
    if bits not in (32, 64):
        raise ValueError("Provider architecture must be 32 or 64")
    directory.mkdir(parents=True, exist_ok=True)
    compiler = (
        Path(os.environ["SYSTEMROOT"]) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
    )
    source = Path(__file__).with_suffix(".cs")
    target = directory / f"tray-provider-{bits}.exe"
    result = subprocess.run(
        [
            command_path(compiler),
            "/nologo",
            "/target:winexe",
            "/platform:" + ("x64" if bits == 64 else "x86"),
            "/reference:System.Windows.Forms.dll",
            "/reference:System.Drawing.dll",
            "/reference:System.Web.Extensions.dll",
            "/out:" + command_path(target),
            command_path(source),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    diagnostic = (result.stdout + result.stderr).replace(
        str(source.parent), "<tool-source>"
    )
    diagnostic = diagnostic.replace(str(directory), "<owned-build>")
    diagnostic = diagnostic.replace(command_path(source.parent), "<tool-source>")
    diagnostic = diagnostic.replace(command_path(directory), "<owned-build>")
    (directory / f"build-{bits}.log").write_text(diagnostic, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"Independent tray provider compilation failed: {bits}-bit")
    return target


class NativeProvider:
    """One bounded provider PID, its immutable commands and generated resources."""

    def __init__(self, executable: Path, directory: Path, configuration: dict):
        self.executable, self.directory, self.configuration = (
            executable,
            directory,
            configuration,
        )
        self.process: subprocess.Popen | None = None
        self.revision = 0
        self._state: dict = {}
        self._log = None
        self.forced_cleanup = False
        self._started = time.monotonic()
        self._lifecycle = {
            "schema": 1,
            "startup_stage": "not-started",
            "cleanup_stage": "not-started",
            "readiness_timeout_s": 15,
            "close_timeout_s": 10,
            "terminate_timeout_s": 5,
        }

    @staticmethod
    def _failure(error):
        result = {"type": type(error).__name__}
        if isinstance(error, OSError):
            if error.errno is not None:
                result["errno"] = error.errno
            # Python/CRT errors can omit this external Windows field.
            with suppress(AttributeError):
                if error.winerror is not None:
                    result["winerror"] = error.winerror
        return result

    def _save_lifecycle(self):
        try:
            stage = self._state.get("stage")
            self._lifecycle.update(
                pid=self.process.pid if self.process is not None else None,
                returncode=self.process.poll() if self.process is not None else None,
                elapsed_ms=round((time.monotonic() - self._started) * 1000),
                forced_cleanup=self.forced_cleanup,
                state_present=(self.directory / "state.json").is_file(),
                state_stage=stage[:32] if isinstance(stage, str) else None,
                log_closed=self._log is None or self._log.closed,
            )
            write_shared_json(
                self.directory / "provider-lifecycle.json", self._lifecycle
            )
        except OSError as error:
            # Observation must not replace a spawn/readiness/cleanup exception.
            self._lifecycle["journal_error"] = self._failure(error)

    def read(self) -> dict:
        try:
            value = read_shared_json(self.directory / "state.json")
        except (FileNotFoundError, PermissionError, ValueError):
            # External process/SMB publication may briefly leave no complete
            # report. Keep the last complete identity while waiting for its ack.
            return self._state
        if not isinstance(value, dict):
            raise ValueError("Invalid native tray provider journal")
        self._state = value
        if "error" in value:
            raise RuntimeError(f"Native tray provider reported {value['error']}")
        return value

    def wait(self, predicate, message: str, seconds: float = 15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            state = self.read()
            if state and predicate(state):
                return state
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError("Native tray provider exited before " + message)
            time.sleep(0.05)
        raise TimeoutError("Native tray provider did not " + message)

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=False)
        path = self.directory / "configuration.json"
        try:
            self._lifecycle["startup_stage"] = "configuration"
            self._save_lifecycle()
            write_shared_json(path, self.configuration)
            self._lifecycle["startup_stage"] = "open-log"
            self._log = (self.directory / "provider.log").open("w", encoding="utf-8")
            self._lifecycle["startup_stage"] = "spawn"
            self._save_lifecycle()
            self.process = subprocess.Popen(
                [str(self.executable), str(self.directory), str(path)],
                stdout=self._log,
                stderr=subprocess.STDOUT,
            )
            self._lifecycle["startup_stage"] = "readiness"
            self._save_lifecycle()
            state = self.wait(lambda value: value["stage"] == "ready", "become ready")
            assert state["pid"] == self.process.pid
            for field in ("bits", "encoding", "size", "version"):
                assert state[field] == self.configuration[field]
            self._lifecycle["startup_stage"] = "ready"
            self._save_lifecycle()
            return self
        except BaseException as error:
            self._lifecycle["startup_error"] = self._failure(error)
            self._lifecycle["startup_failed_at"] = self._lifecycle["startup_stage"]
            self._lifecycle["startup_stage"] = "failed"
            try:
                self.close()
            except BaseException as cleanup_error:
                self._lifecycle["cleanup_error"] = self._failure(cleanup_error)
            self._save_lifecycle()
            raise

    def command(self, action: str, **fields) -> dict:
        if action not in _ACTIONS:
            raise ValueError("Unknown native tray provider action")
        state = self.read()
        self.revision = max(self.revision, state["command_revision"]) + 1
        target = self.directory / f"command-{self.revision:06d}.json"
        if target.exists() or target.with_suffix(".tmp").exists():
            raise FileExistsError("Another driver has an unacknowledged tray command")
        write_shared_json(
            target,
            {"action": action, **fields},
        )
        return self.wait(
            lambda value: value["command_revision"] >= self.revision,
            "acknowledge " + action,
        )

    def close(self) -> None:
        started = time.monotonic()
        try:
            self._lifecycle["cleanup_stage"] = "no-owned-process"
            if self.process is not None:
                self._lifecycle["cleanup_stage"] = "waiting-exit"
                self._save_lifecycle()
                if self.process.poll() is None:
                    (self.directory / "stop").touch()
                    try:
                        self.process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.forced_cleanup = True
                        self._lifecycle["cleanup_stage"] = "terminating"
                        self._save_lifecycle()
                        self.process.terminate()
                        self.process.wait(timeout=5)
                state = self.read()
                if (
                    self.process.returncode
                    or self.forced_cleanup
                    or not state
                    or state["stage"] != "closed"
                    or state["registered"]
                ):
                    raise RuntimeError("Native tray provider cleanup did not verify")
                self._lifecycle["cleanup_stage"] = "verified"
        except BaseException as error:
            self._lifecycle["cleanup_stage"] = "failed"
            self._lifecycle["cleanup_error"] = self._failure(error)
            raise
        finally:
            try:
                if self._log is not None:
                    self._log.close()
            except BaseException as error:
                self._lifecycle["cleanup_stage"] = "failed"
                self._lifecycle["log_error"] = self._failure(error)
                raise
            finally:
                self._lifecycle["cleanup_elapsed_ms"] = round(
                    (time.monotonic() - started) * 1000
                )
                self._save_lifecycle()

    def __exit__(self, _kind, _value, _traceback):
        self.close()


def api_call(state: dict, operation: int) -> dict:
    return next(
        call for call in reversed(state["calls"]) if call["operation"] == operation
    )


def _validate_showtip_wire(case, row, requested_flags, first):
    prefix = bytes.fromhex(row["raw_prefix_hex"])
    assert len(prefix) == 32
    signature, operation, size, window, uid, flags = struct.unpack_from("<6I", prefix)
    assert signature == 0x34753423 and operation == row["operation"] == 1
    assert size == row["wire_cb_size"] and size in (152, 936, 952, 956)
    assert 8 + size <= row["payload_length"] <= 8192
    assert row["native_cb_size"] == case.native_size
    assert window == row["window"] == first["window"] & 0xFFFFFFFF
    assert uid == row["uid"] == first["uid"]
    # Shell32 converts every native structure to its canonical wire layout.
    # Legacy cbSize omits Vista-era SHOWTIP support even when requested. This
    # source-size policy is independently observed in the native header trace;
    # the product decoder must preserve the actual delivered flags unchanged.
    expected = requested_flags if case.size == "full" else requested_flags & ~0x80
    assert flags == row["raw_flags"] == row["flags"] == expected
    assert row["accepted"] and row["forwarded"]
    assert row["version"] == 0  # NIM_MODIFY does not request a protocol version.
    item = row["projection"]
    assert item is not None and item["version"] == case.version
    assert item["show_tip"] is bool(flags & 0x80)
    return bool(flags & 0x80)


def validate_protocol(
    case: ProviderCase, observations: dict, *, native_wire: list[dict] | None
) -> None:
    """Assert native API/identity facts; never infer hover or visual acceptance."""
    first = observations["first-add"]["providers"]["A"]
    assert first["bits"] == case.bits and first["native_size"] == case.native_size
    assert api_call(first, 0)["accepted"]
    if case.size != "v1" and case.version:
        assert (
            api_call(first, 4)["accepted"] and first["callback_version"] == case.version
        )
    previous = observations["first-add"]
    for name, policy in (("showtip-only-on", True), ("showtip-only-off", False)):
        current = observations[name]["providers"]["A"]
        call = api_call(current, 1)
        assert call["accepted"]
        assert call["flags"] == (0x20 if case.identity == "guid" else 0) | (
            0x80 if policy else 0
        )
        assert current["tip"] == first["tip"]
        projection = observations[name]["projection"]
        if projection is not None:
            assert native_wire is not None
            start, end = (
                previous["native_wire_count"],
                observations[name]["native_wire_count"],
            )
            assert 0 <= start < end <= len(native_wire)
            candidates = [
                row
                for row in native_wire[start:end]
                if row["operation"] == 1
                and row["window"] == first["window"] & 0xFFFFFFFF
                and row["uid"] == first["uid"]
            ]
            assert candidates
            delivered_policy = _validate_showtip_wire(
                case, candidates[-1], call["flags"], first
            )
            item = next(iter(projection.values()))
            assert item["tip"] == first["tip"]
            assert item["show_tooltip"] is (case.version < 4 or delivered_policy)
        previous = observations[name]
    second = observations["second-add"]["providers"]["B"]
    assert api_call(second, 0)["accepted"] is (case.identity == "hwnd")
    if case.identity == "guid":
        projection = observations["second-add"]["projection"]
        if projection is not None:
            assert len(projection) == 1
            item = next(iter(projection.values()))
            assert (item["owner"], item["window"], item["uid"]) == (
                first["pid"],
                first["window"] & 0xFFFFFFFF,
                first["uid"],
            )
        modified = observations["second-provider-modify"]
        assert api_call(modified["providers"]["B"], 1)["accepted"]
        if modified["projection"] is not None:
            item = next(iter(modified["projection"].values()))
            assert item["window"] == first["window"] & 0xFFFFFFFF
            assert item["tip"] == modified["providers"]["B"]["tip"]
