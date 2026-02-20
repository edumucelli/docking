"""Logging configuration for the dock."""

import logging
import os

LOG_LEVEL = os.environ.get("DOCKING_LOG_LEVEL", "WARNING").upper()

logging.basicConfig(
    format="%(asctime)s.%(msecs)03d %(name)-18s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
    level=getattr(logging, LOG_LEVEL, logging.WARNING),
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"docking.{name}")
