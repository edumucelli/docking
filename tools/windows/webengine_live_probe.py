"""Observe coarse public login landmarks without exporting real page content."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from PySide6.QtWebEngineCore import QWebEnginePage

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from docking.applets.webapp.windows_companion.runtime import Runtime
from docking.applets.webapp.windows_companion.runtime import main as run

SUMMARY = r"""
JSON.stringify((()=>{
 const has=selector=>Boolean(document.querySelector(selector));
 const video=document.createElement('video');
 return {
  complete:document.readyState==='complete',hidden:document.hidden,
  chromiumMajor:Number(navigator.userAgent.match(/Chrome\/(\d+)/)?.[1]||0),
  embeddedQt:navigator.userAgent.includes('QtWebEngine/'),
  online:navigator.onLine,body:Boolean(document.body),
  publicBridge:Boolean(window.__dockingBrowserBridge),
  installedBridge:Boolean(window.__dockingWhatsAppBridgeInstalled||window.__dockingTelegramBridgeInstalled),
  canvases:document.querySelectorAll('canvas').length,
  svg:document.querySelectorAll('svg').length,
  frames:document.querySelectorAll('iframe').length,
  landmarks:{labelledCanvas:has('canvas[aria-label]'),
   refCanvas:has('canvas[data-ref],[data-ref] canvas'),
   whatsappReady:has('#pane-side'),
   telegramAuth:has('#auth-pages,.auth-pages,#auth-qr,#auth-phone-number,.Auth'),
   whatsappRef:has('[data-ref]'),qrImage:has('img[alt*="QR" i]'),
   qrSvg:has('svg[aria-label*="QR" i]')},
  compatibilityLinks:{chrome:has('a[href*="google.com/chrome"]'),
   firefox:has('a[href*="mozilla.org/firefox"]')},
  codecs:{webm:video.canPlayType('video/webm; codecs="vp8,opus"'),
   mp4:video.canPlayType('video/mp4; codecs="avc1.42E01E,mp4a.40.2"')}
 };
})())
"""


class Observe(Runtime):
    def _build(self) -> None:
        super()._build()
        if os.environ.get("DOCKING_BROWSER_LIVE_CHROMIUM_UA") == "1":
            self.profile.setHttpUserAgent(
                re.sub(r"\sQtWebEngine/\S+", "", self.profile.httpUserAgent())
            )
            self.page.triggerAction(QWebEnginePage.WebAction.ReloadAndBypassCache)

    def command(self, packet) -> None:
        if packet.get("op") == "inspect-service":

            def completed(raw):
                self.emit(
                    "service_probe",
                    id=packet["id"],
                    summary=json.loads(raw) if raw else {},
                )

            self.page.runJavaScript(SUMMARY, completed)
        else:
            super().command(packet)


if __name__ == "__main__":
    run(Observe)
