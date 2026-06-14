## Visual Testing Plan

This document defines how Docking should gain durable visual regression
protection for interactive GTK behavior.

The goal is not just "more tests". The goal is to make regressions in dock
appearance and interaction obvious and unacceptable in CI.

Examples of regressions this plan is meant to catch:

- autohide no longer fully hides the dock
- hover magnification shifts or stops matching icon centers
- folder stacks open in the wrong place or lose their curved layout
- drag-and-drop insertion gaps render in the wrong slot
- preview popups move, clip, or overlap incorrectly
- urgent glow / launch / click effects visibly change or disappear
- dock shelf shape, spacing, or alignment drifts after refactors


## Current Implementation

Phase 1 is now implemented in the repository.

The current shipped stack is:

- screenshot regression tests under `tests/visual/`
- committed PNG baselines under `tests/visual/baselines/`
- SSIM + PSNR comparison with diff artifacts on mismatch
- deterministic `behave` scenarios under `features/`
- a shared deterministic dock harness under `tests/bdd_support/`
- a dedicated CI job for BDD + visual regression

The initial protected visual states are:

- dock bottom idle
- dock bottom hovered
- dock bottom hidden
- dock bottom drag insertion gap
- dock bottom urgent hidden state
- folder stack open
- folder stack hovered item

The initial protected user flows are:

- autohide hide/show
- folder stack open/close
- internal drag reorder
- external launcher drop pinning

This is intentionally phase 1, not the final ceiling.
It establishes a stable visual contract for the most fragile interactions first.


## Why Current Tests Are Not Enough

Docking already has strong unit and integration coverage in `tests/ui/`,
`tests/applets/`, and related platform/core areas.

Those tests are good at verifying:

- state transitions
- geometry decisions
- model updates
- event routing
- popup visibility
- drag/drop behavior

They are not sufficient for verifying what the user actually sees.

A dock can still be "correct" at the state level while being visually wrong:

- the stack arc can flatten
- the preview can be a few pixels off
- the insertion gap can drift
- the hover effect can become weaker or asymmetric
- the dock can flicker while still passing logic tests

Visual testing must therefore be a first-class layer, not an afterthought.


## Testing Strategy

Docking should use three complementary layers:

1. Existing pytest unit and integration tests
2. BDD interaction scenarios with `behave`
3. Screenshot-based visual regression checks

The layers have different purposes.

### 1. Pytest

Keep pytest as the main low-level and mid-level correctness layer.

Pytest should continue to own:

- geometry math
- autohide state machine rules
- menu and stack logic
- DnD state transitions
- model and launcher behavior
- applet state and rendering helpers

### 2. Behave

Use `behave` to express user-visible dock behavior in scenario form.

Behave should verify flows like:

- "When I move the pointer onto the dock, it shows"
- "When I leave the dock in autohide mode, it hides"
- "When I open a folder stack and move to another dock item, the stack closes"
- "When I drag an item within the dock, the order changes"
- "When I drop a desktop launcher externally, it is inserted"

Behave should describe intent and flow.
It should not be the main place for rendering assertions.

### 3. Screenshot Regression

Screenshot-based tests should validate the actual rendered UI state.

This is the critical missing layer.

Screenshot regression should protect:

- dock idle appearance
- dock hovered appearance
- hidden vs shown autohide states
- preview popup placement
- tooltip placement
- folder stack shape, spacing, labels, and arc
- drag insertion gap
- urgent glow
- launch/click effect snapshots


## Core Principle

BDD alone is not enough.

If Docking only adds feature scenarios without screenshot comparison, the most
important visual regressions will still slip through.

The correct architecture is:

- `behave` for user flow
- screenshots for rendered truth


## Proposed Directory Layout

Add these new areas:

- `features/`
- `features/environment.py`
- `features/steps/`
- `tests/bdd_support/`
- `tests/visual/`
- `tests/visual/baselines/`
- `tests/visual/output/`

Suggested file structure:

- `features/dock_visibility.feature`
- `features/drag_and_drop.feature`
- `features/folder_stacks.feature`
- `features/preview_and_tooltip.feature`
- `features/effects.feature`
- `tests/bdd_support/harness.py`
- `tests/bdd_support/steps_common.py`
- `tests/visual/test_visual_baselines.py`
- `tests/visual/baselines/*.png`


