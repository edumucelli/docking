"""BSD staging must include runtime data without editable paths or pip tooling."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "packaging/bsd/build.py"
spec = importlib.util.spec_from_file_location("docking_bsd_build", MODULE_PATH)
assert spec and spec.loader
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.mark.parametrize("prefix", [Path("/usr/local"), Path("/usr/pkg")])
def test_staged_runtime_is_self_contained_without_build_tools(
    monkeypatch, tmp_path, prefix
):
    root = tmp_path / "source"
    for folder in (
        "docking/assets",
        "packaging/shared",
        "packaging/deb/icons/hicolor/48x48/apps",
    ):
        (root / folder).mkdir(parents=True)
    (root / "docking/__init__.py").write_text('__version__="1.2.3"')
    (root / "docking/assets/fixture.svg").write_text("<svg/>")
    (root / "packaging/shared/org.docking.Docking.desktop").write_text(
        "[Desktop Entry]"
    )
    (
        root / "packaging/deb/icons/hicolor/48x48/apps/org.docking.Docking.png"
    ).write_bytes(b"icon")
    (root / "LICENSE").write_text("GPL")
    (root / "packaging/shared/org.docking.camshield.policy").write_text(
        "<annotate>/usr/bin/docking-camshield-helper</annotate>"
    )
    venv = tmp_path / "venv"
    site = (
        venv
        / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    site.mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "bin/escape").write_text("escaped")
    for name in ("dependency.py", "pip.py", "__editable__.pth"):
        (site / name).write_text("runtime" if name == "dependency.py" else "build tool")

    def distribution(name, files):
        return SimpleNamespace(
            metadata={"Name": name},
            files=[Path(f) for f in files],
            locate_file=lambda item: site / item,
        )

    monkeypatch.setattr(builder, "ROOT", root)
    monkeypatch.setattr(builder.sys, "prefix", str(venv))
    monkeypatch.setattr(
        builder.importlib.metadata,
        "distributions",
        lambda **kwargs: [
            distribution("dependency", ["dependency.py", "../../../bin/escape"]),
            distribution("pip", ["pip.py"]),
            distribution("docking", ["__editable__.pth"]),
        ],
    )
    destination = tmp_path / "stage"
    builder.stage(destination, prefix=prefix)
    staged_prefix = destination / prefix.relative_to("/")
    private = staged_prefix / "lib/docking/python"
    assert (private / "dependency.py").read_text() == "runtime"
    assert (private / "docking/assets/fixture.svg").is_file()
    assert not (private / "pip.py").exists()
    assert not (private / "__editable__.pth").exists()
    assert not (staged_prefix / "bin/escape").exists()
    launcher = staged_prefix / "bin/docking"
    assert launcher.stat().st_mode & 0o111
    assert str(prefix / "lib/docking/python") in launcher.read_text()
    assert str(venv) not in launcher.read_text()
    helper = staged_prefix / "bin/docking-camshield-helper"
    assert helper.stat().st_mode & 0o111
    assert " -I -B -c " in helper.read_text()
    assert '"$@"' in helper.read_text()
    policy = staged_prefix / "share/polkit-1/actions/org.docking.camshield.policy"
    assert str(prefix / "bin/docking-camshield-helper") in policy.read_text()


def test_netbsd_package_uses_pkgsrc_staging_and_native_architecture(
    monkeypatch, tmp_path
):
    stage = tmp_path / "stage"
    launcher = stage / "usr/pkg/bin/docking"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("payload")
    work = tmp_path / "work"
    work.mkdir()
    calls = []

    def output(*argv):
        if argv == ("uname", "-p"):
            return "x86_64"
        if argv == ("pkg_info", "-V"):
            return "20260227"
        assert argv[:2] == ("pkg_info", "-e")
        if argv[2] == "dbus-[0-9]*":
            return "dbus-1.0\ndbus-1-helper-1.0"
        return argv[2].replace("-[0-9]*", "-1.0")

    monkeypatch.setattr(builder, "output", output)
    monkeypatch.setattr(builder.platform, "release", lambda: "11.0")
    monkeypatch.setattr(
        builder.subprocess, "run", lambda argv, **_kwargs: calls.append(argv)
    )
    assert builder.netbsd_package(stage, work, "1.2.3") == work / "docking-1.2.3.tgz"
    command = calls[0]
    assert command[0] == "pkg_create"
    assert command[command.index("-c") + 1].startswith("-")
    assert command[command.index("-d") + 1].startswith("-")
    assert command[command.index("-p") + 1] == str(stage / "usr/pkg")
    assert command[command.index("-I") + 1] == "/usr/pkg"
    dependencies = command[command.index("-P") + 1].split()
    assert dependencies[-1] == "polkit>=1.0"
    assert "dbus>=1.0" in dependencies
    assert len(dependencies) == 10
    assert (work / "PLIST").read_text() == "@name docking-1.2.3\nbin/docking\n"
    assert "MACHINE_ARCH=x86_64\n" in (work / "BUILD_INFO").read_text()
    assert "PKGTOOLS_VERSION=20260227\n" in (work / "BUILD_INFO").read_text()


@pytest.mark.skipif(
    not sys.platform.startswith("netbsd")
    or os.environ.get("DOCKING_BSD_VM_TEST") != "1",
    reason="requires the disposable NetBSD package acceptance VM",
)
def test_actual_netbsd_package_upgrade_removal_and_reinstallation(tmp_path):
    packages = list(
        (builder.ROOT / "artifacts/bsd-packages/netbsd").glob("docking-*.tgz")
    )
    assert len(packages) == 1
    candidate = packages[0]
    metadata = json.loads(candidate.with_suffix(".tgz.json").read_text())
    current = tmp_path / f"docking-{metadata['version']}.tgz"
    shutil.copy2(candidate, current)
    prefix = Path("/usr/pkg")
    stage = tmp_path / "stage"
    builder.stage(stage, prefix=prefix)
    obsolete = Path("lib/docking/python/docking/.upgrade-probe")
    (stage / prefix.relative_to("/") / obsolete).write_text("previous package")
    older = builder.netbsd_package(stage, tmp_path, "0.0.0")
    try:
        subprocess.run(["pkg_add", "-U", str(older)], check=True)
        assert (prefix / obsolete).is_file()
        subprocess.run(["pkg_add", "-u", str(current)], check=True)
        assert (
            builder.output("pkg_info", "-e", "docking-[0-9]*")
            == f"docking-{metadata['version']}"
        )
        assert not (prefix / obsolete).exists()
        subprocess.run(["pkg_delete", "docking"], check=True)
        assert not (prefix / "bin/docking").exists()
        assert not (prefix / "lib/docking/python/docking/__init__.py").exists()
    finally:
        subprocess.run(["pkg_add", "-U", str(current)], check=True)
    assert (prefix / "bin/docking").is_file()
