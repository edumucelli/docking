"""Execute the Telegram compatibility shim against browser scheduler semantics."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import gi
import pytest

from docking.applets.telegram.bridge import BACKGROUND_FRAMES_SCRIPT


@pytest.fixture
def javascript():
    try:
        gi.require_version("JavaScriptCore", "4.1")
        from gi.repository import JavaScriptCore
    except (ImportError, ValueError):
        pytest.skip("JavaScriptCore 4.1 is unavailable")
    context = JavaScriptCore.Context.new()
    context.evaluate(
        """
        const window = globalThis;
        const nativeFrames = new Map(), timers = new Map(), listeners = new Map();
        let nextFrame = 0, nextTimer = 0, now = 0;
        const document = {hidden: true, addEventListener: listen};
        const performance = {now: () => now};
        function listen(name, callback) {
            if (!listeners.has(name)) listeners.set(name, []);
            listeners.get(name).push(callback);
        }
        function fire(name) { for (const callback of listeners.get(name) || []) callback(); }
        function runTimers() {
            const batch = Array.from(timers.values()); timers.clear(); now += 100;
            for (const timer of batch) timer.callback();
        }
        function runFrames() {
            const batch = Array.from(nativeFrames.values()); nativeFrames.clear();
            for (const callback of batch) callback(now);
        }
        window.addEventListener = listen;
        window.requestAnimationFrame = callback => {
            nativeFrames.set(++nextFrame, callback); return nextFrame;
        };
        window.cancelAnimationFrame = id => nativeFrames.delete(id);
        window.setTimeout = (callback, delay) => {
            timers.set(++nextTimer, {callback, delay}); return nextTimer;
        };
        window.clearTimeout = id => timers.delete(id);
        const calls = [];
    """,
        -1,
    )
    context.evaluate(BACKGROUND_FRAMES_SCRIPT, -1)
    assert context.get_exception() is None
    return context


@pytest.mark.parametrize(
    "scenario",
    [
        """
        const id = requestAnimationFrame(t => calls.push(t));
        cancelAnimationFrame(id); runTimers(); runFrames();
        calls.length === 0 && timers.size === 0 && nativeFrames.size === 0;
    """,
        """
        requestAnimationFrame(t => calls.push(t)); runTimers(); runFrames();
        calls.length === 1 && calls[0] === 100;
    """,
        """
        requestAnimationFrame(t => calls.push(t));
        document.hidden = false; fire('visibilitychange'); runFrames(); runTimers();
        calls.length === 1 && timers.size === 0;
    """,
        """
        document.hidden = false;
        requestAnimationFrame(t => calls.push(t));
        document.hidden = true; fire('visibilitychange'); runTimers(); runFrames();
        calls.length === 1;
    """,
        """
        requestAnimationFrame(t => calls.push(t)); fire('pagehide'); runTimers(); runFrames();
        const cleaned = calls.length === 0 && timers.size === 0 && nativeFrames.size === 0;
        fire('pageshow'); requestAnimationFrame(t => calls.push(t)); runTimers();
        cleaned && calls.length === 1;
    """,
        """
        function animate(t) { calls.push(t); requestAnimationFrame(animate); }
        requestAnimationFrame(animate);
        const slow = Array.from(timers.values()).every(timer => timer.delay >= 100);
        runTimers(); runTimers(); runTimers(); fire('pagehide');
        slow && calls.join(',') === '100,200,300' && timers.size === 0;
    """,
    ],
)
def test_background_frame_callback_lifecycle(javascript, scenario):
    result = javascript.evaluate(scenario, -1)
    assert javascript.get_exception() is None
    assert result.to_boolean()


def test_real_hidden_webkit_runs_startup_frame_and_public_badging_api():
    from docking.applets.webapp.implementations.unix.backend import webkit_available

    if not webkit_available() or not all(
        shutil.which(name) for name in ("xvfb-run", "dbus-run-session")
    ):
        pytest.skip("WebKitGTK and headless display tools are required")
    result = subprocess.run(
        [
            "xvfb-run",
            "-a",
            "dbus-run-session",
            "--",
            sys.executable,
            "-m",
            "tools.webapp_smoke",
        ],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr[-2000:]
    report = next(
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    )
    assert report["passed"] and report["hidden"]
    assert "login_required" in report["phases"]
    assert report["badges"] == [0, 5]
