"""Presentation state helpers for the System Tray applet."""

from __future__ import annotations

from docking.i18n import _
from docking.platform.tray.state import TrayState


def tooltip_text(state: TrayState) -> str:
    if not state.available:
        if state.error:
            return _("System Tray: {error}").format(error=state.error)
        return _("System Tray unavailable")
    if not state.items:
        if state.legacy_owner:
            return _("System Tray: legacy tray owned by {owner}").format(
                owner=state.legacy_owner
            )
        if state.mode == "host":
            return _("System Tray: waiting for tray apps")
        return _("System Tray: no tray apps")
    lines = [_("System Tray: {n} item(s)").format(n=len(state.items))]
    for item in state.items[:6]:
        lines.append(f"- {item.display_title}")
    if len(state.items) > 6:
        lines.append(_("and {n} more").format(n=len(state.items) - 6))
    return "\n".join(lines)
