"""Small, validated bridge between web applications and the applet process.

The injected script intentionally uses browser-facing APIs and coarse DOM
landmarks only. It does not import private modules, inspect conversations, or
move message/contact data across the browser process boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

BRIDGE_VERSION = 1
MAX_BRIDGE_MESSAGE_BYTES = 4096
MAX_BADGE_COUNT = 999_999

AuthState = Literal["unknown", "login_required", "ready"]
BridgeKind = Literal["badge", "status", "activate"]


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
    if not raw:
        return None
    try:
        if len(raw.encode("utf-8")) > MAX_BRIDGE_MESSAGE_BYTES:
            return None
    except UnicodeError:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != BRIDGE_VERSION:
        return None

    kind = payload.get("kind")
    if kind == "activate":
        return BridgeEvent(kind="activate")
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


def build_bridge_script(
    *, handler: str, marker: str, ready_selector: str, login_selector: str
) -> str:
    """Observe public browser APIs and visible login/app landmarks only."""
    return f"""
    (() => {{
        'use strict';
        if (window[{json.dumps(marker)}]) return;
        Object.defineProperty(window, {json.dumps(marker)}, {{
            value: true,
            configurable: false,
            enumerable: false,
        }});

        const handler = window.webkit?.messageHandlers?.{handler}
            ?? window.chrome?.webview;
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

        // Observe only standard activation events. No notification text crosses
        // this bridge; each engine owns its native presentation and DOM lifetime.
        if (typeof window.Notification === 'function') {{
            const notifications = new Set();
            const NativeNotification = window.Notification;
            window.Notification = new Proxy(NativeNotification, {{
                construct(target, args, newTarget) {{
                    const notification = Reflect.construct(target, args, newTarget);
                    notifications.add(notification);
                    notification.addEventListener('click', () =>
                        post({{kind: 'activate'}}));
                    notification.addEventListener('close', () =>
                        notifications.delete(notification));
                    return notification;
                }},
            }});
            window.addEventListener('pagehide', () => {{
                for (const notification of notifications) notification.close();
                notifications.clear();
            }}, {{once: true}});
        }}

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
            return arguments.length > 0
                ? originalSetBadge(contents) : originalSetBadge();
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
            const visible = selector => Array.from(document.querySelectorAll(selector))
                .some(element => element.getClientRects().length > 0);
            const ready = visible({json.dumps(ready_selector)});
            const qr = !ready && visible({json.dumps(login_selector)});
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
