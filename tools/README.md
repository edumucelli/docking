# Development tools

Run these tools from the repository root. Shared tools stay directly in `tools/`;
platform-specific tools live under the corresponding platform directory.

| Location | Purpose |
| --- | --- |
| [`../tests/visual/`](../tests/visual/README.md) | All renderer and native desktop visual suites, adapters, probes, fixtures and baselines. |
| `linux/` | Linux integration utilities and the pinned software-GPU VM controller. |
| [`linux/tours/`](../docs/TOURS.md) | Record the overview, applet, and Preferences tours from a private X11 desktop. |
| `bsd/` | BSD VM/package setup, checksum pins and installed acceptance orchestration. |
| `windows/lab/` | General Windows VM controls, input and capture transport. |
| `windows/` | Windows CI, packaging, release validation and runtime setup. |
| `tools/` root | Shared translation, packaging, versioning and asset-generation tools. |

The visual harnesses serve related purposes with different coverage.
The Linux harness exercises multiple Wayland compositors and Cinnamon/X11,
including desktop integration and screenshot comparisons against baselines. The
BSD harness runs the complete dock in a private Xvfb/Xfwm session on FreeBSD,
OpenBSD, and NetBSD, and on Linux as a control. It checks placement and interaction using
captured pixels and real XTest input.
The Windows harness runs on the native Win32 desktop using MSYS2 UCRT64 GTK,
with real input, desktop captures and independent native API observations.

```bash
# Linux compositor matrix (Docker).
bash tests/visual/shared/linux/matrix.sh --compositor sway --case placement-bottom --geometry-only

# Linux installed compositor in an isolated local session.
bash tests/visual/shared/linux/local_run.sh matrix weston --case placement-bottom

# BSD X11 checks, also runnable on Linux with the same desktop dependencies.
.venv/bin/python -m tests.visual.backends.x11.run --case placement-bottom

# Windows desktop check (native Windows Python).
python -m tests.visual.backends.windows.smoke --output artifacts/windows/smoke
```

Linux visual evidence goes under `artifacts/visual/`; BSD evidence goes
under `artifacts/bsd-visual/` and Windows evidence under `artifacts/windows/`.
These directories are ignored by Git. Setup and coverage details
are in the [development guide](../docs/DEVELOPMENT.md#bsd-package-and-desktop-checks)
and [compositor testing guide](../docs/VISUAL_COMPOSITOR_TESTING.md), with native
Windows setup in [Windows support](../docs/WINDOWS.md).
