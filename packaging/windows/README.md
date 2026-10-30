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
These are unsigned preview packages. Authenticode signing is deferred; release
checksums identify the published bytes but do not provide a publisher signature.
See the [Windows install and update instructions](../../docs/INSTALLATION.md#windows-preview).
The GStreamer plugin set covers Ambient's OGG playback and generated noise;
Windows web playback uses WebView2.

Build in the same MSYS2 UCRT64 environment as the
[Windows workflow](../../.github/workflows/windows.yml), including the UCRT64 GCC
and NSIS packages. Compile translations before building the application wheel:

```sh
bash tools/i18n.sh --compile
python -m build --wheel --no-isolation
python -m tools.windows.bundle --wheel dist/docking-2.28.0-py3-none-win_amd64.mingw_x86_64_ucrt_gnu.whl \
  --output artifacts/windows/Docking \
  --archive dist/docking-2.28.0-windows-x86_64.zip
python -m tools.windows.installer --archive dist/docking-2.28.0-windows-x86_64.zip \
  --output dist/docking-2.28.0-windows-x86_64-setup.exe
python -m tools.windows.visual.bundle_smoke --archive dist/docking-2.28.0-windows-x86_64.zip \
  --output artifacts/windows/bundle-preflight
python -m tools.windows.visual.smoke \
  --bundle artifacts/windows/bundle-preflight/extracted/Docking \
  --output artifacts/windows/bundle-desktop
python -m tools.windows.visual.release_smoke \
  --archive dist/docking-2.28.0-windows-x86_64.zip \
  --bundle artifacts/windows/bundle-preflight/extracted/Docking \
  --output artifacts/windows/release-readiness
```

The destination must not exist. The builder follows the installed pacman runtime
dependency graph and the wheel's Python requirements; it does not copy the
developer's entire Python installation. Dependency versions, the wheel checksum,
redistribution notices, and a SHA-256 file inventory travel with the application.
The WebView2 SDK is downloaded and verified using `tools.windows.webview2_setup`;
pass `--webview2-package path/to/verified-sdk.nupkg` for an offline build.

Windows wheels include x64 and x86 input-thread helpers compiled by the UCRT64
GCC/binutils toolchain. The helpers use Windows system libraries and require no
compiler or MSYS2 runtime on the installed machine. Their wheel tags accept both
standard Windows x64 Python and MSYS2 UCRT64 Python without a Python-version ABI
dependency. Editable source installs also compile both helpers; source archives
and Linux/BSD wheels exclude generated Windows DLLs.

Windows source builds require setuptools 64.0+ for editable output mappings and
wheel 0.38.2+ for combined Windows/MSYS2 platform tags. The `dev` extra installs
these tools on every OS so the packaging tests exercise the same commands.
Isolated Windows builds resolve these requirements from `pyproject.toml`.

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
python -m tools.windows.installer --fixture-source . \
  --output artifacts/windows/upgrade-fixture
python -m tools.windows.visual.installer_smoke \
  --installer dist/docking-2.28.0-windows-x86_64-setup.exe \
  --archive dist/docking-2.28.0-windows-x86_64.zip \
  --previous-installer artifacts/windows/upgrade-fixture/previous-setup.exe \
  --previous-archive artifacts/windows/upgrade-fixture/previous.zip \
  --output artifacts/windows/installer
```

It checks Unicode installation paths, every installed file's checksum, native
shortcut/Installed Apps registration, in-use rejection, upgrades, startup-choice
preservation and disabling, the installed desktop, and uninstall with retained
preferences and unknown files. Windows CI builds and uploads the ZIP, installer,
checksums, reports, logs and desktop captures.

The upgrade fixture builds a real older-version wheel, portable archive and
installer from an owned temporary source copy. Only that copy's project version
changes, to the current release's `.dev0` version. Both installations are checked
against their own exact inventory, wheel and version; their file counts may
differ. This proves a distinct-version installation transition with current
code, rather than compatibility with every earlier published release. The
original checkout remains unchanged. The fixture can use the same offline
`--webview2-package` option as the bundle builder.

`release_smoke` records launcher-to-visible and launcher-to-responsive times for
three separate starts and normal closes, then samples one 120-second idle session
with Applications, Clock, Calendar, Calculator and Hydration pinned. Each sample
checks native window responsiveness and captures owned-process CPU time, working
set, private bytes, threads, handles and GDI/USER object counts. The report retains
the raw samples, process creation identities, resource deltas, normal exits and
cleanup failures. It isolates configuration and removes development runtime
paths. Measurements follow payload verification, so cache state is uncontrolled;
they do not measure cold boot, login, arbitrary applet workloads or all-day use.
There is no performance SLA or leak-free claim. `--cycles`, `--seconds` and
`--interval` allow investigation, but release staging requires at least three
startup cycles, 120 seconds, complete samples and clean shutdowns.

The release workflow stages the accepted Windows CI artifact alongside the
Linux and BSD assets. It verifies the project version, package checksums and the
matching successful installer report before copying the ZIP and installer to
versioned and `docking-latest-windows-x86_64` names, with `SHA256SUMS-windows`.
Acceptance binds the exact archive, installer, raw payload inventory and wheel
SHA-256 digests plus version, and requires the older-version upgrade and bounded
startup/session reports. A matching file count alone cannot authorize a package.
It requires `artifacts/windows/applications-explorer/result.json` for the same
installed wheel: all three one-file/two-file document-drop routes, the default,
explicit and absent association checks, launcher export, native inventory
changes and Explorer replacement. Embedded child reports must retain physical
input, exact application grouping, normal shutdown and cleanup, plus the old and
replacement shell identities. CI and release staging use the same validator;
missing or dependency-skipped workflows cannot authorize publication.
It also requires both complete 40-case native tray API matrices and both GTK
interaction profiles under `artifacts/windows/tray/`. The installed backend and
GTK reports must match the ZIP's wheel checksum and record successful physical
input, focus routing and cleanup. Captures remain labeled as not visually
reviewed. See [native tray acceptance](../../docs/WINDOWS_TRAY_ACCEPTANCE.md).
The native Windows CI job also runs this staging check before uploading its
artifact, so pull requests exercise the same release checks with real packages.
To stage a downloaded `windows-ucrt64-x64` artifact locally:

```sh
python -m tools.windows.release --directory path/to/downloaded-artifact \
  --version 2.28.0 --output artifacts/release
```

Windows previews remain unsigned. Docking's shared update notice opens the
release page; installation is manual, as with direct Linux/BSD downloads. Close
Docking and its usage-hook processes before running the new installer. Signing
and broader long-session/performance coverage are deferred.
