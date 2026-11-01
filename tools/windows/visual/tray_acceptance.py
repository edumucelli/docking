"""Select and verify the installed wheel exercised by native tray acceptance."""

from __future__ import annotations

import hashlib
import io
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_PROFILES = frozenset(("api-matrix", "gtk-v0-v4", "gtk-v3-v4-guid"))


def verify_installed_wheel(root: Path, wheel: Path) -> str:
    """Compare every shipped Docking file; cache bytecode is generated locally."""
    root = root.resolve()
    data = wheel.read_bytes()
    shipped = set()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            if member.is_dir() or not member.filename.startswith("docking/"):
                continue
            relative = PurePosixPath(member.filename)
            assert ".." not in relative.parts and relative not in shipped
            path = root.joinpath(*relative.parts).resolve()
            assert path.is_relative_to(root), "Installed wheel file escapes runtime"
            assert path.is_file(), "Installed wheel file is missing"
            assert path.read_bytes() == archive.read(member), (
                "Installed wheel bytes differ"
            )
            shipped.add(relative)
    assert PurePosixPath("docking/__init__.py") in shipped, (
        "Wheel has no Docking package"
    )
    for path in (root / "docking").rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts and path.suffix == ".pyc":
            continue
        assert PurePosixPath(relative.as_posix()) in shipped, (
            "Unshipped installed Docking file"
        )
    return hashlib.sha256(data).hexdigest()


def runtime_origins(root: Path, modules: dict[str, str]) -> dict[str, str]:
    """Journal verified origins without exposing a private runtime-share prefix."""
    root = root.resolve()
    result = {}
    for name, filename in modules.items():
        origin = Path(filename).resolve()
        assert origin.is_relative_to(root), (
            name + " loaded outside the selected runtime"
        )
        result[name] = origin.relative_to(root).as_posix()
    return result


@dataclass(frozen=True)
class TrayRuntime:
    root: Path
    installed: bool
    wheel_sha256: str | None

    def acceptance(self, profile: str) -> dict | None:
        assert profile in _PROFILES
        if self.wheel_sha256 is None:
            return None
        assert self.installed
        return {
            "schema": 1,
            "implementation": "installed-wheel",
            "wheel_sha256": self.wheel_sha256,
            "profile": profile,
        }


def select_runtime(installed_root: Path | None, wheel: Path | None) -> TrayRuntime:
    """Verify the requested wheel before importing any Docking implementation."""
    if wheel is not None and installed_root is None:
        raise ValueError("--wheel requires --installed-root")
    source = Path(__file__).resolve().parents[3]
    selected = installed_root.resolve() if installed_root is not None else source
    assert (selected / "docking/__init__.py").is_file(), (
        "Missing selected Docking package"
    )
    digest = verify_installed_wheel(selected, wheel) if wheel is not None else None
    sys.path.insert(0, str(source))
    if selected != source:
        sys.path.insert(0, str(selected))
    return TrayRuntime(selected, installed_root is not None, digest)
