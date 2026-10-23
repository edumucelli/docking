"""Pytest options for visual regression tests."""

from __future__ import annotations


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--update-visual-baselines",
        action="store_true",
        default=False,
        help="Rewrite visual baseline PNGs instead of comparing against them.",
    )
