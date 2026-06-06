import Cairo from "cairo";
import Gio from "gi://Gio";
import GLib from "gi://GLib";
import Meta from "gi://Meta";
import Shell from "gi://Shell";
import {Extension} from "resource:///org/gnome/shell/extensions/extension.js";

const BUS_NAME = "org.docking.Docking.GnomeShellBridge";
const OBJECT_PATH = "/org/docking/Docking/GnomeShellBridge";
const INTERFACE = "org.docking.Docking.GnomeShellBridge1";

const INTROSPECTION_XML = `
<node>
  <interface name="${INTERFACE}">
    <method name="ListWindows">
      <arg name="windows" type="s" direction="out"/>
    </method>
    <method name="ListWorkspaces">
      <arg name="workspaces" type="s" direction="out"/>
    </method>
    <method name="Activate">
      <arg name="id" type="u" direction="in"/>
      <arg name="ok" type="b" direction="out"/>
    </method>
    <method name="Minimize">
      <arg name="id" type="u" direction="in"/>
      <arg name="ok" type="b" direction="out"/>
    </method>
    <method name="Close">
      <arg name="id" type="u" direction="in"/>
      <arg name="ok" type="b" direction="out"/>
    </method>
    <method name="ActivateWorkspace">
      <arg name="id" type="u" direction="in"/>
      <arg name="ok" type="b" direction="out"/>
    </method>
    <method name="PositionDock">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
      <arg name="width" type="i" direction="in"/>
      <arg name="height" type="i" direction="in"/>
      <arg name="ok" type="b" direction="out"/>
    </method>
    <method name="CaptureWindow">
      <arg name="id" type="u" direction="in"/>
      <arg name="width" type="i" direction="in"/>
      <arg name="height" type="i" direction="in"/>
      <arg name="png_base64" type="s" direction="out"/>
    </method>
    <method name="ShowDesktop">
      <arg name="ok" type="b" direction="out"/>
    </method>
    <signal name="Changed"/>
  </interface>
</node>`;

export default class DockingBridgeExtension extends Extension {
  enable() {
    this._dbusImpl = Gio.DBusExportedObject.wrapJSObject(INTROSPECTION_XML, this);
    this._dbusImpl.export(Gio.DBus.session, OBJECT_PATH);
    this._ownerId = Gio.bus_own_name_on_connection(
      Gio.DBus.session,
      BUS_NAME,
      Gio.BusNameOwnerFlags.NONE,
      null,
      null
    );

    this._nextId = 1;
    this._idByWindow = new Map();
    this._windowById = new Map();
    this._signals = [];
    this._dockConfigured = false;
    this._configuredDockWindow = null;
    this._dockConfigureTimerId = 0;
    this._changedSourceId = 0;
    this._connect(global.display, "window-created", () => this._queueChanged());
    this._connect(global.display, "notify::focus-window", () => this._queueChanged());
    this._connect(global.workspace_manager, "workspace-switched", () => this._queueChanged());

    this._refreshWindowSignals();
    this._queueChanged();

    // Proactively hide the dock window from Alt+Tab after a GNOME
    // Shell restart (when PositionDock is not re-called).  We must
    // NOT call set_type(DOCK) here — Mutter may re-centre the window
    // when its type changes, and PositionDock has not run yet.
    // The full configuration happens in PositionDock.
    this._dockConfigureRetries = 10;
    this._dockConfigureTimerId = GLib.timeout_add(
      GLib.PRIORITY_DEFAULT, 500, () => {
        if (this._dockConfigured) {
          this._dockConfigureTimerId = 0;
          return GLib.SOURCE_REMOVE;
        }
        const win = this._findDockWindow();
        if (win) {
          this._hideDockFromSwitcher(win);
          // Don't set _dockConfigured — PositionDock will do the
          // full config when it arrives.
          this._dockConfigureTimerId = 0;
          return GLib.SOURCE_REMOVE;
        }
        this._dockConfigureRetries -= 1;
        if (this._dockConfigureRetries <= 0) {
          this._dockConfigureTimerId = 0;
          return GLib.SOURCE_REMOVE;
        }
        return GLib.SOURCE_CONTINUE;
      }
    );
  }

  disable() {
    if (this._dockConfigureTimerId) {
      GLib.source_remove(this._dockConfigureTimerId);
      this._dockConfigureTimerId = 0;
    }
    if (this._changedSourceId) {
      GLib.source_remove(this._changedSourceId);
      this._changedSourceId = 0;
    }

    this._dockConfigured = false;
    this._configuredDockWindow = null;

    for (const [object, signalId] of this._signals)
      object.disconnect(signalId);
    this._signals = [];
    this._idByWindow = null;
    this._windowById = null;

    if (this._ownerId) {
      Gio.bus_unown_name(this._ownerId);
      this._ownerId = 0;
    }
    if (this._dbusImpl) {
      this._dbusImpl.unexport();
      this._dbusImpl = null;
    }
  }

