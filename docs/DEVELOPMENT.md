# Development

[Back to Docking](../README.md)

Run commands from the repository root. Install the
[source requirements](INSTALLATION.md#source-requirements) before setting up
the development environment. For native Wayland protocol clients, also install
the `[wayland]` extra described there.

Shared development scripts live directly in `tools/`; platform-specific scripts
live in `tools/linux/` and `tools/bsd/`, each with its visual harness under
`visual/`. See the [tools guide](../tools/README.md) for entry points and coverage.

## Source Setup

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
  pip install --no-deps -r requirements-typing.txt
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv venv --python /usr/bin/python3 --system-site-packages .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
PYGOBJECT_STUB_CONFIG=Gtk3,Gdk3 \
  uv pip install --no-deps -r requirements-typing.txt
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

# Run specific module
pytest tests/applets/test_clock.py -v

# Coverage report
pytest tests/ -v --cov=docking --cov-report=term-missing
```

For the GUI/integration-oriented slice under a headless X11 session:

```bash
bash tools/test_gui_headless.sh
```

Requirements for that mode:

- `xvfb-run`
- `dbus-run-session`

By default it runs the dock interaction/UI slice:

- pointer scenarios
- edges
- menu integration
- preview popup integration
- dock window integration
- interaction
- DnD integration
- renderer integration

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

Run the smoke file in its own process. Full-suite import caches can hide
incomplete no-GI fixtures. The type-checker configuration explicitly prioritizes
this checkout over system-installed Docking releases.

When changing platform interfaces, migrate Behave callers in `features/steps/`
as well as pytest fixtures. The shared test factories deliberately construct
Linux adapters for deterministic filesystem/command fixtures; tests reading the
real host must explicitly use the native backend from composition.

## D-Bus Remote Control

Docking exposes the `org.docking.Docking.Items1` session-bus interface for item
inspection and control. See [D-Bus Remote Control](DBUS.md) for the method
reference, examples, and expected responses.

## Building Packages

Build dependencies, commands, output paths, and local installation steps for
every package format live in the [packaging guide](../packaging/README.md).

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes (tests required for new features)
4. Run `ruff format docking/ tests/` for formatting
5. Ensure `ruff check && ty check && pytest tests/` passes
6. Submit a pull request

See also [custom applet development](APPLET_DEVELOPMENT.md),
[translations](TRANSLATIONS.md), and [visual compositor testing](VISUAL_COMPOSITOR_TESTING.md).


## BSD package and desktop checks

CI builds and installs packages in actual FreeBSD and OpenBSD VMs, then runs
core/platform and BSD applet tests plus 13 X11 visual scenarios. Download the
`bsd-visual-freebsd` or `bsd-visual-openbsd` artifact and open `index.html` to
review screenshots, per-case settings, package logs, JUnit, and JSON results
without a BSD machine. The weekly/manual **BSD desktops** workflow runs the
same checks. Hardware readings, audio devices and power actions need physical
hardware validation; the reference desktop is compositing Xfce.

For a local Linux/BSD X11 control, install GTK3/Cairo/Wnck GI bindings, Xvfb,
Xfwm, D-Bus and XTest, then run `python3 -m tools.bsd.visual.run --pytest`.
`--case placement-bottom` selects one scenario, and `--dock-command` can point
at an installed executable. The default output is a timestamped directory under
`artifacts/bsd-visual/`; an explicit `--output` must be empty.
