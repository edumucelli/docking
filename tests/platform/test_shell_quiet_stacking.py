"""Execute the actual GNOME and Cinnamon stacking code against a compositor model."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.core.position import Position
from docking.platform.backends.base import MonitorSnapshot, PlacementRequest, Rect, Size
from docking.platform.backends.cinnamon.shell import CinnamonShellClient


@pytest.mark.parametrize("backend", ["gnome", "cinnamon"])
def test_quiet_shell_stacking_recovers_and_releases_sources(backend):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Shell JavaScript execution requires Node.js")
    setup = """
const assert = require('node:assert/strict');
class Extension {}
class Signals {
    constructor() { this.signals = new Map(); this.next = 0; }
    connect(name, callback) {
        const id = ++this.next; this.signals.set(id, [name, callback]); return id;
    }
    disconnect(id) { assert(this.signals.delete(id)); }
    emit(name) {
        for (const [signal, callback] of [...this.signals.values()])
            if (signal === name) callback();
    }
}
const pending = new Map(); let nextSource = 0;
const GLib = {
    PRIORITY_HIGH: -100, SOURCE_REMOVE: false,
    idle_add(priority, callback) {
        assert.equal(priority, -100);
        const id = ++nextSource; pending.set(id, callback); return id;
    },
    source_remove(id) { assert(pending.delete(id)); }
};
function drain() {
    for (const [id, callback] of [...pending]) {
        pending.delete(id); assert.equal(callback(), false);
    }
}
const Meta = {WindowType: {NORMAL:0, DESKTOP:1, DOCK:2}};
global.imports = {gi:{GLib, Meta}};
const desktop = {meta_window:{get_workspace:()=>({}), get_window_type:()=>1}};
const closing = {meta_window:new Proxy({get_workspace:()=>null}, {
    get(target, key) {
        if (key in target) return target[key];
        throw new Error('Native getter on a closing window: ' + key);
    }
})};
const app = {meta_window:{get_workspace:()=>({}), get_window_type:()=>0}};
const parent = {
    children:[], get_children() { return this.children; },
    set_child_below_sibling(child, sibling) {
        this.children = this.children.filter(item => item !== child);
        this.children.splice(this.children.indexOf(sibling), 0, child);
    }
};
const dock = new Signals(); let above = false;
const actor = {meta_window:dock, get_parent:()=>parent};
Object.assign(dock, {
    get_workspace:()=>({}), get_title:()=> 'Docking',
    get_window_type:()=>2, get_compositor_private:()=>actor,
    set_skip_taskbar() {}, hide_from_window_list() {}, set_type() {},
    stick() {}, unstick() {},
    make_above() { above = true; parent.children = [desktop, closing, app, actor]; },
    unmake_above() { above = false; },
    move_resize_frame() {}, get_frame_rect:()=>({x:0, y:700})
});
global.display = new Signals();
global.get_window_actors = ()=>[actor];
parent.children = [desktop, closing, app, actor];
"""
    if backend == "gnome":
        path = (
            Path(__file__).resolve().parents[2]
            / "docking/platform/backends/gnome/extension/extension.js"
        )
        source = re.sub(r"^import .*\n", "", path.read_text(), flags=re.MULTILINE)
        source = source.replace("export default class", "class")
        script = (
            setup
            + source
            + """
const bridge = new DockingBridgeExtension();
bridge._signals = []; bridge._dockStackSourceId = 0;
bridge._findDockWindow = () => dock;
bridge._connect(global.display, 'restacked', () => bridge._queueDockStack());
const quiet = () => bridge.PositionQuietDock(0, 700, 1280, 100);
const normal = () => bridge.PositionDock(0, 700, 1280, 100);
const stop = () => bridge.disable();
"""
        )
    else:
        proxy = SimpleNamespace(call_sync=MagicMock())
        client = CinnamonShellClient(proxy=proxy)
        scripts = []
        for keep_above in (False, True):
            client.position_dock(
                title="Docking",
                request=PlacementRequest(
                    monitor=MonitorSnapshot(0, Rect(0, 0, 1280, 800)),
                    position=Position.BOTTOM,
                    x=0,
                    y=700,
                    size=Size(1280, 100),
                    keep_above=keep_above,
                ),
                current_workspace_only=False,
            )
            scripts.append(proxy.call_sync.call_args.args[1].unpack()[0])
        client.clear_dock_visibility(title="Docking")
        scripts.append(proxy.call_sync.call_args.args[1].unpack()[0])
        script = setup + "\n".join(
            f"const {name} = () => eval({json.dumps(source)});"
            for name, source in zip(("quiet", "normal", "stop"), scripts, strict=True)
        )
    script += """
quiet(); quiet(); assert.equal(pending.size, 1); drain();
assert.equal(above, false);
assert.deepEqual(parent.children, [desktop, closing, actor, app]);
parent.children = [desktop, closing, app, actor];
global.display.emit('restacked'); drain();
assert.deepEqual(parent.children, [desktop, closing, actor, app]);
// A queued quiet restack must never undo a subsequent normal-mode request.
global.display.emit('restacked'); normal(); drain();
assert.equal(above, true);
assert.deepEqual(parent.children, [desktop, closing, app, actor]);
quiet(); assert.equal(pending.size, 1); stop();
assert.equal(pending.size, 0);
assert.equal(global.display.signals.size, 0);
"""
    subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=True, timeout=30
    )
