# Performance Investigation

This document tracks Docking's performance work, with the current emphasis on
Wayland sessions and especially XWayland. It complements `docs/WAYLAND.md`,
which tracks correctness and freeze/transparency issues.

This file is about speed, frame cost, implementation progress, and how we
measure improvements.

## Goal

The current user-visible symptom is clear: `tools/xwayland_repro.py` feels
lighter and faster than Docking, even when both are running as X11 clients
inside a Wayland session.

The immediate question is not "is Cairo slow?" or "is XWayland slow?" in the
abstract. The useful question is:

- which parts of Docking's own hot path are doing the most work,
- which of those costs are avoidable,
- and how we can measure improvement after each change.

## Measurement Strategy

We need reproducible before/after numbers. Subjective feel matters, but it is
not enough for iterative optimization work.

The current benchmark path is:

```bash
.venv/bin/python tools/benchmark.py
```

This script intentionally measures Docking-side CPU work before compositor
presentation. It does **not** try to benchmark GTK/XWayland/Mutter itself.

Current benchmark coverage:

- geometry frame construction
- hover update with and without a caller-supplied frame
- blur region computation
- renderer draw cost for:
  - no icons
  - icons idle
  - icons hover
  - icons click
  - icons hover+click
- CPU-only approximation of the current motion path
- CPU-only approximation of the current draw path
That split is important:

- the benchmark tells us how expensive Docking's own work is,
- live XWayland traces and manual sessions tell us whether GTK/Mutter later stop delivering
  draw callbacks or presenting frames.

## Runbook

This is the minimum process to continue the performance work without rebuilding
context from scratch.

### 1. Run the benchmark script

From the repo root:

```bash
.venv/bin/python tools/benchmark.py
```

This prints a benchmark table to stdout. Capture that output and compare it
against the "Original Baseline Results" and "Current Results After Implemented
Work" sections in this document.

### 2. What the benchmark does and does not measure

The benchmark script measures Docking-side CPU work only. It is useful for:

- renderer changes
- geometry reuse changes
- hover-path changes
- redraw scheduling effects that reduce Docking-side work

It does not measure:

- compositor presentation
- GTK/XWayland draw delivery problems
- visual freezes directly

Those concerns still belong in `docs/WAYLAND.md`.

### 3. Validate in a live session

Run Docking normally, then interact with it, especially:

- repeated hover movement across several icons
- autohide hide/show
- tooltip and preview interactions if relevant to the change

Use that live run to confirm behavior and responsiveness rather than relying on
the microbenchmark alone.

### 4. Compare before and after correctly

For any performance change:

1. record the current benchmark table before editing
2. make the change
3. rerun:
   - `tools/benchmark.py`
   - a focused pytest/ruff pass for the touched area
4. if the change affects interaction pacing or visible responsiveness, also do
   a manual live-session check
5. update this document with the new measured numbers if the change is kept

Do not rely on subjective feel alone unless the benchmark cannot cover the
change at all.

### 5. Which tests to run

At minimum, after performance changes in the current hot paths:

```bash
pytest tests/ui/test_renderer_integration.py tests/ui/test_dock_window_integration.py tests/ui/test_interaction.py -q
.venv/bin/python -m ruff check docking/ui/renderer.py docking/ui/dock_window.py docking/ui/interaction.py tests/ui/test_renderer_integration.py tests/ui/test_dock_window_integration.py tests/ui/test_interaction.py
```

If only one subsystem changed, narrow the commands accordingly.

### 6. Where the main benchmark targets live

- renderer hot path: `docking/ui/renderer.py`
- window event/draw path: `docking/ui/dock_window.py`
- interaction policy: `docking/ui/interaction.py`
- benchmark harness: `tools/benchmark.py`

### 7. Current benchmark interpretation guidance

The most important rows today are:

- `renderer.draw(no icons)`
- `renderer.draw(icons idle)`
- `renderer.draw(icons hover)`
- `renderer.draw(icons click)`
- `renderer.draw(icons hover+click)`
- `motion pass cpu-only`
- `draw pass cpu-only`

Those rows have been the strongest signal so far.

### 8. If the work shifts back to freeze diagnosis

Use `docs/WAYLAND.md` and `tools/xwayland_repro.py`, not this document, as the
main source of truth. This file is for speed and hot-path cost, not for
compositor/draw-stop debugging.

