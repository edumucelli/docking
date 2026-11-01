"""Build the native input helpers through the actual wheel/editable commands."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import setuptools
from packaging.tags import Tag, parse_tag
from setuptools import errors

_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = "docking/platform/native/windows"
_PACKAGES = (
    "docking",
    "docking.platform",
    "docking.platform.native",
    "docking.platform.native.windows",
)
_HELPERS = ("input-thread-64.dll", "input-thread-32.dll")


@pytest.fixture
def project(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("docking_setup", _ROOT / "setup.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    setup = Mock()
    monkeypatch.setattr(setuptools, "setup", setup)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.chdir(tmp_path)
    for package in _PACKAGES:
        directory = tmp_path.joinpath(*package.split("."))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").touch()
    source = tmp_path / _PACKAGE
    (source / "input_hook.cpp").write_text("// Packaging fixture\n", encoding="utf-8")

    def compile_fixture(_source, output):
        # These bytes prove artifact routing only; no test loads them as a DLL.
        output.mkdir(parents=True, exist_ok=True)
        for name in _HELPERS:
            (output / name).write_bytes(name.encode("ascii"))

    monkeypatch.setattr(module, "build", compile_fixture)

    def distribution():
        result = module.DistributionWithInputRuntime(
            {
                "name": "docking",
                "version": "0.0.1",
                "packages": list(_PACKAGES),
                "package_data": {
                    "docking": [
                        "platform/native/windows/input_hook.cpp",
                        "platform/native/windows/input-thread-*.dll",
                    ]
                },
                "cmdclass": setup.call_args.kwargs["cmdclass"],
            }
        )
        result.script_name = "setup.py"
        return result

    return module, distribution, source


@pytest.mark.parametrize("platform", ["win_amd64", "mingw_x86_64_ucrt_gnu"])
def test_native_wheel_contains_helpers_and_advertises_python_independent_platform(
    project, tmp_path, platform
):
    _module, create, _source = project
    distribution = create()
    command = distribution.get_command_obj("bdist_wheel")
    command.dist_dir = str(tmp_path / "dist")
    command.plat_name = platform
    distribution.run_command("bdist_wheel")
    wheel = next((tmp_path / "dist").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        for name in _HELPERS:
            assert archive.read(f"{_PACKAGE}/{name}") == name.encode("ascii")
        metadata = archive.read("docking-0.0.1.dist-info/WHEEL").decode()
        assert "Root-Is-Purelib: false" in metadata
        tags = {
            tag
            for line in metadata.splitlines()
            if line.startswith("Tag: ")
            for tag in parse_tag(line.removeprefix("Tag: "))
        }
        assert Tag("py3", "none", "win_amd64") in tags
        if platform.startswith("mingw_"):
            assert Tag("py3", "none", platform) in tags
        assert not any(".data/" in name for name in archive.namelist())


def test_strict_editable_install_links_both_in_place_native_helpers(project, tmp_path):
    _module, create, source = project
    distribution = create()
    command = distribution.get_command_obj("editable_wheel")
    command.dist_dir = str(tmp_path / "dist")
    command.mode = "strict"
    distribution.run_command("editable_wheel")
    linked = next((tmp_path / "build").glob("__editable__.*"))
    for name in _HELPERS:
        assert (source / name).read_bytes() == name.encode("ascii")
        assert (linked / _PACKAGE / name).read_bytes() == name.encode("ascii")


def test_editable_compiler_failure_fails_install_instead_of_being_swallowed(
    project, tmp_path, monkeypatch
):
    module, create, _source = project
    monkeypatch.setattr(
        module, "build", Mock(side_effect=RuntimeError("fixture compiler failed"))
    )
    distribution = create()
    command = distribution.get_command_obj("editable_wheel")
    command.dist_dir = str(tmp_path / "dist")
    # Older setuptools wraps the failure; newer releases propagate it directly.
    with pytest.raises((errors.InternalError, RuntimeError)) as failure:
        distribution.run_command("editable_wheel")
    error = failure.value.__cause__ or failure.value
    assert isinstance(error, RuntimeError)
    assert str(error) == "fixture compiler failed"
    assert not list((tmp_path / "dist").glob("*.whl"))


def test_universal_wheel_omits_helpers_left_by_a_windows_editable_build(
    project, tmp_path, monkeypatch
):
    module, create, source = project
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(
        module, "build", Mock(side_effect=AssertionError("compiled on Linux"))
    )
    for name in _HELPERS:
        (source / name).write_bytes(b"stale Windows build")
        cached = tmp_path / "build/lib" / _PACKAGE / name
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(b"stale Windows build output")
    distribution = create()
    command = distribution.get_command_obj("bdist_wheel")
    command.dist_dir = str(tmp_path / "dist")
    distribution.run_command("bdist_wheel")
    wheel = next((tmp_path / "dist").glob("*.whl"))
    assert wheel.name.endswith("py3-none-any.whl")
    with zipfile.ZipFile(wheel) as archive:
        assert not any(name.endswith(".dll") for name in archive.namelist())
        assert f"{_PACKAGE}/input_hook.cpp" in archive.namelist()
        assert (
            "Root-Is-Purelib: true"
            in archive.read("docking-0.0.1.dist-info/WHEEL").decode()
        )
