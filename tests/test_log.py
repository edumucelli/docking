"""Tests for structured logging helpers."""

from __future__ import annotations

import io
import logging

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
