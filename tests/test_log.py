"""Tests for structured logging helpers."""

from __future__ import annotations

import io
import logging

import docking.log as log_mod
from docking.log import DockingFormatter, get_logger, with_context


def test_with_context_adds_fields_to_output():
    # Given
    logger = get_logger("test.log.context")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        DockingFormatter(
            fmt="%(levelname)s %(message)s%(context)s",
            datefmt="%H:%M:%S",
        )
    )
    prev_propagate = logger.propagate
    prev_level = logger.level
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        # When
        with_context(
            logger, applet_id="trash", desktop_id="applet://trash", action="open"
        ).warning("trash action failed")
    finally:
        # Then
        logger.removeHandler(handler)
        logger.propagate = prev_propagate
        logger.setLevel(prev_level)

    output = stream.getvalue()
    assert "applet_id=trash" in output
    assert "desktop_id=applet://trash" in output
    assert "action=open" in output


def test_bind_merges_context():
    # Given
    logger = get_logger("test.log.bind")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        DockingFormatter(
            fmt="%(levelname)s %(message)s%(context)s",
            datefmt="%H:%M:%S",
        )
    )
    prev_propagate = logger.propagate
    prev_level = logger.level
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        base = with_context(logger, applet_id="volume")
        # When
        base.bind(action="set_volume").warning("volume update failed")
    finally:
        # Then
        logger.removeHandler(handler)
        logger.propagate = prev_propagate
        logger.setLevel(prev_level)

    output = stream.getvalue()
    assert "applet_id=volume" in output
    assert "action=set_volume" in output


def test_bind_ignores_none_values():
    logger = get_logger("test.log.bind.none")
    bound = with_context(logger, applet_id="clock").bind(action=None)
    assert bound.extra["applet_id"] == "clock"
    assert "action" not in bound.extra


def test_adapter_process_ignores_non_mapping_extra():
    logger = get_logger("test.log.process")
    adapter = with_context(logger, applet_id="network")

    msg, kwargs = adapter.process("hello", {"extra": "not-a-mapping"})

    assert msg == "hello"
    assert kwargs["extra"]["applet_id"] == "network"


def test_formatter_skips_empty_context_values():
    formatter = DockingFormatter(fmt="%(message)s%(context)s")
    record = logging.LogRecord(
        name="docking.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="msg",
        args=(),
        exc_info=None,
    )
    record.applet_id = ""
    record.desktop_id = "-"
    record.action = None

    rendered = formatter.format(record)

    assert rendered == "msg"


def test_configure_root_logger_updates_existing_handlers(monkeypatch):
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level

    handler = logging.StreamHandler(io.StringIO())
    root.handlers = [handler]

    monkeypatch.setattr(log_mod, "LOG_LEVEL", "NOT_A_LEVEL")

    try:
        log_mod._configure_root_logger()
        assert isinstance(handler.formatter, DockingFormatter)
        assert root.level == logging.WARNING
    finally:
        root.handlers = old_handlers
        root.setLevel(old_level)