## Measured Environments

This document currently includes benchmark tables from two local hosts:

- original baseline host on `2026-04-12`:
  kernel `Linux 6.17.0-20-generic x86_64 GNU/Linux`, CPU
  `Intel(R) Core(TM) i7-5600U CPU @ 2.60GHz`, logical CPUs `4`, Python
  `.venv/bin/python` `3.13.7`
- current host on `2026-04-13`:
  kernel `Linux 5.18.0-1-amd64 x86_64 GNU/Linux`, CPU
  `Intel(R) Core(TM) i7-4600U CPU @ 2.10GHz`, logical CPUs `4`, Python
  `.venv/bin/python` `3.11.2`

These are local comparison numbers, not universal latency budgets. Because the
current host differs from the original baseline host, do not treat percentage
comparisons between the two tables as same-machine before/after measurements.

## Original Baseline Results

Output from the current benchmark script:

```text
benchmark                           median_us     p95_us    mean_us     min_us     max_us
-----------------------------------------------------------------------------------------
geometry.build_frame                    284.7      628.0      350.8      264.9     1043.9
hover.update(frame=...)                 293.6      646.7      363.3      271.3      851.5
hover.update(no frame)                  301.0      604.9      364.7      278.9      970.1
struts.compute_blur_region                0.7        1.3        0.9        0.7        2.1
renderer.draw(no icons)                1089.3     2398.5     1288.4      859.0     6125.5
renderer.draw(icons idle)              3776.6     7069.5     4302.3     3100.8    11952.7
renderer.draw(icons hover)             5891.4    10273.1     6472.9     3204.4    16066.0
renderer.draw(icons click)             5751.0     7385.6     5681.2     3121.6    11607.4
renderer.draw(icons hover+click)       7149.4    12842.8     7857.2     3306.0    57790.7
motion pass cpu-only                    619.1     1008.5      684.8      279.7     3130.3
draw pass cpu-only                     7020.0     8777.1     7492.4     3836.9    84842.9
```

## Current Results On This Machine

The benchmark was rerun on this machine on 2026-04-13 after these implemented
changes:

- icon source-surface caching
- idle icon fast path without temporary effect surfaces
- reusable top-level offscreen surface
- geometry frame reuse between motion and draw
- redraw scheduling / hover redraw coalescing

Current output from the latest local run:

```text
benchmark                           median_us     p95_us    mean_us     min_us     max_us
-----------------------------------------------------------------------------------------
geometry.build_frame                    310.3      481.1      343.4      302.8      827.7
hover.update(frame=...)                 316.1      464.1      343.2      308.5      697.3
hover.update(no frame)                  321.4      563.1      353.1      315.1      865.3
struts.compute_blur_region                0.8        0.8        0.8        0.8        1.1
renderer.draw(no icons)                 769.4     1036.6      808.0      742.6     1449.7
renderer.draw(icons idle)              3029.0     3829.1     3153.8     2813.3     6634.0
renderer.draw(icons hover)             2984.0     3611.6     3094.7     2829.7     5218.5
renderer.draw(icons click)             3149.3     5337.8     3530.5     2824.7     5857.7
renderer.draw(icons hover+click)       3350.3     5974.1     3748.2     2840.9     6943.8
motion pass cpu-only                    314.5      407.8      330.1      308.1      583.0
draw pass cpu-only                     3934.1     7157.4     4411.0     3204.5     7888.4
```

## Interpretation For This Machine

Because the current run was captured on a different host than the original
baseline, the main value of the table above is as a new local reference point
for future same-machine comparisons.

What this machine's run says:

- renderer work still dominates the Docking-side cost envelope
- the icon-bearing draw states are clustered around `3.0ms` to `3.4ms` median
- the full CPU-only draw-path approximation is about `3.9ms` median
- geometry and hover bookkeeping remain much smaller costs at roughly
  `0.31ms` to `0.32ms` median
- `struts.compute_blur_region` is still negligible relative to renderer cost

## Implemented Work

The following optimization work is already implemented in the current tree:

- renderer icon source-surface caching
- renderer idle icon fast path that skips the temporary effect surface when no
  hover/click effect is active
