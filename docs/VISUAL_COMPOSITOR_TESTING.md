# Visual testing Docking on Wayland compositors

Docking's recent Wayland fixes were all found by *looking at a real compositor*:
[#337](https://github.com/edumucelli/docking/pull/337) (compositor allocation sizing),
[#335](https://github.com/edumucelli/docking/pull/335) (autohide pointer crossings),
[#347](https://github.com/edumucelli/docking/issues/347)/[#349](https://github.com/edumucelli/docking/pull/349)
(Cinnamon placement). This harness exists to make that method repeatable: it runs Docking under a
real compositor in a container, captures what the screen actually shows, probes the geometry, and
checks what the compositor did against what Docking asked for. Docking's own reporting is never the
oracle — it is what is under test.

What it has found so far is in Findings: the dock displacing a panel (issue 0), `monitor_connector`
being dead code (issue 1), requested positions reported as measured ones (issues 2 and 3), an `int`
scale field (issue 4), kwin capabilities claimed without a probe (issue 5), a left/right dock
committing a layer surface with width 0 (issue 6), and a reservation race on Cinnamon (issue 7).
Evidence is marked **[reproduced]** when the harness demonstrated it on a real compositor and
**[static]** when it comes from reading the code — static findings have not been executed and
should be confirmed with a harness case before being treated as settled.

The case matrix was produced by `tools/visual_compositor_matrix.sh` on 2026-10-02. Every cell is a
real run, not an expectation.

This document merges and supersedes three former ones — the harness guide, the compositor × case
matrix (`docs/VISUAL_TEST_MATRIX.md`) and the confirmed-issues list
(`docs/WAYLAND_SUPPORT_ISSUES.md`). They overlapped heavily; the overlapping material now appears
once, and the matrix's FAIL cells point at the numbered issues instead of restating them.

## Current coverage (2026-10-05)

The detailed matrix and numbered findings below retain the original 2026-10-02
investigation. This section supersedes its older statements about unverified
adapters, unavailable pointer input and missing display-change scenarios.

The matrix now defines **33 cases** across placement, monitors, reservation,
visibility, interaction, window switching, constrained layouts and live output changes. Capabilities
are per adapter: this is not a claim that every case runs on every desktop.

| Compositor | Verified route and scope | Current limitations |
| --- | --- | --- |
| Sway | Headless Pixman; placement, four-edge autohide, zoom, tooltip, menu, six layouts and three output transitions | Pixel geometry; no native layer-surface allocation query or native dodge service |
| Cinnamon | Nested Wayland in private Xvfb; dock role and panel/switcher exclusion, native frame, reservations, window switching and seven native services | Older Muffin uses an XWayland dock surface with native Cinnamon services; newer layer-shell service composition is covered by unit tests |
| Niri | Nested in headless Sway; five non-panel placement cases | Requires the vertical-edge startup fix in [#353](https://github.com/edumucelli/docking/pull/353); one nested output |
| labwc | Headless wlroots; placement | No native dock-frame query |
| COSMIC | Winit nested in headless Sway; native COSMIC placement on five cases | Overlap notification is probed at runtime; absent protocol makes dodge unsupported |
| KWin | Nested Wayland in headless Sway, QPainter; five placement cases | Reduced visibility service; no native dodge |
| Wayfire | Arch Wayfire 0.11, headless Pixman and IPC; five placement cases and active-window dodge | Debian's older 0.9 build needs a render device; the default lane uses Arch |
| Cage | Real headless kiosk compositor; startup, reduced backend and shutdown | Placement intentionally unsupported |
| GNOME/Mutter | GNOME Shell nested Wayland in private Xvfb; startup, reduced backend and shutdown | Shell extension is deliberately absent; this verifies the compatibility fallback, not native GNOME placement |

### Run the additional cases

```bash
# Geometry and behavior assertions without comparing a machine-specific baseline.
bash tools/visual_compositor_matrix.sh --compositor cinnamon --behavior windows --geometry-only --require-supported
bash tools/visual_compositor_matrix.sh --compositor sway --behavior interaction --geometry-only --require-supported
bash tools/visual_compositor_matrix.sh --compositor sway --behavior layouts --geometry-only --require-supported
bash tools/visual_compositor_matrix.sh --compositor sway --behavior displays --geometry-only --require-supported
bash tools/visual_compositor_matrix.sh --compositor sway --behavior visibility --case autohide-bottom --case autohide-top --case autohide-left --case autohide-right --geometry-only --require-supported
bash tools/visual_compositor_matrix.sh --compositor wayfire --case dodge-active --geometry-only --require-supported

# GNOME's expected unsupported placement still checks real app lifecycle.
bash tools/visual_compositor_matrix.sh --compositor gnome --case placement-bottom --geometry-only
```

Layouts include a 360-pixel output, 32 launchers, scale 2, mixed output scales,
90-degree rotation and a negative output origin. Display tests change resolution,
change scale and remove the output holding the dock. Removal requires a capture
showing the dock on that output before unplugging it, then checks placement on
the remaining output. Scene tests create their own outputs; use `LAB_OUTPUTS=1`.
Logical screenshots normalize scale without inventing compositor coordinates.
The many-icon case checks both endpoint anchors against rendered content; it
cannot prove native surface allocation containment on pixel-only adapters.

Input uses a **persistent harness-only virtual pointer**, or XTest on the private
outer Xvfb display. Nested desktops receive input through their parent. Before
advertising pointer support, a real GTK client must receive motion at a known
coordinate. The proof is saved in `input-delivery.json`. No production protocol
bindings are changed. `GetHoverAnchor` locates an input stimulus; the assertions
use observed pixels, not the method's answer as proof that the effect happened.

Visibility cases save hidden/revealed/hidden-again or restored captures and require
substantial disappearance and recovery of dock pixels. Zoom measures the dock
strip, excluding tooltip content. Tooltip and menu checks require changed pixels
above the resting dock; cursor movement and icon highlighting cannot pass them.
These checks establish popup appearance, not popup allocation or screen-edge
containment. Each case also retains startup, backend, frame checks where available,
settling and shutdown checks.

The Cinnamon `windows` case covers issue #369 using real native GTK applications
and dock clicks. It independently observes Cinnamon's window identities, counts,
focus, minimized state and active workspace. It requires switching without
duplicates, minimize/restore, activation across workspaces, intentional creation
of a second window, switching with multiple windows, and normal client closure.
Intermediate screenshots and `window-switching.windows.json` retain the evidence.
The case requires verified pointer delivery and the shell window API, and runs
in the Cinnamon CI lane alongside reservation tests.

The native Cinnamon services regression boots the complete dock, verifies its
DOCK role and exclusion from the real panel and switcher window lists, and tests
workspace switching/filtering, sticky windows, previews with known pixels and
unchanged focus, Show Desktop hide/restore, idle reset, color picking, dodge
hide/reveal, native/XWayland window termination, Escape/service cancellation,
and modal input cleanup after client crashes. It retains compositor PNGs,
preview images, version information, and `services.json` alongside other CI
artifacts. Run it locally using an already-built Cinnamon image:

```bash
DOCKING_LAB_IMAGE=docking-lab:cinnamon bash tools/visual_compositor/cinnamon_services.sh
```

### CI and image maintenance

`.github/workflows/compositor.yml` runs Sway, Cinnamon and Niri on relevant PRs and
master pushes. Weekly and manually dispatched runs add labwc, COSMIC, KWin,
Wayfire, Cage and GNOME. Sway also runs the 16 new autohide, interaction, layout
and output-change cases; Wayfire additionally checks native active-window dodge.
Placement lanes require supported assertions; Cage and
GNOME are explicit negative compatibility lanes. Failures retain screenshots,
intermediate phases, geometry, input proof and logs for 14 days.

CI uses **geometry and behavior assertions**, not pixel comparisons: separately
built images can have different image identities. Dockerfiles pin base image
SHA256 digests, Debian/Arch package snapshots and Pywayland 0.4.19. Arch synchronizes
the whole system with the snapshot to avoid partial upgrades. Update the digest
and snapshot deliberately, rebuild and inspect captures before recording baselines.
The optional build arguments are `DEBIAN_IMAGE`, `DEBIAN_SNAPSHOT`, `ARCH_IMAGE` and
`ARCH_SNAPSHOT`; their defaults live in the Dockerfiles.

For local pixel regression tests, use the same actual image that generated the
baseline. Normal mode keeps strict image and scene provenance checks. Sixteen new
Sway baselines accompany this expansion. `--geometry-only` never writes baselines
and cannot be combined with `--update-baselines`. `--require-supported` turns an
unexpected unsupported case into an unsuccessful run.

### A newly observed COSMIC gap

Opening a real GTK toplevel during the native COSMIC dodge experiment produced
callback `TypeError`s in `CosmicToplevelAdapter._request_cosmic_info`: callbacks
expected an extra argument for output/workspace events. The compositor in this
lane also lacks the overlap-notification protocol, so dodge is explicitly
unsupported and the scheduled placement tests do not exercise that action.
This is a separate production follow-up; placement passing does not establish
complete COSMIC window-tracking or visibility coverage.

## Why: what the existing lanes could not see

| Lane | What it runs against | Why it misses placement bugs |
| --- | --- | --- |
| `tests/visual/` | An offscreen Cairo surface | `render_case()` never maps a window, so no compositor is involved and placement is never exercised |
| `packaging/deb/runtime-smoke.sh` | A real headless Sway | Asserts log-grep and D-Bus only; it never captures a pixel |
| `tests/ui/test_placement.py` | Fake monitors | Asserts `window.move.assert_called_once_with(...)` — that the dock *asked* to move, not that the compositor *complied* |

That last row is the crux. The Cinnamon bug was the compositor ignoring `move()`, so the
arithmetic was right, the unit tests passed, and the dock still rendered in the wrong place.
Only observing the compositor catches that.

## How it works

### Quick start

```bash
# one-time build (~5-10 min: installs sway + the GTK stack)
bash tools/visual_compositor_matrix.sh --rebuild

# run the placement cases on sway
bash tools/visual_compositor_matrix.sh --compositor sway --behavior placement

# first time: record the baselines, then re-run to verify they pass
bash tools/visual_compositor_matrix.sh --compositor sway --update-baselines

# one case, when iterating
bash tools/visual_compositor_matrix.sh --compositor sway --case placement-bottom
```

Requirements: `docker`, and the project venv (`.venv`) — the comparison runs on the host and
uses Pillow/numpy/scikit-image from the `dev` extra.

Artifacts land in `tools/visual_compositor/evidence/<compositor>-<timestamp>/`:
`<case>.png` (full output), `<case>.log` (Docking's log), `<case>.json` (probed geometry).

### Evidence production vs comparison

The container only *produces evidence*; all comparison happens on the host.

```text
host                                     container (debian:trixie)
─────────────────────────────────────    ──────────────────────────────────
tools/visual_compositor_matrix.sh
  scenarios.py ──▶ cases.json ──────────▶ run_session.sh
                                           adapters/sway.sh
                                             ├─ sway (headless wlroots)
                                             ├─ docking (from /src)
                                             └─ grim → <case>.png
  compare.py ◀──── <case>.png + .json ────┘
    locate dock by pixels
    assert edge relationship
    crop + SSIM/PSNR vs baseline
```

Keeping comparison on the host means the container needs no imaging stack, and the metric code
stays in one place — it reuses `tests/visual/support.py`'s `compute_metrics` and `diff_image`
rather than reimplementing them.

### Layer 1 — geometry

The dock is located in the screenshot by finding the bounding box of non-background pixels
inside the band at the expected edge, and asserted to hug that edge. This is an independent
measurement: **Docking's own reporting is never the oracle**, because it is what is under test.
(`WaylandLayerShellSurfaceService.get_surface_position()` returns the *requested* position,
`docking/platform/backends/wayland/services.py:110-114`.)

For `top` and `left`, the anchor is the *near* side of the bounding box; for `bottom` and
`right`, the *far* side. Measuring the wrong side makes every correct top/left placement look
like a failure.

### Layer 2 — pixels

The band at the **expected** edge is cropped and compared against a per-compositor baseline.
Cropping the expected band, not the measured rect, is deliberate: a crop that follows the dock
would follow the bug and hide it.

Thresholds default to SSIM 0.985 / PSNR 30.0 (`LIVE_THRESHOLDS` in `compare.py`), looser than
`tests/visual/support.py`'s 0.995/35.0, which is calibrated for bit-identical in-process
rendering.

### Result vocabulary

Every cell in the case matrix is one of these states.

| Mark | Meaning |
| --- | --- |
| **PASS** | Ran, produced evidence, all assertions held |
| **FAIL** | Startup, backend selection, shutdown or an assertion failed; inspect the evidence to distinguish a Docking finding from a harness fault |
| **UNSUP** | The adapter lacks a case capability, but Docking's startup, expected backend and shutdown were verified. Not a placement pass |
| **BLOCKED** | The compositor never started, so no case ran |
| *(harness)* | The `UNSUP` is the harness's own gap — the adapter lacks the capability, not the compositor |
| **FLAKY** | A fourth state: the same binary and configuration pass in one run and fail in another — diagnosed in issue 7 |

### Provenance

Baselines are only trusted when their per-case sidecar proves they are still applicable. Each
sidecar records the image identity, the output geometry, panel height and position,
effective workarea, crop rectangle, the edge and gap, and the full case
configuration — and all of them are *required* and compared. Missing metadata fails rather than
being skipped: absent provenance is not agreement. Baselines are also written only after every
assertion in a run has passed, so a failed run cannot leave behind a baseline recorded from a
bad state.

Baselines are scoped to the compositor **and** to the container image (see Limitations), so a
compositor, font or icon-theme change invalidates them.

Older sidecars without scene metadata are rejected. Regenerate them with
`--update-baselines` in the intended panel configuration; do not add guessed metadata
to an old capture. Sway uses its own swaybar, while other adapters can use the shared
synthetic panel. A Sway session never starts both.

All 28 committed baselines now have scene metadata from fresh, independently
verified captures. The Niri captures include the startup fix from
[PR #353](https://github.com/edumucelli/docking/pull/353), commit
`4dc597ff312eb67f39d034855b5bad6c5b12112e`; its vertical-edge cases require
that fix. It was applied only in the temporary capture checkout, not added to
this harness branch. Cinnamon reservation captures use the merged fix from
[PR #351](https://github.com/edumucelli/docking/pull/351).

**Measured on sway: the captures are bit-identical across runs** (SSIM 1.00000 / PSNR `inf` on
an independent re-run of all five cases). `WLR_RENDERER=pixman`, a fixed theme and icon size,
the static pinned set, and the settle loop together remove the usual sources of variation. The
looser default is therefore headroom, not a measured need — it could be tightened to the
in-process values, and the intent is to do so once a second compositor lands and shows whether
the determinism is sway-specific.

**Captures must settle.** `sleep 2` (the packaged smoke test's approach) is not enough for pixel
comparison. The harness captures repeatedly and requires two consecutive byte-identical frames,
bounded at `LAB_SETTLE_ATTEMPTS` (default 12). If it exhausts the budget the case is *failed*
with `settled: false` in its JSON, rather than comparing a mid-animation frame.

### Harness issues found and fixed during this work

Recorded here because they affected results, not because they are app bugs.

1. **Probes resolved under the wrong directory.** `session_probe_json` looked
   in `$LAB_DIR` (the evidence directory) instead of the scripts directory, and
   `2>/dev/null` hid the "No such file" error. It surfaced as a *misleading*
   "session did not provide a Wayland display" on the sway lane — a confidently
   wrong answer of exactly the kind issue 5 describes.
2. **`dbus` could not resolve the running UID.** The container runs as the
   invoking UID so evidence is host-owned, but a base image has no passwd entry
   for an arbitrary UID, so `dbus-run-session` died with
   `Looking up user ID N: not found` before the compositor started. The Debian
   lanes worked only because `docking-smoke` happened to be UID 1000; the Arch
   lanes failed, and **any other host UID would have broken every lane.** Fixed
   by generating a per-run passwd/group from the image's own plus the host user.
3. **A monitor case that could not discriminate.** The first
   `monitor-by-connector` case named the primary output, where "connector
   honoured" and "fell back to primary" give identical placements. It was
   rewritten to target the non-primary output, which is what turned issue 1 from
   a suspicion into a finding. Recorded because a non-discriminating test is
   worse than no test — it produces a pass that means nothing.

## Compositor lanes

### Base images

Compositors are not all installable from one distribution, so the lab has one
Dockerfile per base and the entry script picks between them:

| Dockerfile | Base | Compositors | Why |
| --- | --- | --- | --- |
| `Dockerfile` | pinned Debian trixie + snapshot | sway, labwc, cage, cinnamon, kwin, gnome | Debian packages; older Wayfire target retained for device-backed experiments |
| `Dockerfile.arch` | pinned Arch + archive snapshot | niri, cosmic, wayfire | Newer Wayfire enables software headless rendering |

Targets are built per compositor (`--target sway`, `--target niri`, …) rather than
as one image, because Cinnamon alone pulls ~500 packages. Build one lane at a
time.

### niri and cosmic: neither needs a DRM device

An earlier revision of this guide claimed niri has no nested mode. **That claim
was wrong**, and it was self-reinforcing: the adapter set no parent display, niri
fell through to its TTY backend, and the resulting libseat error was read as
proof that no other route existed.

- **niri selects the windowed backend by itself.** `src/niri.rs:741-755`:
  `has_display = WAYLAND_DISPLAY || WAYLAND_SOCKET || DISPLAY`; with any of them
  set it uses the winit backend, otherwise TTY. There is no `--nested` flag, and
  `--session` does the opposite — it deletes those variables precisely so the
  TTY backend *is* chosen. Nested mode constructs no seat.
- **cosmic-comp has the same route behind an environment variable.**
  `src/backend/mod.rs:20-46` reads `COSMIC_BACKEND`, accepting `x11`, `winit`
  and `kms`. Unset means auto: with a display variable set it tries X11 and
  falls back to winit; with neither set it goes straight to KMS and dies at
  `LibSeatSession::new().context("Failed to acquire session")` — exactly the
  message this lab recorded. There is no official prose documentation of the
  variable; it is code-only.

**So both lanes failed for the same reason: the adapters launched them with no
parent display.** That was a bug in the adapters, misread as a property of the
compositors, and it would have cost a privilege increase the lab does not need.

### Verified for niri

```text
sway (headless, wlroots)     <- parent, supplies the output
  └── niri (winit backend)   <- nested, a plain client of sway
        └── Docking          <- wayland-layer-shell, on niri's own socket
```

Runs with no DRM device, no seat and no extra privilege, and places correctly:
`geom=(0,0 1280x720) workarea=(0,0 1280x720) win=(0,533) size=1280x187`, with
`533 + 187 = 720`.

**Which backend runs depends on `XDG_CURRENT_DESKTOP`.** With it unset Docking
falls through to `wayland-layer-shell`; with `XDG_CURRENT_DESKTOP=niri` it takes
the native niri backend, because selection prefers it — "Niri has a richer IPC
backend than generic layer-shell" (`selection.py:204-211`). The adapter sets it,
so the lane exercises the niri backend, which is the one with no test coverage.
This cost a run: the first harness run declared `wayland-layer-shell` and failed
every case on the backend assertion, while the throwaway probe that had no
`XDG_CURRENT_DESKTOP` had reported the opposite.

Four things this lane needed, each of which cost a run to find:

1. **A parent that fills its output exactly.** A single tiled window on sway
   with `gaps 0` and no borders *is* the whole output, so niri's coordinates are
   the output's. **cage was tried first and left niri at 1272x688 inside a
   1280x720 parent** — a 32px error that would have shifted every bottom-edge
   measurement and read as a placement bug.
2. **Naming the sockets, not globbing them.** Two live sockets share one
   `XDG_RUNTIME_DIR` while this runs. The parent is found by diffing against a
   snapshot; niri is found by reading its own `listening on Wayland socket:`
   line, which is exact and cannot be raced.
3. **`cap_sys_nice` on Arch's sway.** This is general to every Arch lane here,
   not specific to niri; the failure mode is written up under Writing an
   adapter.
4. **Sway's config must parse.** `output ... bg` takes a scaling mode, and
   omitting it silently drops sway back to its **default** config — which has a
   bar, which offsets the nested window by 33px.

Captures come from `grim` against niri's own socket. The parent could be
captured instead, but going through niri avoids dragging the parent's frame into
every measurement. Geometry needs no pixel inference at all here: `niri msg
outputs` reports logical position and size exactly.

**Limitation: this route gives exactly one output.** The parent may have several
(`WLR_HEADLESS_OUTPUTS`), but niri is a single window on one of them, so niri
sees one output. Monitor-selection and hotplug cases therefore cannot run on
this lane as built — they need either a multi-output virtual backend niri
exposes itself, or a different wiring. Do not read `monitor-*` as passing here.

### Original COSMIC investigation (superseded above)

The mechanism is the same and the reading is the same, but `COSMIC_BACKEND=winit`
has not been run. Treat cosmic as unverified until it is. (An earlier revision of
this section said to treat both niri and cosmic as unverified; niri is now
verified end to end above, so that instruction applies to cosmic only.)

On a machine with a free DRM device:

- the kernel here has **no `vkms` module** (checked: none built for
  `5.18.0-1-amd64`), so there is no virtual DRM device to create;
- the only card node is held by the running desktop session, so passing
  `/dev/dri` through does not let either compositor acquire DRM master.

The adapters therefore attempt the start and, on failure, print the compositor's
own last output rather than a bare timeout. They work unchanged on a machine with
a free DRM device or a kernel with `vkms`. **Treat cosmic as unverified until run
on such a machine.**

### Original support table (2026-10-02; superseded above)

Adapters exist for the compositors below. An adapter existing is not evidence
that its lane works; the status distinguishes implementation from verification —
"written, unverified" is deliberate.

| Compositor | Status | Base | Headless route | Dock rect source | Pointer |
| --- | --- | --- | --- | --- | --- |
| **sway** | **verified** | debian | `WLR_BACKENDS=headless WLR_RENDERER=pixman` | derived + pixels | no |
| cinnamon | **reservation verified** | debian | `cinnamon --nested --wayland` in Xvfb | **`org.Cinnamon.Eval` → `get_frame_rect()` (exact)** | no |
| kwin | written, unverified | debian | `kwin_wayland --virtual` | derived + pixels | no |
| labwc | written, unverified | debian | `WLR_BACKENDS=headless` | derived + pixels | no |
| wayfire | written, unverified | debian | `WLR_BACKENDS=headless` | derived + pixels | no |
| cage | written, unverified | debian | `WLR_BACKENDS=headless` | derived + pixels | no |
| **niri** | **verified** — 5/6 placement; its 2 original failures were a Docking protocol violation the other lanes tolerate, now fixed (issue 6) | **arch** | nested: `sway` (headless) → `niri` (winit backend) | **dock pixels; `niri msg outputs` gives exact output geometry** | no |
| cosmic | written, **not yet run** | **arch** | `COSMIC_BACKEND=winit` inside a lab compositor | derived + pixels | no |

Sway and Cinnamon reservation cases have been run end to end. Cinnamon is the
reference lane for geometry because it is the one compositor that can be *asked*
where the dock is rather than having it measured from pixels.

### Per-lane notes

**sway.** Verified against `sway-ipc.7.scd`: `GET_TREE`'s `shell` field documents
only `xdg_shell` and `xwayland`, there is no layer-surface node type, and
`GET_OUTPUTS` returns output bounds only. A layer-shell surface simply is not
addressable through sway's IPC. So on sway the dock's position is measured from
pixels, and the expected rect is derived from the output geometry plus the
configured edge. Its five non-panel placement cases pass (5/5) and have been
reproduced twice with bit-identical captures; its matrix total is 7/9, and the
two FAILs are issues 0 and 1.

**cinnamon.** The column used to read UNSUP across the board, with the
reproduction recorded as `requested win=(0,557)` / `actual y=0` /
`backend reduced`. **That is no longer what happens.** `cinnamon-wayland` needs
layer-shell *and* Muffin's debug API; older Muffin versions use `cinnamon-shell`,
the built-in shell bridge added by PR #349. With #349 merged, Muffin's lack of
layer-shell is handled by the shell-API positioning fallback, the adapter now
probes `expected_backend: cinnamon-shell`, and the lane places correctly on all
four edges and with the gap:

```text
requested: win=(0,517) size=1280x163      (above Cinnamon's own 40px panel)
actual:    y=517                          (Muffin's own get_frame_rect)
```

Five placement cases pass with committed baselines; `placement-bottom-panel`
is UNSUP because the Cinnamon adapter has no panel capability, not because of
the product.

Reservation is covered by six cases covering all four edges, a 40-pixel bottom
gap, and forced termination:

```bash
bash tools/visual_compositor_matrix.sh --compositor cinnamon --behavior reservation --update-baselines
bash tools/visual_compositor_matrix.sh --compositor cinnamon --behavior reservation
```

All six matched their baselines exactly on an independent rerun on Debian 13's
Cinnamon 6.4.10 and Muffin 6.4.1. Each case captures Docking alone, starts a real
GTK Wayland window, and asks GTK to maximize it. Muffin reports the actual
maximized frame and workarea. The oracle compares that frame against the dock's
visible pixels, then verifies that both the workarea and the still-maximized
window regain their original bounds after Docking exits. The crash case uses
SIGKILL to verify shell-owned cleanup without Docking's shutdown handlers.
Evidence includes `<case>.maximized.png`, `<case>.released.png`, and the
`reservation` section of `<case>.json`. Dock localization excludes Cinnamon's
own panels by using the external workarea captured before Docking starts.
This reproduced the #347 follow-up as a 52-pixel overlap before the fix.

*Version note.* The guide records verification on Muffin 6.4.1, while the matrix
commentary attributes the layer-shell gap to Muffin 6.6; both version facts are
kept as written, and the mapping has not been reconciled.

Two things follow, and the second is the one to keep an eye on.

- **The harness has the acceptance criterion it promised.** One command per
  lane now says whether placement regressed. That is the regression check for a
  bug that previously needed a reporter and a multi-day investigation.
- **The same lane is now the one that is flaky** (issue 7). The #347 fix reads
  back the compositor's real rect, which is the right shape; but the retry loop
  around it re-issues a move computed against the pre-reservation workarea, and
  in 2 of 10 runs the compositor's answer wins over the request. A lane that is
  the acceptance test for a placement fix should not itself be nondeterministic,
  so this is worth resolving before the lane is trusted as a gate.
- **The generic version of the #347 lesson is still unfixed.**
  `WaylandLayerShellSurfaceService` (`services.py:110-114`) and the GNOME bridge
  (`bridge.py:778-787`) still report *requested* positions as known ones — see
  issues 2 and 3.

**labwc.** `monitor-*` is UNSUP *(harness)* — not a compositor limitation. labwc
is a wlroots compositor with the same multi-output capability as sway; its
adapter simply never declares `multi_output` because only `sway.sh` implements
`LAB_OUTPUTS>1`. **Fix:** port the sway output-positioning block into `labwc.sh`
(it is the same `WLR_HEADLESS_OUTPUTS` mechanism) and declare the capability.
Cheap, and it would double the lanes that can test monitor selection.

`placement-bottom-panel` was UNSUP *(harness)* because only `sway.sh` provided a
panel. labwc now runs the synthetic layer-shell panel (`probes/panel_probe.py`)
and declares the capability, and **the same failure reproduces there, byte for
byte**: content overshoots the edge by 39px, against a different panel
implementation. That is what makes the panel finding a statement about Docking
rather than about swaybar, and it is why the synthetic probe was worth writing.
Its five non-panel placement cases pass; the sixth matrix case is the panel FAIL.

**cage.** All cases UNSUP — *working as designed, but the fix discussion is worth
having*. The probe confirms cage advertises no layer-shell, matching
`selection.py:262-267`. Docking runs on the documented `reduced` tier.
**Fix discussion.** This is honest degradation, and the earlier decision on this
project was explicit that reduced is a known degraded answer — so arguably no
fix. But one question is worth settling deliberately: **should Docking place
itself at all on a kiosk compositor?** Today `reduced` calls `window.move()`,
which cage ignores, so the dock lands wherever cage puts it. An alternative is
for the reduced tier to *decline* to position and render as a plain window,
which at least makes the outcome predictable. Worth a decision, not a bug.

**Lanes that were called blocked.**

| Lane | Blocker (from the compositor's own output) | Outcome |
| --- | --- | --- |
| ~~**niri**~~ | `panicked: error initializing the TTY backend / Failed to open session: Function not implemented (os error 38)` | **UNBLOCKED — it was never blocked.** The seat reading was wrong: `src/niri.rs:741-755` selects the windowed winit backend as soon as `WAYLAND_DISPLAY`, `WAYLAND_SOCKET` or `DISPLAY` is set. The adapter set none. It now runs nested in a headless sway, with Docking placing correctly — see "niri and cosmic: neither needs a DRM device" and "Verified for niri" above |
| **cosmic** | `Error occured in main(): Failed to acquire session` | **Same cause, not yet run.** `COSMIC_BACKEND=winit` should avoid libseat entirely; inferred from the source (`src/backend/mod.rs:20-46`), not yet executed — see "Not yet verified for cosmic" above |
| **wayfire** | `Failed to get DRM file descriptor, try specifying a valid WLR_RENDER_DRM_DEVICE!` — the 0.9.0 headless backend wants a DRM fd even with `WLR_RENDERER=pixman` | Open. Pass `/dev/dri/renderD128` **and** grant `render` group access. The first attempt passed the device but not the group, and failed identically. A render node needs no DRM master, so this would not disturb the host session. **Worth retrying the nested route first** — the same trick that rescued niri may apply, and wayfire has an X11/wayland backend this lab has not tried |
| **kwin** | Debian trixie's `kwin-wayland` ships **no backend plugin packages**, so `--virtual` is unavailable; the binary also carries `cap_sys_nice=ep` and implements no `--help` | Open. Move the lane to the Arch image, whose `kwin` package ships the backends. Verify by running, not by grepping the binary — two grep attempts gave contradictory answers. Note kwin carries `cap_sys_nice` too, so the Arch image's `setcap -r` step matters here as well |

**Fix discussion, revised.** The earlier version of this table said niri and
cosmic needed a seat, and argued against paying the privilege cost. **That was
wrong on the facts**, and the full correction is in "niri and cosmic: neither
needs a DRM device" above: the adapters gave them no parent display, and the fix
is to do so and wait for the *new* socket. The generalisable lesson, since it
applies to the two lanes still open: a compositor that fails to start in this lab
is reported through its *last log line before dying*, and that line names the
layer it died in — not the reason it was reached. `Failed to open session` from
libseat said nothing about seats; it said niri had chosen the TTY backend, which
it did because no display variable was set. Both readings are consistent with the
output, and the wrong one was cheaper to believe. **wayfire's blocker deserves
the same suspicion** before it is accepted: its message names a DRM fd, and the
nested route has not been tried there.

**kwin remains the more valuable of the two open lanes**: a backend with
statically-declared capabilities and no test coverage at all, which is where
untested risk concentrates.

**Pointer-driven scenarios.** `WLR_BACKENDS=headless` creates no input devices,
so a client never receives `wl_pointer`. `swaymsg seat - cursor set x y` moves
the cursor but does not deliver an enter event. Scenarios that need a pointer
(hover reveal, hover state) must therefore be reported as **explicitly
unsupported** on sway rather than silently skipped, and belong on a compositor
where a virtual-pointer device can be held open.

## The case matrix

Produced by `tools/visual_compositor_matrix.sh` on 2026-10-02. Every cell is a
real run, not an expectation.

| Case | sway | labwc | cage | cinnamon | wayfire | kwin | niri | cosmic |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `placement-bottom` | **PASS** | **PASS** | UNSUP | **PASS** | BLOCKED | BLOCKED | **PASS** | BLOCKED |
| `placement-top` | **PASS** | **PASS** | UNSUP | **PASS** | BLOCKED | BLOCKED | **PASS** | BLOCKED |
| `placement-left` | **PASS** | **PASS** | UNSUP | **PASS** | BLOCKED | BLOCKED | **PASS** | BLOCKED |
| `placement-right` | **PASS** | **PASS** | UNSUP | **PASS** | BLOCKED | BLOCKED | **PASS** | BLOCKED |
| `placement-bottom-gap` | **PASS** | **PASS** | UNSUP | **PASS** | BLOCKED | BLOCKED | **PASS** | BLOCKED |
| `placement-bottom-panel` | **FAIL** | **FAIL** | UNSUP | UNSUP *(no panel)* | BLOCKED | BLOCKED | UNSUP *(no panel)* | BLOCKED |
| `monitor-primary` | **PASS** | UNSUP *(harness)* | UNSUP | UNSUP *(no IPC)* | BLOCKED | BLOCKED | UNSUP *(one output)* | BLOCKED |
| `monitor-second-by-index` | **PASS** | UNSUP *(harness)* | UNSUP | UNSUP *(no IPC)* | BLOCKED | BLOCKED | UNSUP *(one output)* | BLOCKED |
| `monitor-by-connector-nonprimary` | **FAIL** | UNSUP *(harness)* | UNSUP | UNSUP *(no IPC)* | BLOCKED | BLOCKED | UNSUP *(one output)* | BLOCKED |
| `reservation-bottom` | UNSUP *(harness)* | UNSUP *(harness)* | UNSUP | **FLAKY** | BLOCKED | BLOCKED | UNSUP *(harness)* | BLOCKED |
| `reservation-top` | UNSUP *(harness)* | UNSUP *(harness)* | UNSUP | **FLAKY** | BLOCKED | BLOCKED | UNSUP *(harness)* | BLOCKED |
| `reservation-left` | UNSUP *(harness)* | UNSUP *(harness)* | UNSUP | **PASS** | BLOCKED | BLOCKED | UNSUP *(harness)* | BLOCKED |
| `reservation-right` | UNSUP *(harness)* | UNSUP *(harness)* | UNSUP | **PASS** | BLOCKED | BLOCKED | UNSUP *(harness)* | BLOCKED |
| `reservation-bottom-gap` | UNSUP *(harness)* | UNSUP *(harness)* | UNSUP | **PASS** | BLOCKED | BLOCKED | UNSUP *(harness)* | BLOCKED |
| `reservation-bottom-crash` | UNSUP *(harness)* | UNSUP *(harness)* | UNSUP | **PASS** | BLOCKED | BLOCKED | UNSUP *(harness)* | BLOCKED |

**The cage, wayfire and kwin images were removed** to reclaim disk during the
Docker-root migration and need a rebuild before those lanes can run again. Their
columns are unchanged from the last run against them.

`FLAKY` is a fourth state and it is not a hedge: the same binary, the same
configuration and a byte-identical placement request produce a pass in one run
and a failure in another. See issue 7. A lane that reports both cannot be a CI
gate until it is resolved.

Totals on the four lanes that execute cases: **sway 7/9, labwc 5/6, cinnamon 5/6
placement and 4/6 reservation, niri 5/6.** Counted as failures, that is four
distinct product defects plus one flake.

**niri's two FAILs were the most interesting thing in this table, and are now
fixed.** They were never niri bugs — niri is the only compositor in the lab that
*enforces* the layer-shell rule the other three quietly tolerate. See issue 6: a
left/right dock committed its surface with width 0 on an axis it had anchored
only once, a protocol error the spec calls out explicitly. sway, labwc and
cinnamon accepted it; niri killed the client. Adding a stricter lane is what made
a latent four-compositor defect visible, and the fix was removing a placeholder
anchor set that taught gtk-layer-shell to zero the wrong axis.

Read the table with this in mind: **a FAIL is a claim about Docking; an UNSUP is
a claim about the lane.** The five FAIL cells resolve to three issues: both
`placement-bottom-panel` cells (sway and labwc) are issue 0, the sway
`monitor-by-connector-nonprimary` cell is issue 1, and niri's two vertical-edge
cells were issue 6. All are real. Most UNSUPs are either documented degradation or
the harness's own missing support — only two of the four UNSUP groups say
anything about the product.

### Docked config in every case

Docking's first-run pinned set is applets — clock, calendar, weather, systemmonitor
(`docking/core/config.py:249-257`, `build_initial_pinned` at `:354`). Those render live,
time-varying data, so a dock left at defaults **never settles** and no pixel baseline can
converge. Every case therefore pins a fixed set of synthetic launchers
(`STATIC_PINNED` in `scenarios.py`), and `run_session.sh` writes the matching `.desktop` files
into `XDG_DATA_HOME` before the dock starts.

If you add a case, keep this invariant: anything that renders time-varying data (clock,
weather, system monitor, live window counts) must be excluded, or the settle loop will fail.

### Original coverage gaps (2026-10-02; superseded above)

Fifteen cases across four behaviours. The harness implements **placement,
monitor selection and reservation**, plus frame containment checks where an adapter
provides the actual native dock frame. Pixel-only lanes (including Sway and niri)
cannot prove that the surface allocation fits on screen. Not
implemented, on any compositor:

- **visibility** — autohide, dodge, overlap, reveal. Blocked on input, not on
  protocol: see below.
- **rendering** — indicator styles, badges, progress, applets
- **interaction** — hover, menus, tooltips, previews, drag-and-drop
- **display changes** — hotplug, resolution, rotation, scale, primary-output
  changes

So "15 cases × 8 compositors" still overstates the coverage. What exists is a
placement-plus-reservation matrix that happens to be deep enough to have found
three real defects; the behaviours that produced `#335` and `#337` are still
untested.

Specific gaps worth naming:

- **Multi-monitor and scaling are not tested.** `Case.output_index` selects
  which output a case targets and is wired through comparison, but no case uses
  a non-zero index yet, and a run with a scaled output is reported as
  `unsupported` rather than measured with wrong assumptions. Monitor-layout and
  scale cases are Phase 2.
- **Cinnamon's shell reservation path is verified**, including maximized-window
  overlap and reservation release. This does not establish coverage of newer
  Muffin's separate layer-shell path.
- **Reservation verification uses a single output and no pointer injection**,
  and **pointer-driven behavior is unavailable on sway**, so no hover or
  autohide-reveal scenario is claimed.
- **niri's lane gives exactly one output**, so monitor-selection and hotplug
  cases cannot run there as built (see "Verified for niri").

**On the visibility blocker specifically.** The standing assumption has been
that pointer-driven cases need `zwlr_virtual_pointer_v1` vendored into
`docking/platform/backends/wayland/protocols/`. The niri work suggests a
cheaper route first: **a nested compositor receives input from its parent**, so
driving the parent drives the child. Every compositor that can nest — niri,
cosmic, and Cinnamon, which already runs inside Xvfb — could take injected
input without any protocol work at all. Unverified, but it is the same shape of
assumption that turned out to be wrong about niri's nested mode, so it is worth
an experiment before a vendoring project.

## Findings

Compiled from the visual compositor harness (`tools/visual_compositor_matrix.sh`),
static analysis of the backend code, and the `#347` investigation. Each issue
states how it was found, the evidence, the root cause with file:line, the user
impact, and the fix I would make.

Evidence is marked **[reproduced]** when the harness demonstrated it on a real
compositor, and **[static]** when it comes from reading the code — static
findings have not been executed and should be confirmed with a harness case
before being treated as settled.

### 0. Docking cannot see a panel on Wayland, and its exclusive zone pushes the panel off the screen edge

**[reproduced]** on sway, 2026-10-02 — with a screenshot.

Rechecked after disabling the duplicate synthetic panel on Sway: with only
the 40px swaybar, `placement-bottom-panel` still fails, overshooting the expected
edge by 39px. The older capture below predates that harness correction.

**Symptom.** With a 40px swaybar at the bottom, launching Docking places the
**dock at the very bottom of the screen and pushes the panel up above it**. A
user with a bottom panel gets their panel displaced the moment the dock starts.

**Evidence.** The capture (`placement-bottom-panel`) shows, from the bottom of a
720px output upward:

```text
y 672..712   the dock           (light shelf, three icons)
y 632..664   the swaybar panel  (dark bar, workspace "1", status text)
y < 632      empty desktop
```

A bottom panel of height 40 should occupy `y=680..720`. It occupies
`y≈632..668` — displaced upward by ~52px, which is the strip the dock's
exclusive zone took.

Docking's own placement line for the same run:

```text
monitor=0 geom=(0,0 1280x720) workarea=(0,0 1280x720) external=None win=(0,557) size=1280x163
```

`workarea` is the **full 1280x720**. The panel is not in it. `external=None`
says no external workarea was obtained, so the fallback path ran.

**Root cause.** `WaylandLayerShellSurfaceService` does not override
`external_workarea`, so `SurfaceService.external_workarea` returns `None`
(`base.py:385-392` — only X11 and the Cinnamon shell service override it).
`position_dock` then falls back to GDK's `monitor.get_workarea()`.

On Wayland, GDK **cannot** know about another client's exclusive zone; there is
no protocol for it. `get_workarea()` therefore equals `get_geometry()` — the
panel is invisible to it. Verified directly: `geom` and `workarea` are
identical in the log line above.

So the dock sizes itself against the whole monitor, anchors to the bottom, and
its own exclusive zone reserves the bottom strip. swaybar's zone then stacks
above it. Neither surface is wrong in isolation; the dock simply never knew the
panel was there.

This is the scenario `docs/CONFIGURATION.md` and the compatibility table
describe as "dock respects external workarea". On Wayland it does not, and
cannot with the current data source.

**Impact.** Any user running a panel on the same edge as the dock sees the panel
jump. It is most visible on a fresh install, and it looks like the panel is
broken rather than the dock. The dock also overstates the space available to it,
so on a smaller output the shelf can be sized larger than the usable area.

Note this is *distinct from* the degraded-tier story: the dock here is on the
`wayland-layer-shell` backend, the one advertised as fully working.

**Best fix.** There is no Wayland protocol for reading another client's
exclusive zone, so the dock cannot discover the panel by asking. The workable
options, in order:

1. **Do not take an exclusive zone when the dock does not need one.** The dock's
   reservation is `set_reservation`/`exclusive_zone` in
   `wayland/services.py:183-191, 277-285`. If the dock is configured with
   `hide_mode=none` it genuinely needs the space; if it autohides or is
   `always-on-top` it does not, and reserving is what displaces the panel.
   Making the reservation conditional on the hide mode removes the displacement
   for exactly the configurations where it is gratuitous.
2. **Let the user state the reservation explicitly.** A configured
   "reserved thickness" the dock uses instead of measuring would let a user with
   a panel at the same edge say so once. This matches what
   `additional_distance_from_edge` already does for the cross axis.
3. **Derive the usable area from the compositor where the backend can.** Backends
   that already talk to their compositor (niri, wayfire, hyprland, cosmic) can
   be asked for output workareas through their IPC, and several already return
   them. Feeding that into `external_workarea` fixes the backends that can
   answer and leaves the honest `None` elsewhere.

Option 1 is the smallest correct change and addresses the visible symptom;
option 3 is the one that makes the feature real. Do not ship option 3 for one
backend only — the compatibility table would then be accurate for some
compositors and wrong for others, which is worse than uniformly wrong.
Whichever is chosen, a harness case now exists to prove it:
`placement-bottom-panel` on a swaybar of known height, which fails today with
`content overshoots the edge by 39px`.
The same failure reproduces on labwc against a different panel implementation
(see per-lane notes).

**Caveat, stated plainly.** Layer-shell exclusive zones legitimately stack, so
"the panel is displaced" is compositor-determined and not a protocol violation.
What is unambiguously wrong is that Docking believes the whole monitor is usable
— `geom == workarea` in its own log — which also lets it oversize itself. The
fix is about Docking knowing the truth, not about changing stacking order.

### 1. `monitor_connector` never works — the documented stable monitor identity is dead code

**[reproduced]** on sway, two outputs, 2026-10-02.

**Symptom.** `docs/CONFIGURATION.md:85` documents `monitor_connector` as the setting that
"survives display reordering", preferred over the numeric index. Setting it has
no effect: the dock silently lands on the primary monitor instead.

**Evidence.** Ran the harness on a two-output sway session laid out as:

```text
HEADLESS-2   x=1280   (GDK primary, GDK index 0)
HEADLESS-1   x=0      (GDK index 1)
```

| Case | Config | Requested x | Correct? |
| --- | --- | --- | --- |
| `monitor-primary` | `monitor_index=-1` | 1280 | yes (primary) |
| `monitor-second-by-index` | `monitor_index=1` | 0 | yes (index path works) |
| `monitor-by-connector-nonprimary` | `monitor_connector="HEADLESS-1"`, `monitor_index=-1` | **1280** | **no — should be 0** |

The connector case requested x=1280, which is the *primary*, not the output it
named. The case is deliberately pointed at the **non-primary** output: naming
the primary would be non-discriminating, because "connector honoured" and "fell
back to primary" produce the same placement.

**Root cause.** `docking/ui/placement.py:686-691`:

```python
@staticmethod
def _monitor_connector(monitor: Gdk.Monitor) -> str | None:
    getter = getattr(monitor, "get_connector", None)
    if not callable(getter):
        return None
    ...
```

`Gdk.Monitor` has **no `get_connector` method** in GTK 3. Verified by
introspection — the complete set of getters is `get_geometry`,
`get_height_mm`, `get_manufacturer`, `get_model`, `get_scale_factor`,
`get_subpixel_layout`, `get_width_mm`, `get_workarea`.

So `_monitor_connector` always returns `None`, therefore
`_monitor_for_connector` (`placement.py:668-683`) can never match, and
`_resolve_target_monitor` falls through to `monitor_index`. The lookup is not
merely unreliable — it cannot succeed on any backend, because the method name
does not exist anywhere in GTK 3.

**Impact.** Silent wrong-monitor placement. The user sets a stable identity
precisely to survive reordering, gets no error, and the dock appears on a
different monitor than configured. It also leaves the codebase without any
stable monitor identity, so `monitor_index` — a GDK enumeration order — is the
only mechanism, which is the exact fragility the setting was added to remove.

**Best fix.**

**X11: use the method that exists.** `GdkX11.Monitor.get_output()` returns the
XRandR output name (e.g. `HDMI-1`), which is a genuine stable connector.
Verified present in `GdkX11-3.0.typelib` (alongside
`GdkX11.Screen.get_monitor_output`). It must be reached through the X11
subclass, so the call belongs behind a display-server check, not on the generic
`Gdk.Monitor`:

```python
try:
    gi.require_version("GdkX11", "3.0")
    from gi.repository import GdkX11
    if isinstance(monitor, GdkX11.Monitor):
        return str(monitor.get_output() or "").strip() or None
except (ValueError, ImportError):
    pass
```

**This half is a clear win** and makes the feature work where monitors are
most often reordered.

**Wayland: GTK 3 exposes no connector at all.** There is no equivalent of
`get_output` on `GdkWayland`; `get_model()` returns an EDID model string that is
not unique across identical monitors. So the setting cannot be honoured from
GDK on Wayland. Two acceptable resolutions, in preference order:

1. **Resolve it against the backend's own monitor names.** Docking's Wayland
   backends already carry a `MonitorSnapshot.name` populated from the
   compositor (niri/hyprland/cosmic expose real output names, and sway can
   supply output names from `swaymsg -t get_outputs`). Matching
   `monitor_connector` against that would make the setting work on Wayland for
   the backends that can supply a name, which is most of them.
2. **Fail loudly where it cannot work.** If a connector is configured and no
   monitor on the current session exposes one, log a warning once naming the
   setting — the current silent fallback is what makes this expensive to
   diagnose. `docs/CONFIGURATION.md:85` should also state which sessions expose
   connectors.

A regression test belongs at the unit level (a fake monitor with no
`get_connector` must not crash, and a connector match must win over the index)
**plus** the harness case above, which is the only thing that proves the
end-to-end path.

Note the interaction with the panel finding (issue 0): both need
per-compositor output identity, so the same plumbing serves both. Doing them
together is likely cheaper than doing them twice.

### 2. Layer-shell reports the *requested* position as the known position

**[static]** — high confidence, not yet exercised by a harness case.

**Root cause.** `docking/platform/backends/wayland/services.py:110-114`:

```python
def get_surface_position(self) -> tuple[int, int] | None:
    """Return the last compositor placement requested for the dock."""
    if self._surface_x is not None and self._surface_y is not None:
        return self._surface_x, self._surface_y
    return None
```

The values are assigned from the *request* in `position_or_anchor`. The
docstring is accurate about what it returns; the problem is the contract it
satisfies.

`SurfaceService.get_surface_position` (`base.py:372-380`) is documented as
"the dock surface's **screen (root) position, if known**", and
`window_screen_position` (`ui/display.py:78-90`) documents it as the
"backend-owned" position to be **preferred over GTK** and used for popup
anchoring, tooltips, menus, DnD and the dodge rectangle.

So a *requested* value is consumed as a *measured* one.

**Why it matters even though layer-shell usually obeys.** For layer-shell the
anchored position normally does follow the request, so the bug is latent. It
becomes live when the compositor does not do what was asked — which is a real,
observed case:

- `_set_monitor_for_window` is best-effort. If `display.get_monitor(index)`
  returns `None`, or the GtkLayerShell build lacks `set_monitor`, no output is
  set, and per the protocol the compositor places the surface on the **focused
  output**. The dock then sits on one monitor while every reported coordinate
  describes another.
- The cross-axis extent can exceed the requested size, because
  `set_size_request(-1, h)` is a *minimum* and the code deliberately leaves
  zoom/bounce headroom. The requested `y` is therefore not the achieved `y`.

**Impact.** Popup menus, tooltips, the dodge rectangle and DnD indicators are
anchored from a coordinate that may describe a monitor the dock is not on.
Symptom is misplaced popups and wrong autohide/dodge behaviour, with nothing in
the logs.

**Best fix.** Read back what actually happened rather than caching the request —
the pattern `cinnamon/shell.py` already uses, where `_position` is written
**only** when the compositor returns a rect:

```python
position = self._client.position_dock(...)
if position is not None:
    self._position = position
```

For layer-shell the equivalent is to derive the achieved rect from the
compositor's configured size and the anchor/margins actually applied, and to
return `None` when that cannot be established — `None` is already the honest
"unknown" that makes callers fall back, and is strictly better than a plausible
wrong number. At minimum, `_surface_x/_surface_y` should be cleared rather than
left stale when `set_monitor` fails.

Note `self._monitor` is written in `position_or_anchor` and **never read** —
the natural place to re-validate was left unused; that is worth removing or
using.

**Harness coverage.** Issue 2 now has harness coverage: a case compares
Docking's `GetHoverAnchor` reply against the pixels. It **passes** on the
layer-shell path, which demotes the finding from a live defect to a latent one.
Issues 3 and 5 remain static.

### 3. GNOME bridge caches a position the compositor refused

**[static]** — high confidence.

**Root cause.** `docking/platform/backends/gnome/bridge.py:778-787`:

```python
def _apply_position_request(self, request: PlacementRequest) -> bool:
    # Track the compositor-assigned position so get_surface_position()
    # can report it.  GTK's get_position() returns (0,0) on Wayland.
    self._surface_x = request.x
    self._surface_y = request.y
    return bool(
        self._bridge.position_dock(
            request.x, request.y, request.size.width, request.size.height
        )
    )
```

The cache is written **unconditionally**, and the return value — which is
`False` when the extension cannot find the dock window (`bridge.py:125-131`) —
is discarded by the caller. The comment is also incorrect: nothing
"compositor-assigned" is being tracked, only the request.

The retry loop (`_DOCK_POSITION_RETRY_*`, 15 attempts at 100 ms) re-sends the
same request but never reads back `Meta.Window.get_frame_rect()`, so once the
1.5 s window closes, the reported position can remain wrong for the rest of the
session.

This is the same failure mode as `#347`, on a different compositor.

**Impact.** On GNOME Wayland, if placement fails, every popup and the dodge
rectangle anchor to a position the dock never reached. Because the retry loop
gives up silently (no `log.warning`, unlike the Cinnamon fix which logs
`"Cinnamon could not position the dock window"`), the user gets no signal.

**Best fix.** Gate the cache on success and read back the real rect, mirroring
`cinnamon/shell.py`:

```python
if not self._bridge.position_dock(...):
    return False
self._surface_x, self._surface_y = request.x, request.y
return True
```

and add a read-back of `Meta.Window.get_frame_rect()` through the bridge so the
cached value is the compositor's answer, not the request. Add the same
give-up warning the Cinnamon path has.

### 4. `MonitorSnapshot.scale` is an `int`, so fractional scaling cannot be represented

**[static]**.

**Root cause.** `docking/platform/backends/base.py:94`: `scale: int = 1`.

Wayland fractional scaling (1.25, 1.5, 1.75) is common and is not an integer
ratio. The field cannot express it, so any consumer sees a truncated value.

Consumers that then do arithmetic on it are additionally wrong:

- `wayland/treeland.py:40-48` computes logical size as `width // scale`, which
  floors.
- `ui/placement.py:698-729` sets `scale=self._window.get_scale_factor()` — the
  **window's** scale, not the target monitor's. Those disagree during and after a
  cross-monitor move, and the value is baked into the reservation cache key
  (`placement.py:781-793`) and X11 barrier geometry.

**Impact.** On a fractional-scale mixed-DPI setup: reservation thickness, barrier
geometry and logically-sized regions are computed from a truncated or wrong
factor. Visual symptoms are mis-sized reservations and off-by-a-fraction popup
offsets — hard to attribute without a harness that can set scale per output.

**Best fix.** Change the field to `float` and stop flooring:

```python
scale: float = 1.0
```

Replace `width // scale` with a rounding division (`round(width / scale)`) so
odd logical sizes round consistently rather than always down, and use
`monitor.get_scale_factor()` rather than the window's for monitor-derived
values in `_monitor_snapshot`.

This is a cross-cutting type change and should land with a harness case at
scale 1 and 2 (at minimum) so the before/after is observable. The harness
currently reports a non-1 output scale as `unsupported` rather than measuring
it wrongly, which is the right state until this lands.

### 5. KWin declares workspace support that its service only soft-probes

**[static]**.

**Root cause.** `docking/platform/backends/kwin/session.py:288-289` hard-codes:

```python
supports_workspace_list=True,
supports_workspace_switch=True,
```

while `KWinWorkspaceService.start()` (`kwin/session.py:99-115`) wraps its D-Bus
connection in `except Exception: log.exception(...)` — it swallows every failure
and leaves `self._proxy = None`. `AtspiWindowService` has the same shape.

The capability is therefore a static claim about a runtime condition. If
`org.kde.KWin.VirtualDesktopManager` is unreachable, the dock still advertises
workspace support with no proxy behind it.

**Impact.** The UI offers workspace features that silently do nothing, and the
degradation is dishonest — the same failure mode as `#347`, where the app
believed a capability it could not deliver.

**Best fix.** Gate the capabilities on the probe, the way
`treeland_session.py:48-73` already does with `replace()` over a base capability
set — only set `supports_workspace_list/switch` when the service actually
connected:

```python
@property
def capabilities(self) -> PlatformCapabilities:
    base = PlatformCapabilities(...)          # static, safe defaults
    if self._workspaces.available:            # set by start()
        base = replace(base, supports_workspace_list=True,
                              supports_workspace_switch=True)
    return base
```

with `available` set in `start()` on success and cleared on failure. The same
treatment applies to the AT-SPI window service.

### 6. A left/right dock commits a layer surface with width 0 — a protocol error

**[reproduced]** on niri, 2026-10-02. Docking dies; sway and labwc tolerate the
identical request.

**Symptom.** With `position` set to `left` or `right`, Docking exits during
startup. `bottom` and `top` work on the same compositor in the same session. The
user gets no dock at all on either vertical edge.

**Evidence.** Docking never reaches its D-Bus service, and dies about a second
after starting:

```text
alive=no  dbus=no
** (Docking:166): WARNING **: 16:36:46.883: Timed out waiting for initial .configure
Gdk-Message: 16:36:46.889: Error 71 (Protocol error) dispatching to Wayland display.
```

niri's own log, with every protocol message recorded, names both the offending
request and the rule (`RUST_LOG=niri=debug`, `WAYLAND_DEBUG=1`):

```text
-> zwlr_layer_surface_v1#37.set_anchor(7)      # TOP|BOTTOM|LEFT — a left dock
-> zwlr_layer_surface_v1#37.set_size(0, 200)   # width 0, only LEFT anchored
wl_display#1.error(zwlr_layer_surface_v1#37, 1,
    "width 0 requested without setting left and right anchors")
```

The same trace for `bottom`, which works:

```text
-> zwlr_layer_surface_v1#37.set_anchor(14)     # BOTTOM|LEFT|RIGHT
-> zwlr_layer_surface_v1#37.set_size(0, 200)   # width 0 — legal, both ends anchored
-> zwlr_layer_surface_v1#37.set_size(0, 187)   # the real cross size, one frame later
```

The first committed size is `(0, 200)` in **both** orientations. It is legal for a
horizontal dock only because `BOTTOM|LEFT|RIGHT` anchors both horizontal ends.

**Root cause.** The layer-shell spec for `set_size` is explicit:

> If you pass 0 for either value, the compositor will assign it and inform you of
> the assignment in the configure event. **You must set your anchor to opposite
> edges in the dimensions you omit; not doing so is a protocol error.** Both
> values are 0 by default.

A left or right dock anchors one horizontal edge and both vertical ones, so a
width of 0 is unassignable and the compositor is right to reject it.
`WaylandLayerShellSurfaceService.position_or_anchor`
(`docking/platform/backends/wayland/services.py:155-181`) sets the cross-axis
minimum with `set_size_request` and then calls `resize(1, 1)`; the surface is
committed before the window has a nonzero width. The vertical orientation never
gets a valid width onto the wire, and because the *first* commit already failed,
the correction a horizontal dock sends later (`set_size(0, 187)`) never happens.

**Impact.**

- **On niri: total failure.** No dock on either vertical edge.
- **On sway and labwc: works, because they do not enforce the rule.** This is a
  latent protocol violation on every wlroots lane, not a niri quirk — niri is
  simply the first compositor in the lab that reports it truthfully. Any future
  wlroots release that tightens validation, or any strictly-validating
  compositor, behaves like niri.
- The failure mode is the bad kind: silent on the compositors most users run,
  fatal on the one that checks.

**Fix — applied** in [#353](https://github.com/edumucelli/docking/pull/353),
which is separate from this tooling. `configure_before_realize` used to install a
placeholder `Position.BOTTOM` anchor set before placement had run. gtk-layer-shell decides
which axis of the committed size to leave to the compositor — sending 0 for it —
from the anchors in effect when it first configures the surface, so the bottom
placeholder taught it to zero the **width**. Placement then re-anchored to
left/right, where a 0 width is illegal, and the first commit went out with the
new anchors and the old, bottom-shaped size.

Removing that placeholder call is the whole fix. The surface is not mapped until
placement has run, so the real anchors are always in place before the first
commit, and gtk-layer-shell derives the size from those.

```
before:  set_anchor(7)  +  set_size(0, 200)   -> protocol error, client killed
after:   set_anchor(7)  +  set_size(187, 0)   -> accepted
```

Verified on niri: `placement-left` and `placement-right` now pass (geometry,
self-report, and pixels), with no change to `bottom`, `top` or the gap case,
which still pass at SSIM 1.00000.

**What was tried first, and did not work.** Both of these were rejected by the
harness, and are recorded so nobody repeats them:

1. Passing the cross-axis extent to `resize()` instead of `resize(1, 1)`. The
   wire trace was unchanged — that call does not feed the size gtk-layer-shell
   sends.
2. A non-zero minimum on both axes in `configure_before_realize`. Left/right
   still died, and it perturbed `placement-top` (SSIM 1.0 → 0.963 with geometry
   still correct) — a rendering change with no justification, so it was reverted.

**Remaining work on this class.** The fix removes the trigger but adds no guard:
nothing asserts that a surface never sends 0 on an axis it has not anchored at
both ends, so the next orientation-dependent size bug would also have to be
caught by a compositor rather than by a test.

**Keep a lane that enforces it.** The vertical-edge cases on niri are the only
ones that exercise this path at all — a lane that tolerates a violation hides it,
which is precisely how this survived.

**Interaction with issue 2:** both live in `position_or_anchor` and both concern
the surface's relationship to what the compositor actually grants. A fix for
either should be checked against the other.

Issue 6 is the best argument for widening the lane set: it had been silently
present on every wlroots compositor the project supports, and only became visible
when a compositor that *enforces* the layer-shell spec was added to the matrix.

### 7. The Cinnamon reservation race

The `reservation-*` cases on Cinnamon are `FLAKY` in the matrix — 8 of 10 runs
pass, 2 of 10 fail, from an unchanged working tree.

**What the case does.** Start the dock, then map a real maximized Wayland window
and ask Muffin for its frame *and* for the workarea. The maximized frame is an
independent oracle for what the compositor believes is usable: if the dock
reserved the right amount of space, the window stops just short of the dock's
visible shelf. Then the dock is SIGKILLed and the workarea is read again.

**When it works — 8 of 10 runs:**

```text
workarea   680  ->  627  ->  680      (dock running, then after SIGKILL)
dock       visible content at y 629..679
window     y 0..627, stopping 1-2px above the dock
```

Three things are confirmed by that: the reservation is **53px**, which is the
resting shelf and not the 163px window — matching the `animation_headroom=109.7`
that Docking's own placement line reports (`163 − 109.7 ≈ 53`); the maximized
window gets exactly the space that is left; and **the reservation survives a
crash correctly**, because the strut actor is tied to the window's `unmanaged`
signal and the workarea returns to 680 after `SIGKILL`.

**When it fails — 2 of 10 runs:** the dock lands displaced **inward by exactly
its own reservation** — 53px on the bottom edge, 53px down from the top, 93px
with the 40px gap, and on all four edges at once in one run. Its visible shelf
then sits outside its own reserved strip and is drawn over the maximized window.
Measured overlap: 52px on the bottom edge, 134px on the top.

**This is a race in the product, not a code difference between runs.** The two
outcomes come from an unchanged working tree, and the Docking logs are identical
modulo timestamps and PID — including the placement line, byte for byte:

```text
win=(0,517) size=1280x163 cross=163 animation_headroom=109.7
```

Same request, same log, two different compositor answers. An earlier comparison
was confounded because the source was edited between runs; the three repeats
that produced pass / fail / pass were run with no edits at all, which settles it.

**Mechanism: what is established and what is not.** Established: the displacement
always equals `thickness + edge_offset` on the axis the reservation applies to,
which is the signature of the dock's own strut being counted twice. Two things in
the code make that possible and neither is proven to be the trigger:
`_apply_position()` calls `position_dock` and only then `reserve_dock`, and
`_retry_position` re-issues the same move every 100ms for up to 2s — so a move
computed against the pre-reservation workarea is replayed after the strut has
reduced it. The code intends the shell move to be exempt from that constraint
(`w.move_resize_frame(true, …)`, with the comment "a shell/user move may occupy
the reserved strip"); the evidence says it is not always exempt. The 8-of-10
ratio is not explained by the ordering alone, so treat the ordering as the
leading hypothesis, not the conclusion.

**Fix discussion.**

1. **Stop re-issuing an accepted move.** `_retry_position` retries unconditionally
   even once `get_frame_rect()` has returned the requested rect, so any later,
   constrained replay silently becomes the final answer. Stopping the loop on
   agreement removes the window in which that can happen. Small and strictly
   safer, but it does not fix a first move that is already constrained.
2. **Compensate using the read-back the code already performs.** `position_dock`
   returns Muffin's `get_frame_rect()`; if it differs from the request by about
   the reservation extent, re-issue the move with the strut temporarily cleared —
   the same trick `workarea(exclude_title=…)` already uses on the measurement
   side, and whose comment names this exact failure ("every relayout includes our
   own strut and walks inward"). This is the deterministic fix: it makes the
   final position a function of what the compositor reports, not of whether a
   move happened to be exempt.
3. **Warn when the read-back disagrees.** Even without a fix, a dock that asked
   for `y=517`, got `y=464` and said nothing is the #347 diagnosis problem again.

**Recommendation:** 1 and 2 together. 1 alone reduces the frequency without
making the outcome deterministic, which is the property that matters.

**Harness change made alongside this finding.** `check_reservation` asserted only
that the maximized window *clears* the dock, which a dock reserving its whole
163px animation surface would also satisfy — a 110px error reported as a
comfortable pass. It is now two-sided (`RESERVATION_SLACK_PX = 12`), and both
directions were verified against deliberately wrong inputs.

**Cross-cutting note: the same shape, five times.** Issues 1, 3 and 5 are the
same defect wearing different clothes: **a claim made without checking.** Issue 1
trusts a method that does not exist, issue 3 caches a value it never verified,
issue 5 declares a capability it never probed. Issues 2 and 4 are the sibling
shape: **a value that is not what its name promises** — a request presented as a
position, an integer standing in for a ratio.

The durable remedy is the one the harness already enforces for itself: when the
answer is not known, say so, and make "not known" a representable state (`None`,
`unsupported`, a logged warning). Each of these five is a place where an unknown
was silently converted into a confident wrong answer.

## Writing an adapter

Create `tools/visual_compositor/adapters/<name>.sh` implementing the contract, and add the
package to the Dockerfile. The shared lifecycle lives in `adapters/common.sh` and is lifted
from `packaging/deb/runtime-smoke.sh`: cleanup trap with PID sentinels, socket discovery that
globs `wayland-*` with a liveness check inside the loop, two readiness gates, and
SIGTERM→10s→SIGUSR1→SIGKILL shutdown escalation.

| Function | Contract |
| --- | --- |
| `adapter_prepare` | write compositor config, export env |
| `adapter_start` | launch the compositor, set `ADAPTER_COMPOSITOR_PID` |
| `adapter_wait_ready` | second gate: the desktop, not just the socket |
| `adapter_capabilities` | JSON `{expected_backend, native_geometry, pointer, screenshot_method}` |
| `adapter_screenshot <path>` | full output → PNG |
| `adapter_geometry` | JSON output layout + dock rect (or `null`) |
| `adapter_pointer <x> <y>` | move the pointer, or fail |
| `adapter_stop` | terminate only owned PIDs |

Set `native_geometry: true` only when `adapter_geometry` provides the actual dock
frame, not just output geometry. Such lanes fail if the frame is missing, empty or
extends beyond the selected output (with an 8px tolerance). The visible dock's
edge relationship is checked separately, so animation headroom is allowed.

### Gotchas that cost a run

**A bare `x="$(cmd)"` aborts the session when `cmd` fails.** An assignment takes
the exit status of its command substitution, so a *poll* that legitimately finds
nothing yet kills the run — and, because the EXIT trap then tears down the
compositor, it presents as the compositor dying at startup. The niri adapter's
socket poll did exactly this: `niri_display="$(grep … | tail | awk)"` exits 1 on
the first poll, before niri has written its log line, and niri was `SIGTERM`ed
three milliseconds after it started listening. Append `|| true` and check the
value on the next line. `pipefail` makes this worse: any stage failing fails the
assignment.

**`cmd | python3 - <<'PY'` is a SIGPIPE.** The heredoc *is* python's stdin, so
`cmd` writes into a pipe with no reader and dies with status 141, which
`pipefail` propagates and `set -e` turns into a dead session. It presents as the
run failing on the first case with `exit 141` and no other explanation. Pass
data in as an argument, or use `python3 -c` with the program in the argument and
the data on stdin — not both on stdin.

**Arch binaries can carry `cap_sys_nice`.** Arch ships sway with
`cap_sys_nice=ep`, and executing it in a container without that capability fails
with EPERM *before `main()` runs* — which surfaces as a bare "Operation not
permitted" and looks nothing like a capability problem; the image strips it.
kwin carries `cap_sys_nice` too, so the Arch image's `setcap -r` step matters
there as well. The niri lane hit this first (item 3 in "Verified for niri").

**Never `pkill`/`killall`.** Terminate only PIDs the run owns, with the
empty-string sentinels in `common.sh` (`docs/HEADLESS_WAYLAND_TESTING.local.md:880`).

## Limitations

- **sway reports no dock rect** (above). Geometry there is derived and pixel-measured.
- Pointer support is enabled only after the input-delivery probe succeeds. Unsupported
  native visibility services remain unsupported even when pointer input works.
- GNOME currently exercises the compatibility fallback without its shell extension.
- Popup edge containment, drag-and-drop, applet rendering and fractional scaling
  still need dedicated scenarios.
- `WLR_RENDERER=pixman` is software rendering: correctness-representative, not
  performance-representative.
- Baselines are scoped to the compositor **and** to the container image. A compositor, font or
  icon-theme change invalidates them; regenerate with `--update-baselines`.
- **Panels run for the whole session.** `LAB_PANEL_HEIGHT` changes the capture for
  every case, not just `placement-bottom-panel`. Panel configuration, workarea and
  crop are checked against baseline provenance before pixel comparison. The baseline
  path still holds one scene per case; regenerate when changing the scene.
- This harness is expected to *surface* real seam bugs. Those are findings to triage, not
  harness defects.
