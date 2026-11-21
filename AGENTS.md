# Working on Docking

Docking is a Python 3.10+ desktop dock built with GTK 3 and Cairo. It runs on
Linux, BSD, and Windows, with desktop features varying by backend. Start with the
[development guide](docs/DEVELOPMENT.md) for environment setup and test commands.

## Where changes belong

- `docking/core/` holds configuration, persistence, and shared dock behavior.
  `docking/ui/` owns GTK widgets, rendering, and desktop interaction.
- `docking/platform/composition/` wires platform services together. Keep shared
  contracts in the owning platform domain, with concrete adapters under
  `implementations/`. Window-management and display-session integrations live
  in `docking/platform/backends/`.
- `docking/applets/` contains built-in applets. Follow the
  [applet development guide](docs/APPLET_DEVELOPMENT.md) for lifecycle, rendering,
  and native service integration.
- `tests/` contains pytest coverage, `features/` contains Behave scenarios, and
  [`tests/visual/`](tests/visual/README.md) covers rendering and native desktop
  behavior. Build and release instructions live in the
  [packaging guide](packaging/README.md).

## Checking a change

- Run the pytest tests for the affected behavior. For Python changes, follow
  [CI](.github/workflows/ci.yml) for Ruff, ty, and the broader test matrix.
  Run `tests/smoke/test_python314_smoke.py` on its own because imports cached
  during full-suite collection can hide missing no-GI fixtures.
- For changes to input, window management, placement, or rendering, use the
  relevant [visual or native backend suite](tests/visual/README.md). Check the
  affected backend when behavior depends on the compositor or host platform.
- Keep user-facing strings translatable. Update the gettext template and check
  catalogs using the [translation workflow](docs/TRANSLATIONS.md#changing-translatable-strings).
- Run `npm --prefix website test` for website changes. The
  [website development guide](docs/DEVELOPMENT.md#website) covers the generated
  language pages and browser tests.

Record user-visible changes under **Unreleased** in the [changelog](CHANGELOG.md).
Use [GitHub Issues](https://github.com/edumucelli/docking/issues) for ordinary
bugs and the [security policy](SECURITY.md) for private vulnerability reporting.
