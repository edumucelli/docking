"""Build a relocatable Windows runtime from an application wheel and MSYS2."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import importlib.metadata as metadata
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import sysconfig
import urllib.request
import uuid
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path, PurePosixPath, WindowsPath

from packaging.requirements import Requirement

from tools.windows import webview2_setup

RUNTIME_PACKAGES = (
    "python",
    "python-gobject",
    "python-cairo",
    "python-pillow",
    "python-psutil",
    "python-numpy",
    "gtk3",
    "librsvg",
    "gstreamer",
    "gst-plugins-base",
    "gst-plugins-good",
    "gst-plugins-bad",
)
_PACKAGE_PREFIX = "mingw-w64-ucrt-x86_64-"
# Ambient uses GStreamer for bundled OGG files and generated noise.
# WebView2 owns Windows web media. Avoid scanning unrelated video codecs at boot.
GSTREAMER_PLUGINS = frozenset(
    "libgst" + name + ".dll"
    for name in (
        "app",
        "audioconvert",
        "audioresample",
        "audiotestsrc",
        "autodetect",
        "coreelements",
        "gio",
        "ogg",
        "playback",
        "typefindfunctions",
        "volume",
        "vorbis",
        "wasapi",
        "wasapi2",
    )
)
_EXECUTABLES = {
    "python.exe",
    "pythonw.exe",
    "gdk-pixbuf-query-loaders.exe",
    "glib-compile-schemas.exe",
    "gspawn-win64-helper.exe",
    "gspawn-win64-helper-console.exe",
    "gst-inspect-1.0.exe",
}


def native_path(path: Path) -> str:
    """Pass native separators to tools that do not accept MSYS2's path spelling."""
    return str(path).replace("/", "\\")


class _NativeWindowsPath(WindowsPath):
    # MSYS2 changes pathlib's preferred separator. Extended Win32 paths require
    # backslashes even when ordinary short paths accept the shell's spelling.
    def __str__(self) -> str:
        return super().__str__().replace("/", "\\")

    def as_posix(self) -> str:
        return super().__str__().replace("\\", "/")


def filesystem_path(path: Path) -> Path:
    """Access Windows package files without depending on long-path policy."""
    if os.name != "nt":
        return path
    value = native_path(path.absolute())
    if value.startswith("\\\\?\\"):
        return _NativeWindowsPath(value)
    if value.startswith("\\\\"):
        return _NativeWindowsPath("\\\\?\\UNC\\" + value[2:])
    return _NativeWindowsPath("\\\\?\\" + value)


def walk_tree(root: Path) -> Iterator[Path]:
    """Retain native paths while enumerating, including under an MSYS2 shell."""
    # pathlib's globber joins plain strings with the MSYS2 separator, losing
    # extended-path support before it reaches the filesystem operation.
    with os.scandir(root) as entries:
        children = [
            (root / entry.name, entry.is_dir(follow_symlinks=False))
            for entry in entries
        ]
    for path, directory in children:
        yield path
        if directory:
            yield from walk_tree(path)


@contextmanager
def staging_directory(parent: Path, prefix: str) -> Iterator[Path]:
    """Keep every staging and cleanup operation in the native path namespace."""
    stage = filesystem_path(parent) / (prefix + uuid.uuid4().hex)
    stage.mkdir()
    try:
        yield stage
    finally:
        # tempfile/shutil join child names using MSYS2's separator, which breaks
        # the extended prefix. These are only files in this newly created stage.
        if stage.exists():
            for path in sorted(
                walk_tree(stage), key=lambda item: len(item.parts), reverse=True
            ):
                if path.is_dir() and not path.is_symlink():
                    path.rmdir()
                else:
                    path.unlink()
            stage.rmdir()


