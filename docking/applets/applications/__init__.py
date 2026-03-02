"""Applications applet public API."""

from .applet import ApplicationsApplet
from .state import _build_app_categories

__all__ = ["ApplicationsApplet", "_build_app_categories"]