## Shared Harness

Both behave and screenshot tests should use one deterministic dock harness.

The current implementation uses a split that keeps phase 1 deterministic:

- offscreen Cairo rendering for screenshot baselines
- real controller/event-routing paths with deterministic fakes for behave flows

Future work can expand this toward full live-window capture where it adds value,
especially for preview/tooltip handoff or compositor-sensitive cases.

The harness must:

- build a real `DockWindow`
- use fake model / launcher / tracker inputs where needed
- allow deterministic pointer movement
- allow deterministic item selection
- allow deterministic drag/drop simulation
- allow deterministic popup opening
- allow deterministic animation progression
- capture screenshots from the real draw pipeline

The harness should live in test code only.
Do not add production hooks purely for testing.

### Harness responsibilities

The harness should expose operations like:

- `create_dock(...)`
- `set_hide_mode(...)`
- `set_position(...)`
- `show_window()`
- `move_pointer_to_item(desktop_id)`
- `move_pointer_off_dock()`
- `left_click_item(desktop_id)`
- `right_click_item(desktop_id)`
- `open_folder_stack(desktop_id)`
- `begin_drag(desktop_id)`
- `drag_to_index(index)`
- `drop_external_uri(uri, target_index)`
- `advance_time(ms)`
- `capture_screenshot(name)`

### Determinism requirements

The harness must control:

- GTK theme
- icon size
- zoom percent
- font selection if needed
- window scale
- Xvfb display size
- timing progression

Tests that rely on real sleeps should be rejected.


## Screenshot Architecture

### Baselines

Use baseline PNGs stored in `tests/visual/baselines/`.

Each screenshot should represent one carefully chosen visual contract, not a
random intermediate state.

Examples:

- `dock-bottom-idle.png`
- `dock-bottom-hovered.png`
- `dock-bottom-hidden.png`
- `folder-stack-open-bottom.png`
- `folder-stack-hover-item.png`
- `preview-popup-open.png`
- `drag-insertion-gap.png`
- `urgent-glow-frame.png`

### Captured output

During test runs, write current output to `tests/visual/output/`.

On mismatch, save:

- actual image
- expected image
- diff image

This is required so failures are debuggable in CI artifacts.

### Diff policy

Do not require byte-for-byte equality.

Use image comparison with both:

- SSIM (structural similarity)
- PSNR (peak signal-to-noise ratio)

The preferred default is to require both metrics to pass:

- SSIM must stay above a strict minimum threshold
- PSNR must stay above a strict minimum threshold
- geometry must still match exactly unless a scenario explicitly allows a mask

Why both are needed:

- SSIM is good at catching structural drift:
  spacing, alignment, silhouette, arc shape, popup placement
- PSNR is good at catching lower-level pixel degradation:
  blur, anti-aliasing shifts, unexpected color/noise changes

Optional masked regions should be used only if strictly necessary.
They should be rare and documented per scenario.

The thresholds must be strict enough to catch layout drift and subtle visual
quality regressions.

### Screenshot scope

Do not snapshot every interaction frame.

Snapshot only meaningful stable states:

- post-layout idle
- steady hovered
- fully hidden
- stack fully opened
- preview fully opened
- drag gap visible
- effect at a deterministic sampled frame


## BDD Scenario Design

Behave scenarios should be user-facing and intentional.

Good scenario style:

```gherkin
Scenario: Folder stack closes when the pointer moves to another dock item
  Given the dock is visible on the bottom edge
  And a pinned folder item named "Docs" exists
  And a pinned application item named "Firefox" exists
  When I left click the "Docs" item
  Then the folder stack is visible
  When I move the pointer to the "Firefox" item
  Then the folder stack is not visible
```

Bad scenario style:

- naming raw GTK signals
- asserting internal private fields directly
- describing implementation details instead of user behavior


## First Scenario Set

### 1. Dock Visibility

Scenarios:

- dock remains visible in `none` mode
- dock hides when pointer leaves in `autohide`
- dock reappears when pointer enters
- dock stays visible while a menu is open
- dock stays visible while preview handoff is active

Baseline screenshots:

