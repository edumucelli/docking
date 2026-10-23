# Development

[Back to Docking](../README.md)

Run commands from the repository root. Install the
[source requirements](INSTALLATION.md#source-requirements) before setting up
the development environment. For native Wayland protocol clients, also install
the `[wayland]` extra described there.

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
