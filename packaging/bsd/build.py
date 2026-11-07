"""Build and install a BSD package using the guest's actual package tools."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = Path("/usr/local")


def output(*argv: str) -> str:
    return subprocess.check_output(argv, text=True).strip()


def stage(destination: Path, *, prefix: Path = PREFIX) -> None:
    """Keep GI/Cairo/psutil native; vendor the resolved Python runtime deps."""
    staged_prefix = destination / prefix.relative_to("/")
    installed_private = prefix / "lib/docking/python"
    private = staged_prefix / "lib/docking/python"
    private.mkdir(parents=True)
    # Only the venv's distributions: native bindings come from system packages.
    site = (
        Path(sys.prefix)
        / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    if sys.prefix == sys.base_prefix or not site.is_dir():
        raise ValueError("Build with the prepared .venv-bsd interpreter")
    for dist in importlib.metadata.distributions(path=[str(site)]):
        if dist.metadata["Name"].lower().replace("_", "-") in {
            "pip",
            "setuptools",
            "wheel",
            "docking",
        }:
            continue
        for item in dist.files or ():
            if (
                ".." in item.parts
                or "__pycache__" in item.parts
                or item.suffix == ".pyc"
            ):
                continue
            source = Path(dist.locate_file(item))
            if source.is_file():
                target = private / item
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    shutil.copytree(
        ROOT / "docking",
        private / "docking",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for po in (private / "docking/locale").glob("*/LC_MESSAGES/docking.po"):
        subprocess.run(
            ["msgfmt", "-o", str(po.with_suffix(".mo")), str(po)], check=True
        )
    executable = Path(sys.executable).resolve()
    launcher = staged_prefix / "bin/docking"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        "#!/bin/sh\nset -eu\n"
        "export PYTHONDONTWRITEBYTECODE=1\n"
        f'export PYTHONPATH="{installed_private}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
        f'exec {shlex.quote(str(executable))} -P -m docking.launcher "$@"\n'
    )
    launcher.chmod(0o755)
    # Privileged helpers use only the root-owned installed payload. Python -I
    # excludes the caller's environment, current directory, and user site.
    helper = staged_prefix / "bin/docking-camshield-helper"
    helper.write_text(
        "#!/bin/sh\nset -eu\n"
        f"exec {shlex.quote(str(executable))} -I -B -c "
        + shlex.quote(
            f"import sys; sys.path.insert(0, {str(installed_private)!r}); "
            "from docking.applets.camshield.helper import main; "
            "raise SystemExit(main())"
        )
        + ' "$@"\n'
    )
    helper.chmod(0o755)
    policy = staged_prefix / "share/polkit-1/actions/org.docking.camshield.policy"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        (ROOT / "packaging/shared/org.docking.camshield.policy")
        .read_text()
        .replace(
            "/usr/bin/docking-camshield-helper",
            str(prefix / "bin/docking-camshield-helper"),
        )
    )
    desktop = staged_prefix / "share/applications/org.docking.Docking.desktop"
    desktop.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "packaging/shared/org.docking.Docking.desktop", desktop)
    icons = staged_prefix / "share/icons/hicolor"
    shutil.copytree(ROOT / "packaging/deb/icons/hicolor", icons)
    license_path = staged_prefix / "share/licenses/docking"
    license_path.mkdir(parents=True)
    shutil.copy2(ROOT / "LICENSE", license_path / "LICENSE")


def freebsd_package(stage_dir: Path, work: Path, version: str) -> Path:
    suffix = f"{sys.version_info.major}{sys.version_info.minor}"
    python_package = output("pkg", "which", "-q", str(Path(sys.executable).resolve()))
    dependencies = {}
    for spec in (
        python_package,
        f"py{suffix}-pygobject",
        f"py{suffix}-pycairo",
        f"py{suffix}-psutil",
        "gtk3",
        "libwnck3",
        "dbus",
        "libXScrnSaver",
        "polkit",
        "xterm",
    ):
        name, dep_version, origin = output("pkg", "query", "%n\t%v\t%o", spec).split(
            "\t"
        )
        dependencies[name] = {"version": dep_version, "origin": origin}
    files = {
        "/" + str(path.relative_to(stage_dir)): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(stage_dir.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "name": "docking",
        "version": version,
        "origin": "x11/docking",
        "comment": "A lightweight desktop dock",
        "desc": "Docking for compositing X11 desktops on FreeBSD.",
        "www": "https://docking.cc",
        "maintainer": "edumucelli@gmail.com",
        "prefix": str(PREFIX),
        "arch": output("pkg", "config", "ABI"),
        "licenses": ["GPLv3+"],
        "licenselogic": "single",
        "deps": dependencies,
        "files": files,
    }
    metadata = work / "manifest.json"
    metadata.write_text(json.dumps(manifest))
    subprocess.run(
        ["pkg", "create", "-M", str(metadata), "-r", str(stage_dir), "-o", str(work)],
        check=True,
    )
    return work / f"docking-{version}.pkg"


def openbsd_package(stage_dir: Path, work: Path, version: str) -> Path:
    installed = output("pkg_info", "-a", "-q").splitlines()
    prefixes = (
        f"python-{sys.version_info.major}.{sys.version_info.minor}.",
        "py3-gobject3-",
        "py3-cairo-",
        "py3-psutil-",
        "gtk+3-",
        "libwnck3-",
        "dbus-",
        "polkit-",
    )
    dependencies = []
    for prefix in prefixes:
        matches = [
            name
            for name in installed
            if name.startswith(prefix) and name[len(prefix) : len(prefix) + 1].isdigit()
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one installed dependency for {prefix}: {matches}"
            )
        name = matches[0]
        stem = re.split(r"-(?=\d)", name, maxsplit=1)[0]
        spec = (
            f"python->={sys.version_info.major}.{sys.version_info.minor},"
            f"<{sys.version_info.major}.{sys.version_info.minor + 1}"
            if stem == "python"
            else f"{stem}-*"
        )
        dependencies.extend(["-P", f"{output('pkg_info', '-qP', name)}:{spec}:{name}"])
    plist = work / "PLIST"
    plist.write_text(
        "\n".join(
            str(path.relative_to(stage_dir / "usr/local"))
            for path in sorted((stage_dir / "usr/local").rglob("*"))
            if path.is_file()
        )
        + "\n"
    )
    description = work / "DESCR"
    description.write_text(
        "Docking is a lightweight dock for compositing X11 desktops.\n"
    )
    package = work / f"docking-{version}.tgz"
    subprocess.run(
        [
            "pkg_create",
            "-A",
            platform.machine(),
            "-B",
            str(stage_dir),
            "-p",
            str(PREFIX),
            "-f",
            str(plist),
            "-d",
            str(description),
            "-D",
            "COMMENT=A lightweight desktop dock",
            "-D",
            "FULLPKGPATH=x11/docking",
            "-D",
            "MAINTAINER=edumucelli@gmail.com",
            *dependencies,
            str(package),
        ],
        check=True,
    )
    return package


def netbsd_package(stage_dir: Path, work: Path, version: str) -> Path:
    """Build a pkgsrc package with compatible runtime dependency patterns."""
    prefix = Path("/usr/pkg")
    suffix = f"{sys.version_info.major}{sys.version_info.minor}"
    dependencies = []
    for stem in (
        f"python{suffix}",
        f"py{suffix}-gobject3",
        f"py{suffix}-cairo",
        f"py{suffix}-psutil",
        "gtk3+",
        "librsvg",
        "adwaita-icon-theme",
        "libwnck3",
        "dbus",
        "polkit",
    ):
        matches = [
            package
            for package in output("pkg_info", "-e", f"{stem}-[0-9]*").splitlines()
            if package.rpartition("-")[0] == stem
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected one installed dependency for {stem}: {matches}")
        dependencies.append(f"{stem}>={matches[0].rpartition('-')[2]}")
    plist = work / "PLIST"
    plist.write_text(
        f"@name docking-{version}\n"
        + "\n".join(
            str(path.relative_to(stage_dir / prefix.relative_to("/")))
            for path in sorted(stage_dir.rglob("*"))
            if path.is_file()
        )
        + "\n"
    )
    build_info = work / "BUILD_INFO"
    build_info.write_text(
        "OPSYS=NetBSD\n"
        f"PKGTOOLS_VERSION={output('pkg_info', '-V')}\n"
        f"OS_VERSION={platform.release()}\n"
        f"MACHINE_ARCH={output('uname', '-p')}\n"
        "PKGPATH=x11/docking\n"
    )
    package = work / f"docking-{version}.tgz"
    subprocess.run(
        [
            "pkg_create",
            "-p",
            str(stage_dir / prefix.relative_to("/")),
            "-I",
            str(prefix),
            "-B",
            str(build_info),
            "-P",
            " ".join(dependencies),
            "-c",
            "-A lightweight desktop dock",
            "-d",
            "-Docking for compositing X11 desktops on NetBSD.",
            "-f",
            str(plist),
            str(package),
        ],
        check=True,
    )
    return package


def install_and_check(
    package: Path, system: str, work: Path, version: str, *, prefix: Path = PREFIX
) -> None:
    if system in {"openbsd", "netbsd"}:
        # pkg_add checks the filename against the archive's @name. Downloads
        # retain the OS/release suffix; installation uses the canonical name.
        canonical = work / f"docking-{version}.tgz"
        shutil.copy2(package, canonical)
        package = canonical
    commands = {
        "freebsd": ["pkg", "add", str(package)],
        "openbsd": ["pkg_add", "-I", "-D", "unsigned", str(package)],
        "netbsd": ["pkg_add", "-U", str(package)],
    }
    command = commands[system]
    subprocess.run(command, check=True)
    env = dict(
        os.environ,
        PYTHONPATH=str(prefix / "lib/docking/python"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    installed_private = str(prefix / "lib/docking/python") + "/"
    subprocess.run(
        [
            str(Path(sys.executable).resolve()),
            "-c",
            (
                "import docking, docking.applets.weather.api; "
                f"assert docking.__file__.startswith({installed_private!r}); "
                'print("Installed payload:", docking.__file__)'
            ),
        ],
        cwd=work,
        env=env,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    system = platform.system().lower()
    if system not in {"freebsd", "openbsd", "netbsd"}:
        parser.error("Build on a real FreeBSD, OpenBSD or NetBSD guest")
    version = re.search(
        r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"',
        (ROOT / "pyproject.toml").read_text(),
        re.M,
    )
    if version is None:
        raise ValueError("Missing stable project version")
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="docking-pkg-") as temporary:
        work = Path(temporary)
        stage_dir = work / "stage"
        prefix = Path("/usr/pkg") if system == "netbsd" else PREFIX
        stage(stage_dir, prefix=prefix)
        builders = {
            "freebsd": freebsd_package,
            "openbsd": openbsd_package,
            "netbsd": netbsd_package,
        }
        package = builders[system](stage_dir, work, version[1])
        release = re.match(r"\d+\.\d+", platform.release())[0]
        filename = (
            f"docking-{version[1]}-{system}-{release}-"
            f"{platform.machine()}{package.suffix}"
        )
        destination = args.output / filename
        shutil.copy2(package, destination)
        # Exercise the same latest filename used by the installation guide.
        alias = work / (
            f"docking-latest-{system}-{release}-{platform.machine()}{package.suffix}"
        )
        shutil.copy2(package, alias)
        install_and_check(alias, system, work, version[1], prefix=prefix)
        metadata = {
            "name": "docking",
            "version": version[1],
            "os": system,
            "release": release,
            "architecture": platform.machine(),
            "filename": filename,
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "python": platform.python_version(),
        }
        destination.with_suffix(destination.suffix + ".json").write_text(
            json.dumps(metadata, indent=2) + "\n"
        )
        print(destination)


if __name__ == "__main__":
    main()