  ListWindows() {
    this._refreshWindowSignals();
    return JSON.stringify(this._listWindows());
  }

  ListWorkspaces() {
    const manager = global.workspace_manager;
    const active = manager.get_active_workspace_index();
    const rows = [];
    for (let index = 0; index < manager.n_workspaces; index++) {
      const workspace = manager.get_workspace_by_index(index);
      rows.push({
        "id": index,
        "index": index,
        "name": workspace?.get_name?.() || `${index + 1}`,
        "active": index === active,
      });
    }
    return JSON.stringify(rows);
  }

  Activate(id) {
    const window = this._windowById.get(id);
    if (!window)
      return false;
    if (window.minimized && typeof window.unminimize === "function")
      window.unminimize();
    window.activate(global.get_current_time());
    return true;
  }

  Minimize(id) {
    const window = this._windowById.get(id);
    if (!window || !window.minimize)
      return false;
    window.minimize();
    return true;
  }

  Close(id) {
    const window = this._windowById.get(id);
    if (!window || !window.delete)
      return false;
    window.delete(global.get_current_time());
    return true;
  }

  ActivateWorkspace(id) {
    const workspace = global.workspace_manager.get_workspace_by_index(id);
    if (!workspace)
      return false;
    workspace.activate(global.get_current_time());
    return true;
  }

  ShowDesktop() {
    // Toggle show-desktop: minimise all minimisable windows on the
    // active workspace when any are visible, or restore them when all
    // are already minimised.
    const workspace = global.workspace_manager.get_active_workspace();
    const windows = global.display.get_tab_list(
      Meta.TabList.NORMAL_ALL, workspace
    );
    const minimisable = windows.filter(
      w => w.can_minimize() && !w.skip_taskbar
    );
    if (minimisable.length === 0)
      return false;

    const anyVisible = minimisable.some(w => !w.minimized);
    if (anyVisible) {
      for (const w of minimisable)
        w.minimize();
    } else {
      for (const w of minimisable)
        w.unminimize();
    }
    return true;
  }

  PositionDock(x, y, width, height) {
    const win = this._findDockWindow();
    if (!win)
      return false;
    if (!this._dockConfigured || this._configuredDockWindow !== win) {
      this._configureDockWindow(win);
      this._dockConfigured = true;
      this._configuredDockWindow = win;
    }
    // gravity 0 = NORTH_WEST: (x,y) is the top-left corner
    win.move_resize_frame(0, x, y, width, height);
    return true;
  }

  _hideDockFromSwitcher(win) {
    // Hide from Alt+Tab *without* changing the window type.
    // Safe to call before the window is positioned — set_type(DOCK)
    // must wait until PositionDock arrives, otherwise Mutter may
    // re-centre the window.
    if (typeof win.set_skip_taskbar === "function")
      win.set_skip_taskbar(true);
    if (typeof win.hide_from_window_list === "function")
      win.hide_from_window_list();
    if (typeof win.make_above === "function")
      win.make_above();
  }

  _configureDockWindow(win) {
    // Full dock configuration — called from PositionDock after the
    // window has been positioned.  Safe to set the type now.
    this._hideDockFromSwitcher(win);
    if (typeof win.set_type === "function")
      win.set_type(Meta.WindowType.DOCK);
  }

  CaptureWindow(id, width, height) {
    const window = this._windowById.get(id);
    if (!window)
      return "";
    try {
      return this._captureWindowImage(id, window, width, height);
    } catch (e) {
      log("Docking bridge: CaptureWindow failed: " + e);
      return "";
    }
  }

  _captureWindowImage(id, window, width, height) {
    const actor = window.get_compositor_private();
    if (!actor || typeof actor.get_image !== "function")
      return "";

    // Capture the window content at its native size.
    const fullSurface = actor.get_image(null);
    if (!fullSurface)
      return "";

    const srcW = fullSurface.getWidth();
    const srcH = fullSurface.getHeight();
    if (srcW <= 0 || srcH <= 0) {
      // cairo surfaces must be explicitly destroyed
      return "";
    }

    // Scale to cover the requested thumbnail size (fill, not fit).
    // Excess is cropped equally on both sides.
    const scale = Math.max(width / srcW, height / srcH);
    const destW = Math.max(1, Math.floor(srcW * scale));
    const destH = Math.max(1, Math.floor(srcH * scale));
    const offsetX = Math.floor((width - destW) / 2);
    const offsetY = Math.floor((height - destH) / 2);

    const thumb = new Cairo.ImageSurface(Cairo.Format.ARGB32, width, height);
    const cr = new Cairo.Context(thumb);

    // Fill opaque black behind any source transparency.
    cr.setSourceRGBA(0, 0, 0, 1);
    cr.paint();

    cr.translate(offsetX, offsetY);
    cr.scale(scale, scale);
    cr.setSourceSurface(fullSurface, 0, 0);
    cr.paint();

    // Write the thumbnail to a temporary PNG file
    const tmpPath = GLib.build_filenamev([
      GLib.get_tmp_dir(),
      `docking-preview-${id}.png`,
    ]);
    thumb.writeToPNG(tmpPath);

    const [ok, contents] = GLib.file_get_contents(tmpPath);
    GLib.unlink(tmpPath);

    if (!ok || !contents)
      return "";

    return GLib.base64_encode(contents);
  }