- idle visible
- hovered visible
- hidden state

### 2. Folder Stacks

Scenarios:

- left click opens a folder stack
- stack opens above the dock when dock is on bottom
- moving to another dock item closes the stack
- right click still opens folder admin menu
- stack overflow uses the `More in ...` action
- image entries use thumbnails when available

Baseline screenshots:

- stack open
- stack hover item
- stack overflow action chip

### 3. Drag and Drop

Scenarios:

- internal drag reorders pinned items
- external drop inserts launcher
- drag leave clears insertion gap
- dragging item away removes it

Baseline screenshots:

- insertion gap visible
- drag reorder midpoint

### 4. Preview and Tooltip

Scenarios:

- preview opens for a running app
- moving from dock to preview keeps interaction alive
- preview hides after leaving it
- tooltip does not fight preview

Baseline screenshots:

- preview open
- tooltip open

### 5. Effects

Scenarios:

- hover zoom activates on pointer enter
- urgent glow appears when item is urgent
- click/launch effect appears at a stable sampled frame

Baseline screenshots:

- hover zoom frame
- urgent glow frame
- click effect frame


## Animation Testing

Animations must be sampled deterministically.

This means:

- freeze time or control time progression through the harness
- advance the animation clock to a specific frame
- capture at known timestamps

Do not rely on wall-clock sleep like `sleep(0.2)`.

Recommended model:

- expose animation pump control in the test harness
- render after advancing to exact time offsets

Example:

- hover animation at `0 ms`
- hover animation at `120 ms`
- urgent glow pulse at one chosen frame

Only a small number of stable frames should be asserted.


## CI Rollout Plan

Add one new CI job first:

- `bdd-visual`

This should run on:

- Ubuntu 24.04
- one Python version, likely 3.12
- Xvfb

Do not add it to every matrix target initially.

### CI job responsibilities

- install dev dependencies including `behave`
- run BDD scenarios
- run visual regression tests
- upload screenshot diff artifacts on failure

### Failure artifacts

Always upload:

- actual screenshots
- diff screenshots
- scenario logs

This is required to make visual failures actionable.


## Dependency Plan

Add dev dependencies:

- `behave`
- `Pillow`
- image-metric support for SSIM and PSNR

Recommended implementation:

- use `skimage.metrics.structural_similarity` for SSIM
- compute PSNR with either `skimage.metrics.peak_signal_noise_ratio` or an
  equivalent local helper if we want to avoid a heavier dependency

Only add more libraries if clearly needed.

Avoid heavy browser automation unless GTK capture proves impossible.


## Implementation Phases

### Phase 1: Harness and Behave Skeleton

- add `behave`
- add `features/`
- add `tests/bdd_support/harness.py`
- implement a minimal dock creation harness
- add one smoke scenario for dock visibility

### Phase 2: Screenshot Infrastructure

- add baseline directory
- add screenshot capture helper
- add image diff helper
- add CI artifact upload for failures
- create first 3-5 golden screenshots

### Phase 3: Core Interaction Coverage

- dock visibility
- folder stacks
- drag/drop
- preview/tooltip

### Phase 4: Effects Coverage

- hover zoom
- urgent glow
- launch/click effects

### Phase 5: Expansion

- multi-monitor scenarios
- active-display monitor switching
- hide-mode transitions
- applet popup visuals


## Update Workflow

When a visual change is intentional:

1. run the visual suite locally
2. inspect the new output
3. update the baseline image deliberately
4. include a clear explanation in the commit/PR

Baseline updates should be reviewed like code changes.


## Non-Goals

This plan is not trying to:

- replace pytest
- snapshot every widget
- test every frame of every animation
- freeze UI evolution

It is trying to make visual regressions expensive and obvious.


## Acceptance Criteria

This effort is successful when:

- a folder stack arc regression fails CI
- a preview placement regression fails CI
- an insertion-gap visual regression fails CI
- a hide/show dock regression fails CI
- a hover/effect regression fails CI
- failures produce diff artifacts that are easy to inspect


## Recommendation

Start with:

1. dock visibility
2. folder stacks
3. drag/drop
4. screenshot baselines for those three

That will deliver the highest value quickly and protect the most fragile,
high-visibility interactions first.
