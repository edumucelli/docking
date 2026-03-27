"""Scaffold a new applet package.

Usage::

    python -m docking.scaffold myapplet
    python -m docking.scaffold myapplet --category WELLNESS
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from docking.applets.identity import AppletCategory

_ROOT = Path(__file__).resolve().parent
_APPLETS_DIR = _ROOT / "applets"
_TESTS_DIR = _ROOT.parent / "tests" / "applets"

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_INIT_PY = '''\
"""Public surface for the {display} applet."""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="{aid}",
    name="{display}",
    category=AppletCategory.{category},
)

from .applet import {class_name}  # noqa: E402, F401

__all__ = ["meta", "{class_name}"]
'''

_STATE_PY = '''\
"""Pure state and formatting logic for {display} applet."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from docking.i18n import _


@dataclass(frozen=True, slots=True)
class {state_class}:
    """State for {display} applet."""


def state_from_prefs(prefs: Mapping[str, Any] | None) -> {state_class}:
    """Build state from persisted preferences."""
    return {state_class}()


def prefs_from_state(state: {state_class}) -> dict[str, object]:
    """Return preferences payload to persist."""
    return {{}}


def tooltip_text() -> str:
    """Build tooltip string."""
    return _("{display}")
'''

_RENDER_PY = '''\
"""Pure Cairo rendering for {display} applet icon."""

from __future__ import annotations

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from docking.applets.base import load_theme_icon


def render_icon(*, size: int) -> GdkPixbuf.Pixbuf | None:
    """Render applet icon at the given size."""
    return load_theme_icon(name="{icon_name}", size=size)
'''

_APPLET_PY = '''\
"""GTK lifecycle glue for {display} applet."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from docking.applets.{aid} import meta as _meta
from docking.applets.base import Applet
from docking.applets.{aid}.render import render_icon
from docking.applets.{aid}.state import tooltip_text
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config


class {class_name}(Applet):
    """{display} applet."""

    id = _meta.id
    name = _("{display}")
    icon_name = "{icon_name}"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        super().__init__(icon_size, config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size)

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text()
'''

_TEST_PY = '''\
"""Tests for the {display} applet."""

from __future__ import annotations

from docking.applets.{aid} import {class_name}


class Test{class_name}:
    def test_creates_with_icon(self):
        applet = {class_name}(48)
        assert applet.item.icon is not None

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = {class_name}(size)
            pixbuf = applet.create_icon(size=size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size
'''

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_class_name(aid: str) -> str:
    """Convert 'myapplet' to 'MyappletApplet'."""
    return aid.capitalize() + "Applet"


def _to_display(aid: str) -> str:
    """Convert 'myapplet' to 'Myapplet'."""
    return aid.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Scaffold a new docking applet")
    parser.add_argument("name", help="Applet id (lowercase, no spaces)")
    parser.add_argument(
        "--category",
        choices=[c.name for c in AppletCategory],
        default="OTHER",
        help="Applet category (default: OTHER)",
    )
    args = parser.parse_args()

    aid: str = args.name.lower().replace("-", "").replace("_", "")
    category: str = args.category
    class_name = _to_class_name(aid)
    state_class = aid.capitalize() + "State"
    display = _to_display(aid)
    icon_name = "application-x-executable"

    pkg_dir = _APPLETS_DIR / aid
    if pkg_dir.exists():
        print(f"ERROR: {pkg_dir} already exists", file=sys.stderr)
        sys.exit(1)

    ctx = {
        "aid": aid,
        "display": display,
        "class_name": class_name,
        "state_class": state_class,
        "category": category,
        "icon_name": icon_name,
    }

    # Create package.
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text(_INIT_PY.format(**ctx))
    (pkg_dir / "state.py").write_text(_STATE_PY.format(**ctx))
    (pkg_dir / "render.py").write_text(_RENDER_PY.format(**ctx))
    (pkg_dir / "applet.py").write_text(_APPLET_PY.format(**ctx))

    # Create test.
    _TESTS_DIR.mkdir(parents=True, exist_ok=True)
    test_file = _TESTS_DIR / f"test_{aid}.py"
    test_file.write_text(_TEST_PY.format(**ctx))

    print(f"Created applet '{aid}' at {pkg_dir.relative_to(_ROOT.parent)}")
    print(f"Created test at {test_file.relative_to(_ROOT.parent)}")
    print()
    print("Next steps:")
    print(f"  1. Edit {pkg_dir.name}/state.py - add your state logic")
    print(f"  2. Edit {pkg_dir.name}/render.py - customize the icon")
    print(f"  3. Edit {pkg_dir.name}/applet.py - add lifecycle behavior")
    print(f"  4. Run: python -m pytest tests/applets/test_{aid}.py -v")


if __name__ == "__main__":
    main()
