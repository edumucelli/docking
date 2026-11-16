"""Run one native visual target, or list the supported desktop matrix."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from tests.visual.targets import ROOT, TARGETS, Target, target_named

LINUX = ROOT / "tests/visual/shared/linux"


def commands(
    target: Target, output: Path, installed_root: Path | None
) -> list[list[str]]:
    """Keep platform runners and their independent assertions intact."""
    if target.system == "windows":
        runtime = ["--installed-root", str(installed_root)] if installed_root else []
        return [
            [
                sys.executable,
                "-m",
                "tests.visual.backends.windows.smoke",
                "--placement-only",
                "--position",
                edge,
                "--output",
                str(output / f"placement-{edge}"),
                *runtime,
            ]
            for edge in ("bottom", "top", "left", "right")
        ] + [
            [
                sys.executable,
                "-m",
                f"tests.visual.backends.windows.{module}",
                "--output",
                str(output / module),
                *(runtime if module != "fullscreen_smoke" else []),
                *(
                    ["--multiple-windows", "--uncataloged"]
                    if module == "runtime_pin_smoke"
                    else []
                ),
            ]
            for module in ("smoke", "runtime_pin_smoke", "fullscreen_smoke")
        ]
    if target.services == "x11":
        return [
            [
                sys.executable,
                "-m",
                "tests.visual.backends.x11.run",
                "--desktop",
                target.desktop,
                "--minimum",
                "--output",
                str(output),
            ]
        ]
    result = []
    if target.backend != "reduced":
        result.append(
            [
                "bash",
                str(LINUX / "matrix.sh"),
                "--compositor",
                target.name,
                "--case",
                "placement-bottom",
                "--case",
                "placement-top",
                "--case",
                "placement-left",
                "--case",
                "placement-right",
                "--case",
                "placement-bottom-gap",
                "--geometry-only",
                "--require-supported",
            ]
        )
    result.append(["bash", str(LINUX / "native_services.sh"), target.name, "launcher"])
    if target.services == "cinnamon":
        result.append(["bash", str(target.directory / "services.sh")])
    elif target.services in {"visual", "foreign", "treeland"}:
        result.append(
            [
                "bash",
                str(LINUX / "native_services.sh"),
                target.name,
                target.services,
            ]
        )
    if target.services == "foreign":
        result.append(
            [
                "bash",
                str(LINUX / "matrix.sh"),
                "--compositor",
                target.name,
                "--case",
                "autohide-bottom",
                "--geometry-only",
                "--require-supported",
            ]
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--list", action="store_true")
    choice.add_argument("--target", choices=[target.name for target in TARGETS])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--installed-root", type=Path)
    args = parser.parse_args()
    if args.list:
        print(json.dumps([asdict(target) for target in TARGETS], indent=2))
        return 0
    target = target_named(args.target)
    system = "windows" if sys.platform == "win32" else sys.platform.rstrip("0123456789")
    if target.system != system:
        parser.error(
            f"{target.name} requires {target.system}; run it in its native CI job"
        )
    if args.installed_root and system != "windows":
        parser.error("--installed-root is only supported by Windows targets")
    output = (
        args.output or ROOT / "artifacts/visual" / target.name / str(time.time_ns())
    ).resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("--output must be empty")
    output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "LAB_GNOME_BRIDGE": "1" if target.name == "gnome" else "0"}
    if system == "linux":
        image = env.get("DOCKING_LAB_IMAGE", f"docking-lab:{target.image_target}")
        if subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode:
            subprocess.run(
                [
                    "docker",
                    "build",
                    "-f",
                    str(LINUX / target.image_file),
                    "--target",
                    target.image_target,
                    "-t",
                    image,
                    str(LINUX),
                ],
                check=True,
            )
        env["DOCKING_LAB_IMAGE"] = image
        if (
            target.virtual_gpu
            and not env.get("DOCKING_VISUAL_GUEST")
            and not env.get("DOCKING_LAB_RENDER_NODE")
        ):
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.linux.visual_vm",
                    "--target",
                    target.name,
                    "--image",
                    image,
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=False,
            ).returncode
    selected = commands(target, output, args.installed_root)
    if system == "linux" and target.services == "x11":
        bindings = []
        for name, entry in (
            ("passwd", f"lab:x:{os.getuid()}:{os.getgid()}:lab:/tmp:/bin/sh\n"),
            ("group", f"lab:x:{os.getgid()}:\n"),
        ):
            path = output / f".{name}"
            path.write_bytes(
                subprocess.check_output(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--entrypoint",
                        "cat",
                        env["DOCKING_LAB_IMAGE"],
                        f"/etc/{name}",
                    ]
                )
                + entry.encode()
            )
            bindings.extend(["-v", f"{path}:/etc/{name}:ro"])
        selected = [
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "-e",
                "HOME=/tmp",
                "-e",
                "PYTHONPATH=/src",
                "-v",
                f"{ROOT}:/src:ro",
                "-v",
                f"{output}:/evidence",
                *bindings,
                "-w",
                "/src",
                env["DOCKING_LAB_IMAGE"],
                "python3",
                "-m",
                "tests.visual.backends.x11.run",
                "--desktop",
                target.desktop,
                "--minimum",
                "--output",
                "/evidence/session",
            ]
        ]
    results = []
    for index, command in enumerate(selected):
        env["DOCKING_VISUAL_OUTPUT"] = str(output / str(index))
        status = subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
        results.append(
            {
                "command": command,
                "status": "pass" if status == 0 else "fail",
                "exit_code": status,
            }
        )
        (output / "target.json").write_text(
            json.dumps({"target": asdict(target), "results": results}, indent=2)
        )
        if status:
            return status
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
