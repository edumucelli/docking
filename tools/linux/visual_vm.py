"""Run a visual target with a software-rendered VirtIO GPU on hosted runners."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

from tests.visual.targets import ROOT, target_named

IMAGE = "debian-13-generic-amd64-20261001-2618.qcow2"
IMAGE_URL = f"https://cloud.debian.org/images/cloud/trixie/20261001-2618/{IMAGE}"
IMAGE_SHA512 = (
    "6f0f93335bdef4ccf523c4317cc663ea52ca23b862667785f3d6d186ab4674a9373"
    "85ccff0c55996b4b2e4c19e440cdf211e2a75ffa2f6548037ab43950c841d"
)


def verify_image(path: Path) -> bool:
    digest = hashlib.sha512()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == IMAGE_SHA512


def qemu_command(disk: Path, seed: Path, port: int) -> list[str]:
    return [
        "xvfb-run",
        "-a",
        "-s",
        "-screen 0 1280x720x24 +extension GLX",
        "qemu-system-x86_64",
        "-accel",
        "kvm" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "tcg",
        "-m",
        "4096",
        "-smp",
        "2",
        "-display",
        "sdl,gl=on",
        "-serial",
        "stdio",
        "-no-reboot",
        "-device",
        "virtio-vga-gl",
        "-drive",
        f"file={disk},if=virtio,format=qcow2",
        "-drive",
        f"file={seed},if=virtio,format=raw,readonly=on",
        "-netdev",
        f"user,id=net0,hostfwd=tcp:127.0.0.1:{port}-:22",
        "-device",
        "virtio-net-pci,netdev=net0",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    target_named(args.target)
    for command in (
        "qemu-system-x86_64",
        "xvfb-run",
        "qemu-img",
        "cloud-localds",
        "ssh",
        "scp",
        "ssh-keygen",
        "docker",
    ):
        if shutil.which(command) is None:
            parser.error(f"Missing VM prerequisite: {command}")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache = ROOT / "artifacts/visual-vm-cache"
    cache.mkdir(parents=True, exist_ok=True)
    image = cache / IMAGE
    if not image.exists() or not verify_image(image):
        pending = image.with_suffix(".download")
        try:
            with (
                urllib.request.urlopen(IMAGE_URL, timeout=60) as response,
                pending.open("wb") as stream,
            ):
                shutil.copyfileobj(response, stream)
            if not verify_image(pending):
                raise ValueError("Debian guest image failed its pinned SHA512 check")
            pending.replace(image)
        finally:
            pending.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="docking-visual-vm-") as temporary:
        work = Path(temporary)
        key = work / "key"
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-C",
                "docking-visual-ephemeral",
                "-N",
                "",
                "-f",
                str(key),
            ],
            check=True,
        )
        config = {
            "users": [
                {
                    "name": "visual",
                    "lock_passwd": True,
                    "groups": ["docker", "render", "video"],
                    "shell": "/bin/bash",
                    "sudo": "ALL=(ALL) NOPASSWD:ALL",
                    "ssh_authorized_keys": [
                        key.with_suffix(".pub").read_text().strip()
                    ],
                }
            ],
            "apt": {"preserve_sources_list": True},
            "write_files": [
                {
                    "path": "/etc/apt/apt.conf.d/99snapshot",
                    "content": 'Acquire::Check-Valid-Until "false";\n',
                },
                {
                    "path": "/etc/apt/mirrors/debian.list",
                    "content": "http://snapshot.debian.org/archive/debian/20261002T000000Z/\n",
                },
                {
                    "path": "/etc/apt/mirrors/debian-security.list",
                    "content": "http://snapshot.debian.org/archive/debian-security/20261002T000000Z/\n",
                },
            ],
            "groups": ["docker"],
            "packages": ["docker.io", "python3-pil", "python3-numpy", "python3-cairo"],
            "runcmd": [["systemctl", "enable", "--now", "docker"]],
        }
        user_data = work / "user-data"
        user_data.write_text("#cloud-config\n" + json.dumps(config))
        metadata = work / "meta-data"
        metadata.write_text(
            "instance-id: docking-visual\nlocal-hostname: docking-visual\n"
        )
        seed = work / "seed.img"
        subprocess.run(
            ["cloud-localds", str(seed), str(user_data), str(metadata)], check=True
        )
        disk = work / "guest.qcow2"
        subprocess.run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                str(image),
                str(disk),
                "24G",
            ],
            check=True,
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        options = [
            "-i",
            str(key),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=3",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            f"UserKnownHostsFile={work / 'known_hosts'}",
        ]
        ssh = ["ssh", *options, "-p", str(port), "visual@127.0.0.1"]
        scp = ["scp", *options, "-P", str(port)]
        with (output / "guest-boot.log").open("w") as log:
            guest = subprocess.Popen(
                qemu_command(disk, seed, port),
                stdout=log,
                stderr=subprocess.STDOUT,
                env={
                    **os.environ,
                    "LIBGL_ALWAYS_SOFTWARE": "1",
                    "SDL_VIDEODRIVER": "x11",
                },
            )
            try:
                deadline = time.monotonic() + 300
                while True:
                    if guest.poll() is not None:
                        raise RuntimeError(
                            "Virtual GPU guest exited; inspect guest-boot.log"
                        )
                    if (
                        subprocess.run(
                            [*ssh, "true"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        ).returncode
                        == 0
                    ):
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Virtual GPU guest did not start SSH")
                    time.sleep(1)
                subprocess.run(
                    [*ssh, "sudo cloud-init status --wait"], check=True, timeout=900
                )
                if args.target == "gamescope":
                    # Lavapipe exports its CPU-rendered buffers through udmabuf.
                    subprocess.run(
                        [
                            *ssh,
                            (
                                "sudo modprobe udmabuf && "
                                "sudo chgrp video /dev/udmabuf && "
                                "sudo chmod 660 /dev/udmabuf"
                            ),
                        ],
                        check=True,
                    )
                source = work / "source.tar"
                files = (
                    subprocess.check_output(
                        [
                            "git",
                            "ls-files",
                            "--cached",
                            "--others",
                            "--exclude-standard",
                            "-z",
                            "--",
                            "docking",
                            "tests",
                            "tools",
                            "pyproject.toml",
                            "setup.py",
                        ],
                        cwd=ROOT,
                    )
                    .decode()
                    .split("\0")
                )
                with tarfile.open(source, "w") as archive:
                    for name in sorted(set(files)):
                        if name and (ROOT / name).is_file():
                            archive.add(ROOT / name, arcname=name, recursive=False)
                subprocess.run(
                    [*scp, str(source), "visual@127.0.0.1:source.tar"], check=True
                )
                subprocess.run(
                    [*ssh, "mkdir src && tar -xf source.tar -C src"], check=True
                )
                with subprocess.Popen(
                    ["docker", "save", args.image], stdout=subprocess.PIPE
                ) as saved:
                    subprocess.run(
                        [*ssh, "sudo docker load"], stdin=saved.stdout, check=True
                    )
                    assert saved.wait() == 0, "Failed to export compositor image"
                # The emulated GPU supplies DRM buffers for guest CPU rendering.
                command = (
                    "set -eu; render=$(find /dev/dri -name 'renderD*' | head -1); "
                    'test -c "$render"; '
                    "cd src; env DOCKING_VISUAL_GUEST=1 "
                    'DOCKING_LAB_RENDER_NODE="$render" LAB_RENDER_DEVICE="$render" '
                    "LAB_PARENT_RENDERER=gles2 LAB_KWIN_COMPOSE=O2 "
                    "LAB_SOFTWARE_RENDERING=0 "
                    f"DOCKING_LAB_IMAGE={shlex.quote(args.image)} "
                    "python3 -m tests.visual "
                    f"--target {shlex.quote(args.target)} "
                    "--output /home/visual/evidence"
                )
                status = subprocess.run(
                    [*ssh, command], check=False, timeout=1800
                ).returncode
                subprocess.run(
                    [*ssh, "sudo chown -R visual:visual evidence"], check=False
                )
                copied = subprocess.run(
                    [*scp, "-r", "visual@127.0.0.1:evidence", str(output)], check=False
                )
                return status or copied.returncode
            finally:
                guest.terminate()
                try:
                    guest.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    guest.kill()
                    guest.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
