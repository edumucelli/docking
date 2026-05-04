# OOP Quality Measurement and Improvement Plan

This plan defines how Docking will measure object-oriented design quality before
large refactors, then use those measurements to guide small, reviewable cleanup
PRs.

The goal is not to optimize for one number. The goal is to make coupling,
cohesion, size, and complexity visible enough that architectural cleanup can be
prioritized and verified.

## Measurement Toolkit

Docking uses external tools for the baseline:

- `radon` for cyclomatic complexity and maintainability index.
- `lizard` for function NLOC, cyclomatic complexity, token count, and parameter
  count.
- `cohesion` for class cohesion.
- `import-linter` for package dependency contracts.

Install them with the existing development extra:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the measurement set from the repository root:

```bash
python -m radon cc docking -s -a
python -m radon mi docking -s
python -m lizard -l python docking -C 12 -a 6 -w
cohesion --files $(git ls-files 'docking/**/*.py') --below 50
lint-imports --config pyproject.toml
```

The first baseline should be recorded in this file or a follow-up metrics
appendix before any structural cleanup PR. New gates should start as "no worse
than baseline" instead of strict pass/fail thresholds.

`lizard -w` exits non-zero when it finds current threshold warnings. That is
expected for the initial baseline. Treat those warnings as measurement output
until the project explicitly chooses CI gates.

## Current Baseline Findings

A first AST/import-graph inspection of the current tree found 274 Python files
under `docking/`.

Largest and lowest-cohesion class candidates:

| Class | Approx. LOC | Methods | Attributes | Concern |
| --- | ---: | ---: | ---: | --- |
| `docking.ui.menu.MenuHandler` | 1697 | 74 | 98 | Context menus, item menus, folder stacks, file monitors, popup lifecycle |
| `docking.ui.renderer.DockRenderer` | 879 | 17 | 18 | Large rendering pipeline, but likely cohesive |
| `docking.ui.settings.SettingsWindowController` | 822 | 38 | 69 | Multiple preferences pages and widget binding responsibilities |
| `docking.ui.dock_window.DockWindow` | 809 | 33 | 72 | GTK shell, event routing, UI collaborator ownership |
| `docking.applets.bluetooth.applet.BluetoothApplet` | 643 | 37 | 44 | Applet UI plus Bluetooth action coordination |
| `docking.platform.model.DockModel` | 632 | 31 | 25 | Item model plus live applet ownership |
| `docking.applets.network.applet.NetworkApplet` | 597 | 29 | 42 | Applet UI plus network action coordination |
| `docking.ui.dnd.DnDHandler` | 571 | 20 | 31 | Drag/drop policy and visual effects |

Highest runtime fan-out modules:

| Module | Runtime imports out | Imported by | Concern |
| --- | ---: | ---: | --- |
| `docking.ui.dock_window` | 23 | 1 | UI composition root has broad direct knowledge |
| `docking.ui.menu` | 13 | 1 | Menu subsystem owns several unrelated surfaces |
| `docking.applets.weather.applet` | 12 | 0 | Applet controller with API, state, render, UI, and prefs wiring |
| `docking.applets.certwatch.applet` | 11 | 0 | Applet controller with networking and UI coordination |
| `docking.app` | 11 | 0 | Startup composition root, expected to have fan-out |

Boundary issues found by import inspection:

- `docking.core.config` imports applet identity helpers and `platform.launcher`.
- `docking.platform.model` imports applet discovery and applet metadata.
- Several applet render modules import `docking.ui.overlays`.
- `docking.applets.popup` imports `docking.ui.display`.

Type-checking imports create apparent UI import cycles in naive analysis, but
runtime imports are currently acyclic. The refactor target is still to reduce
type-only dependency on `DockWindow` by introducing narrower protocols.

Measured tool highlights:

| Tool | Highest-signal findings |
| --- | --- |
| `radon cc` | `DockRenderer._draw_content` is `F (55)`, `DockModel.update_running` is `D (23)`, `DockWindow._on_button_release` is `C (20)`, `Launcher.resolve` is `C (19)`, and window-tracker matching/update functions are `C (18)`. |
| `radon mi` | Lowest maintainability modules include `docking.applets.music.state` and `docking.ui.menu` at `C (0.00)`, plus `docking.applets.network.applet`, `docking.applets.bluetooth.applet`, `docking.applets.bluetooth.state`, `docking.ui.renderer`, `docking.applets.keyboardlayout.state`, `docking.applets.hackernews.applet`, and `docking.ui.settings` in the low `B` range. |
| `lizard` | Current warning thresholds flag 80+ functions, led by `DockRenderer._draw_content`, `MenuHandler._list_directory`, `DockModel.update_running`, `DockWindow._on_button_release`, `HoverManager.update`, and several applet parsing/backend functions. |
| `cohesion` | Large controller-style classes have low cohesion: `MenuHandler` around `4.41%`, `SettingsWindowController` around `8.01%`, `DockWindow` around `10.53%`, `BluetoothApplet` around `9.87%`, and `NetworkApplet` around `13%`. |
| `import-linter` | All three baseline contracts are kept with the current explicit exceptions. |

