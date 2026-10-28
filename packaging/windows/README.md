# Windows packaging

The Windows x64 installer and portable build contain Docking, Python, GTK 3,
GStreamer, their runtime dependencies, and the pinned Microsoft WebView2 loader.
Extract the entire ZIP and open `Docking/Docking.exe`. No checkout, Python installation,
MSYS2 shell, administrator access, or machine-wide PATH change is needed.
The target is Windows 11 x64; WebView2-based applets also need Microsoft's
Evergreen WebView2 Runtime. The separate experimental Qt browser is not bundled.
The installer defaults to `%LOCALAPPDATA%\Programs\Docking`, adds a Start menu
shortcut and an Installed Apps entry, and offers an optional launch-at-login
checkbox. Windows Startup Apps policy continues to apply.
The GStreamer plugin set covers Ambient's OGG playback and generated noise;
Windows web playback uses WebView2.

Build in the same MSYS2 UCRT64 environment as the
[Windows workflow](../../.github/workflows/windows.yml), including the UCRT64 GCC
and NSIS packages. Compile translations before building the application wheel:

```sh
bash tools/i18n.sh --compile
python -m build --wheel --no-isolation
python -m tools.windows.bundle --wheel dist/docking-2.28.0-py3-none-any.whl \
  --output artifacts/windows/Docking \
  --archive dist/docking-2.28.0-windows-x86_64.zip
python -m tools.windows.installer --archive dist/docking-2.28.0-windows-x86_64.zip \
  --output dist/docking-2.28.0-windows-x86_64-setup.exe
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

Setup verifies the portable artifact before building the executable. Upgrades
require Docking and its usage hooks to close, extract a new version directory,
and atomically switch the stable launcher to it. Previous payloads are retained;
they are removed when Docking is uninstalled. Stable usage-hook commands continue
to work after an upgrade. The uninstaller removes owned provider-hook entries,
installed files and generated Python caches, while preserving application
preferences and unknown files. Such files can leave installation folders behind.
Ownership and the removal inventory are written before payload data so an
interrupted first copy remains recognizable for retry and cleanup.

Silent installation supports `/S` and `/STARTUP=1` or `/STARTUP=0`; omitting
`/STARTUP` preserves an existing choice. An optional `/D=` path must be last,
unquoted, and inside the current user's local Programs directory. Setup refuses
unrelated nonempty folders and migration to a different registered location.

The acceptance tools remove development search paths and exercise installed
modules, SVG/PNG loaders, theme icons, multimedia plugins, applet discovery, and
the native camera helper. The desktop check launches `Docking.exe`, opens menus
and Preferences, edits a setting, captures the desktop, and quits normally.

Run the complete installer check on an interactive Windows desktop with no other
Docking instance or existing installed copy:

```sh
python -m tools.windows.visual.installer_smoke \
  --installer dist/docking-2.28.0-windows-x86_64-setup.exe \
  --output artifacts/windows/installer
```

It checks Unicode installation paths, every installed file's checksum, native
shortcut/Installed Apps registration, in-use rejection, upgrades, startup-choice
preservation and disabling, the installed desktop, and uninstall with retained
preferences and unknown files. Windows CI builds and uploads the ZIP, installer,
checksums, reports, logs and desktop captures.

The release workflow stages the accepted Windows CI artifact alongside the
Linux and BSD assets. It verifies the project version, package checksums and the
matching successful installer report before copying the ZIP and installer to
versioned and `docking-latest-windows-x86_64` names, with `SHA256SUMS-windows`.
The native Windows CI job also runs this staging check before uploading its
artifact, so pull requests exercise the same release checks with real packages.
To stage a downloaded `windows-ucrt64-x64` artifact locally:

```sh
python -m tools.windows.release --directory path/to/downloaded-artifact \
  --version 2.28.0 --output artifacts/release
```

Release signing, automatic update delivery and broader startup-performance
acceptance remain pending.
