# Visual testing

All visual suites live here. Native suites are grouped by the backend that owns
their integration, with desktop modules for generic layer-shell and reduced
backends. Platform setup and general VM/packaging utilities remain in `tools/`.

| Directory | Contents |
| --- | --- |
| `rendering/` | Cairo PNG baselines, image metrics and GTK popup regressions. |
| `backends/` | Native desktop adapters, probes, fixtures, acceptance checks and their baselines. |
| `shared/linux/` | Compositor lifecycle, native input, scenario execution and independent pixel comparisons. |
| `targets.py` | The supported target registry, also used to generate the Linux Actions matrix. |

```sh
# List supported native targets without starting a desktop.
.venv/bin/python -m tests.visual --list

# Run the minimum native suite for one Linux desktop (isolated Docker image).
.venv/bin/python -m tests.visual --target sway
.venv/bin/python -m tests.visual --target x11-xfce

# Renderer and GTK screenshot regressions.
xvfb-run -a .venv/bin/python -m pytest tests/visual/rendering -m visual -o addopts= -q

# Run Windows checks against the installed wheel, on native Windows.
python -m tests.visual --target windows-server --installed-root artifacts/windows/runtime

# Installed BSD packages continue through their native VM workflow.
sh tools/bsd/vm-run.sh artifacts/bsd-visual/freebsd
```

Each native target requires independently captured dock pixels, real launcher
input, the expected backend and successful shutdown. Placement-capable targets
check all four edges and a configured gap. Window-capable backends additionally
exercise their native actions and supported visibility behavior. Reduced targets
must visibly render and accept launcher input; unsupported placement is never a
substitute for this check. Missing required capabilities fail the target.

Every pull request runs all registered Linux targets. The `Native visual (NAME)`
checks are required by `master` branch protection, alongside the BSD and Windows
desktop checks. Keep the workflow's pull-request trigger unfiltered so required
checks report a result for every PR.

Linux native evidence goes under `artifacts/visual/`. Renderer failures go under
`rendering/output/`. Existing Windows and BSD release acceptance reports retain
their artifact paths and formats. Baseline updates remain explicit; moving a
suite does not change its PNGs, thresholds or provenance.

The native Linux Actions workflow runs every registered Linux target on relevant
PRs, pushes, weekly and manual runs. Windows and BSD retain their existing native
Actions workflows. Renderer comparisons run in the main CI under Xvfb. Ordinary
pytest and pre-commit run harness unit tests, without launching native desktops;
tests marked `visual` remain excluded by default.

GPU-dependent Linux targets use the pinned Debian guest in
`tools/linux/visual_vm.py`: an emulated VirtIO GPU supplies DRM buffers through
VirGL, backed by host Mesa llvmpipe on a private Xvfb display. Rendering stays on
the CPU. The host needs QEMU with SDL/OpenGL, Xvfb, cloud-image-utils, SSH and Docker; no
physical GPU or self-hosted runner is required. The image is checked against its
pinned SHA512 before boot. Guest failures remain failures, with the boot log and
available native evidence retained. The download cache is `artifacts/visual-vm-cache/`.

Desktop names recognized only by detection code, including River, Louvre,
Pantheon and Unity, are outside this visual target list. Distribution aliases do
not create additional desktop targets. Windows 11 hosted coverage uses AMD64
emulation on the ARM runner; it does not establish a native ARM64 distribution.

See [the compositor investigation](../../docs/VISUAL_COMPOSITOR_TESTING.md) for
the deeper scenarios, capture contracts and historical findings.