## Architectural Contracts

The initial import-linter contracts are intentionally baseline-aware:

- `core` must not depend on `applets`, `platform`, or `ui`.
- `platform` must not depend on `ui`.
- `applets` must not depend on `ui` internals.

Known current exceptions are listed in `pyproject.toml` as `ignore_imports`.
Each cleanup PR should remove at least one exception when it fixes the
underlying dependency.

Do not add CI enforcement until:

1. The baseline command runs reliably in the local development environment.
2. Existing exceptions are documented.
3. The first cleanup PR proves the direction by removing at least one exception.

## Improvement Roadmap

### 1. Remove Layer Coupling First

Start with package boundary cleanup before class splitting:

- Move applet URI parsing/formatting needed by config into a core-level item
  identity module, then stop importing `docking.applets.identity` from config.
- Move starter-dock desktop probing out of `Config` and into startup/bootstrap
  code so config normalization does not instantiate `Launcher`.
- Inject an applet registry or factory into `DockModel` so the platform model no
  longer imports applet discovery directly.
- Move shared applet overlay drawing from `docking.ui.overlays` into an applet
  or core drawing helper that does not depend on UI internals.
- Move applet popup positioning helpers out of `docking.ui.display` or expose a
  small neutral display geometry helper.

Success criteria:

- Remove the `core.config` import-linter exceptions.
- Remove the `applets -> ui` overlay/display exceptions.
- Keep all behavior unchanged.

### 2. Split Large UI Classes by Responsibility

After layer cleanup, split the largest UI classes:

- Break `MenuHandler` into focused collaborators:
  - dock background menu builder,
  - item context menu builder,
  - folder-stack popup controller,
  - folder file-monitor/cache controller.
- Break `SettingsWindowController` by page:
  - appearance,
  - behavior,
  - applets,
  - updates,
  with shared binding helpers for scalar config widgets.
- Keep `DockWindow` as the GTK shell/composition root, but reduce direct method
  calls by routing collaborators through protocols or runtime surfaces.
- Treat `DockRenderer` as lower priority unless radon/lizard shows specific
  high-complexity functions; it is large, but its responsibilities are more
  cohesive than menu/settings.

Success criteria:

- `MenuHandler` loses folder-stack and file-monitor state.
- Settings pages become individually testable without constructing every tab.
- No new `DockWindow` imports outside UI composition and protocol typing.

### 3. Thin Large Applet Controllers

Prioritize applet controllers where UI, backend probing, menus, and persistence
are mixed:

- Bluetooth.
- Network.
- Currency FX.
- Weather.
- Cert Watch.

Keep the existing applet package convention:

- `applet.py` owns GTK lifecycle and user interaction.
- `state.py` owns pure parsing/state transitions.
- `render.py` owns icon drawing.
- optional backend/API modules own external system or network calls.

Success criteria:

- Applet `applet.py` files become mostly orchestration.
- Backend operations become injectable/testable without GTK widgets.
- State and menu option builders are unit tested without a live desktop.

## Testing and Acceptance Criteria

For the measurement PR:

- `python -m radon cc docking -s -a`
- `python -m radon mi docking -s`
- `python -m lizard -l python docking -C 12 -a 6 -w`
- `cohesion --files $(git ls-files 'docking/**/*.py') --below 50`
- `lint-imports --config pyproject.toml`
- `git diff --check`

For each cleanup PR:

- Run the relevant targeted tests, for example:
  - `python -m pytest tests/ui/test_menu.py tests/ui/test_menu_integration.py -q`
  - `python -m pytest tests/ui/test_settings.py -q`
  - `python -m pytest tests/platform/test_model.py tests/applets/test_registry.py -q`
  - applet-specific tests under `tests/applets/`.
- Rerun the relevant metric command and compare against the baseline.
- Remove any import-linter exception that is no longer needed.
- Run the full suite before merge.

## Guardrails

- Do not weaken production contracts to make metrics look better.
- Do not split classes only by line count; split around ownership boundaries.
- Do not introduce abstract factories unless they remove a concrete dependency
  or make tests materially simpler.
- Keep PRs small enough that behavior can be reviewed independently from
  movement.
- Prefer "measure, refactor one boundary, verify" over broad mechanical churn.
