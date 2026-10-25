"""Interactive Windows lab runner, controlled through the dedicated SMB share.

This is test infrastructure, never installed by Docking itself. Only the local
host's lab share controls jobs. Each request gets a durable response, so a
controller timeout does not cause an already-running job to be repeated.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import platform
import subprocess
import sys
import time
import traceback
from pathlib import Path


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    payload = json.dumps(value, indent=2)
    while True:
        try:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(path)
            return
        except OSError:
            # SMB readers can briefly deny replacement. Keep the completed
            # result in memory rather than executing the request a second time.
            logging.exception("Waiting to publish %s", path.name)
            time.sleep(1)


def windows_release() -> dict[str, str | int]:
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    ) as key:
        return {
            name: winreg.QueryValueEx(key, name)[0]
            for name in ("EditionID", "DisplayVersion", "CurrentBuildNumber", "UBR")
        }


def main() -> None:
    logging.basicConfig(
        filename=Path(__file__).with_name("agent.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(None, False, "Local\\DockingLabAgent")
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        return
    runtime = Path(sys.executable).parent
    os.environ["PATH"] = str(runtime) + os.pathsep + os.environ.get("PATH", "")
    os.environ["PYGI_DLL_PATH"] = str(runtime)
    # Retain the handle for the lifetime of this process.
    _dll_directory = os.add_dll_directory(str(runtime))
    share = Path(r"\\host.lan\Data")
    lab = share / "lab"
    while True:
        try:
            lab.mkdir(exist_ok=True)
            break
        except OSError:
            # Windows startup may run before the shared drive is reachable.
            time.sleep(1)
    jobs = lab / "jobs"
    jobs.mkdir(exist_ok=True)
    running: dict[str, subprocess.Popen[bytes]] = {}
    write_json(
        lab / "ready.json",
        {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "windows_version": str(sys.getwindowsversion()),
            "windows_release": windows_release(),
            "pid": os.getpid(),
            "started_at": time.time(),
        },
    )
    while True:
        write_json(lab / "heartbeat.json", {"pid": os.getpid(), "time": time.time()})
        for request in sorted(jobs.glob("*.request.json")):
            stem = request.name.removesuffix(".request.json")
            response = jobs / f"{stem}.response.json"
            if response.exists():
                continue
            started = jobs / f"{stem}.started.json"
            if started.exists():
                write_json(
                    response,
                    {
                        "ok": False,
                        "error": "Previous agent exited after claiming this job. "
                        "Inspect its processes and artifacts before "
                        "submitting a new job.",
                    },
                )
                continue
            write_json(started, {"agent_pid": os.getpid(), "time": time.time()})
            try:
                job = json.loads(request.read_text(encoding="utf-8"))
                action = job["action"]
                if action in {"run", "start"}:
                    env = {**os.environ, **job.get("env", {})}
                    options = {
                        "cwd": job.get("cwd", str(share / "repo")),
                        "env": env,
                        "creationflags": subprocess.CREATE_NO_WINDOW,
                    }
                    if action == "run":
                        result = subprocess.run(
                            job["argv"],
                            **options,
                            capture_output=True,
                            text=True,
                            timeout=job.get("timeout", 180),
                        )
                        value = {
                            "exit_code": result.returncode,
                            "stdout": result.stdout,
                            "stderr": result.stderr,
                        }
                    else:
                        with (jobs / f"{stem}.log").open("w", encoding="utf-8") as log:
                            child = subprocess.Popen(
                                job["argv"],
                                **options,
                                stdout=log,
                                stderr=subprocess.STDOUT,
                            )
                        running[stem] = child
                        value = {"pid": child.pid}
                elif action == "screenshot":
                    from PIL import ImageGrab

                    filename = jobs / f"{stem}.png"
                    ImageGrab.grab(all_screens=True).save(filename)
                    value = {"image": filename.name}
                elif action == "status":
                    value = {
                        key: {"pid": p.pid, "exit_code": p.poll()}
                        for key, p in running.items()
                    }
                elif action == "stop":
                    child = running[job["job_id"]]
                    child.terminate()
                    value = {"exit_code": child.wait(timeout=10)}
                else:
                    raise ValueError(f"Unknown job action: {action}")
                write_json(response, {"ok": True, **value})
            except Exception:
                write_json(response, {"ok": False, "error": traceback.format_exc()})
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Lab agent exited")
        raise
