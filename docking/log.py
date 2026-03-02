"""Logging configuration and structured context helpers."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any, MutableMapping, cast

LOG_LEVEL = os.environ.get("DOCKING_LOG_LEVEL", "WARNING").upper()
_CONTEXT_FIELDS = ("applet_id", "desktop_id", "action")


class DockingFormatter(logging.Formatter):
    """Formatter that appends optional structured context fields."""

    def format(self, record: logging.LogRecord) -> str:
        parts: list[str] = []
        for key in _CONTEXT_FIELDS:
            value = getattr(record, key, None)
            if value in (None, "", "-"):
                continue
            parts.append(f"{key}={value}")
        record.context = f" [{' '.join(parts)}]" if parts else ""
        return super().format(record)


class DockingContextAdapter(logging.LoggerAdapter):
    """Logger adapter that carries structured context across calls."""

    def process(
        self,
        msg: Any,
        kwargs: MutableMapping[str, Any],
    ) -> tuple[Any, MutableMapping[str, Any]]:
        merged = dict(cast(Mapping[str, Any], self.extra))
        incoming = kwargs.get("extra")
        if isinstance(incoming, Mapping):
            merged.update(incoming)
        kwargs["extra"] = merged
        return msg, kwargs

    def bind(self, **context: object) -> DockingContextAdapter:
        extra = dict(cast(Mapping[str, Any], self.extra))
        for key, value in context.items():
            if value is None:
                continue
            extra[key] = value
        return DockingContextAdapter(self.logger, extra)


def _configure_root_logger() -> None:
    """Configure root logger with Docking formatter (idempotent)."""
    level = getattr(logging, LOG_LEVEL, logging.WARNING)
    fmt = "%(asctime)s.%(msecs)03d %(name)-18s %(levelname)-5s %(message)s%(context)s"
    formatter = DockingFormatter(
        fmt=fmt,
        datefmt="%H:%M:%S",
    )
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            handler.setFormatter(formatter)
    root.setLevel(level)


_configure_root_logger()


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the 'docking.' namespace."""
    return logging.getLogger(f"docking.{name}")


def with_context(
    logger: logging.Logger | DockingContextAdapter, **context: object
) -> DockingContextAdapter:
    """Return a logger adapter with context fields (applet_id, desktop_id, action)."""
    if isinstance(logger, DockingContextAdapter):
        return logger.bind(**context)
    return DockingContextAdapter(logger, {}).bind(**context)
