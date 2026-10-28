"""Build a per-user Windows setup executable from a verified portable artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path, PurePosixPath

from packaging.version import Version

from tools.windows.bundle import extract_bundle, filesystem_path

MANIFEST_HEADER = "Docking-Install-Manifest-1\r\n"


def removal_manifest(files: list[str]) -> bytes:
    """Inventory only shipped paths; never recursively remove installation data."""
    if not files:
        raise ValueError("Windows payload must contain files")
    paths: list[PurePosixPath] = []
    seen: set[str] = set()
    directories: set[PurePosixPath] = set()
    for name in files:
        path = PurePosixPath(name)
        if path.is_absolute() or path.as_posix() != name:
            raise ValueError(f"Invalid Windows payload path: {name}")
        for part in path.parts:
            stem = part.split(".", 1)[0].upper()
            if (
                part in {".", ".."}
                or part.rstrip(". ") != part
                or any(ord(value) < 32 or value in '\\:<>"|?*' for value in part)
                or stem in {"CON", "PRN", "AUX", "NUL"}
                or re.fullmatch(r"(?:COM|LPT)[1-9]", stem)
            ):
                raise ValueError(f"Invalid Windows payload path: {name}")
        key = name.casefold()
        if not path.parts or key in seen or key == "uninstall-files.txt":
            raise ValueError(f"Duplicate or reserved payload path: {name}")
        seen.add(key)
        paths.append(path)
        directories.update(parent for parent in path.parents if parent.parts)
    if seen.intersection(directory.as_posix().casefold() for directory in directories):
        raise ValueError("Payload file is also a parent directory")
    lines = ["F\t" + str(path).replace("/", "\\") for path in sorted(paths)]
    lines.extend(
        "D\t" + str(path).replace("/", "\\")
        for path in sorted(directories, key=lambda item: (-len(item.parts), str(item)))
    )
    return (MANIFEST_HEADER + "\r\n".join(lines) + "\r\n").encode("utf-16")


def nsis_value(value: str, *, runtime: bool = True) -> str:
    if any(character in value for character in '\r\n"'):
        raise ValueError("Invalid NSIS setting")
    return value.replace("$", "$$") if runtime else value


def payload_script(files: list[str]) -> str:
    """Write ownership before payload files so interrupted copies can be removed."""
    lines = [
        'SetOutPath "\\\\?\\$VersionDir"',
        'File /oname=uninstall-files.txt "${PAYLOAD}\\uninstall-files.txt"',
    ]
    parent = PurePosixPath(".")
    for name in sorted(files):
        path = PurePosixPath(name)
        if path.parent != parent:
            parent = path.parent
            suffix = (
                "\\" + nsis_value(str(parent).replace("/", "\\"))
                if parent.parts
                else ""
            )
            lines.append(f'SetOutPath "\\\\?\\$VersionDir{suffix}"')
        relative = nsis_value(name.replace("/", "\\"), runtime=False)
        lines.append(
            f'File "/oname={nsis_value(path.name)}" "${{PAYLOAD}}\\{relative}"'
        )
    return "\n".join(lines) + "\n"


def build(archive: Path, output: Path, prefix: Path) -> None:
    if output.exists():
        raise FileExistsError(f"Choose a new installer destination: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[2] / "packaging/windows"
    with tempfile.TemporaryDirectory(
        dir=output.parent, prefix="docking-setup-"
    ) as temp:
        stage = Path(temp)
        payload = filesystem_path(extract_bundle(archive, stage / "payload"))
        files = sorted(
            path.relative_to(payload).as_posix()
            for path in payload.rglob("*")
            if path.is_file()
        )
        (payload / "uninstall-files.txt").write_bytes(removal_manifest(files))
        (stage / "payload.nsh").write_text(payload_script(files), encoding="utf-8")
        metadata = json.loads(
            (payload / "runtime/share/docking/runtime.json").read_text(encoding="utf-8")
        )
        modules = list(
            payload.glob(
                "runtime/lib/python*/site-packages/"
                "docking/platform/backends/windows/installation.py"
            )
        )
        if len(modules) != 1:
            raise ValueError(
                "Portable artifact lacks the installed-runtime maintenance module"
            )
        version = Version(metadata["version"])
        inventory_hash = hashlib.sha256(
            (payload / "files.sha256.json").read_bytes()
        ).hexdigest()
        version_id = (
            re.sub(r"[^A-Za-z0-9._-]", "-", str(version)) + "-" + inventory_hash[:12]
        )
        for filename, destination, flags in (
            ("installed_launcher.c", "Docking.exe", ["-mwindows"]),
            (
                "installed_launcher.c",
                "docking-hook.exe",
                ["-mwindows", "-DHOOK_LAUNCHER"],
            ),
            (
                "install_guard.c",
                "install-guard.exe",
                ["-lrstrtmgr", "-lshell32", "-lole32", "-luuid"],
            ),
        ):
            subprocess.run(
                [
                    str(prefix / "bin/gcc.exe"),
                    "-municode",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-static-libgcc",
                    str(source / filename),
                    "-o",
                    str(stage / destination),
                    *flags,
                ],
                check=True,
            )
        settings = {
            "PAYLOAD": str(payload),
            "HELPERS": str(stage),
            "OUTPUT": str(stage / "Docking-setup.exe"),
            "APP_VERSION": str(version),
            "VERSION_ID": version_id,
            "FILE_VERSION": ".".join(
                str(value) for value in (*version.release, 0, 0, 0, 0)[:4]
            ),
            "INSTALLED_KIB": str(
                sum(
                    path.stat().st_size for path in payload.rglob("*") if path.is_file()
                )
                // 1024
            ),
        }
        include = stage / "settings.nsh"
        include.write_text(
            "\n".join(
                f'!define {key} "{nsis_value(value, runtime=False)}"'
                for key, value in settings.items()
            )
            + "\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                str(prefix / "bin/makensis.exe"),
                "/V3",
                "/INPUTCHARSET",
                "UTF8",
                f"/DSETTINGS={include}",
                str(source / "installer.nsi"),
            ],
            check=True,
        )
        (stage / "Docking-setup.exe").rename(output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".exe.sha256").write_text(
        f"{digest}  {output.name}\n", encoding="utf-8"
    )
    print(f"Built Docking {version}: {output}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        sys.platform != "win32"
        or struct.calcsize("P") != 8
        or not sysconfig.get_platform().startswith("mingw")
        or Path(sys.base_prefix).name != "ucrt64"
    ):
        parser.error(
            "Build with MSYS2 UCRT64 Python on Windows x64, including GCC and NSIS"
        )
    build(
        args.archive.resolve(), args.output.resolve(), Path(sys.base_prefix).resolve()
    )


if __name__ == "__main__":
    main()
