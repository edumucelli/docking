# Windows development tools

Run these tools from the repository root. Native checks require Windows.

- `ci`: native runtime preflight, Windows/core tests and GTK behavior checks.
- `webview2_setup`: install the verified Microsoft WebView2 loader.
- `bundle`: assemble a relocatable application from a wheel and its native runtime;
  see [Windows packaging](../../packaging/windows/README.md).
- `installer`: build per-user setup from a verified portable ZIP using NSIS.
- `release`: stage versioned Windows packages from accepted CI artifacts.
- [`../../tests/visual/backends/windows/`](../../tests/visual/backends/windows/README.md): native desktop acceptance, fixtures, captures and independent observations.
- `lab/`: general VM controls, input and capture transport.

Start with [Windows source setup](../../docs/WINDOWS.md#source-development).

```sh
python -m tools.windows.lab.run --help
python -m tools.windows.lab.run --share /path/to/shared status
python -m tools.windows.ci --stage preflight
python -m tools.windows.ci --stage tests
python -m tools.windows.ci --stage gtk
```

The [Windows workflow](../../.github/workflows/windows.yml) runs these checks
with MSYS2 UCRT64, builds the wheel, portable ZIP and per-user installer, then
exercises each installation. Reports, screenshots and packages are uploaded as
Actions artifacts; generated Windows captures do not belong in `images/`.

## Desktop lab

The [lab compose files](../../packaging/windows/lab/) run an owned Windows VM with
loopback-only browser/VNC/RDP access and durable jobs under the shared artifact
directory. Supply an official Windows 11 x64 ISO and private environment file
with `DOCKING_WINDOWS_STORAGE`, `DOCKING_WINDOWS_SHARE`,
`DOCKING_WINDOWS_SOURCE`, `DOCKING_WINDOWS_ISO` and
`DOCKING_WINDOWS_PASSWORD`.

```sh
docker compose --env-file /path/to/lab.env -f packaging/windows/lab/compose.yaml -f packaging/windows/lab/compose.install.yaml up -d
python -m tools.windows.lab.run --share /path/to/shared/artifacts configure-share
python -m tools.windows.lab.run --share /path/to/shared/artifacts ready
```

After installation, stop gracefully and confirm `storage/windows.boot`. Later
starts use `compose.yaml` without the installation overlay. The original ISO
can be removed after verifying an ISO-free boot; the installed disk and firmware
are the persistent VM.

Run `configure-share` after each container start so SMB leases do not hide host
updates. Check the readiness report's UAC/elevation/integrity facts before
acceptance. Standard-user behavior needs a normal medium-integrity desktop.

Jobs have unique IDs, durable responses and a heartbeat. After an observation
timeout, inspect those files before submitting another copy; an unknown outcome
does not authorize a duplicate job. Stop the regular dock and use isolated
settings/fresh outputs for probes. Prepare the GStreamer registry during runtime
setup rather than counting its initial plugin scan as normal startup.

Capture actual desktop input and native observations. Preserve unrelated files,
applications and settings. Destructive modes such as Explorer restart, Recycle
Bin emptying, camera policy changes and session power actions require their
owned fixtures and verified cleanup. Keep generated results/captures in the
shared artifact directory.
