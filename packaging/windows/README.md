# Windows packaging

The portable Windows x64 build contains Docking, Python, GTK 3, GStreamer, their
runtime dependencies, and the pinned Microsoft WebView2 loader. Extract the
entire ZIP and open `Docking/Docking.exe`. No checkout, Python installation,
MSYS2 shell, administrator access, or machine-wide PATH change is needed.
The target is Windows 11 x64; WebView2-based applets also need Microsoft's
Evergreen WebView2 Runtime. The separate experimental Qt browser is not bundled.
The GStreamer plugin set covers Ambient's OGG playback and generated noise;
Windows web playback uses WebView2.

Build in the same MSYS2 UCRT64 environment as the
[Windows workflow](../../.github/workflows/windows.yml), adding the UCRT64 GCC
package. Compile translations before building the application wheel:

```sh
bash tools/i18n.sh --compile
python -m build --wheel --no-isolation
python -m tools.windows.bundle --wheel dist/docking-2.28.0-py3-none-any.whl \
  --output artifacts/windows/Docking \
  --archive dist/docking-2.28.0-windows-x86_64.zip
python -m tools.windows.visual.bundle_smoke --archive dist/docking-2.28.0-windows-x86_64.zip \
  --output artifacts/windows/bundle-preflight
python -m tools.windows.visual.smoke \
  --bundle artifacts/windows/bundle-preflight/extracted/Docking \
  --output artifacts/windows/bundle-desktop
```

The destination must not exist. The builder follows the installed pacman runtime
dependency graph and the wheel's Python requirements; it does not copy the
developer's entire Python installation. Dependency versions, the wheel checksum,
redistribution notices, and a SHA-256 file inventory travel with the application.
The WebView2 SDK is downloaded and verified using `tools.windows.webview2_setup`;
pass `--webview2-package path/to/verified-sdk.nupkg` for an offline build.

The native launcher uses the interpreter beside it, in isolated mode. Runtime
resources are located relative to that interpreter, including after moving the
application directory. Camera and AI usage helpers keep a real Python executable.
User configuration remains in the normal per-user application directories.

The acceptance tools remove development search paths and exercise installed
modules, SVG/PNG loaders, theme icons, multimedia plugins, applet discovery, and
the native camera helper. The desktop check launches `Docking.exe`, opens menus
and Preferences, edits a setting, captures the desktop, and quits normally.

This portable artifact is an incremental packaging target. A per-user installer,
upgrade/uninstall flow, startup integration, and release signing remain pending.
