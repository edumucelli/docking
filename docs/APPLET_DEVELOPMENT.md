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
