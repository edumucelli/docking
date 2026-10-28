"""Versioned installation hooks and real native launcher/removal ownership."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from docking.applets.aiusage import hook_command
from docking.platform.backends.windows import installation as install_module
from docking.platform.backends.windows.installation import installed_hook
from tools.windows.bundle import payload_root
from tools.windows.installer import payload_script, removal_manifest


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
    owned = Path("\\\\?\\" + str(payload / name))
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
            assert "EXE:" + str(target) in lines
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
        "PAYLOAD": str(payload),
        "HELPERS": str(native_helpers),
        "OUTPUT": str(setup),
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
            f"/DSETTINGS={settings}",
            str(source),
        ],
        check=True,
    )
    programs = Path(os.environ["LOCALAPPDATA"]) / "Programs"
    programs.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=programs, prefix="Docking-interruption-"
    ) as directory:
        root = Path(directory)
        command = subprocess.list2cmdline([str(setup), "/S"]) + " /D=" + str(root)
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
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
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