def payload_root(root: Path) -> Path:
    """Resolve either a portable bundle or the active per-user installation."""
    if (root / "runtime/share/docking/runtime.json").is_file():
        return root
    from docking.platform.backends.windows.installation import installed_hook

    try:
        data = (root / "current.ini").read_bytes()
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(
            data.decode("utf-16" if data.startswith(b"\xff\xfe") else "utf-8-sig")
        )
        version = parser.get("Docking", "Current")
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise ValueError(f"Incomplete Windows installation: {root}") from exc
    if (
        not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,125}", version)
        or ".." in version
        or version.endswith(".")
    ):
        raise ValueError("Invalid active Windows runtime version")
    payload = root / "versions" / version
    if (
        installed_hook(payload / "runtime") != root / "docking-hook.exe"
        or not (payload / "runtime/share/docking/runtime.json").is_file()
    ):
        raise ValueError(f"Incomplete Windows installation: {root}")
    return payload


@dataclass(frozen=True)
class NativePackage:
    name: str
    version: str
    dependencies: tuple[str, ...]
    provides: tuple[str, ...]
    files: tuple[str, ...]


def sections(text: str) -> dict[str, tuple[str, ...]]:
    """Read pacman's local database without invoking its MSYS interpreter."""
    result: dict[str, tuple[str, ...]] = {}
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        if lines and lines[0].startswith("%") and lines[0].endswith("%"):
            result[lines[0][1:-1]] = tuple(lines[1:])
    return result


def read_packages(database: Path) -> tuple[NativePackage, ...]:
    packages = []
    for description in sorted(database.glob("*/desc")):
        data = sections(description.read_text(encoding="utf-8"))
        # Metapackages have an empty file database and only contribute edges.
        files = sections(
            description.with_name("files").read_text(encoding="utf-8")
        ).get("FILES", ())
        packages.append(
            NativePackage(
                data["NAME"][0],
                data["VERSION"][0],
                data.get("DEPENDS", ()),
                data.get("PROVIDES", ()),
                files,
            )
        )
    return tuple(packages)


def dependency_closure(
    packages: tuple[NativePackage, ...], roots: tuple[str, ...]
) -> tuple[NativePackage, ...]:
    by_name = {package.name: package for package in packages}
    providers: dict[str, NativePackage] = {}
    for package in packages:
        for provided in package.provides:
            providers.setdefault(re.split(r"[<>=]", provided, maxsplit=1)[0], package)
    selected: dict[str, NativePackage] = {}
    pending = list(roots)
    while pending:
        name = re.split(r"[<>=]", pending.pop(), maxsplit=1)[0]
        package = by_name.get(name) or providers.get(name)
        if package is None:
            raise ValueError(f"Missing installed runtime dependency: {name}")
        if package.name not in selected:
            selected[package.name] = package
            pending.extend(package.dependencies)
    return tuple(selected[name] for name in sorted(selected))


