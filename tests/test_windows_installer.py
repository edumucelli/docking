"""Versioned installation hooks and real native launcher/removal ownership."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from docking.applets.aiusage import hook_command
from docking.platform.backends.windows import installation as install_module
from docking.platform.backends.windows.installation import installed_hook
from tools.windows import installer as installer_tool
from tools.windows.bundle import filesystem_path, native_path, payload_root
from tools.windows.installer import build, payload_script, removal_manifest
from tools.windows.visual import installer_smoke


@pytest.mark.parametrize("different", [False, True])
def test_installed_identity_uses_payload_bytes_not_matching_file_count(
    tmp_path, monkeypatch, different
):
    payload = installation(tmp_path, "candidate")
    metadata = payload / "runtime/share/docking/runtime.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"version": "1.2.3", "wheel_sha256": "1" * 64}))
    (tmp_path / "Docking.exe").write_bytes(b"stable launcher")
    (tmp_path / "docking-hook.exe").touch()
    original = {
        "runtime/share/docking/runtime.json": hashlib.sha256(
            metadata.read_bytes()
        ).hexdigest()
    }
    inventory = payload / "files.sha256.json"
    inventory.write_text(json.dumps(original))
    expected = {
        "payload_inventory_sha256": hashlib.sha256(inventory.read_bytes()).hexdigest(),
        "wheel_sha256": "1" * 64,
        "version": "1.2.3",
        "payload_files": 1,
    }
    if different:
        # A coherent, same-count payload must still match the accepted archive.
        metadata.write_text(json.dumps({"version": "1.2.3", "wheel_sha256": "2" * 64}))
        original["runtime/share/docking/runtime.json"] = hashlib.sha256(
            metadata.read_bytes()
        ).hexdigest()
        inventory.write_text(json.dumps(original))
    monkeypatch.setattr(
        installer_smoke,
        "registry_value",
        lambda _key, name: str(tmp_path) if name == "InstallLocation" else "1.2.3",
    )
    monkeypatch.setattr(
        installer_smoke, "programs_directory", lambda: tmp_path / "Start"
    )
    monkeypatch.setattr(
        installer_smoke,
        "read_shortcut",
        lambda _path: SimpleNamespace(executable=tmp_path / "Docking.exe"),
    )
    if different:
        with pytest.raises(AssertionError, match="differs"):
            installer_smoke.verify_installation(tmp_path, expected)
    else:
        assert installer_smoke.verify_installation(tmp_path, expected) == (payload, 1)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_upgrade_fixture_changes_only_project_version_and_is_strictly_older(newline):
    text = newline.join(
        (
            "[project]",
            'name = "docking"',
            'version = "2.28.0"',
            "[tool.other]",
            'version = "keep"',
            "",
        )
    )
    changed, current, previous = installer_tool.upgrade_fixture_project(text)
    assert (current, previous) == ("2.28.0", "2.28.0.dev0")
    assert changed == text.replace('version = "2.28.0"', 'version = "2.28.0.dev0"')


@pytest.mark.parametrize(
    "text",
    [
        '[project]\ndynamic = ["version"]\n',
        '[tool.other]\nversion = "2.28.0"\n',
        '[project]\nversion = "2.28.0.dev0"\n',
    ],
)
def test_upgrade_fixture_requires_a_real_older_static_version(text):
    with pytest.raises(ValueError):
        installer_tool.upgrade_fixture_project(text)


@pytest.mark.parametrize("failure", [None, "build", "publish"])
def test_upgrade_fixture_builds_owned_copy_and_preserves_source(
    tmp_path, monkeypatch, failure
):
    source = tmp_path / "checkout"
    output = tmp_path / "deep" / ("output" * 12) / "upgrade-fixture"
    temporary = tmp_path / "os-temp"
    temporary.mkdir()
    monkeypatch.setattr(installer_tool.tempfile, "gettempdir", lambda: str(temporary))
    source.mkdir()
    original = '[project]\nname = "docking"\nversion = "2.28.0"\n'
    (source / "pyproject.toml").write_text(original, encoding="utf-8")
    for name in (
        "setup.py",
        "setup.cfg",
        "MANIFEST.in",
        "README.md",
        "LICENSE",
        "requirements-windows-browser.txt",
        "tools/windows/__init__.py",
        "tools/windows/input_runtime.py",
        "docking/__init__.py",
        "docking/locale/en.mo",
        "docking/__pycache__/old.pyc",
        "docking/helper.dll",
        ".private/unrelated",
    ):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"source fixture")
    observed = []

    def compile_wheel(command, *, cwd, check):
        assert check is True
        copied = Path(cwd)
        assert copied != source
        assert copied.parent.parent == temporary
        assert not copied.is_relative_to(output.parent)
        assert 'version = "2.28.0.dev0"' in (copied / "pyproject.toml").read_text()
        assert not (copied / ".private").exists()
        assert not (copied / "docking/helper.dll").exists()
        assert not (copied / "docking/__pycache__").exists()
        assert (copied / "docking/locale/en.mo").read_bytes() == b"source fixture"
        observed.append(copied)
        if failure == "build":
            raise subprocess.CalledProcessError(1, command)
        wheels = Path(command[command.index("--outdir") + 1])
        wheels.mkdir()
        (wheels / "docking-2.28.0.dev0-py3-none-win_amd64.whl").write_bytes(
            b"old wheel"
        )

    def bundle(wheel, destination, prefix, webview):
        assert wheel.read_bytes() == b"old wheel"
        assert prefix == tmp_path / "runtime-prefix"
        assert webview is None
        destination.mkdir()
        (destination / "Docking.exe").write_bytes(b"old launcher")

    def setup(archive, destination, prefix):
        with zipfile.ZipFile(archive) as zipped:
            assert zipped.read("Docking/Docking.exe") == b"old launcher"
        destination.write_bytes(b"old setup")

    monkeypatch.setattr(installer_tool.subprocess, "run", compile_wheel)
    # The fake compiler reads host paths; native subprocess path conversion is
    # covered by the Windows packaging checks.
    monkeypatch.setattr(installer_tool, "command_path", str)
    monkeypatch.setattr(installer_tool, "build_bundle", bundle)
    monkeypatch.setattr(installer_tool, "build", setup)
    copy_file = installer_tool.copy_file

    def publish(source, destination):
        assert destination.parent.parent == output.parent
        copy_file(source, destination)
        if failure == "publish":
            raise OSError("Fixture publication failed")

    monkeypatch.setattr(installer_tool, "copy_file", publish)
    if failure:
        error = subprocess.CalledProcessError if failure == "build" else OSError
        with pytest.raises(error):
            installer_tool.build_upgrade_fixture(
                source, output, tmp_path / "runtime-prefix"
            )
        assert not output.exists()
    else:
        installer_tool.build_upgrade_fixture(
            source, output, tmp_path / "runtime-prefix"
        )
        record = json.loads((output / "fixture.json").read_text())
        assert record["fixture_version"] == "2.28.0.dev0"
        assert record["historical_release_compatibility"] is False
        assert record["wheel_sha256"] == hashlib.sha256(b"old wheel").hexdigest()
    assert (source / "pyproject.toml").read_text() == original
    assert observed and not observed[0].exists()
    assert not list(temporary.iterdir())
    assert not list(output.parent.glob("docking-upgrade-*"))


@pytest.mark.parametrize(
    "files",
    [
        [],
        ["../escape"],
        ["C:/escape"],
        ["runtime/NUL.txt"],
        ["trailing."],
        ["runtime/owned", "RUNTIME/OWNED"],
        ["runtime", "runtime/child"],
        ["uninstall-files.txt"],
        ["runtime/control\nfile"],
        ["runtime\\file"],
    ],
)
def test_installer_inventory_rejects_escaping_aliased_and_reserved_files(files):
    with pytest.raises(ValueError):
        removal_manifest(files)


def test_uninstall_removes_only_this_installations_hooks_and_keeps_provider_settings(
    tmp_path, monkeypatch
):
    root = tmp_path / "Docking 日本語 [1]"
    helper = root / "docking-hook.exe"
    home = tmp_path / "provider-home"
    claude = home / ".claude/settings.json"
    codex = home / ".codex/config.toml"
    claude.parent.mkdir(parents=True)
    codex.parent.mkdir(parents=True)
    unrelated = {"type": "command", "command": "another-hook", "args": ["keep"]}
    foreign = {
        "type": "command",
        "command": str(tmp_path / "other/docking-hook.exe"),
        "args": ["claude", "Stop"],
    }
    settings = {
        "keep": "résumé",
        "hooks": {
            "Stop": [
                {
                    "matcher": "*",
                    "other": "retain",
                    "hooks": [
                        unrelated,
                        foreign,
                        {
                            "type": "command",
                            "command": str(helper),
                            "args": ["claude", "Stop"],
                        },
                    ],
                }
            ],
            "SessionStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": str(helper),
                            "args": ["claude", "SessionStart"],
                        }
                    ]
                }
            ],
            "OtherEvent": [{"hooks": [unrelated]}],
        },
    }
    claude.write_text(json.dumps(settings), encoding="utf-8")
    remaining = '# user comment\nmodel = "example"\n[profiles.work]\nkeep = true\n'
    codex.write_text(
        "notify = " + json.dumps([str(helper), "codex"]) + "\n" + remaining,
        encoding="utf-8",
    )
    if sys.platform != "win32":
        # Pure ownership tests run everywhere; native CI exercises ReplaceFileW.
        monkeypatch.setattr(
            install_module,
            "_replace_preferences",
            lambda target, replacement: replacement.replace(target),
        )
    install_module.remove_provider_hooks(root, home)
    result = json.loads(claude.read_text(encoding="utf-8"))
    assert result["keep"] == "résumé"
    assert result["hooks"]["Stop"] == [
        {"matcher": "*", "other": "retain", "hooks": [unrelated, foreign]}
    ]
    assert result["hooks"]["SessionStart"] == []
    assert result["hooks"]["OtherEvent"] == settings["hooks"]["OtherEvent"]
    assert codex.read_text(encoding="utf-8") == "\n" + remaining
    before = {path: path.read_bytes() for path in (claude, codex)}
    install_module.remove_provider_hooks(root, home)
    assert all(path.read_bytes() == content for path, content in before.items())


def test_foreign_and_nested_codex_notify_commands_are_preserved(tmp_path):
    helper = tmp_path / "Docking/docking-hook.exe"
    for text in (
        'notify = ["another-tool", "codex"]\n',
        "[profiles.work]\nnotify = " + json.dumps([str(helper), "codex"]),
    ):
        assert install_module.without_codex_hook(text.encode("utf-8"), helper) is None


def test_invalid_provider_settings_abort_before_changing_the_other_file(tmp_path):
    claude = tmp_path / ".claude/settings.json"
    claude.parent.mkdir()
    claude.write_bytes(b"invalid JSON")
    codex = tmp_path / ".codex/config.toml"
    codex.parent.mkdir()
    content = json.dumps([str(tmp_path / "Docking/docking-hook.exe"), "codex"])
    codex.write_text("notify = " + content, encoding="utf-8")
    with pytest.raises(ValueError):
        install_module.remove_provider_hooks(tmp_path / "Docking", tmp_path)
    assert codex.read_text(encoding="utf-8") == "notify = " + content


def installation(root: Path, version: str = "1.0-first") -> Path:
    payload = root / "versions" / version
    (payload / "runtime/bin").mkdir(parents=True, exist_ok=True)
    (root / "installation.ini").write_text(
        "[Docking]\nApplication=org.docking.Docking\nSchema=1\n", encoding="utf-16"
    )
    (root / "current.ini").write_text(
        f"[Docking]\nCurrent={version}\n", encoding="utf-16"
    )
    return payload


def test_installed_hook_stays_stable_for_old_runtime_after_upgrade(
    tmp_path, monkeypatch
):
    old = installation(tmp_path)
    current = installation(tmp_path, "2.0-second")
    hook = tmp_path / "docking-hook.exe"
    hook.touch()
    for version in (old, current):
        prefix = version / "runtime"
        assert installed_hook(prefix) == hook
        monkeypatch.setattr(hook_command, "is_windows", lambda: True)
        monkeypatch.setattr(
            hook_command,
            "sys",
            SimpleNamespace(
                prefix=str(prefix), executable=str(prefix / "bin/pythonw.exe")
            ),
        )
        assert hook_command.hook_argv("claude", "Stop") == [str(hook), "claude", "Stop"]


@pytest.mark.parametrize("mode", ["portable", "foreign", "schema", "broken", "missing"])
def test_source_portable_and_unowned_installations_keep_interpreter_hooks(
    tmp_path, mode
):
    payload = installation(tmp_path)
    (tmp_path / "docking-hook.exe").touch()
    marker = tmp_path / "installation.ini"
    if mode == "portable":
        prefix = tmp_path / "runtime"
    else:
        prefix = payload / "runtime"
        if mode == "missing":
            (tmp_path / "docking-hook.exe").unlink()
        else:
            marker.write_text(
                {
                    "foreign": "[Docking]\nApplication=other\nSchema=1\n",
                    "schema": "[Docking]\nApplication=org.docking.Docking\nSchema=2\n",
                    "broken": "not an INI file",
                }[mode],
                encoding="utf-8",
            )
    assert installed_hook(prefix) is None


def test_acceptance_resolves_the_active_installed_or_portable_runtime(tmp_path):
    root = tmp_path / "installed"
    for version in ("1.0-first", "2.0-second"):
        payload = installation(root, version)
        (root / "docking-hook.exe").touch()
        marker = payload / "runtime/share/docking/runtime.json"
        marker.parent.mkdir(parents=True)
        marker.write_text("{}", encoding="utf-8")
        assert payload_root(root) == payload
        assert payload_root(payload) == payload
    (root / "current.ini").write_text(
        "[Docking]\nCurrent=..\\outside\n", encoding="utf-16"
    )
    with pytest.raises(ValueError, match="Invalid active"):
        payload_root(root)


def test_native_removal_handles_payload_paths_beyond_max_path(tmp_path, native_helpers):
    payload = installation(tmp_path)
    name = "runtime/lib/" + "long-directory/" * 15 + "owned.txt"
    owned = filesystem_path(payload / name)
    owned.parent.mkdir(parents=True)
    owned.write_text("owned")
    (payload / "uninstall-files.txt").write_bytes(removal_manifest([name]))
    assert len(str(owned)) > 260
    assert guard(native_helpers, "--remove-version", payload) == 0
    assert not owned.exists()


@pytest.fixture(scope="module")
def native_helpers(tmp_path_factory):
    if sys.platform != "win32":
        pytest.skip("Requires native Windows process and filesystem APIs")
    compiler = Path(sys.base_prefix) / "bin/gcc.exe"
    if not compiler.is_file():
        pytest.skip("Requires the UCRT64 GCC package used by Windows CI")
    output = tmp_path_factory.mktemp("installer-tools")
    root = Path(__file__).resolve().parents[1]
    for source, name, flags in (
        ("packaging/windows/installed_launcher.c", "Docking.exe", ["-mwindows"]),
        (
            "packaging/windows/installed_launcher.c",
            "docking-hook.exe",
            ["-mwindows", "-DHOOK_LAUNCHER"],
        ),
        (
            "packaging/windows/install_guard.c",
            "install-guard.exe",
            ["-lrstrtmgr", "-lshell32", "-lole32", "-luuid"],
        ),
        ("tests/fixtures/windows_installer_child.c", "child.exe", []),
    ):
        subprocess.run(
            [
                str(compiler),
                "-municode",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-static-libgcc",
                str(root / source),
                "-o",
                str(output / name),
                *flags,
            ],
            check=True,
        )
    return output


def guard(helpers: Path, operation: str, root: Path) -> int:
    return subprocess.run(
        [str(helpers / "install-guard.exe"), operation, str(root)], timeout=30
    ).returncode


def manifest(payload: Path, *lines: str) -> None:
    (payload / "uninstall-files.txt").write_bytes(
        ("Docking-Install-Manifest-1\r\n" + "\r\n".join(lines) + "\r\n").encode(
            "utf-16"
        )
    )


def test_native_installed_launchers_follow_current_version_and_preserve_io(
    tmp_path, native_helpers
):
    root = tmp_path / "Docking résumé 日本語"
    installation(root)
    shutil.copy2(native_helpers / "Docking.exe", root / "Docking.exe")
    shutil.copy2(native_helpers / "docking-hook.exe", root / "docking-hook.exe")
    arguments = ["claude", "", 'quoted "word"', "日本語", "trailing\\"]
    for version in ("1.0-first", "2.0-second"):
        payload = installation(root, version)
        shutil.copy2(native_helpers / "child.exe", payload / "Docking.exe")
        shutil.copy2(native_helpers / "child.exe", payload / "runtime/bin/python.exe")
        for name in ("Docking.exe", "docking-hook.exe"):
            result = subprocess.run(
                [str(root / name), *arguments],
                input="résumé 日本語\n",
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=20,
            )
            assert result.returncode == 23, result.stderr
            lines = result.stdout.splitlines()
            expected = arguments
            if name == "docking-hook.exe":
                expected = [
                    "-X",
                    "utf8",
                    "-I",
                    "-m",
                    "docking.applets.aiusage.hook",
                    *arguments,
                ]
                assert "UTF8:1" in lines
            assert [
                line.removeprefix("ARG:") for line in lines if line.startswith("ARG:")
            ] == expected
            target = payload / (
                "Docking.exe" if name == "Docking.exe" else "runtime/bin/python.exe"
            )
            assert [
                Path(line.removeprefix("EXE:"))
                for line in lines
                if line.startswith("EXE:")
            ] == [target]
            assert "INPUT:résumé 日本語" in lines
            assert result.stderr.strip() == "child stderr"


@pytest.mark.parametrize(
    "version", ["../other", "..\\other", "C:\\other", "bad.", "", "x" * 128]
)
def test_native_hook_rejects_invalid_version_without_running_a_child(
    tmp_path, native_helpers, version
):
    installation(tmp_path)
    (tmp_path / "current.ini").write_text(
        f"[Docking]\nCurrent={version}\n", encoding="utf-16"
    )
    shutil.copy2(native_helpers / "docking-hook.exe", tmp_path / "docking-hook.exe")
    result = subprocess.run([str(tmp_path / "docking-hook.exe")], timeout=10)
    assert result.returncode == 1


def test_native_guard_distinguishes_owned_process_from_same_named_external_process(
    tmp_path, native_helpers
):
    root = tmp_path / "installed"
    payload = installation(root)
    assert guard(native_helpers, "--in-use", root) == 0
    assert guard(native_helpers, "--in-use", tmp_path / "absent") == 0
    for relative in (
        "Docking.exe",
        "runtime/bin/python.exe",
        "runtime/bin/pythonw.exe",
    ):
        shutil.copy2(native_helpers / "child.exe", payload / relative)
    for executable, expected in (
        (native_helpers / "child.exe", 0),
        (payload / "runtime/bin/pythonw.exe", 10),
    ):
        with subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as child:
            try:
                while child.stdout.readline().strip() != b"READY":
                    assert child.poll() is None
                assert guard(native_helpers, "--in-use", root) == expected
                assert child.poll() is None
            finally:
                child.communicate(b"done\n", timeout=10)
            assert child.returncode == 23
    assert guard(native_helpers, "--in-use", root) == 0


def test_native_installer_claims_only_owned_or_empty_local_program_folders(
    tmp_path, native_helpers
):
    programs = Path(os.environ["LOCALAPPDATA"]) / "Programs"
    programs.mkdir(exist_ok=True)
    assert guard(native_helpers, "--validate-root", tmp_path) == 12
    with tempfile.TemporaryDirectory(
        dir=programs, prefix="Docking-guard-test-"
    ) as name:
        root = Path(name)
        assert guard(native_helpers, "--validate-root", root) == 0
        sentinel = root / "user-data.txt"
        sentinel.write_text("keep")
        assert guard(native_helpers, "--validate-root", root) == 12
        payload = installation(root)
        assert guard(native_helpers, "--validate-root", root) == 0
        assert guard(native_helpers, "--validate-current", root) == 12
        manifest(payload, "D\truntime\\bin", "D\truntime")
        assert guard(native_helpers, "--validate-current", root) == 0
        assert sentinel.read_text() == "keep"


def test_native_generated_inventory_removes_a_complete_owned_payload(
    tmp_path, native_helpers
):
    payload = installation(tmp_path)
    files = [
        "runtime/bin/owned.dll",
        "runtime/lib/package/owned.py",
        "runtime/share/日本語/notice.txt",
    ]
    for name in files:
        path = payload / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("owned", encoding="utf-8")
    (payload / "uninstall-files.txt").write_bytes(removal_manifest(files))
    assert guard(native_helpers, "--validate-version", payload) == 0
    assert (payload / files[0]).exists()
    assert guard(native_helpers, "--remove-version", payload) == 0
    assert not payload.exists()


def test_native_builder_compiles_and_cleans_an_installer_in_the_msys_environment(
    tmp_path, native_helpers
):
    compiler = Path(sys.base_prefix) / "bin/makensis.exe"
    if not compiler.is_file():
        pytest.skip("Requires the NSIS package used by Windows CI")
    # A small compile-only payload exercises the real builder, not the desktop
    # runtime. The complete runtime and setup lifecycle have separate acceptance.
    files = {
        "Docking.exe": (native_helpers / "child.exe").read_bytes(),
        "runtime/share/docking/runtime.json": json.dumps({"version": "0.0.0"}).encode(),
        "runtime/lib/python3/site-packages/docking/platform/backends/windows/installation.py": Path(
            install_module.__file__
        ).read_bytes(),
    }
    archive = tmp_path / "portable.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr(
            "Docking/files.sha256.json",
            json.dumps(
                {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
            ),
        )
        for name, data in files.items():
            target.writestr("Docking/" + name, data)
    output = tmp_path / "Docking-setup.exe"
    build(archive, output, Path(sys.base_prefix))
    assert output.read_bytes().startswith(b"MZ")
    assert (
        output.with_suffix(".exe.sha256").read_text().split()[0]
        == hashlib.sha256(output.read_bytes()).hexdigest()
    )
    assert not list(tmp_path.glob("docking-setup-*"))


def test_native_interrupted_first_install_keeps_ownership_and_removal_inventory(
    tmp_path, native_helpers
):
    from tools.windows.visual.installer_smoke import (
        UNINSTALL_KEY,
        registry_value,
    )

    compiler = Path(sys.base_prefix) / "bin/makensis.exe"
    if not compiler.is_file():
        pytest.skip("Requires the NSIS package used by Windows CI")
    if registry_value(UNINSTALL_KEY, "InstallLocation") is not None:
        pytest.skip("Requires a Windows profile without an existing Docking install")
    source = Path(__file__).resolve().parents[1] / "packaging/windows/installer.nsi"
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "owned.txt").write_text("owned", encoding="utf-8")
    inventory = removal_manifest(["owned.txt"])
    (payload / "uninstall-files.txt").write_bytes(inventory)
    commands = payload_script(["owned.txt"])
    # Emulate a slow storage write immediately after the complete inventory.
    # Termination here exercises the actual setup script before payload data.
    boundary = 'File /oname=uninstall-files.txt "${PAYLOAD}\\uninstall-files.txt"\n'
    assert commands.count(boundary) == 1
    (native_helpers / "payload.nsh").write_text(
        commands.replace(boundary, boundary + "Sleep 10000\n"), encoding="utf-8"
    )
    setup = tmp_path / "interruption-setup.exe"
    settings = tmp_path / "settings.nsh"
    values = {
        "PAYLOAD": native_path(payload),
        "HELPERS": native_path(native_helpers),
        "OUTPUT": native_path(setup),
        "APP_VERSION": "0.0.0",
        "VERSION_ID": "0.0.0-interruption",
        "FILE_VERSION": "0.0.0.0",
        "INSTALLED_KIB": "1",
    }
    settings.write_text(
        "\n".join(f'!define {name} "{value}"' for name, value in values.items()) + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            str(compiler),
            "/V2",
            "/INPUTCHARSET",
            "UTF8",
            f"/DSETTINGS={native_path(settings)}",
            native_path(source),
        ],
        check=True,
    )
    programs = Path(os.environ["LOCALAPPDATA"]) / "Programs"
    programs.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=programs, prefix="Docking-interruption-"
    ) as directory:
        root = Path(directory)
        command = (
            subprocess.list2cmdline([native_path(setup), "/S"])
            + " /D="
            + native_path(root)
        )
        with subprocess.Popen(command) as process:
            try:
                manifest_path = root / "versions/0.0.0-interruption/uninstall-files.txt"
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if (
                        manifest_path.is_file()
                        and manifest_path.read_bytes() == inventory
                    ):
                        break
                    assert process.poll() is None
                    time.sleep(0.02)
                else:
                    raise AssertionError(
                        "Setup did not publish its ownership inventory"
                    )
                assert not (root / "current.ini").exists()
                assert not manifest_path.with_name("owned.txt").exists()
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
        assert guard(native_helpers, "--validate-root", root) == 0
        version = manifest_path.parent
        assert guard(native_helpers, "--remove-version", version) == 0
        assert not version.exists()
        assert registry_value(UNINSTALL_KEY, "InstallLocation") is None


def test_native_manifest_cleanup_preserves_unknown_files_and_source_cache(
    tmp_path, native_helpers
):
    payload = installation(tmp_path)
    package = payload / "runtime/lib/example"
    cache = package / "__pycache__"
    cache.mkdir(parents=True)
    (package / "owned.py").write_text("owned")
    (cache / "owned.cpython-314.pyc").write_bytes(b"generated")
    sentinel = cache / "user.cpython-314.pyc"
    sentinel.write_bytes(b"keep")
    (payload / "user-data.txt").write_text("keep")
    manifest(
        payload,
        "F\truntime\\lib\\example\\owned.py",
        "D\truntime\\lib\\example",
        "D\truntime\\lib",
        "D\truntime\\bin",
        "D\truntime",
    )
    assert guard(native_helpers, "--remove-version", payload) == 0
    assert not (package / "owned.py").exists()
    assert not (cache / "owned.cpython-314.pyc").exists()
    assert sentinel.read_bytes() == b"keep"
    assert (payload / "user-data.txt").read_text() == "keep"
    assert not (payload / "uninstall-files.txt").exists()


@pytest.mark.parametrize(
    "bad",
    [
        "..\\outside.txt",
        "C:\\outside.txt",
        "runtime\\..\\outside.txt",
        "runtime\\*",
        "runtime\\NUL.txt",
        "runtime\\trailing.",
        "runtime\\",
        "runtime\\bad\x00ignored",
    ],
)
def test_native_invalid_inventory_is_rejected_before_any_file_is_deleted(
    tmp_path, native_helpers, bad
):
    payload = installation(tmp_path)
    owned = payload / "owned.txt"
    owned.write_text("keep on validation failure")
    manifest(payload, "F\towned.txt", "F\t" + bad)
    assert guard(native_helpers, "--remove-version", payload) == 12
    assert owned.exists()
    assert (payload / "uninstall-files.txt").exists()


def test_native_locked_owned_file_reports_failure_and_can_be_retried(
    tmp_path, native_helpers
):
    payload = installation(tmp_path)
    owned = payload / "owned.txt"
    owned.write_text("owned")
    manifest(payload, "F\towned.txt", "D\truntime\\bin", "D\truntime")
    with owned.open("rb"):
        assert guard(native_helpers, "--remove-version", payload) == 12
        assert (payload / "uninstall-files.txt").exists()
    assert guard(native_helpers, "--remove-version", payload) == 0
    assert not payload.exists()


def test_native_manifest_cleanup_rejects_directory_junction_before_deleting(
    tmp_path, native_helpers
):
    payload = installation(tmp_path / "installation")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("keep")
    owned = payload / "owned.txt"
    owned.write_text("keep on validation failure")
    junction = payload / "linked"
    subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            native_path(junction),
            native_path(outside),
        ],
        check=True,
        capture_output=True,
    )
    try:
        manifest(payload, "F\towned.txt", "F\tlinked\\sentinel.txt")
        assert guard(native_helpers, "--remove-version", payload) == 12
        assert owned.exists()
        assert (outside / "sentinel.txt").read_text() == "keep"
    finally:
        junction.rmdir()
