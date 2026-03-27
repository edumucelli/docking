"""Public surface for the AI Usage applet."""

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="aiusage",
    name="AI Usage",
    category=AppletCategory.PRODUCTIVITY,
)

# Re-export for convenience.
from .applet import AiUsageApplet

__all__ = ["AiUsageApplet", "meta"]
