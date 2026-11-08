"""Present the real Chromium engine without Qt's incompatible product token."""

from __future__ import annotations

import re


def chromium_user_agent(value: str) -> str:
    # WhatsApp rejects the additional QtWebEngine product identifier. Keep the
    # actual Chromium version and platform; do not claim a newer browser engine.
    if "Chrome/" not in value:
        return value
    return re.sub(r"(?<!\S)QtWebEngine/\S+(?:\s+|$)", "", value).strip()
