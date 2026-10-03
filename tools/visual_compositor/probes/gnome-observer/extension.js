import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const XML = `<node><interface name="org.docking.VisualLab.Gnome1">
  <method name="GetGeometry"><arg type="s" direction="out"/></method>
  <method name="ListWindows"><arg type="s" direction="out"/></method>
  <method name="ListWorkspaces"><arg type="s" direction="out"/></method>
  <method name="CaptureOutput"><arg type="u" direction="in"/><arg type="s" direction="in"/><arg type="b" direction="out"/></method>
  <method name="SetBackground"><arg type="s" direction="in"/></method>
</interface></node>`;

export default class VisualObserver extends Extension {
    enable() {
        // This session is disposable. Keep the canvas static and panel-free so
        // the independent pixel oracle can distinguish the dock from Shell UI.
        new Gio.Settings({schema_id: 'org.gnome.desktop.interface'})
            .set_boolean('enable-animations', false);
        const background = new Gio.Settings({schema_id: 'org.gnome.desktop.background'});
        background.set_string('picture-uri', '');
        background.set_string('picture-uri-dark', '');
        background.set_string('primary-color', '#000000');
        background.set_string('picture-options', 'none');
        Main.overview.hide();
        Main.panel.hide();
        new Gio.Settings({schema_id: 'org.gnome.mutter'})
            .set_strv('experimental-features', ['scale-monitor-framebuffer']);
        new Gio.Settings({schema_id: 'org.gnome.mutter'})
            .set_boolean('dynamic-workspaces', false);
        new Gio.Settings({schema_id: 'org.gnome.desktop.wm.preferences'})
            .set_int('num-workspaces', 2);
        this._dbus = Gio.DBusExportedObject.wrapJSObject(XML, this);
        this._dbus.export(Gio.DBus.session, '/org/docking/VisualLab/Gnome');
        this._owner = Gio.bus_own_name_on_connection(Gio.DBus.session,
            'org.docking.VisualLab.Gnome', Gio.BusNameOwnerFlags.NONE, null, null);
    }

    disable() {
        this._dbus?.unexport();
        if (this._owner)
            Gio.bus_unown_name(this._owner);
        Main.panel.show();
    }

    _windows() {
        return global.get_window_actors().map(actor => actor.meta_window);
    }

    ListWindows() {
        return JSON.stringify(this._windows().map(window => ({
            title: window.get_title(), app_id: window.get_wm_class(),
            active: window === global.display.focus_window,
            minimized: window.minimized,
            workspace: window.get_workspace()?.index(),
        })));
    }

    ListWorkspaces() {
        return JSON.stringify(Array.from({length: global.workspace_manager.n_workspaces}, (_, index) => ({
            id: String(index), name: String(index + 1),
            active: index === global.workspace_manager.get_active_workspace_index(),
        })));
    }

    SetBackground(color) {
        new Gio.Settings({schema_id: 'org.gnome.desktop.background'}).set_string('primary-color', color);
    }

    async CaptureOutputAsync([index, path], invocation) {
        try {
            const rect = global.display.get_monitor_geometry(index);
            const stream = Gio.File.new_for_path(path).replace(null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
            await new Shell.Screenshot().screenshot_area(rect.x, rect.y, rect.width, rect.height, stream);
            stream.close(null);
            invocation.return_value(new GLib.Variant('(b)', [true]));
        } catch (error) {
            console.error(error);
            invocation.return_value(new GLib.Variant('(b)', [false]));
        }
    }

    GetGeometry() {
        const outputs = [];
        for (let index = 0; index < global.display.get_n_monitors(); index++) {
            const rect = global.display.get_monitor_geometry(index);
            outputs.push({name: `output-${index}`, x: rect.x, y: rect.y,
                width: rect.width, height: rect.height,
                scale: global.display.get_monitor_scale(index)});
        }
        const docks = this._windows().filter(window => window.get_title() === 'Docking');
        const frame = docks.length === 1 ? docks[0].get_frame_rect() : null;
        return JSON.stringify({outputs, overview_visible: Main.overview.visible, dock_rect: frame ? {
            x: frame.x, y: frame.y, width: frame.width, height: frame.height,
        } : null});
    }
}