- renderer top-level offscreen surface reuse across same-size draws
- geometry frame reuse between motion and draw
- redraw scheduling so repeated motion/model-change requests are coalesced
  instead of issuing an immediate `queue_draw()` every time
- benchmark coverage in `tools/benchmark.py`

These changes are the reason the renderer and draw-path benchmark rows are now
substantially cheaper than the original baseline.

## Architectural Lessons From The Work

### 1. The biggest wins were not in generic "Wayland" code

The measured improvements came from making Docking itself cheaper:

- removing repeated Cairo surface allocation
- reusing prepared render resources
- reusing geometry work
- pacing redraw requests

This is important because it keeps the focus on changes that improve Docking
even when the compositor/toolkit layer is imperfect.

### 2. Resource ownership matters

The renderer improvements were easier to implement and reason about after the
cache boundary became explicit. `DockRenderer` now has a real cache owner
instead of several parallel cache fields with ad hoc invalidation rules.

The same lesson applies on the window side: when cached artifacts exist,
ownership and validity rules should be explicit, not implied by scattered
attributes.

### 3. Not every refactor is a performance win

Some structural refactors improved clarity, but they were not the source of the
measured speedups. The direct gains came from concrete hot-path reductions.

That distinction matters when evaluating future cleanup:

- keep behavior/performance changes that reduce work
- be willing to simplify or revert abstraction churn that does not help
  measurement, ownership, or correctness

### 4. Public boundaries should stay public

One thing we learned during the cache refactors is that internal cache shape
should not leak into neighboring components. If `interaction.py` needs access
to an interaction-relevant frame, that should be provided by an explicit
`DockWindow` method, not by reaching into a private cache object.

That is both a design point and a maintenance point: optimization work should
not silently erode module boundaries.

## What Is Still Missing

Even with the implemented work and the current benchmark table, some useful
measurement and cleanup work is still missing:

- rerun a representative real hover/autohide session and record qualitative
  observations alongside the benchmark results
- add a benchmark row that models the shared "motion then draw" path more
  directly instead of treating motion and draw as separate CPU-only passes
- measure tooltip/preview cost before optimizing them
- decide whether the `DockWindow` cache refactor is carrying its weight or
  whether some of that structure should be simplified while keeping the actual
  optimizations

## Current Priority

Given the current numbers and implemented work, the highest-value remaining
optimization target is still redraw pacing and caller pressure around the draw
path, followed by measurement-led cleanup of input-region/tooltip/preview work.

## What The Baseline Already Tells Us

### 1. Rendering dominates the frame budget

`renderer.draw(icons hover+click)` has a median cost of about `7.1ms`, and even
`renderer.draw(icons idle)` is about `3.8ms`.

That is the single biggest measured Docking-side cost in this investigation.
At a 60Hz target, the total frame budget is about `16.7ms`, so Docking is
already consuming a large fraction of that budget before compositor-side
presentation is considered.

### 2. Icon rendering is much more expensive than the shelf/background path

`renderer.draw(no icons)` vs the icon-bearing cases:

- no icons: about `1.1ms`
- icons idle: about `3.8ms`
- icons hover: about `5.9ms`
- icons click: about `5.8ms`
- icons hover+click: about `7.1ms`

So the icon path itself accounts for roughly:

- `+2.7ms` from just drawing icons at idle
- another `~2.1ms` when hover lightening is active
- another `~1.3ms` when hover and click-darken stack together

That lines up with the renderer implementation:

