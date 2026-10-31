"""Verify the hidden Telegram WebKit host with a fixture or its live login page.

Run under an isolated display/session, for example:
  xvfb-run -a dbus-run-session -- python -m tools.webapp_smoke
  xvfb-run -a dbus-run-session -- python -m tools.webapp_smoke --live

The default fixture makes no network requests. --live opens Telegram's official
anonymous login page. Both modes use temporary website data and never sign in.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="docking-webapp-smoke-") as folder:
        os.environ["XDG_DATA_HOME"] = str(Path(folder) / "data")
        os.environ["XDG_CACHE_HOME"] = str(Path(folder) / "cache")
        return run(live=args.live)


def run(*, live: bool) -> int:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    from docking.applets.telegram.state import PROFILE
    from docking.applets.webapp.implementations.unix.backend import (
        WebAppBrowser,
        load_webkit,
    )
    from docking.applets.webapp.state import BrowserPhase

    initialized, _args = Gtk.init_check()
    if not initialized:
        print(json.dumps({"passed": False, "error": "GTK display unavailable"}))
        return 2
    loop = GLib.MainLoop()
    phases: list[str] = []
    counts: list[int | None] = []

    def maybe_finish() -> None:
        if BrowserPhase.LOGIN_REQUIRED.value in phases and (live or 5 in counts):
            loop.quit()

    def on_phase(phase: BrowserPhase, _error: str) -> None:
        phases.append(phase.value)
        if phase in {BrowserPhase.ERROR, BrowserPhase.UNAVAILABLE}:
            loop.quit()
        maybe_finish()

    def on_badge(count: int | None, _visible: bool) -> None:
        counts.append(count)
        maybe_finish()

    browser = WebAppBrowser(
        profile=PROFILE,
        on_phase=on_phase,
        on_title=lambda _title: None,
        on_badge=on_badge,
        on_notification=lambda: None,
        on_attention_cleared=lambda: None,
    )
    webkit = load_webkit()
    timeout = GLib.timeout_add_seconds(45 if live else 15, lambda: loop.quit() or False)
    if live:
        started = browser.start()
    else:
        # Create the real browser and content manager without issuing its first
        # remote navigation. The fixture still runs at the profile's real origin
        # so origin-scoped document-start injection is exercised by WebKit.
        with patch.object(webkit.WebView, "load_uri"):
            started = browser.start()
        if started:
            browser._view.load_html(
                """<!doctype html><title>Telegram Web</title><body><script>
                const updateBadge = navigator.setAppBadge.bind(navigator);
                updateBadge(0);
                requestAnimationFrame(() => {
                    const login = document.createElement('div');
                    login.id = 'auth-pages';
                    login.style.cssText = 'width:50px;height:50px';
                    document.body.append(login);
                    updateBadge(5);
                });
                </script></body>""",
                PROFILE.url,
            )
    try:
        if started:
            loop.run()
        hidden = not browser.visible
        viewport = (
            [browser._view.get_allocated_width(), browser._view.get_allocated_height()]
            if browser._view is not None
            else [0, 0]
        )
        passed = (
            started
            and hidden
            and min(viewport) > 0
            and BrowserPhase.LOGIN_REQUIRED.value in phases
            and (live or 5 in counts)
        )
        print(
            json.dumps(
                {
                    "passed": passed,
                    "live": live,
                    "hidden": hidden,
                    "viewport": viewport,
                    "phases": phases,
                    "badges": counts,
                }
            ),
            flush=True,
        )
        return 0 if passed else 1
    finally:
        if GLib.MainContext.default().find_source_by_id(timeout) is not None:
            GLib.source_remove(timeout)
        browser.stop()


if __name__ == "__main__":
    raise SystemExit(main())
