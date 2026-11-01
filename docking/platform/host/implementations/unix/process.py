"""Existing Unix hook invocation format."""

from pathlib import Path


def hook_argv(root: Path, executable: str, provider: str) -> list[str]:
    return [
        "env",
        f"PYTHONPATH={root}",
        executable,
        "-m",
        "docking.applets.aiusage.hook",
        provider,
    ]


def hook_command(root: Path, executable: str) -> str:
    return f"PYTHONPATH={root} {executable} -m docking.applets.aiusage.hook"


def prepare_process() -> None:
    import faulthandler
    import signal
    import sys

    from docking.platform.gamescope import prepare_gamescope_wayland_environment

    prepare_gamescope_wayland_environment()
    faulthandler.enable()
    faulthandler.register(signal.SIGUSR1)
    vendor = "/usr/lib/docking/vendor"
    if Path(vendor).is_dir():
        sys.path.insert(0, vendor)