- [renderer.py](/home/eduardo/Projects/docking/docking/ui/renderer.py#L348)
  allocates a full offscreen surface every frame
- [renderer.py](/home/eduardo/Projects/docking/docking/ui/renderer.py#L781)
  allocates a temporary `icon_surface` for every icon on every frame
- [renderer.py](/home/eduardo/Projects/docking/docking/ui/renderer.py#L872)
  builds a new Cairo surface from the pixbuf on every frame as well

This means the icon path currently performs at least two Cairo surface
allocations per icon per frame:

- pixbuf -> source surface
- source surface -> adjusted icon surface

That is the strongest currently measured optimization target.

### 3. Geometry building is real work, but not the biggest problem

`geometry.build_frame` is about `0.28ms` median in the current warmed run.

That is not catastrophic by itself, but it matters because Docking currently
does it in more than one place in the same interaction cycle:

- motion path:
  [_on_motion](/home/eduardo/Projects/docking/docking/ui/dock_window.py#L589)
- draw path:
  [_on_draw](/home/eduardo/Projects/docking/docking/ui/dock_window.py#L487)

So on hover-heavy interaction, Docking often pays geometry-build cost twice:

- once in motion handling
- once again in drawing

That makes frame reuse a valid target, but the benchmark shows it is not the
largest win available.

### 4. Blur region math is not a CPU bottleneck

`struts.compute_blur_region` is about `0.7us` median.

That is effectively free compared with the other measured work. The current
code already avoids redundant native blur-hint writes:

- [dock_window.py](/home/eduardo/Projects/docking/docking/ui/dock_window.py#L925)
  returns early if the blur region is unchanged

So for performance, blur-region **computation** is not a priority hotspot.

This does **not** mean blur hints are irrelevant to correctness or XWayland
stability. It only means they do not look like a primary CPU cost center.

### 5. Motion-path CPU cost is moderate, but event rate can amplify it

The current motion-path approximation is about `0.62ms` median.

That is not huge per event, but motion events can arrive much faster than the
display refresh rate. If Docking reacts to every motion event with fresh frame
construction and a queued full redraw, the total work scales with mouse event
rate, not with frame rate.

This is why redraw coalescing still matters even though the per-call number is
smaller than render cost.

## Methodical Investigation Of Each Candidate

Below is the current ranking based on measured cost and code inspection.

### A. Reuse the geometry frame between motion and draw

Current code:

- [_on_motion](/home/eduardo/Projects/docking/docking/ui/dock_window.py#L597)
  builds a frame
- [_on_draw](/home/eduardo/Projects/docking/docking/ui/dock_window.py#L526)
  builds another frame

Why it matters:

- the frame is the shared geometry source for hover, hit testing, input region,
  and rendering
- building it twice in one hover cycle is unnecessary when no invalidating
  state changed between motion and draw

Baseline evidence:

- `geometry.build_frame`: `~0.28ms` median

Assessment:

- worthwhile
- moderate expected win
- low architectural risk if done carefully

How to measure improvement:

- rerun `tools/benchmark.py`
- compare `geometry.build_frame`
- add a new benchmark after implementation for
  "motion+draw with shared frame" vs current split path

### B. Coalesce input-region updates instead of treating motion as a trigger

Current code:

- [_on_motion](/home/eduardo/Projects/docking/docking/ui/dock_window.py#L599)
  calls `update_input_region(frame=frame)`
- [_on_draw](/home/eduardo/Projects/docking/docking/ui/dock_window.py#L552)
  calls it again
- [update_input_region](/home/eduardo/Projects/docking/docking/ui/dock_window.py#L856)
  already skips the native X11 update if the rect is unchanged

What this means:

- the expensive native region update is already coalesced by rectangle equality
- but Docking still performs the input-region bookkeeping in both hot paths

Assessment:

- lower-value than icon/render optimization
- mostly valuable as part of geometry-frame reuse, not as a standalone target

How to measure improvement:

- add counters for:
  - `update_input_region` calls
  - actual `input_shape_combine_region(...)` native writes
- compare per second during a standard hover run

### C. Keep blur updates coarse

Current code:

- [_sync_background_blur_hint](/home/eduardo/Projects/docking/docking/ui/dock_window.py#L899)
  computes the blur region from `frame.background_rect`
- unchanged regions are skipped

Baseline evidence:

- blur-region computation itself is negligible

Assessment:

- not a CPU optimization priority
- still relevant as a correctness/stability variable on XWayland

How to measure improvement:

- for speed: not worth prioritizing now
- for stability: use runtime counters and freeze traces, not microbenchmarks

### D. Reuse the renderer's offscreen surface

Current code:

- [renderer.py](/home/eduardo/Projects/docking/docking/ui/renderer.py#L348)
  allocates a new offscreen surface every draw

Why it matters:

- this happens once per frame regardless of icon count
- it contributes to the `renderer.draw(no icons)` baseline of `~1.1ms`

Assessment:

- meaningful optimization target
- medium expected win
- should be tied to window-allocation invalidation, not ad-hoc reuse

How to measure improvement:

- compare `renderer.draw(no icons)`
- compare full `draw pass cpu-only`

### E. Remove per-icon per-frame surface allocation

Current code:

- [renderer.py](/home/eduardo/Projects/docking/docking/ui/renderer.py#L781)
  allocates `icon_surface` every frame for every icon
- [renderer.py](/home/eduardo/Projects/docking/docking/ui/renderer.py#L872)
  allocates another surface when converting pixbuf to Cairo

Why it matters:

- this is the clearest explanation for the large gap between:
  - `renderer.draw(no icons)` `~1.1ms`
  - `renderer.draw(icons idle)` `~3.8ms`
  - `renderer.draw(icons hover+click)` `~7.1ms`

Assessment:

- highest-priority Docking-side optimization target
- highest likely payoff

Likely implementation directions:

- cache pixbuf -> Cairo source surface by item identity and icon object
- stop cloning the icon into a temporary surface when no lighten/darken effect
  is active
- when effects are inactive, draw the cached surface directly
- when effects are active, consider a cheaper effect path before falling back
  to a temporary surface

How to measure improvement:

- primary metric:
  `renderer.draw(icons idle)`
- secondary metrics:
  `renderer.draw(icons hover)`
- stress metric:
  `renderer.draw(icons hover+click)`
- system metric:
  `draw pass cpu-only`

### F. Throttle hover redraws to frame rate instead of motion rate

Current code:

- [_on_motion](/home/eduardo/Projects/docking/docking/ui/dock_window.py#L600)
  queues a draw for every motion event

Why it matters:

- a mouse can generate many more events than the compositor can present as
  frames
- processing every motion as a fresh full update path creates avoidable churn

Assessment:

- important for interaction smoothness
- especially valuable on XWayland where extra churn may increase the chance of
  triggering presentation bugs

How to measure improvement:

- add a runtime counter for:
  - motion events received
  - actual queued redraws
  - completed draw callbacks
- compare those ratios before and after coalescing

### G. Optional heavy features: previews, tooltips, blur, micro-animations

Current benchmark scope:

- previews are disabled
- tooltips are stubbed
- blur is only measured at compute cost, not at compositor cost

Assessment:

- these are still valid suspects for user-perceived heaviness
- but they are currently unmeasured in the benchmark

Next benchmark extension:

- tooltip update benchmark
- preview scheduling benchmark
- optional benchmark cases with:
  - `tooltips_enabled=False`
  - `previews_enabled=False`
  - animation state active/inactive

### H. Debug logging

Current code:

- per-draw debug logging exists in:
  [dock_window.py](/home/eduardo/Projects/docking/docking/ui/dock_window.py#L508)
  and parts of the renderer

Assessment:

- this can materially distort perceived smoothness during investigation runs
- but it is not a release-path optimization target if normal users do not run
  at `DEBUG`

How to measure improvement:

- only benchmark explicitly in debug-investigation mode
- keep it out of the normal baseline

## Priority Order Based On Current Evidence

Current recommended order:

1. eliminate per-icon per-frame surface allocation
2. reuse the top-level offscreen surface
3. reuse geometry frames between motion and draw
4. coalesce hover redraws to frame rate
5. add counters for input-region and blur native writes
6. extend benchmarks to tooltip/preview paths

This order reflects measured cost, not just intuition.

## Architecture Themes Emerging From The Investigation

These performance issues are not a random pile of hot lines. The same few
architecture gaps keep reappearing:

- **No explicit render-resource lifecycle**
  The renderer owns useful reusable resources in practice, but the code still
  treats them as throwaway per-frame artifacts.

- **No explicit frame lifecycle**
  Geometry is centralized, which is good, but frame production/consumption is
  still opportunistic. Motion and draw both act like producers.

- **No redraw scheduler**
  Event rate and draw rate are too tightly coupled. Motion currently behaves as
  if every input event deserves its own render pass.

- **Observability arrived after the runtime model**
  We now have benchmark coverage and runtime counters, but those metrics were
  added after the architecture was already in place. That means we can now see
  costs clearly, but the code structure is not yet shaped around those costs.

- **Good geometry centralization, incomplete downstream consumption**
  The geometry refactor did the right thing conceptually. The remaining gap is
  not geometry ownership itself, but how consistently the rest of the runtime
  treats one frame as the shared answer for a whole update cycle.

This matters because the optimization work should not become a sequence of
small hacks. The real direction is to tighten the runtime around:

- reusable render resources
- explicit frame ownership
- event-to-frame pacing
- first-class measurement

## Detailed Improvement Plans

### 1. Per-icon surface allocation removal

#### Problem Statement

The renderer is the largest measured Docking-side cost center. The strongest
evidence is in the icon-bearing render rows:

- `renderer.draw(no icons)`: `~1.1ms`
- `renderer.draw(icons idle)`: `~3.8ms`
- `renderer.draw(icons hover)`: `~5.9ms`
- `renderer.draw(icons hover+click)`: `~7.1ms`

That means the icon path alone adds several milliseconds to the frame cost
before GTK/XWayland presentation is involved.

#### Current Design

The current icon path in [renderer.py](/home/eduardo/Projects/docking/docking/ui/renderer.py):

- converts pixbufs to Cairo surfaces on demand
- allocates a temporary icon surface for each icon every frame
- applies hover/click effects into that temporary surface
- paints the temporary surface to the final context

This path is used even when an icon has no active hover or click effect.

#### Architecture Gaps / Issues

- There is **no durable icon source-surface cache boundary**.
- Source preparation and effect composition are mixed into one per-frame path.
- The renderer has no explicit rule for when icon pixels are stable enough to
  reuse.
- Idle icons are paying for an effect-friendly path even when no effect is
  active.

#### Target Design

Split icon rendering into three stages:

1. **Source acquisition**
   - persistent cached Cairo surface for the icon image itself
2. **Effect composition**
   - optional path only when hover/click adjustments are non-zero
3. **Final paint**
   - paint cached surface directly when no effect is active

The cache should be owned by the renderer and keyed by item identity plus icon
object/version semantics that are stable enough to detect icon replacement.

#### Implementation Plan

Phase 1:
- Introduce a renderer-owned icon source-surface cache.
- Route pixbuf-to-surface conversion through that cache.

Phase 2:
- Add an idle fast path in `_draw_icon`.
- When `lighten == 0` and `darken == 0`, paint the cached source surface
  directly with scaling and no temporary icon surface.

Phase 3:
- Keep the temporary effect surface only for non-idle icons.
- Make the effect path consume the cached source surface instead of rebuilding
  it.

Phase 4:
- Define invalidation/cleanup policy for disappeared items and replaced icons.

#### Measurement Plan

Primary metrics:
- `renderer.draw(icons idle)`
- `renderer.draw(icons hover)`

Stress metric:
- `renderer.draw(icons hover+click)`

System metric:
- `draw pass cpu-only`

Live validation:
- compare hover responsiveness and visual stability in a real session

#### Correctness / Regression Risks

- stale icon pixels after icon replacement
- hover/click visuals diverging between cached and non-cached paths
- cached-surface growth if old icon entries are never reclaimed

#### Acceptance Criteria

- idle icon rendering gets measurably cheaper
- hover/click visuals are unchanged
- icon updates still appear correctly after runtime model changes

### 2. Top-level offscreen surface reuse

#### Problem Statement

The renderer still pays a meaningful frame cost even without icons:

- `renderer.draw(no icons)`: `~1.1ms`

Part of that baseline is the per-frame allocation of the full offscreen buffer.

#### Current Design

The top-level draw path in [renderer.py](/home/eduardo/Projects/docking/docking/ui/renderer.py)
creates a new offscreen surface every draw, renders the dock into it, then
blits it to the target with `OPERATOR_SOURCE`.

That approach is visually correct for this transparent dock window, but it has
no resource lifecycle. The buffer exists only for one frame.

#### Architecture Gaps / Issues

- There is **no frame-buffer lifecycle** inside the renderer.
- Window size and scale may be stable for long stretches, but the code does not
  model that stability.
- The renderer has no explicit invalidation boundary tied to allocation or
  scale-factor changes.

#### Target Design

Introduce a persistent offscreen backing surface owned by the renderer:

- allocate once per `(width, height, scale)` tuple
- recreate only when those inputs change
- keep the atomic offscreen-to-visible blit behavior unchanged

#### Implementation Plan

Phase 1:
- Add renderer state for the reusable offscreen surface and its allocation key.

Phase 2:
- Replace per-draw `create_similar(...)` allocation with reuse when the
  allocation key matches.

Phase 3:
- Ensure resize/allocation changes invalidate and recreate the surface.

Phase 4:
- Validate transparent clearing rules so stale pixels are never presented.

#### Measurement Plan

Primary metric:
- `renderer.draw(no icons)`

Secondary metric:
- `draw pass cpu-only`

#### Correctness / Regression Risks

- stale pixels if the reusable buffer is not fully cleared
- incorrect results after size/scale changes
- transparency regressions if the blit semantics change

#### Acceptance Criteria

- `renderer.draw(no icons)` improves measurably
- no visual regression in transparency or autohide motion

### 3. Geometry frame reuse between motion and draw

#### Problem Statement

Geometry is centralized, but Docking still often builds it twice in one update
cycle:

- once in motion handling
- once again in draw

The raw number is smaller than renderer cost, but it still repeats in the
hottest interaction path.

#### Current Design

- [_on_motion](/home/eduardo/Projects/docking/docking/ui/dock_window.py#L589)
  builds a frame and uses it for hover/input handling
- [_on_draw](/home/eduardo/Projects/docking/docking/ui/dock_window.py#L487)
  builds another frame and uses it for rendering and blur/input updates

#### Architecture Gaps / Issues

- There is **no explicit pending/current frame lifecycle** in `DockWindow`.
- Motion and draw both behave like frame producers.
- Geometry centralization exists, but whole-cycle frame ownership does not.

#### Target Design

Make `DockWindow` treat the geometry frame as a produced-once, consumed-many
artifact for a single update cycle:

- motion may produce the next frame
- draw consumes it if still valid
- explicit invalidation rules reset it when state changes

The invalidation boundary must cover:

- cursor movement
- model changes
- autohide state / hide offset
- zoom progress
- DnD insertion state

#### Implementation Plan

Phase 1:
- Add explicit cached-frame and invalidation helpers in `DockWindow`.

Phase 2:
- Let motion populate the cached frame.

Phase 3:
- Let draw consume the cached frame when valid.

Phase 4:
- Reduce duplicate `build_frame()` call sites in paths that already have a
  valid frame.

#### Measurement Plan

Primary metrics:
- `geometry.build_frame`
- `hover.update(frame=...)` vs `hover.update(no frame)`

Live validation:
- confirm hover, hit-testing, and autohide still track the same frame

#### Correctness / Regression Risks

- stale hover/hit geometry during active animation
- stale tooltip/preview anchors
- stale input region during autohide transitions

#### Acceptance Criteria

- duplicate frame construction in the hot path is reduced
- hover, click, tooltip, and autohide behavior remain aligned

### 4. Hover redraw coalescing / redraw scheduler

#### Problem Statement

Motion currently queues a draw immediately for every motion event. Even if the
per-motion CPU cost is moderate, mouse-event rate can exceed any realistic draw
rate.

#### Current Design

[_on_motion](/home/eduardo/Projects/docking/docking/ui/dock_window.py#L600)
calls `queue_draw()` directly. Other paths also schedule redraws independently:

- model animation ticks
- urgent glow
- autohide transitions

There is no single place that decides whether a new redraw is already pending.

#### Architecture Gaps / Issues

- There is **no redraw scheduler layer** between event producers and draw
  execution.
- Docking currently treats many event sources as if they should directly pace
  rendering.
- The runtime does not have a first-class concept of "redraw already pending".

#### Target Design

Introduce a redraw scheduler in `DockWindow` or an adjacent helper that:

- records redraw reasons
- coalesces multiple requests into at most one pending draw
- keeps animation/urgent paths compatible
- preserves responsiveness for hover/autohide/DnD

#### Implementation Plan

Phase 1:
- Introduce explicit redraw-request reasons and one scheduling entry point.

Phase 2:
- Route motion-triggered redraw through the scheduler.

Phase 3:
- Route animation and urgent-glow redraws through the same scheduler.

Phase 4:
- Remove direct unconditional motion `queue_draw()` from the hot path.

#### Measurement Plan

Benchmark metric:
- `motion pass cpu-only`

The important outcome is smoother visible pacing in real interaction, not just
lower CPU time.

#### Correctness / Regression Risks

- hover lag
- tooltip lag
- DnD responsiveness regression
- autohide reveal/hide delay

#### Acceptance Criteria

- draw callback rate decouples from raw motion-event rate
- hover remains visually responsive
- DnD and autohide behavior remain correct

### 5. Input-region update coalescing / observability

#### Problem Statement

Input-shape writes are already guarded against unchanged rectangles, but the
runtime still calls `update_input_region()` from several hot paths. The bigger
issue now is not raw CPU cost, but lack of a clear policy boundary for when
shape updates are expected.

#### Current Design

[update_input_region](/home/eduardo/Projects/docking/docking/ui/dock_window.py#L856)
recomputes or consumes a frame, compares the input rect, and only performs the
native X11 write when the rect changed.

This is good local behavior, but it is still invoked from:

- motion
- draw
- placement/model-change related paths

#### Architecture Gaps / Issues

- There is **no first-class definition of shape-update triggers**.
- Observability existed late; until the new counters, write frequency was mostly
  invisible.
- Callers still think in terms of "maybe update now" instead of state-driven
  geometry transitions.

#### Target Design

Treat input-region updates as state-driven geometry synchronization:

- keep the existing rect equality guard
- define which runtime transitions are supposed to trigger checks
- reduce redundant call sites after geometry-frame reuse is in place

#### Implementation Plan

Phase 1:
- use current counters to establish baseline call/write frequency

Phase 2:
- document expected triggers:
  - autohide state/offset changes
  - layout/model changes
  - placement/monitor changes

Phase 3:
- after frame reuse lands, prune redundant call sites

#### Measurement Plan

Live metrics:
- `update_input_region_calls`
- `input_region_native_writes`

Success means fewer unnecessary checks and unchanged native-write correctness.

#### Correctness / Regression Risks

- broken click-through behavior
- hidden trigger strip regressions
- mismatch between visible dock band and actual input region

#### Acceptance Criteria

- native writes remain correct
- click-through and hidden-trigger behavior stay intact
- redundant checks are reduced where safe

### 6. Tooltip / preview measurement expansion

#### Problem Statement

Tooltips and previews may contribute to perceived heaviness, but we do not
currently have proper benchmark coverage for them. Optimizing them now would be
guesswork.

#### Current Design

The current benchmark harness:

- stubs tooltip work
- disables preview behavior

That keeps the benchmark focused, but it leaves popup-related cost unmeasured.

#### Architecture Gaps / Issues

- This is primarily a **measurement gap**, not yet a confirmed design flaw.
- We do not know whether popup-related work is a real CPU issue, a perceived
  latency issue, or mostly a compositor interaction issue.

#### Target Design

Add benchmark coverage for popup-adjacent hover work before making optimization
decisions:

- tooltip update path
- preview scheduling path
- enabled/disabled comparisons where feasible

#### Implementation Plan

Phase 1:
- extend the benchmark harness with tooltip-update cost

Phase 2:
- add preview-scheduling benchmark coverage

Phase 3:
- only after measurement, decide whether optimization work is warranted

#### Measurement Plan

New benchmark rows:
- tooltip update
- preview scheduling
- enabled/disabled comparisons when practical

#### Correctness / Regression Risks

- none if this stage remains measurement-only

#### Acceptance Criteria

- popup-related cost becomes measurable
- any later optimization can be justified with numbers

## What We Should Add Next To The Metrics

The current benchmark gives us a stronger baseline now, but it is still not the
full system. The next additions should be:

1. a benchmark mode for icon effects
   - no hover/click effects
   - hover lighten active
   - click darken active
   - hover + click active
   - now implemented in `tools/benchmark.py`

2. a benchmark mode for popup-related hover work
   - tooltips enabled/disabled
   - previews enabled/disabled

## Validation Stack For Every Future Optimization

Every optimization in this document should be validated in the same way:

1. run `tools/benchmark.py`
2. record before/after results in this document
3. validate visually on Wayland/XWayland during a representative
   hover/autohide session for:
   - hover responsiveness
   - tooltip freshness
   - preview timing
   - autohide behavior
   - no new freeze or transparency regression

## Working Rule

No optimization should land based only on "it feels faster".

For this investigation, the expected workflow is:

1. run the benchmark script
2. make one targeted optimization
3. rerun the benchmark script
4. record the delta in this document
5. separately validate behavior on the live Wayland/XWayland session

That keeps the performance work measurable and prevents cargo-cult changes.
