"""Build and own a generated-frame virtual camera in an elevated Windows lab.

Run with a fresh local --output directory and an explicit --journal path. The
camera is current-user/session scoped; write JOURNAL_DIRECTORY/stop to close it.
After a crashed owner, --recover JOURNAL removes only its owned registration.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

CLSID = "{4A3B3ECF-5225-4AF0-B7F5-633F1CB4D915}"
NAME = "Docking Generated Camera Fixture"
KEY = "SOFTWARE\\Classes\\CLSID\\" + CLSID


def _publish(path, report):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.02)


def _register(dll):
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, KEY):
            pass
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError(
            "The fixture COM class already exists; recover its journal first"
        )
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, KEY, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, NAME)
    with winreg.CreateKeyEx(
        winreg.HKEY_LOCAL_MACHINE, KEY + "\\InprocServer32", 0, winreg.KEY_WRITE
    ) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(dll))
        winreg.SetValueEx(key, "ThreadingModel", 0, winreg.REG_SZ, "Both")


def _unregister(dll):
    import winreg

    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, KEY)
    except FileNotFoundError:
        return
    with root:
        try:
            label = winreg.QueryValueEx(root, "")[0]
        except FileNotFoundError:
            if winreg.QueryInfoKey(root)[:2] != (0, 0):
                raise RuntimeError("Fixture COM ownership is incomplete") from None
            label = None
        if label not in (None, NAME):
            raise RuntimeError("Fixture COM ownership changed")
    try:
        server = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, KEY + "\\InprocServer32")
    except FileNotFoundError:
        server = None
    if server is not None:
        with server:
            try:
                path = Path(winreg.QueryValueEx(server, "")[0])
            except FileNotFoundError:
                if winreg.QueryInfoKey(server)[:2] != (0, 0):
                    raise RuntimeError(
                        "Fixture COM server ownership is incomplete"
                    ) from None
                path = dll
            if path != dll:
                raise RuntimeError("Fixture COM server ownership changed")
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, KEY + "\\InprocServer32")
    winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, KEY)


def _build(output, compiler):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a fresh local build directory")
    if not output.drive or str(output).startswith("\\\\"):
        raise ValueError("The COM DLL requires a local Windows path")
    output.mkdir(parents=True, exist_ok=True)
    source = output / "source.cpp"
    shutil.copy2(Path(__file__).with_name("camera_source.cpp"), source)
    command = [
        str(compiler),
        "-std=c++17",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-shared",
        "-static-libgcc",
        "-static-libstdc++",
        "-Wl,--no-undefined",
        "-o",
        str(output / "source.dll"),
        str(source),
        "-lmfplat",
        "-lmf",
        "-lmfuuid",
        "-luuid",
        "-lole32",
        "-lksuser",
        "-Wl,-Bstatic",
        "-lwinpthread",
        "-Wl,-Bdynamic",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        log, _ = process.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=15,
        )
        process.communicate(timeout=10)
        raise
    (output / "build.log").write_text(log, encoding="utf-8")
    if process.returncode:
        raise RuntimeError(
            "Fixture compilation failed; see " + str(output / "build.log")
        )
    return output / "source.dll"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--recover", type=Path)
    parser.add_argument(
        "--compiler", type=Path, default=Path(r"C:\msys64\ucrt64\bin\g++.exe")
    )
    parser.add_argument("--seconds", type=float, default=180)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires an elevated native Windows 11 lab")
    if args.recover is None and (args.output is None or args.journal is None):
        parser.error("Supply --output and --journal, or --recover")
    if not 0 < args.seconds <= 600:
        parser.error("The fixture lifetime must be at most 600 seconds")
    if not ctypes.windll.shell32.IsUserAnAdmin():
        parser.error("The fixture requires elevation to register its COM class")
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    import psutil

    from docking.applets.camshield.windows_sensor import inventory
    from docking.platform.backends.windows.com import (
        HRESULT,
        apartment,
        method,
        release,
    )
    from docking.platform.backends.windows.ffi import bind

    if args.recover:
        journal = args.recover.resolve()
        report = json.loads(journal.read_text(encoding="utf-8"))
        if report.get("clsid") != CLSID or report.get("name") != NAME:
            raise ValueError("Not a generated-camera fixture journal")
        try:
            process = psutil.Process(report["pid"])
            if process.create_time() == report["process_created_at"]:
                raise RuntimeError(
                    "Fixture owner is still running; request its normal stop"
                )
        except psutil.NoSuchProcess:
            pass
        dll = Path(report["dll"])
        if hashlib.sha256(dll.read_bytes()).hexdigest() != report["dll_sha256"]:
            raise ValueError("Fixture DLL differs from its ownership journal")
        report["recovered"] = True
    else:
        journal = args.journal.resolve()
        if journal.exists() or (journal.parent / "stop").exists():
            raise ValueError("Use a fresh journal directory")
        dll = _build(args.output.resolve(), args.compiler)
        report = {
            "pid": os.getpid(),
            "process_created_at": psutil.Process().create_time(),
            "clsid": CLSID,
            "name": NAME,
            "dll": str(dll),
            "dll_sha256": hashlib.sha256(dll.read_bytes()).hexdigest(),
            "lifetime": "session",
            "access": "current-user",
            "stage": "prepared",
        }
        journal.parent.mkdir(parents=True, exist_ok=True)

    pointer, u32 = ctypes.c_void_p, ctypes.c_uint32
    camera = pointer()
    registered = bool(args.recover)
    with apartment(multithreaded=True) as ole:
        mf = ctypes.WinDLL("mfplat")

        def check(status):
            if status < 0:
                raise OSError(f"Virtual camera failed: 0x{status & 0xFFFFFFFF:08x}")

        check(bind(mf, "MFStartup", HRESULT, u32, u32)(0x20070, 0))
        try:
            if not args.recover:
                report["baseline"] = inventory(ole)
                if report["baseline"]:
                    raise RuntimeError("Use an owned lab without existing cameras")
                _publish(journal, report)
                # Journal ownership precedes the first registry mutation.
                registered = True
                try:
                    _register(dll)
                except FileExistsError:
                    registered = False
                    raise
            library = ctypes.WinDLL("mfsensorgroup")
            create = bind(
                library,
                "MFCreateVirtualCamera",
                HRESULT,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
                pointer,
                u32,
                ctypes.POINTER(pointer),
            )
            check(create(0, 0, 0, NAME, CLSID, None, 0, ctypes.byref(camera)))
            if args.recover:
                report["stage"] = "recovering"
                _publish(journal, report)
            else:
                report["stage"] = "starting"
                _publish(journal, report)
                check(method(camera, 36, HRESULT, pointer)(camera, None))
                report["devices"] = inventory(ole)
                if len(report["devices"]) != 1 or not report["devices"][0][
                    "name"
                ].startswith(NAME):
                    raise RuntimeError("Generated camera was not published")
                # Publishing can leave a native controlling client attributed
                # to this owner. A real read/stop cycle both validates frames
                # and establishes the fixture's idle baseline.
                report["stage"] = "warming"
                _publish(journal, report)
                warmup = dll.parent / "warmup"
                result = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("camshield_capture.py")),
                        "--device",
                        report["devices"][0]["id"],
                        "--output",
                        str(warmup),
                        "--seconds",
                        "2",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                (dll.parent / "warmup.log").write_text(
                    result.stdout + result.stderr, encoding="utf-8"
                )
                if result.returncode:
                    raise RuntimeError("Generated camera warmup failed")
                report["warmup"] = json.loads(
                    (warmup / "capture.json").read_text(encoding="utf-8")
                )
                if not report["warmup"].get("ok") or not report["warmup"].get("closed"):
                    raise RuntimeError("Generated camera warmup did not close normally")
                report["stage"] = "ready"
                _publish(journal, report)
                print(json.dumps(report), flush=True)
                deadline = time.monotonic() + args.seconds
                while (
                    time.monotonic() < deadline
                    and not (journal.parent / "stop").exists()
                ):
                    time.sleep(0.2)
        except BaseException as exc:
            report["error"] = repr(exc)
            raise
        finally:
            try:
                if camera.value:
                    report["stop_status"] = method(camera, 37, HRESULT)(camera)
                    report["remove_status"] = method(camera, 38, HRESULT)(camera)
                    report["shutdown_status"] = method(camera, 43, HRESULT)(camera)
                    release(camera)
                    for key in ("stop_status", "remove_status", "shutdown_status"):
                        check(report[key])
                if registered:
                    _unregister(dll)
                    report["com_removed"] = True
                report["final_inventory"] = inventory(ole)
                report["stage"] = "closed"
            except BaseException as exc:
                report["cleanup_error"] = repr(exc)
                raise
            finally:
                bind(mf, "MFShutdown", HRESULT)()
                _publish(journal, report)
                print(json.dumps(report), flush=True)


def _exclusive_main():
    if sys.platform != "win32":
        return main()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateMutexW
    create.restype = ctypes.c_void_p
    create.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_wchar_p]
    close = kernel.CloseHandle
    close.argtypes = [ctypes.c_void_p]
    release = kernel.ReleaseMutex
    release.argtypes = [ctypes.c_void_p]
    ctypes.set_last_error(0)
    handle = create(None, True, "Global\\DockingGeneratedCameraFixture-" + CLSID)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        close(handle)
        raise RuntimeError("Another generated-camera fixture controller is running")
    try:
        return main()
    finally:
        release(handle)
        close(handle)


if __name__ == "__main__":
    _exclusive_main()
