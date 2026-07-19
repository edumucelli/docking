"""Keep the user-facing applet catalog aligned with runtime discovery."""

from __future__ import annotations

import re
from pathlib import Path

from docking.applets import get_applet_catalog
from docking.applets.identity import APPLET_CATEGORY_ORDER

ROOT = Path(__file__).resolve().parents[1]
APPLETS_GUIDE = ROOT / "docs" / "APPLETS.md"
README = ROOT / "README.md"


def test_applets_guide_documents_every_discovered_applet_once() -> None:
    catalog = get_applet_catalog()
    documented = re.findall(
        r"^### (.+)$",
        APPLETS_GUIDE.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )

    assert len(documented) == len(set(documented))
    assert set(documented) == {meta.name for meta in catalog.values()}


def test_applets_guide_uses_runtime_category_order() -> None:
    text = APPLETS_GUIDE.read_text(encoding="utf-8")
    headings = re.findall(r"^## (.+)$", text, flags=re.MULTILINE)

    assert headings == [
        "Add and Manage Applets",
        "Catalog",
        *(category.value for category in APPLET_CATEGORY_ORDER),
    ]


def test_applets_guide_places_applets_in_runtime_categories() -> None:
    catalog = get_applet_catalog()
    actual: dict[str, str] = {}
    current_category = ""
    for level, heading in re.findall(
        r"^(##|###) (.+)$",
        APPLETS_GUIDE.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    ):
        if level == "##":
            current_category = heading
        else:
            actual[heading] = current_category

    assert actual == {meta.name: meta.category.value for meta in catalog.values()}


def test_readme_applet_count_matches_discovered_catalog() -> None:
    applet_count = len(get_applet_catalog())
    readme = README.read_text(encoding="utf-8")

    assert f"{applet_count} built-in applets" in readme
    assert "[Applets guide](docs/APPLETS.md)" in readme
