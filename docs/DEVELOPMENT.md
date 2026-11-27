# Development

[Back to Docking](../README.md)

Run commands from the repository root. Install the
[source requirements](INSTALLATION.md#source-requirements) before setting up
the development environment. For native Wayland protocol clients, also install
the `[wayland]` extra described there. On Windows, follow the
[UCRT64 setup instructions](WINDOWS.md#source-development).

Find shared scripts in `tools/` and platform scripts in `tools/linux/` and
`tools/bsd/`. The [tools guide](../tools/README.md) covers setup and VM utilities.
The [visual test suites](../tests/visual/README.md) are grouped by backend and desktop.

## Source setup

```bash
# Clone
git clone https://github.com/edumucelli/docking.git
cd docking

# Create venv with access to system GI bindings
python3 -m venv --system-site-packages .venv
source .venv/bin/activate

# Install development dependencies, then GTK 3 type stubs without replacing
# the distribution-provided PyGObject runtime
pip install -e ".[dev]"
PYGOBJECT_STUB_CONFIG=Gtk3,Gdk3 \
  pip install --no-deps -r tools/requirements-typing.txt
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv venv --python /usr/bin/python3 --system-site-packages .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
PYGOBJECT_STUB_CONFIG=Gtk3,Gdk3 \
  uv pip install --no-deps -r tools/requirements-typing.txt
```

## Running

```bash
# Via entry point
docking

# Or directly
python run.py

# With debug logging
DOCKING_LOG_LEVEL=DEBUG python run.py
```

## Tests

```bash
# Run all tests
pytest tests/ -v

# Run the no-GI smoke suite in a separate process
pytest tests/smoke/test_python314_smoke.py -q

# Run specific module
pytest tests/applets/test_clock.py -v

# Coverage report
pytest tests/ -v --cov=docking --cov-report=term-missing
```

Run the smoke command in its own process. Imports cached during full-suite
collection can hide missing no-GI fixtures.

CI also checks pull requests targeting prerequisite branches, so stacked changes
receive the same checks before their base reaches master. Release jobs remain
restricted to master.

To run GUI integration tests in a headless X11 session, use:

```bash
bash tools/test_gui_headless.sh
```

The script needs `xvfb-run` and `dbus-run-session`. It tests pointer and edge
behavior, menus, preview popups, the dock window, drag and drop, and rendering.

You can also pass explicit pytest targets:

```bash
bash tools/test_gui_headless.sh tests/ui/test_pointer_scenarios.py
```

## Platform boundaries

Shared application policy lives in `platform/applications/`, applet policy and
GTK wiring in each `applets/<name>/` directory, and concrete adapters in the
owning domain's `implementations/<family>/` package. Use `unix` for shared
Linux/BSD protocols and tools, `linux` or `bsd` for kernel APIs, and `shared`
for portable native tools. Keep package initializers lightweight.

Within BSD adapters, keep OS-specific behavior in matching `freebsd.py`,
`openbsd.py`, and `netbsd.py` modules. Dispatch through the owning backend using
`is_freebsd()`, `is_openbsd()`, and `is_netbsd()` from `platform/environment/`.
Keep behavior common to all three BSDs shared.

`platform/host/` supplies paths, storage durability, file locking, hook commands,
and mounted-filesystem facts without importing GI. Host selection is cached;
XDG variables are read when directory helpers are called. Desktop file/URI
operations and GUI lifecycle hooks live separately in `platform/desktop/`.
Window-management sessions remain under `platform/backends/`; an OS and its
GTK display/session are separate choices.

`platform/composition/` assembles these interfaces at startup. Only entry points
use the complete `PlatformProvider`. Registries, launchers, the model, applets,
and Search receive the narrow dependencies they need. Native discovery sources
and desktop monitors stay in their adapters. Association lookups return an
`AssociationResolution`: inspect its status and use `.listing` or `.name`.
Unix lookups remain synchronous, and ready-empty differs from unavailable.

`platform/applications/backend.py` collects the application platform contracts
and their catalog, launch, and registration results. Each capability remains
independently injected. Canonical application values stay in `types.py`, and
association requests/statuses/resolutions stay in `associations.py`. The common
contract module imports no GI or native implementations.

Owned platform contracts use `ABC` and `@abstractmethod`. Implementations and
backend test doubles inherit their contracts explicitly; an incomplete adapter
must fail at construction. Required metadata uses abstract read-only properties,
which concrete properties or class constants can implement. Shared Unix host,
provider, and applet factory classes remain abstract where Linux/BSD supply the
remaining operations. Preserve structural protocols for callable factories
(`BrowserFactory`), external native handles, and consumer views. Keep native
resource acquisition in concrete implementations and retain their existing
method signatures and lifetimes.

Keep each domain's contracts in its root `backend.py`, separate from state and
native code. Import canonical modules directly; avoid import-only compatibility
files or forwarding modules with a single caller. Shared native helpers expose
public functions when other implementation modules call them. Tests patch I/O
at the module that owns it. Tray consumers import `platform/tray/`; SNI transport
lives under `platform/tray/implementations/unix/`.

Create one shared `TrayService` before constructing applets. Consumers borrow
it and unsubscribe on stop; composition owns shutdown. Stateful backend
factories produce independent instances. A serial worker queues related native
I/O and finalizes resources on its own thread, including when stopped before its
first job. Always guard replies across stop/restart. Stop UI and applet consumers
before session, tray, and registry providers, including on startup failure.

For a complete regression run without a desktop session:

```bash
xvfb-run -a dbus-run-session -- .venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pytest tests/smoke/test_python314_smoke.py -q
xvfb-run -a dbus-run-session -- .venv/bin/behave -q
```

The type checker looks in this checkout before system-installed Docking releases.

Missing-import checks remain enabled, with an explicit allowlist for system
Cairo, BSD-only psutil, native GI namespaces, and optional PyWayland modules
that lack stubs in some installations. Strict generic narrowing requires callers
to validate container contents instead of treating an arbitrary mapping as
having known key/value types. Strict equality semantics keeps equality tests
from silently narrowing values to literal types.
Use rule-specific `ty: ignore[...]` comments when suppression is necessary;
legacy `type: ignore` comments do not suppress ty diagnostics.

Attribute checks apply to all handwritten application code, including native
adapters and GTK wiring. Generated Wayland bindings retain their source exclusion.
The `typings/` search path supplies the GTK 3 GdkX11 APIs used by Docking because
upstream GdkX11 stubs target GTK 4. Keep this small overlay aligned with the native
API; remove it when upstream provides matching GTK 3 stubs. Remove native
namespaces from the missing-import allowlist when their stubs become available.

When changing platform interfaces, migrate Behave callers in `features/steps/`
as well as pytest fixtures. The shared test factories deliberately construct
Linux adapters for deterministic filesystem/command fixtures; tests reading the
real host must explicitly use the native backend from composition.

## Writing custom applets

See the [built-in applets](APPLETS.md) for examples.

Docking discovers applets through `AppletMeta` and loads each one when you enable
it. Applets inherit `create_icon()` and click, scroll, and menu hooks from
`docking/applets/base.py`. They can also implement `start()` and `stop()`.
Keep package imports lightweight. Use separate modules for GTK code, state,
and icon drawing so tests can run without a desktop session.

```text
docking/applets/myapplet/
  __init__.py   # metadata only: AppletMeta declaration
  applet.py     # GTK wiring and lifecycle
  state.py      # pure state/logic helpers
  render.py     # icon rendering helpers
```

`__init__.py`:

```python
from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="myapplet",
    name="My Applet",
    category=AppletCategory.PRODUCTIVITY,
)

__all__ = ["meta"]
```

`applet.py`:

```python
from docking.applets.base import Applet, load_theme_icon
from docking.core.config import Config
from docking.i18n import _

from . import meta


class MyApplet(Applet):
    id = meta.id
    name = _("My Applet")
    icon_name = "utilities-terminal"

    def __init__(self, icon_size: int, config: Config) -> None:
        super().__init__(icon_size, config)
        self.present()

    def create_icon(self, size: int):
        return load_theme_icon(name=self.icon_name, size=size)

    def refresh_tooltip(self) -> None:
        self.item.name = _("Useful status")
```

Use `self.present()` after state changes to refresh icon, tooltip, and dock UI.
Keep parsing/state logic in plain Python modules so tests do not need a display.

Native applets follow the [platform boundaries](#platform-boundaries). Keep
`state.py` focused on values, parsing, preferences, and presentation policy.
Desktop settings groups use `operations.py` for contracts shared across
reader/control backends. In Music, the hybrid controller implements
`MusicBackend` and delegates to the MPRIS and playerctl helpers.

Inherit `PlatformServicesApplet` when an applet needs native backends. Its
constructor requires `platform_services: AppletPlatformServices`; obtain a backend
from the corresponding typed `platform_services.backends` factory before the
first read, and pass the services to `super().__init__`. Add a typed factory to
`applets/platform.py` and implement it in `platform/composition/` when introducing
a new native domain. Constructors and package metadata must not start workers or
register subscriptions unnecessarily. Use fresh factories for new owned client
lifetimes and release subscriptions, timers, and workers in `stop()`.

Applications, Run Command, and Music use `ApplicationServicesApplet` for the
application registry, launcher, and `platform_services`. Pass late session/UI
dependencies through `set_services()`. System Tray borrows the shared service
at construction. Browser applets provide a `BrowserProfile` and obtain a
`BrowserFactory`.

Supply these dependencies explicitly in tests. Cover stop/restart and late
replies for backends that own state or asynchronous work.

## Translations

Docking uses gettext catalogs in `docking/locale/` and falls back to English.
Package compilation is documented in [Translation Ownership](../packaging/README.md#translation-ownership).

The dock automatically uses your system locale. To test a specific language:

```bash
LANGUAGE=pt_BR python run.py
```

### Adding a new translation

Create a catalog from the template, edit it with a PO editor such as Poedit or
Lokalize, run the validation workflow below, and submit the `.po` file in a pull
request.

```bash
mkdir -p docking/locale/XX/LC_MESSAGES
msginit --input=docking/locale/docking.pot \
  --locale=XX \
  --output=docking/locale/XX/LC_MESSAGES/docking.po
```

### Changing translatable strings

After adding or modifying a user-visible `_("...")` string, regenerate the
template and run the same checks as CI:

```bash
./tools/i18n.sh --extract
./tools/i18n.sh --check-pot-sync
./tools/i18n.sh --check-catalogs --allow-incomplete
./tools/i18n.sh --compile
```

Regular feature commits only update `docking/locale/docking.pot`; they do not
need to refresh every catalog or fill every new `msgstr`.

### Translation maintenance

Translation-only updates merge the current template into every catalog and can
apply the stricter completeness check:

```bash
./tools/i18n.sh --update-translations
./tools/i18n.sh --check-catalogs --require-complete
```

## Building packages

Build dependencies, commands, output paths, and local installation steps for
every package format live in the [packaging guide](../packaging/README.md).

## D-Bus remote control

Scripts can inspect and control dock items through the `org.docking.Docking.Items1`
interface on the session bus. The bus name is `org.docking.Docking` and the
object path is `/org/docking/Docking`.

```bash
gdbus introspect --session --dest org.docking.Docking --object-path /org/docking/Docking

gdbus call --session --dest org.docking.Docking --object-path /org/docking/Docking \
  --method org.docking.Docking.Items1.ListTransientIds
gdbus call --session --dest org.docking.Docking --object-path /org/docking/Docking \
  --method org.docking.Docking.Items1.Pin firefox.desktop
```

Use exact IDs from the list methods, such as `firefox.desktop` or `applet://clock`.
Commands that change items return `false` if Docking cannot apply the change.

| Method | Result |
| --- | --- |
| `GetCount()` | Visible item count. |
| `ListPinnedIds()` | Persistent item IDs. |
| `ListTransientIds()` | Visible transient item IDs. |
| `Pin(id)` | `true` when the transient item is pinned. |
| `Unpin(id)`, `Remove(id)` | `true` when the pinned item is removed from the persistent dock. A running app can remain as a transient item. |
| `GetHoverAnchor(id)` | `(true, x, y, edge)` with screen coordinates and `top`, `bottom`, `left` or `right`. Returns `(false, -1, -1, '')` when no anchor is available. |

The service emits `Changed()` when the dock item set changes. See the
[interface definition](../docking/ipc/introspection.py) for the method signatures.

## Website

The website uses Node.js 22 or later to generate static English, Portuguese,
French and Spanish pages. Run from the repository root:

```bash
npm --prefix website ci
npm --prefix website run dev
```

Open `http://127.0.0.1:8000`. The development command builds every language page
and serves the site with Python 3. Restart the command after editing to rebuild.
In `website/`, use `npm run dev`. Run `npm --prefix website test` to check the
generated pages. The static and browser tests both rebuild the site.

Hetzner hosts `website/dist` through a dedicated native Komodo Stack. The website
workflow promotes tested website inputs to `deploy/docking-cc`; unrelated desktop
commits do not trigger or block website deployment. Runtime/dependency/workflow
changes require operational review.
Publish the built directory, not the source `website` directory. Build output
and `node_modules` are ignored by Git. Tailwind runs at build time; visitors
do not download a CSS compiler. Applet icons come from the repository, and the
original `website/hero.gif` is copied without recompression.

Edit the English template in `website/index.html`, applet cards in
`website/applets.mjs`, and corresponding strings in `website/translations.json`.
Use the exact English text as the key in the translation catalog. The build
requires Portuguese, French, and Spanish translations for each key it uses.
Keep shell commands and technical names the same in every language. The build
generates `/`, `/pt/`, `/fr/` and `/es/`. Each page links to the other languages
with `hreflang` and uses its own URL as the canonical URL. All content is
readable without JavaScript. The site can suggest a language based on the browser
settings. A language link or a saved choice takes precedence.

Keep `website/sitemap.xml` synchronized with published pages. Update each
`lastmod` only when that page's content changes, not on every build. The public
sitemap to submit in Google Search Console is `https://docking.cc/sitemap.xml`.
Submit new language URLs for inspection after deploying the generated pages.

Browser checks cover all languages on desktop and mobile, JavaScript disabled,
accent-insensitive applet search, language preferences and reduced motion:

```bash
cd website
npx playwright install chromium
npm run test:browser
```

To use an existing Chrome installation, set `CHROME_PATH` to its executable
instead of installing Chromium. The website workflow runs both the static SEO
checks and browser checks on relevant changes.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes (tests required for new features)
4. Run `ruff format docking/ tests/` for formatting
5. Ensure `ruff check && ty check && pytest tests/` passes
6. Submit a pull request

## BSD package and desktop checks

CI builds and installs packages in actual FreeBSD, OpenBSD, and NetBSD VMs, then
runs core/platform, BSD applet, and battery/volume lifecycle tests plus 13 X11
visual scenarios. NetBSD also verifies package upgrade, removal, and
reinstallation. Download the `bsd-visual-freebsd`, `bsd-visual-openbsd`, or
`bsd-visual-netbsd` artifact and open `index.html` to review screenshots,
per-case settings, package logs, JUnit, and JSON results
without a BSD machine. The weekly/manual **BSD desktops** workflow runs the
same checks. Hardware readings, audio devices and power actions need physical
hardware validation; the reference desktop is compositing Xfce.

The NetBSD target is 11.0/amd64 with pkgsrc Python 3.12. When changing this target,
update the X11 SHA512 pins in `tools/bsd/checksums/` from the signed NetBSD
release hash list. The installer verifies each archive before extraction.
The CI workflow sets the following environment. Export it before running the
NetBSD VM scripts manually:

```bash
export DOCKING_BSD_PYTHON=/usr/pkg/bin/python3.12
export PATH="/usr/pkg/bin:/usr/pkg/sbin:/usr/X11R7/bin:$PATH"
```

Packages and installed visual checks use `/usr/pkg`; keep native
GTK/GI/Cairo/psutil packages visible with
`--system-site-packages`. NetBSD sensor tests cover envsys units and unavailable
readings without requiring a laptop or temperature sensor in the VM.

For a local Linux/BSD X11 control, install GTK3/Cairo/Wnck GI bindings, Xvfb,
Xfwm, D-Bus and XTest, then run
`"${DOCKING_BSD_PYTHON:-python3}" -m tests.visual.backends.x11.run --pytest`.
`--case placement-bottom` selects one scenario, and `--dock-command` can point
at an installed executable. The default output is a timestamped directory under
`artifacts/bsd-visual/`; an explicit `--output` must be empty.

## Dependency cooldown

Dependabot checks Python, Actions and website npm updates daily with a seven-day
version-update cooldown. Security updates can be proposed earlier; they do not
bypass the Python installation cutoff.

The main CI and security workflows bootstrap Python dependencies with:

```bash
export PIP_UPLOADED_PRIOR_TO="$(python tools/dependency_policy.py cutoff)"
python tools/dependency_policy.py bootstrap
python -m pip install -e ".[dev]"
```

The standard-library helper checks the pinned pip wheel's PyPI upload timestamp,
yanked status and committed SHA256 before installing it offline without
dependencies. Update the version and hash together from PyPI's release metadata
and review the wheel before changing this trust anchor. No unpinned pip upgrade
should precede the helper. Missing metadata, download failures and hash
mismatches fail the build; do not disable the cutoff to retry.

The UTC cutoff is reused within a job and persisted through `GITHUB_ENV` for
later steps, including security audits. Build-isolation subprocesses inherit
the same cutoff. This does not validate already installed system packages.
Packaging subprocesses/containers, compositor setup, npm lockfile checks and
Actions SHA pinning still require the remaining rollout work.
