"""Build the small, Python-independent Windows input-thread helpers."""

import sys
from pathlib import Path
from runpy import run_path

from setuptools import Distribution, setup
from setuptools.command.build_ext import build_ext
from setuptools.command.build_py import build_py
from wheel.bdist_wheel import bdist_wheel

# PEP 517 does not put the source root on sys.path when executing setup.py.
build = run_path(str(Path(__file__).parent / "tools/windows/input_runtime.py"))["build"]


class DistributionWithInputRuntime(Distribution):
    def has_ext_modules(self):
        return sys.platform == "win32" or super().has_ext_modules()


_INPUT_PACKAGE = "docking.platform.backends.windows"
_INPUT_HELPERS = ("input-thread-64.dll", "input-thread-32.dll")


class BuildPython(build_py):
    def run(self):
        if sys.platform != "win32":
            output = Path(self.build_lib).joinpath(*_INPUT_PACKAGE.split("."))
            for name in _INPUT_HELPERS:
                (output / name).unlink(missing_ok=True)
        super().run()

    def find_data_files(self, package, src_dir):
        files = super().find_data_files(package, src_dir)
        if sys.platform != "win32":
            # A checkout previously used for a Windows editable build may
            # contain generated DLLs. They never belong in a universal wheel.
            files = [
                name for name in files if not Path(name).match("input-thread-*.dll")
            ]
        return files


class BuildInputRuntime(build_ext):
    def _helper_mapping(self):
        python = self.get_finalized_command("build_py")
        source = Path(python.get_package_dir(_INPUT_PACKAGE))
        output = Path(self.build_lib).joinpath(*_INPUT_PACKAGE.split("."))
        return {str(output / name): str(source / name) for name in _INPUT_HELPERS}

    def run(self):
        super().run()
        if sys.platform == "win32":
            mapping = self._helper_mapping()
            built, source = next(iter(mapping.items()))
            output = Path(source if self.inplace else built).parent
            build(Path(source).with_name("input_hook.cpp"), output)

    def get_outputs(self):
        outputs = super().get_outputs() if self.extensions else []
        if sys.platform == "win32":
            outputs.extend(self._helper_mapping())
        return sorted(set(outputs))

    def get_output_mapping(self):
        # These helpers have no Python extension modules or interpreter ABI.
        mapping = super().get_output_mapping() if self.extensions else {}
        if sys.platform == "win32" and self.inplace:
            mapping.update(self._helper_mapping())
        return mapping


class InputRuntimeWheel(bdist_wheel):
    def get_tag(self):
        python, abi, platform = super().get_tag()
        if sys.platform == "win32":
            # MSYS2 advertises its own platform tag, while these DLLs use only
            # system APIs and work with standard CPython on Windows x64 too.
            if platform.startswith("mingw_"):
                platform = f"win_amd64.{platform}"
            return "py3", "none", platform
        return python, abi, platform


setup(
    distclass=DistributionWithInputRuntime,
    cmdclass={
        "build_py": BuildPython,
        "build_ext": BuildInputRuntime,
        "bdist_wheel": InputRuntimeWheel,
    },
)
