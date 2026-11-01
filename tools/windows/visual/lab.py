"""Submit durable jobs to the interactive Windows VM test runner."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--share", type=Path, required=True)
    parser.add_argument("--wait", type=float, default=0)
    parser.add_argument("--cwd")
    parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--container", default="docking-windows-lab")
    parser.add_argument(
        "action",
        choices=(
            "configure-share",
            "ready",
            "status",
            "screenshot",
            "run",
            "start",
            "stop",
        ),
    )
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    lab = args.share / "lab"
    if args.action == "configure-share":
        pending = [
            p
            for p in (lab / "jobs").glob("*.started.json")
            if not p.with_name(
                p.name.replace(".started.json", ".response.json")
            ).exists()
        ]
        if pending:
            parser.error(
                "Wait for claimed jobs to finish before reconnecting the share"
            )
        # Host-side source edits must invalidate Windows' SMB cache. Dockur
        # regenerates this file at container startup; configure before each run.
        program = """from pathlib import Path
import re, subprocess
p = Path("/etc/samba/smb.conf")
s = p.read_text()
assert "[Data]" in s and "path = /shared" in s, "Unexpected lab share"
settings = "    smb2 leases = no\\n    oplocks = no\\n    level2 oplocks = no\\n"
if "    smb2 leases = no" not in s:
    p.write_text(s.replace("[global]\\n", "[global]\\n" + settings))
subprocess.run(["testparm", "-s"], check=True, stdout=subprocess.DEVNULL)
clients = subprocess.check_output(["smbstatus", "-b"], text=True)
subprocess.run(["smbcontrol", "all", "reload-config"], check=True)
for address in set(re.findall(r"ipv4:([0-9.]+):", clients)):
    subprocess.run(["smbcontrol", "smbd", "kill-client-ip", address], check=True)
print("Configured the live lab share without restarting Windows")
"""
        subprocess.run(
            ["docker", "exec", args.container, "python3", "-c", program], check=True
        )
        return
    if args.action == "ready":
        print((lab / "ready.json").read_text())
        print((lab / "heartbeat.json").read_text())
        return
    jobs = lab / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    job_id = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    job = {"action": args.action, "timeout": args.timeout}
    if args.action in {"run", "start"}:
        job["argv"] = args.argv
        if args.cwd:
            job["cwd"] = args.cwd
        job["env"] = dict(value.split("=", 1) for value in args.env)
    elif args.action == "stop":
        job["job_id"] = args.argv[0]
    temporary = jobs / f"{job_id}.tmp"
    temporary.write_text(json.dumps(job), encoding="utf-8")
    temporary.rename(jobs / f"{job_id}.request.json")
    print(json.dumps({"job_id": job_id}), flush=True)
    response = jobs / f"{job_id}.response.json"
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        if response.exists():
            print(response.read_text(encoding="utf-8"))
            return
        time.sleep(0.25)


if __name__ == "__main__":
    main()
