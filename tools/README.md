# Development tools

Run these tools from the repository root. Shared tools stay directly in `tools/`;
platform-specific tools live under the corresponding platform directory.

| Location | Purpose |
| --- | --- |
| `linux/visual/` | Linux desktop and compositor checks, including the matrix runner, adapters, probes, Docker images, and visual baselines. |
| `linux/` | Live COSMIC protocol checks, GNOME bridge management, Wayfire IPC, and the XWayland redraw reproducer. |
| `bsd/visual/` | Full-process X11 visual checks and BSD VM setup and execution helpers. |
| `tools/` root | Shared translation, packaging, versioning, test, benchmark, and asset-generation tools. |

Future Windows-specific tools belong in `tools/windows/`, with visual checks under
`tools/windows/visual/` when implemented.

The two existing visual harnesses serve related purposes with different coverage.
The Linux harness exercises multiple Wayland compositors and Cinnamon/X11,
including desktop integration and screenshot comparisons against baselines. The
BSD harness runs the complete dock in a private Xvfb/Xfwm session on FreeBSD and
OpenBSD, and on Linux as a control. It checks placement and interaction using
captured pixels and real XTest input.

```bash
# Linux compositor matrix (Docker).
bash tools/linux/visual/matrix.sh --compositor sway --case placement-bottom --geometry-only

# Linux installed compositor in an isolated local session.
bash tools/linux/visual/local_run.sh matrix weston --case placement-bottom

# BSD X11 checks, also runnable on Linux with the same desktop dependencies.
.venv/bin/python -m tools.bsd.visual.run --case placement-bottom
```

Linux visual evidence goes under `tools/linux/visual/evidence/`; BSD evidence goes
under `artifacts/bsd-visual/`. Both are ignored by Git. Setup and coverage details
are in the [development guide](../docs/DEVELOPMENT.md#bsd-package-and-desktop-checks)
and [compositor testing guide](../docs/VISUAL_COMPOSITOR_TESTING.md).
