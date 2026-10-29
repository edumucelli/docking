"""Owned classic executables and Unicode shortcuts for native identity checks."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import tempfile
import uuid
from contextlib import ExitStack
from pathlib import Path

from .desktop_driver import WindowsDesktop, read_shared_json

_SOURCE = r"""
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

public static class ClassicFixture {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr GetCommandLineW();

    [STAThread]
    public static void Main(string[] args) {
        var process = Process.GetCurrentProcess();
        var directory = Path.GetDirectoryName(process.MainModule.FileName);
        var factsPath = args.Length >= 2 ? args[0] :
            Path.Combine(directory, "facts-generated.json");
        var title = args.Length >= 2 ? args[1] :
            File.ReadAllText(Path.Combine(directory, "fixture-title.txt"));
        var payload = new string[Math.Max(0, args.Length - 2)];
        if (payload.Length > 0) Array.Copy(args, 2, payload, 0, payload.Length);
        var facts = new {
            pid = process.Id,
            executable = process.MainModule.FileName,
            argv = payload,
            cwd = Environment.CurrentDirectory,
            command_line = Marshal.PtrToStringUni(GetCommandLineW())
        };
        var json = new JavaScriptSerializer().Serialize(facts);
        File.WriteAllText(factsPath + ".tmp", json, new UTF8Encoding(false));
        File.Move(factsPath + ".tmp", factsPath);
        var text = "Application: " + new DirectoryInfo(
            Path.GetDirectoryName(process.MainModule.FileName)).Name +
            "\nArguments:\n" + String.Join("\n", payload) +
            "\nWorking directory: " +
            new DirectoryInfo(Environment.CurrentDirectory).Name;
        if (File.Exists(Path.Combine(directory, "fixture-two-windows.txt"))) {
            var context = new ApplicationContext();
            var remaining = 2;
            for (var index = 0; index < 2; index++) {
                var form = new Form();
                form.Text = title + (index == 0 ? "" : " Second");
                form.Width = 460;
                form.Height = 240;
                form.StartPosition = FormStartPosition.Manual;
                form.Left = 120 + index * 80;
                form.Top = 120 + index * 60;
                var content = new TextBox();
                content.Multiline = true;
                content.ReadOnly = true;
                content.Dock = DockStyle.Fill;
                content.Text = text;
                form.Controls.Add(content);
                form.FormClosed += delegate {
                    if (--remaining == 0) context.ExitThread();
                };
                form.Show();
            }
            Application.Run(context);
            context.Dispose();
            return;
        }
        MessageBox.Show(text, title, MessageBoxButtons.OK,
                        MessageBoxIcon.Information);
    }
}
"""


def _quoted(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def write_shortcut(path: Path, *, executable: Path, argv: list[str], cwd: Path):
    """Use IShellLinkW, independent of the production AppsFolder reader."""
    from docking.platform.backends.windows.com import (
        GUID,
        HRESULT,
        apartment,
        method,
        query,
        release,
    )
    from docking.platform.backends.windows.ffi import bind

    with apartment() as ole, ExitStack() as stack:
        clsid = GUID.parse("00021401-0000-0000-c000-000000000046")
        iid = GUID.parse("000214f9-0000-0000-c000-000000000046")
        pointer = ctypes.c_void_p()
        create = bind(
            ole,
            "CoCreateInstance",
            HRESULT,
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        assert (
            create(
                ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(pointer)
            )
            >= 0
        )
        stack.callback(release, pointer)
        arguments = subprocess.list2cmdline(argv)
        for index, text in ((20, str(executable)), (11, arguments), (9, str(cwd))):
            assert method(pointer, index, HRESULT, ctypes.c_wchar_p)(pointer, text) >= 0
        persistence = query(
            stack, pointer, GUID.parse("0000010b-0000-0000-c000-000000000046")
        )
        assert (
            method(persistence, 6, HRESULT, ctypes.c_wchar_p, ctypes.c_int32)(
                persistence, str(path), 1
            )
            >= 0
        )
        retained = ctypes.create_unicode_buffer(32768)
        assert (
            method(pointer, 10, HRESULT, ctypes.c_wchar_p, ctypes.c_int32)(
                pointer, retained, len(retained)
            )
            >= 0
        )
        assert retained.value == arguments


class ClassicApplications:
    """Same-name programs with two pins, saved history and an unlisted target."""

    def __init__(self, output: Path, *, install_shortcuts: bool = True):
        self.output = output
        self._install_shortcuts = install_shortcuts
        self.root = Path(tempfile.mkdtemp(prefix="docking-classic-")).resolve()
        nonce = uuid.uuid4().hex[:10]
        self.labels = {
            key: f"Docking Classic {key} résumé 日本語 {nonce}"
            for key in ("First", "Second", "Recent", "Other")
        }
        self.links = (
            Path(os.environ["APPDATA"])
            / "Microsoft/Windows/Start Menu/Programs"
            / ("Docking Classic résumé 日本語 " + nonce)
        )
        self.executables = {
            key: self.root
            / (f"Other résumé 日本語 {nonce}" if key == "Other" else key)
            / "SharedFixture.exe"
            for key in self.labels
        }
        self.native = WindowsDesktop()
        self._owns_links = False

    def __enter__(self):
        try:
            if self._install_shortcuts:
                self.links.mkdir()
                self._owns_links = True
            for path in self.executables.values():
                path.parent.mkdir()
            for key, path in self.executables.items():
                (path.parent / "fixture-title.txt").write_text(
                    self.labels[key], encoding="utf-8"
                )
            source = self.root / "fixture.cs"
            source.write_text(_SOURCE)
            compiled = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
                    "Add-Type -TypeDefinition (Get-Content -Raw -LiteralPath "
                    + _quoted(source)
                    + ") -OutputType WindowsApplication -OutputAssembly "
                    + _quoted(self.executables["First"])
                    + " -ReferencedAssemblies "
                    "System.Windows.Forms,System.Web.Extensions",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=90,
            )
            assert compiled.returncode == 0, compiled.stdout + compiled.stderr
            for key in ("Second", "Recent", "Other"):
                shutil.copyfile(self.executables["First"], self.executables[key])
            if self._install_shortcuts:
                for key in ("First", "Second", "Recent"):
                    self.update(key)
            return self
        except BaseException:
            self.close()
            raise

    def facts_path(self, key: str, phase: int = 0) -> Path:
        return self.output / f"facts-{key}-{phase}.json"

    def arguments(self, key: str, phase: int = 0) -> list[str]:
        return [
            str(self.facts_path(key, phase)),
            self.labels[key],
            f"mode {phase}",
            "",
            'literal "quotes"',
            "日本語 résumé #100%",
        ]

    def update(self, key: str, phase: int = 0) -> None:
        cwd = self.executables[key].parent / f"working directory {phase} résumé"
        cwd.mkdir(exist_ok=True)
        write_shortcut(
            self.links / (self.labels[key] + ".lnk"),
            executable=self.executables[key],
            argv=self.arguments(key, phase),
            cwd=cwd,
        )

    def facts(self, key: str, phase: int = 0):
        path = self.facts_path(key, phase)
        self.native.wait_for(
            path.is_file, "Owned classic application did not report its launch", 45
        )
        facts = read_shared_json(path)
        assert Path(facts["executable"]).samefile(self.executables[key])
        assert facts["argv"] == self.arguments(key, phase)[2:], facts
        self.native.wait_for(
            lambda: self.native.windows(facts["pid"]),
            "Owned classic application's window did not appear",
        )
        return facts

    def close(self):
        import psutil

        facts_paths = (
            *self.output.glob("facts-*.json"),
            *self.root.rglob("facts-generated.json"),
        )
        for path in facts_paths:
            facts = read_shared_json(path)
            try:
                process = psutil.Process(facts["pid"])
            except psutil.NoSuchProcess:
                continue
            assert Path(process.exe()).is_relative_to(self.root)
            for window in self.native.windows(process.pid):
                assert any(
                    window["title"] in (label, label + " Second")
                    for label in self.labels.values()
                )
                assert self.native.user.PostMessageW(window["hwnd"], 0x10, 0, 0)
            assert process.wait(20) == 0
        if self._owns_links and self.links.exists():
            shutil.rmtree(self.links)
        if self.root.exists():
            shutil.rmtree(self.root)

    def __exit__(self, *_exception):
        self.close()