def relative_package_file(value: str, prefix: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError(f"Invalid package path: {value}")
    if not path.parts or path.parts[0] != prefix:
        raise ValueError(f"Runtime dependency escapes {prefix}: {value}")
    return PurePosixPath(*path.parts[1:])


def runtime_file(path: PurePosixPath) -> bool:
    """Keep runtime resources and notices, excluding development/test output."""
    if any(
        part in {"__pycache__", "include", "pkgconfig", "cmake", "test", "tests"}
        for part in path.parts
    ):
        return False
    if path.suffix in {".pyc", ".pyo", ".pth", ".a", ".h", ".la"}:
        return False
    if path.name == "direct_url.json" or path.name.startswith("__editable__"):
        return False
    if path.parent == PurePosixPath("lib/gstreamer-1.0") and path.suffix == ".dll":
        return path.name in GSTREAMER_PLUGINS
    if path.parts[0] == "bin":
        return path.suffix == ".dll" or path.name in _EXECUTABLES
    if path.parts[0] == "share":
        return len(path.parts) > 1 and path.parts[1] not in {
            "doc",
            "man",
            "gtk-doc",
            "gir-1.0",
            "aclocal",
            "vala",
            "bash-completion",
        }
    return path.parts[0] in {"lib", "libexec", "etc", "ssl"}


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def extract_bundle(archive_path: Path, destination: Path) -> Path:
    """Extract a portable artifact, verifying every payload against its inventory."""
    requested = destination
    destination = filesystem_path(destination)
    if destination.exists():
        raise FileExistsError(f"Choose a new extraction destination: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(filesystem_path(archive_path)) as archive:
        inventory = json.loads(archive.read("Docking/files.sha256.json"))
        files = [entry for entry in archive.infolist() if not entry.is_dir()]
        expected = {"Docking/" + name for name in inventory}
        expected.add("Docking/files.sha256.json")
        if (
            len(files) != len(expected)
            or {entry.filename for entry in files} != expected
        ):
            raise ValueError("Portable archive does not match its file inventory")
        for entry in files:
            path = PurePosixPath(entry.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in entry.filename
                or ":" in entry.filename
                or path.parts[0] != "Docking"
            ):
                raise ValueError(f"Invalid portable archive path: {entry.filename}")
        with staging_directory(destination.parent, "docking-extract-") as stage:
            for entry in files:
                data = archive.read(entry)
                relative = PurePosixPath(entry.filename).relative_to("Docking")
                if entry.filename != "Docking/files.sha256.json":
                    digest = hashlib.sha256(data).hexdigest()
                    if digest != inventory[relative.as_posix()]:
                        raise ValueError(f"Portable file checksum mismatch: {relative}")
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            stage.rename(destination)
    return requested


def install_wheel(wheel: Path, site: Path) -> tuple[str, tuple[str, ...]]:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        records = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(records) != 1:
            raise ValueError("Expected one application distribution in the wheel")
        info = Parser().parsestr(archive.read(records[0]).decode("utf-8"))
        if info["Name"] != "docking":
            raise ValueError("Expected a Docking wheel")
        for name in names:
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in name
                or ":" in name
                or any(part.endswith(".data") for part in path.parts)
            ):
                raise ValueError(f"Unsupported wheel path: {name}")
        # zipfile joins plain strings using MSYS2's separator, which drops the
        # extended Win32 namespace. Keep each validated destination as a Path.
        site = filesystem_path(site)
        for entry in archive.infolist():
            target = site / PurePosixPath(entry.filename)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))
    return info["Version"], tuple(info.get_all("Requires-Dist", ()))


def copy_python_dependencies(
    requirements: tuple[str, ...], prefix: Path, runtime: Path
) -> dict[str, str]:
    """Copy the installed requirement closure, never the whole site-packages."""
    versions: dict[str, str] = {}
    pending = list(requirements)
    while pending:
        requirement = Requirement(pending.pop())
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        distribution = metadata.distribution(requirement.name)
        name = distribution.metadata["Name"]
        if name in versions:
            continue
        if distribution.version not in requirement.specifier:
            raise ValueError(f"Installed {name} does not satisfy {requirement}")
        if distribution.files is None:
            raise ValueError(f"Missing installed file inventory: {name}")
        versions[name] = distribution.version
        pending.extend(distribution.requires or ())
        for entry in distribution.files:
            source = Path(distribution.locate_file(entry)).resolve()
            if not source.is_relative_to(prefix):
                raise ValueError(f"Dependency file outside the native prefix: {name}")
            relative = source.relative_to(prefix)
            if source.is_file() and runtime_file(PurePosixPath(relative.as_posix())):
                copy_file(source, runtime / relative)
    return versions


