// Owned KWin 6 script. Only this script is loaded/run/unloaded by Docking.
const destination = __DESTINATION__;
const path = __PATH__;
const plugin = __PLUGIN__;
const ownPid = __PID__;
const iface = "org.docking.KWinBridge";
let busy = false;
let dirty = false;
const watched = new WeakSet();

function connect(object, name, callback) {
    // KWin versions expose different optional signals. Probe the real API.
    const signal = object[name];
    if (signal && typeof signal.connect === "function") signal.connect(callback);
}

function watch(window) {
    if (watched.has(window)) return;
    watched.add(window);
    ["frameGeometryChanged", "activeChanged", "minimizedChanged",
     "fullScreenChanged", "desktopsChanged", "activitiesChanged",
     "hiddenChanged", "captionChanged", "skipTaskbarChanged",
     "maximizedChanged", "windowClassChanged", "desktopFileNameChanged",
     "closeableChanged", "minimizeableChanged"].forEach(name => connect(window, name, publish));
}

function publish() {
    if (busy) { dirty = true; return; }
    busy = true;
    dirty = false;
    callDBus("org.freedesktop.DBus", "/org/freedesktop/DBus",
             "org.freedesktop.DBus", "NameHasOwner", destination, function(alive) {
        if (!alive) {
            callDBus("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
                     "unloadScript", plugin);
            return;
        }
        const windows = workspace.stackingOrder.filter(window =>
            !window.deleted && !window.specialWindow && window.pid !== ownPid);
        const rows = windows.map(function(window) {
            watch(window);
            const id = String(window.internalId);
            const rect = window.frameGeometry;
            const desktops = window.desktops.map(desktop => desktop.id);
            const activityVisible = !window.activities.length ||
                window.activities.indexOf(workspace.currentActivity) !== -1;
            const currentDesktop = workspace.currentDesktop.id;
            const current = window.onAllDesktops || desktops.indexOf(currentDesktop) !== -1;
            return {
                id: id, title: window.caption, app_id: window.desktopFileName,
                wm_class: window.resourceClass, pid: window.pid,
                active: window.active, minimized: window.minimized,
                fullscreen: window.fullScreen,
                maximized: window.maximizeMode === 3,
                geometry: [rect.x, rect.y, rect.width, rect.height],
                workspace_id: current ? currentDesktop : (desktops.length ? desktops[0] : null),
                sticky: window.onAllDesktops, dialog: window.dialog,
                on_current_workspace: current && activityVisible,
                visible: !window.minimized && !window.hidden && activityVisible && current,
                can_activate: true, can_minimize: window.minimizable,
                can_close: window.closeable, taskbar: !window.skipTaskbar,
            };
        });
        callDBus(destination, path, iface, "Publish", JSON.stringify(rows), function() {
            busy = false;
            if (dirty) publish();
        });
    });
}

connect(workspace, "windowAdded", window => { watch(window); publish(); });
["windowRemoved", "windowActivated", "currentDesktopChanged", "currentActivityChanged",
 "stackingOrderChanged"].forEach(name => connect(workspace, name, publish));
workspace.stackingOrder.forEach(watch);
publish();
