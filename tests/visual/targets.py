"""Native visual targets shared by the CLI, shell runners and CI matrix."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Target:
    name: str
    backend: str
    desktop: str
    system: str = "linux"
    image_file: str = "Dockerfile"
    services: str = "visual"
    virtual_gpu: bool = False

    @property
    def directory(self) -> Path:
        base = ROOT / "tests/visual/backends" / self.backend
        return (
            base / self.desktop if self.backend in {"layer_shell", "reduced"} else base
        )

    @property
    def image_target(self) -> str:
        return "gnome" if self.name == "gnome-fallback" else self.name


TARGETS = (
    Target("sway", "sway", "sway"),
    Target("cinnamon", "cinnamon", "cinnamon", services="cinnamon"),
    Target("niri", "niri", "niri", image_file="Dockerfile.arch"),
    Target("labwc", "layer_shell", "labwc", services="foreign"),
    Target(
        "cosmic", "cosmic", "cosmic", image_file="Dockerfile.arch", virtual_gpu=True
    ),
    Target("kwin", "kwin", "kwin", virtual_gpu=True),
    Target("wayfire", "wayfire", "wayfire", image_file="Dockerfile.arch"),
    Target(
        "hyprland",
        "hyprland",
        "hyprland",
        image_file="Dockerfile.arch",
        virtual_gpu=True,
    ),
    Target(
        "treeland",
        "treeland",
        "treeland",
        services="treeland",
        image_file="Dockerfile.arch",
        virtual_gpu=True,
    ),
    Target(
        "jay", "layer_shell", "jay", image_file="Dockerfile.arch", services="foreign"
    ),
    Target("miriway", "layer_shell", "miriway", services="foreign"),
    Target(
        "gamescope",
        "layer_shell",
        "gamescope",
        image_file="Dockerfile.arch",
        services="launcher",
        virtual_gpu=True,
    ),
    Target("phoc", "layer_shell", "phoc", services="foreign"),
    Target("phosh", "layer_shell", "phosh", services="foreign"),
    Target("weston", "reduced", "weston", services="launcher"),
    Target("cage", "reduced", "cage", services="launcher"),
    Target("gnome", "gnome", "gnome"),
    Target("gnome-fallback", "reduced", "gnome", services="launcher"),
    *(
        Target(f"x11-{desktop}", "x11", desktop, services="x11")
        for desktop in ("gnome", "plasma", "cinnamon", "xfce", "mate", "lxde", "lxqt")
    ),
    *(
        Target(f"{system}-xfce", "x11", "xfce", system=system, services="x11")
        for system in ("freebsd", "openbsd", "netbsd")
    ),
    Target("windows-server", "windows", "server", system="windows", services="windows"),
    Target("windows-11", "windows", "11", system="windows", services="windows"),
)


def target_named(name: str) -> Target:
    for target in TARGETS:
        if target.name == name:
            return target
    raise ValueError(f"Unknown visual target: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter")
    parser.add_argument("--matrix", choices=("linux", "windows", "bsd"))
    args = parser.parse_args()
    if args.adapter:
        try:
            target = target_named(args.adapter)
        except ValueError as error:
            parser.error(str(error))
        print(target.directory / "adapter.sh")
    else:
        targets = [
            t
            for t in TARGETS
            if args.matrix is None
            or (
                t.system in {"freebsd", "openbsd", "netbsd"}
                if args.matrix == "bsd"
                else t.system == args.matrix
            )
        ]
        print(
            json.dumps(
                {
                    "include": [
                        {**asdict(t), "image_target": t.image_target} for t in targets
                    ]
                }
            )
        )


if __name__ == "__main__":
    main()
