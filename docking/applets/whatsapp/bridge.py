"""WhatsApp's narrow public-browser bridge and visible login landmarks."""

from docking.applets.webapp_bridge import (
    BRIDGE_VERSION,
    MAX_BADGE_COUNT,
    MAX_BRIDGE_MESSAGE_BYTES,
    BridgeEvent,
    build_bridge_script,
    parse_bridge_message,
)

BRIDGE_HANDLER = "dockingWhatsApp"
BRIDGE_SCRIPT = build_bridge_script(
    handler=BRIDGE_HANDLER,
    marker="__dockingWhatsAppBridgeInstalled",
    ready_selector="#pane-side",
    login_selector="canvas[aria-label], canvas[data-ref], [data-ref] canvas",
)
__all__ = [
    "BRIDGE_HANDLER",
    "BRIDGE_SCRIPT",
    "BRIDGE_VERSION",
    "MAX_BADGE_COUNT",
    "MAX_BRIDGE_MESSAGE_BYTES",
    "BridgeEvent",
    "parse_bridge_message",
]