def build(
    wheel: Path, output: Path, prefix: Path, webview2_package: Path | None = None
) -> None:
    if output.exists():
        raise FileExistsError(f"Choose a new bundle destination: {output}")
    packages = dependency_closure(
        read_packages(prefix.parent / "var/lib/pacman/local"),
        tuple(_PACKAGE_PREFIX + name for name in RUNTIME_PACKAGES),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with staging_directory(output.parent, "docking-build-") as temporary:
        stage = temporary / "Docking"
        runtime = stage / "runtime"
        for package in packages:
            print(f"Copying {package.name} {package.version}", flush=True)
            for entry in package.files:
                if entry.endswith("/"):
                    continue
                # Pacman hooks run during package installation, never at runtime.
                if entry.startswith("usr/share/libalpm/"):
                    continue
                relative = relative_package_file(entry, prefix.name)
                if runtime_file(relative):
                    copy_file(prefix / relative, runtime / relative)
        print("Installing the application wheel", flush=True)
        python_library = f"python{sys.version_info.major}.{sys.version_info.minor}"
        site = runtime / "lib" / python_library / "site-packages"
        version, requirements = install_wheel(wheel, site)
        print("Copying Python runtime dependencies", flush=True)
        dependencies = copy_python_dependencies(requirements, prefix, runtime)
        print("Verifying the WebView2 SDK", flush=True)
        if webview2_package is None:
            with urllib.request.urlopen(webview2_setup.URL, timeout=30) as response:
                sdk = response.read(20 * 1024 * 1024 + 1)
        else:
            sdk = webview2_package.read_bytes()
        webview2_setup.install(sdk, runtime / "share/docking/runtimes/webview2")
        cache = runtime / "lib/gdk-pixbuf-2.0/2.10.0/loaders.cache"
        environment = dict(os.environ)
        # The query tool derives relocatable module names from its own DLL.
        environment.pop("GDK_PIXBUF_MODULEDIR", None)
        environment.pop("GDK_PIXBUF_MODULE_FILE", None)
        environment["PATH"] = (
            str(runtime / "bin") + os.pathsep + environment.get("PATH", "")
        )
        with cache.open("wb") as stream:
            subprocess.run(
                [str(runtime / "bin/gdk-pixbuf-query-loaders.exe")],
                env=environment,
                stdout=stream,
                check=True,
            )
        subprocess.run(
            [
                str(runtime / "bin/glib-compile-schemas.exe"),
                str(runtime / "share/glib-2.0/schemas"),
            ],
            check=True,
        )
        source = Path(__file__).resolve().parents[2] / "packaging/windows/launcher.c"
        print("Compiling the native launcher", flush=True)
        subprocess.run(
            [
                str(prefix / "bin/gcc.exe"),
                "-municode",
                "-mwindows",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(source),
                "-o",
                str(stage / "Docking.exe"),
            ],
            check=True,
        )
        manifest = {
            "version": version,
            "python": sys.version.split()[0],
            "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "native_packages": {package.name: package.version for package in packages},
            "python_packages": dependencies,
        }
        marker = runtime / "share/docking/runtime.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print("Hashing the runtime inventory", flush=True)
        inventory = {
            path.relative_to(stage).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(walk_tree(stage))
            if path.is_file()
        }
        (stage / "files.sha256.json").write_text(
            json.dumps(inventory, indent=2) + "\n", encoding="utf-8"
        )
        stage.rename(output)
    print(f"Built Docking {version}: {output}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--webview2-package", type=Path)
    parser.add_argument(
        "--archive", type=Path, help="Also write a portable ZIP archive"
    )
    args = parser.parse_args()
    if (
        sys.platform != "win32"
        or struct.calcsize("P") != 8
        or not sysconfig.get_platform().startswith("mingw")
        or Path(sys.base_prefix).name != "ucrt64"
    ):
        parser.error("Build with MSYS2 UCRT64 Python on Windows x64")
    build(
        args.wheel.resolve(),
        args.output.resolve(),
        Path(sys.base_prefix).resolve(),
        args.webview2_package,
    )
    if args.archive is not None:
        print("Creating the portable ZIP archive", flush=True)
        args.archive.parent.mkdir(parents=True, exist_ok=True)
        bundle = filesystem_path(args.output)
        with zipfile.ZipFile(args.archive, "x", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(walk_tree(bundle)):
                if path.is_file():
                    archive.write(
                        path, "Docking/" + path.relative_to(bundle).as_posix()
                    )
        digest = hashlib.sha256(args.archive.read_bytes()).hexdigest()
        args.archive.with_suffix(".zip.sha256").write_text(
            f"{digest}  {args.archive.name}\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
