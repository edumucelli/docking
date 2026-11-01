# Writing Custom Applets

[Back to Docking](../README.md)

Set up a checkout using the [development guide](DEVELOPMENT.md).
The [applets guide](APPLETS.md) describes the built-in catalog and user interactions.

Applets are discovered from `AppletMeta` metadata and loaded lazily when
enabled. They inherit the common lifecycle and UI hooks from
`docking/applets/base.py`, including `create_icon()`, click, scroll, and menu
handling, plus optional `start()` and `stop()` methods. Keep package imports
cheap and separate GTK wiring from pure state and rendering helpers so most
logic remains testable without a live desktop session.

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

class MyApplet(Applet):
    def create_icon(self, size):
        return load_theme_icon(name="my-icon", size=size)

    def refresh_tooltip(self):
        self.item.name = "My Applet"
        self.item.tooltip_text = "Useful status"

    def start(self, notify):
        super().start(notify)

    def stop(self):
        super().stop()
```

Use `self.present()` after state changes to refresh icon, tooltip, and dock UI.
Keep parsing/state logic in plain Python modules so tests do not need a display.


Native applets keep their contracts at the domain root and their implementations
under `implementations/unix/`, `implementations/linux/`, or
`implementations/bsd/`. Keep `state.py` limited to values, parsing, preferences,
and presentation policy. Existing desktop settings groups use small
`operations.py` contracts where they are independent of the reader/control
backend. Shared applets must not select OS implementations or call OS factories.

Owned backend contracts are abstract base classes. Inherit the contract and
implement every abstract operation and property, including metadata. Required
constant metadata may be supplied as class attributes. Test doubles follow the
same contract or use mocks with that contract as their spec. Keep callable
factory protocols such as `BrowserFactory` structural. In Music, the hybrid
controller implements `MusicBackend`; its MPRIS/playerctl helpers have separate
signatures and do not inherit that contract.

Inherit `PlatformServicesApplet` when an applet needs native backends. Its
constructor requires `platform_services: AppletPlatformServices`; obtain a backend
from the corresponding typed `platform_services.backends` factory before the
first read, and pass the services to `super().__init__`. Add a typed factory to
`applets/platform.py` and implement it in `platform/composition/` when introducing
a new native domain. Constructors and package metadata must not start workers or
register subscriptions unnecessarily. Use fresh factories for new owned client
lifetimes and release subscriptions, timers, and workers in `stop()`.

Applications, Run Command, and Music use `ApplicationServicesApplet`, which
requires the application registry, launcher, and `platform_services` together.
The base makes native factories available before an applet's first read, without
another combined dependency base. Ordinary `Applet` and `TargetServicesApplet`
retain their narrower constructor contracts. Late
session/UI dependencies still arrive through `set_services()`; do not replace
construction-time dependencies there. System Tray borrows its shared service
from construction and never starts or stops that service itself. Browser applets
provide a `BrowserProfile` and obtain a `BrowserFactory` instead of subclassing
a concrete WebKit host.

Tests supply these dependencies explicitly, construct native adapters at their
canonical paths, and patch I/O where the adapter owns it. Keep the original
behavior assertions when moving tests. Add stop/restart and late-reply checks
when a backend owns state or asynchronous work.
