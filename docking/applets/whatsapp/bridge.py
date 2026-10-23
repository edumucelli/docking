"""Small, validated bridge between WhatsApp Web and the applet process.

The injected script intentionally uses browser-facing APIs and coarse DOM
landmarks only. It does not import WhatsApp modules, inspect conversations, or
move message/contact data across the WebKit process boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

BRIDGE_HANDLER = "dockingWhatsApp"
BRIDGE_VERSION = 1
MAX_BRIDGE_MESSAGE_BYTES = 4096
MAX_BADGE_COUNT = 999_999

AuthState = Literal["unknown", "login_required", "ready"]
BridgeKind = Literal["badge", "status"]


@dataclass(frozen=True, slots=True)
class BridgeEvent:
    """One schema-checked event received from the page."""

    kind: BridgeKind
    badge_count: int | None = None
    badge_visible: bool = False
    online: bool | None = None
    auth: AuthState = "unknown"


def parse_bridge_message(raw: str) -> BridgeEvent | None:
    """Parse one bounded bridge payload and reject unknown shapes."""
    if not raw or len(raw.encode("utf-8")) > MAX_BRIDGE_MESSAGE_BYTES:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != BRIDGE_VERSION:
        return None

    kind = payload.get("kind")
    if kind == "badge":
        visible = payload.get("visible")
        count = payload.get("count")
        if not isinstance(visible, bool):
            return None
        if count is not None and (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 0 <= count <= MAX_BADGE_COUNT
        ):
            return None
        return BridgeEvent(
            kind="badge",
            badge_count=count,
            badge_visible=visible,
        )

    if kind == "status":
        online = payload.get("online")
        auth = payload.get("auth")
        if not isinstance(online, bool) or auth not in {
            "unknown",
            "login_required",
            "ready",
        }:
            return None
        return BridgeEvent(kind="status", online=online, auth=auth)
    return None


# This runs in the default script world because it needs to wrap Navigator's
# public Badging API as observed by the page. The handler accepts only the
# narrow schema above, and WebKit limits injection to web.whatsapp.com.
BRIDGE_SCRIPT = f"""
(() => {{
    'use strict';
    if (window.__dockingWhatsAppBridgeInstalled) return;
    Object.defineProperty(window, '__dockingWhatsAppBridgeInstalled', {{
        value: true,
        configurable: false,
        enumerable: false,
    }});

    const handler = window.webkit?.messageHandlers?.{BRIDGE_HANDLER};
    if (!handler) return;

    const post = (payload) => {{
        try {{
            handler.postMessage(JSON.stringify({{
                version: {BRIDGE_VERSION},
                ...payload,
            }}));
        }} catch (_error) {{
            // The native side may already be shutting down.
        }}
    }};

    const originalSetBadge = typeof navigator.setAppBadge === 'function'
        ? navigator.setAppBadge.bind(navigator)
        : null;
    const originalClearBadge = typeof navigator.clearAppBadge === 'function'
        ? navigator.clearAppBadge.bind(navigator)
        : null;

    const setBadge = function(contents) {{
        let count = null;
        if (arguments.length > 0) {{
            const numeric = Number(contents);
            if (Number.isFinite(numeric) && numeric >= 0) {{
                count = Math.min({MAX_BADGE_COUNT}, Math.floor(numeric));
            }}
        }}
        post({{kind: 'badge', count, visible: count === null || count > 0}});
        if (!originalSetBadge) return Promise.resolve();
        return arguments.length > 0 ? originalSetBadge(contents) : originalSetBadge();
    }};

    const clearBadge = function() {{
        post({{kind: 'badge', count: 0, visible: false}});
        return originalClearBadge ? originalClearBadge() : Promise.resolve();
    }};

    const installNavigatorMethod = (name, value) => {{
        try {{
            Object.defineProperty(navigator, name, {{
                value,
                configurable: true,
                enumerable: false,
            }});
            return;
        }} catch (_error) {{
            // Fall through to assignment for older WebKit releases.
        }}
        try {{ navigator[name] = value; }} catch (_error) {{}}
    }};
    installNavigatorMethod('setAppBadge', setBadge);
    installNavigatorMethod('clearAppBadge', clearBadge);

    let lastStatus = '';
    const reportStatus = () => {{
        const ready = document.querySelector('#pane-side') !== null;
        const qr = !ready && document.querySelector(
            'canvas[aria-label], canvas[data-ref], [data-ref] canvas'
        ) !== null;
        const payload = {{
            kind: 'status',
            online: navigator.onLine,
            auth: ready ? 'ready' : (qr ? 'login_required' : 'unknown'),
        }};
        const serialized = JSON.stringify(payload);
        if (serialized !== lastStatus) {{
            lastStatus = serialized;
            post(payload);
        }}
    }};
    window.addEventListener('online', reportStatus);
    window.addEventListener('offline', reportStatus);
    let statusInterval = 0;
    const startStatus = () => {{
        reportStatus();
        statusInterval = window.setInterval(reportStatus, 2000);
    }};
    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', startStatus, {{once: true}});
    }} else {{
        startStatus();
    }}
    window.addEventListener('pagehide', () => {{
        if (statusInterval) window.clearInterval(statusInterval);
    }}, {{once: true}});
}})();
""".strip()
