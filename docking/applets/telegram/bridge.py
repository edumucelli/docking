"""Telegram login landmarks; no private modules, messages, or contacts are read."""

from docking.applets.webapp_bridge import build_bridge_script

BRIDGE_HANDLER = "dockingTelegram"
# Telegram's startup waits for animation frames, which WebKit pauses in an
# unmapped window. Keep pending frames progressing slowly while hidden, without
# changing document visibility or marking the user as active. Visible windows
# continue to use native frame scheduling. No Telegram internals are accessed.
BACKGROUND_FRAMES_SCRIPT = """
(() => {
    'use strict';
    if (window.__dockingTelegramFramesInstalled) return;
    window.__dockingTelegramFramesInstalled = true;
    const nativeRequest = window.requestAnimationFrame.bind(window);
    const nativeCancel = window.cancelAnimationFrame.bind(window);
    const pending = new Map();
    let suspended = false;
    const finish = (id, timestamp) => {
        const frame = pending.get(id);
        if (!frame) return;
        pending.delete(id);
        window.clearTimeout(frame.timer);
        if (frame.nativeId) nativeCancel(frame.nativeId);
        frame.callback(timestamp);
    };
    const onNativeFrame = (id, timestamp) => {
        const frame = pending.get(id);
        if (!frame) return;
        frame.nativeId = 0;
        if (document.hidden) arm(id, frame);
        else finish(id, timestamp);
    };
    const queueNativeFrame = (id, frame) => {
        if (!frame.nativeId) {
            frame.nativeId = nativeRequest(timestamp => onNativeFrame(id, timestamp));
        }
    };
    const arm = (id, frame) => {
        if (!document.hidden || frame.timer) return;
        frame.timer = window.setTimeout(() => {
            frame.timer = 0;
            if (document.hidden) finish(id, performance.now());
            else queueNativeFrame(id, frame);
        }, 100);
    };
    window.requestAnimationFrame = callback => {
        if (suspended) return nativeRequest(callback);
        const id = nativeRequest(timestamp => onNativeFrame(id, timestamp));
        const frame = {callback, timer: 0, nativeId: id};
        pending.set(id, frame);
        if (document.hidden) {
            nativeCancel(id);
            frame.nativeId = 0;
        }
        arm(id, frame);
        return id;
    };
    window.cancelAnimationFrame = id => {
        const frame = pending.get(id);
        if (frame) {
            window.clearTimeout(frame.timer);
            if (frame.nativeId) nativeCancel(frame.nativeId);
        } else nativeCancel(id);
        pending.delete(id);
    };
    document.addEventListener('visibilitychange', () => {
        for (const [id, frame] of pending) {
            if (document.hidden) {
                if (frame.nativeId) nativeCancel(frame.nativeId);
                frame.nativeId = 0;
                arm(id, frame);
            } else queueNativeFrame(id, frame);
        }
    });
    window.addEventListener('pagehide', () => {
        suspended = true;
        for (const [id, frame] of pending) {
            window.clearTimeout(frame.timer);
            if (frame.nativeId) nativeCancel(frame.nativeId);
        }
        pending.clear();
    });
    window.addEventListener('pageshow', () => { suspended = false; });
})();
""".strip()

BRIDGE_SCRIPT = (
    BACKGROUND_FRAMES_SCRIPT
    + "\n"
    + build_bridge_script(
        handler=BRIDGE_HANDLER,
        marker="__dockingTelegramBridgeInstalled",
        ready_selector="#column-left .chatlist, #LeftColumn .chat-list",
        login_selector="#auth-pages, .auth-pages, #auth-qr, #auth-phone-number, .Auth",
    )
)
