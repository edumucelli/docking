"""Own browser processes and profile replacement without blocking GTK."""

from __future__ import annotations

import ctypes
import hashlib
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from docking.applets.webapp.implementations.windows.companion.protocol import (
    Configuration,
    encode,
    read,
)

SHUTDOWN_GRACE_SECONDS = 8


class BrowserProcess:
    def __init__(
        self,
        executable: Path,
        configuration: Configuration,
        *,
        on_packet: Callable[[dict[str, Any]], None],
        on_closed: Callable[[], None],
        entrypoint: Path | None = None,
    ) -> None:
        self._executable, self._configuration = executable, configuration
        self._entrypoint = entrypoint or Path(__file__).with_name("__main__.py")
        self._on_packet, self._on_closed = on_packet, on_closed
        self._commands: queue.Queue[dict[str, Any] | None] = queue.Queue(128)
        self._process: subprocess.Popen[bytes] | None = None
        self._job = None
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._launched = threading.Event()
        self.closed = threading.Event()

    def start(self) -> None:
        threading.Thread(
            target=self._serve, name="webapp-companion", daemon=False
        ).start()

    def allow_foreground(self) -> None:
        if sys.platform == "win32":
            from docking.platform.native.windows.ffi import bind

            with self._lock:
                process = self._process
            if process is not None and process.poll() is None:
                bind(
                    ctypes.WinDLL("user32", use_last_error=True),
                    "AllowSetForegroundWindow",
                    ctypes.c_int32,
                    ctypes.c_uint32,
                )(process.pid)

    def send(self, packet: dict[str, Any]) -> None:
        if self._closing.is_set() or self.closed.is_set():
            raise OSError("Browser companion stopped")
        encode(packet)
        try:
            self._commands.put_nowait(packet)
        except queue.Full as error:
            self.stop()
            raise OSError("Browser companion command queue is full") from error

    def _write(
        self,
        process: subprocess.Popen[bytes],
        commands: queue.Queue[dict[str, Any] | None],
    ) -> None:
        try:
            assert process.stdin is not None
            while (packet := commands.get()) is not None:
                process.stdin.write(encode(packet))
                process.stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            if process.stdin is not None:
                with suppress(OSError):
                    process.stdin.close()

    def _deadline(
        self, process: subprocess.Popen[bytes], finished: threading.Event
    ) -> None:
        if finished.wait(SHUTDOWN_GRACE_SECONDS):
            return
        with self._lock:
            if self._process is not process:
                return
            if self._job is not None:
                self._job.close()
                self._job = None
            elif process.poll() is None:
                process.terminate()

    @staticmethod
    def _discard(commands: queue.Queue[dict[str, Any] | None]) -> None:
        while True:
            try:
                commands.get_nowait()
            except queue.Empty:
                break
        commands.put_nowait(None)

    def _run_child(self) -> int | None:
        writer = None
        restart: int | None = None
        process = None
        finished = threading.Event()
        commands = self._commands
        try:
            process = subprocess.Popen(
                [str(self._executable), "-u", str(self._entrypoint)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "PYTHONUTF8": "1"},
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
            with self._lock:
                self._process = process
                if sys.platform == "win32":
                    from docking.platform.native.windows.process_job import ProcessJob

                    self._job = ProcessJob()
                    # CPython's owned handle avoids a second lookup by reusable PID.
                    self._job.assign(int(process._handle))
            self._launched.set()
            # Child waits for configuration before constructing QApplication or
            # Chromium, so descendants inherit the assigned Windows job.
            assert process.stdin is not None
            process.stdin.write(encode(self._configuration.packet()))
            process.stdin.flush()
            self._launched.set()
            writer = threading.Thread(
                target=self._write,
                args=(process, commands),
                name="webapp-commands",
                daemon=False,
            )
            writer.start()
            assert process.stdout is not None
            while packet := read(process.stdout):
                identifier = packet.get("id")
                if (
                    packet.get("event") == "restart"
                    and type(identifier) is int
                    and 0 < identifier < 2**31
                ):
                    restart = identifier
                    self._discard(commands)
                    threading.Thread(
                        target=self._deadline,
                        args=(process, finished),
                        name="webapp-clear-close",
                        daemon=False,
                    ).start()
                else:
                    self._on_packet(packet)
        finally:
            self._launched.set()
            with self._lock:
                if self._job is not None:
                    self._job.close()
                    self._job = None
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                process.wait()
                if process.stdout is not None:
                    process.stdout.close()
            finished.set()
            self._discard(commands)
            if writer is not None:
                writer.join()
            if process is not None and process.stdin is not None:
                with suppress(OSError):
                    process.stdin.close()
        return restart

    def _erase_profile(self) -> None:
        directory = self._configuration.directory
        if directory.is_symlink() or (
            sys.platform == "win32"
            and directory.exists()
            and directory.lstat().st_file_attributes & 0x400
        ):
            raise OSError("Browser directory is a link")
        deadline = time.monotonic() + 5
        while True:
            try:
                if directory.exists():
                    shutil.rmtree(directory)
                directory.mkdir(parents=True, exist_ok=True)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)

    def _serve(self) -> None:
        guard = None
        try:
            if sys.platform == "win32":
                from docking.platform.native.windows.process_job import ProfileMutex

                digest = hashlib.sha256(
                    str(self._configuration.directory).casefold().encode()
                ).hexdigest()
                guard = ProfileMutex("Local\\Docking.WebApp.Profile." + digest)
            self._configuration.directory.mkdir(parents=True, exist_ok=True)
            while not self._closing.is_set():
                restart = self._run_child()
                if restart is None or self._closing.is_set():
                    break
                try:
                    # All owned native processes have actually exited. Keep the
                    # profile mutex while deleting data and starting its replacement.
                    self._erase_profile()
                except OSError:
                    self._on_packet(
                        {"version": 1, "event": "cleared", "id": restart, "ok": False}
                    )
                    break
                self._commands = queue.Queue(128)
                self._on_packet(
                    {"version": 1, "event": "cleared", "id": restart, "ok": True}
                )
        except (OSError, ValueError):
            pass
        finally:
            self._launched.set()
            self._closing.set()
            if guard is not None:
                guard.close()
            self.closed.set()
            self._on_closed()

    def stop(self) -> None:
        with self._lock:
            if self._closing.is_set():
                return
            self._closing.set()
        while True:
            try:
                self._commands.get_nowait()
            except queue.Empty:
                break
        self._commands.put_nowait({"op": "stop"})
        self._commands.put_nowait(None)

        def finish() -> None:
            self._launched.wait()
            if self.closed.wait(SHUTDOWN_GRACE_SECONDS):
                return
            with self._lock:
                if self._job is not None:
                    self._job.close()
                    self._job = None
                elif self._process is not None and self._process.poll() is None:
                    self._process.terminate()

        threading.Thread(target=finish, name="webapp-close", daemon=False).start()