  _findDockWindow() {
    for (const actor of global.get_window_actors()) {
      const win = actor.meta_window;
      const wmClass = win.get_wm_class();
      const title = win.get_title();
      if (wmClass === "Docking" || title === "Docking")
        return win;
    }
    return null;
  }

  _connect(object, signal, callback) {
    this._signals.push([object, object.connect(signal, callback)]);
  }

  _refreshWindowSignals() {
    const current = new Set(global.get_window_actors().map(actor => actor.meta_window));
    for (const window of current) {
      if (this._idByWindow.has(window))
        continue;
      this._idByWindow.set(window, this._nextId);
      this._windowById.set(this._nextId, window);
      this._nextId += 1;
      this._connect(window, "unmanaged", () => this._forgetWindow(window));
      this._connect(window, "notify::title", () => this._queueChanged());
      this._connect(window, "notify::minimized", () => this._queueChanged());
      this._connect(window, "position-changed", () => this._queueChanged());
      this._connect(window, "size-changed", () => this._queueChanged());
    }
  }

  _forgetWindow(window) {
    const id = this._idByWindow.get(window);
    if (id)
      this._windowById.delete(id);
    this._idByWindow.delete(window);
    if (this._configuredDockWindow === window) {
      this._configuredDockWindow = null;
      this._dockConfigured = false;
    }
    this._queueChanged();
  }

  _listWindows() {
    const tracker = Shell.WindowTracker.get_default();
    const rows = [];
    for (const actor of global.get_window_actors()) {
      const window = actor.meta_window;
      if (!this._shouldExportWindow(window))
        continue;
      const id = this._idForWindow(window);
      const rect = window.get_frame_rect();
      const workspace = window.get_workspace();
      const app = tracker.get_window_app(window);
      rows.push({
        "id": id,
        "title": window.get_title() || "Window",
        "app-id": this._appIdFor(app, window),
        "active": global.display.focus_window === window,
        "minimized": Boolean(window.minimized),
        "maximized": this._isMaximized(window),
        "fullscreen": Boolean(window.fullscreen),
        "monitor": window.get_monitor(),
        "workspace": workspace ? workspace.index() : -1,
        "x": rect.x,
        "y": rect.y,
        "width": rect.width,
        "height": rect.height,
      });
    }
    return rows;
  }

  _idForWindow(window) {
    let id = this._idByWindow.get(window);
    if (!id) {
      id = this._nextId;
      this._nextId += 1;
      this._idByWindow.set(window, id);
      this._windowById.set(id, window);
    }
    return id;
  }

  _shouldExportWindow(window) {
    if (!window || window.skip_taskbar)
      return false;
    // Never export the docking surface itself — it is a panel, not a
    // managed application window.
    if (window.get_wm_class() === "Docking" || window.get_title() === "Docking")
      return false;
    const type = window.get_window_type();
    return type === Meta.WindowType.NORMAL || type === Meta.WindowType.DIALOG;
  }

  _appIdFor(app, window) {
    if (app?.get_id)
      return app.get_id() || "";
    if (window.get_wm_class)
      return window.get_wm_class() || "";
    return "";
  }

  _isMaximized(window) {
    if (typeof window.get_maximized === "function") {
      const flags = Meta.MaximizeFlags;
      return Boolean(window.get_maximized() & (flags.HORIZONTAL | flags.VERTICAL));
    }
    // GNOME < 46 fallback
    if ("maximized_horizontally" in window || "maximized_vertically" in window)
      return Boolean(window.maximized_horizontally || window.maximized_vertically);
    return false;
  }

  _queueChanged() {
    if (this._changedSourceId)
      return;
    this._changedSourceId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
      this._changedSourceId = 0;
      if (this._dbusImpl)
        this._dbusImpl.emit_signal("Changed", null);
      return GLib.SOURCE_REMOVE;
    });
  }
}
