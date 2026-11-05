# Development tools

Run these tools from the repository root. Shared tools stay directly in `tools/`;
platform-specific tools live under the corresponding platform directory.

| Location | Purpose |
| --- | --- |
| `linux/visual/` | Linux desktop and compositor checks, including the matrix runner, adapters, probes, Docker images, and visual baselines. |
| `linux/` | Live COSMIC protocol checks, GNOME bridge management, Wayfire IPC, and the XWayland redraw reproducer. |
| `bsd/visual/` | Full-process X11 visual checks and BSD VM setup and execution helpers. |
| `windows/visual/` | Native Windows desktop checks, fixtures, VM controls, captures and independent observations. |
| `windows/` | Windows CI entry points and WebView2 loader setup. |
| `tools/` root | Shared translation, packaging, versioning, test, benchmark, and asset-generation tools. |

The visual harnesses serve related purposes with different coverage.
The Linux harness exercises multiple Wayland compositors and Cinnamon/X11,
including desktop integration and screenshot comparisons against baselines. The
BSD harness runs the complete dock in a private Xvfb/Xfwm session on FreeBSD and
OpenBSD, and on Linux as a control. It checks placement and interaction using
captured pixels and real XTest input.
The Windows harness runs on the native Win32 desktop using MSYS2 UCRT64 GTK,
with real input, desktop captures and independent native API observations.

```bash
# Linux compositor matrix (Docker).
bash tools/linux/visual/matrix.sh --compositor sway --case placement-bottom --geometry-only

# Linux installed compositor in an isolated local session.
bash tools/linux/visual/local_run.sh matrix weston --case placement-bottom

# BSD X11 checks, also runnable on Linux with the same desktop dependencies.
.venv/bin/python -m tools.bsd.visual.run --case placement-bottom

# Windows desktop check (native Windows Python).
python -m tools.windows.visual.smoke --output artifacts/windows/smoke
```

Linux visual evidence goes under `tools/linux/visual/evidence/`; BSD evidence goes
under `artifacts/bsd-visual/` and Windows evidence under `artifacts/windows/`.
These directories are ignored by Git. Setup and coverage details
are in the [development guide](../docs/DEVELOPMENT.md#bsd-package-and-desktop-checks)
and [compositor testing guide](../docs/VISUAL_COMPOSITOR_TESTING.md), with native
Windows setup in [Windows support](../docs/WINDOWS.md).
