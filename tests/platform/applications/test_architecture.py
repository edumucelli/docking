"""Architecture guards for the composed application platform."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from docking.platform.applications.identity import ProcessIdentityService
from docking.platform.applications.recents import RecentApplications
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.backends.wayland.session import WaylandLayerShellSessionBackend
from docking.search.services.recent_files import RecentFilesCatalog


def _required_parameter(callable_object: object, name: str) -> None:
    parameter = inspect.signature(callable_object).parameters[name]
    assert parameter.default is inspect.Parameter.empty


def test_stable_application_dependencies_are_required() -> None:
    _required_parameter(ProcessIdentityService, "provenance_store")
    _required_parameter(WaylandLayerShellSessionBackend, "model")
    _required_parameter(WaylandLayerShellSessionBackend, "application_registry")
    _required_parameter(
        WaylandLayerShellSessionBackend,
        "process_identity_service",
    )
    _required_parameter(RecentFilesCatalog, "target_service")
    _required_parameter(RecentApplications, "persistence")


def test_registry_exposes_only_consumer_owned_queries() -> None:
    assert {
        "applications_by_id",
        "resolvable_snapshot",
        "resolve",
        "resolve_all_by_executable_path",
        "recommended_for_content_type",
        "add_listener",
        "remove_listener",
        "default_listing_for_content_type",
        "recommended_listings_for_content_type",
        "all_listings_for_content_type",
    }.isdisjoint(ApplicationRegistry.__dict__)


def test_registry_delegates_desktop_discovery_and_parsing() -> None:
    source = (
        Path(__file__).parents[3]
        / "docking"
        / "platform"
        / "applications"
        / "registry.py"
    ).read_text(encoding="utf-8")

    assert "discovery.discover(" in source
    assert "load_desktop_key_file" not in source
    assert "class _FileFacts" not in source


def test_dnd_does_not_materialize_application_items_or_resolve_gio_apps() -> None:
    source_path = Path(__file__).parents[3] / "docking" / "ui" / "dnd.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id != "DockItem":
                continue
            kind = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "kind"),
                None,
            )
            assert not (isinstance(kind, ast.Name) and kind.id == "APP_KIND"), (
                "DnD must delegate application materialization to DockModel"
            )

        if not isinstance(node, ast.Attribute):
            continue
        assert node.attr not in {
            "get_all",
            "get_default_for_type",
            "new_from_filename",
        }, "DnD must not perform Gio application discovery"


def test_recent_application_projection_has_no_callback_deferral_loop() -> None:
    package = Path(__file__).parents[3] / "docking"
    recent_tree = ast.parse(
        (package / "platform/applications/recents.py").read_text(encoding="utf-8")
    )
    model_tree = ast.parse((package / "platform/model.py").read_text(encoding="utf-8"))
    settings_tree = ast.parse((package / "ui/settings.py").read_text(encoding="utf-8"))

    recent_methods = {
        node.name for node in ast.walk(recent_tree) if isinstance(node, ast.FunctionDef)
    }
    model_methods = {
        node.name for node in ast.walk(model_tree) if isinstance(node, ast.FunctionDef)
    }
    settings_init = next(
        node
        for node in ast.walk(settings_tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "__init__"
        and any(argument.arg == "parent" for argument in node.args.kwonlyargs)
    )

    assert "subscribe" not in recent_methods
    assert "_defer_recent_application_updates" not in model_methods
    assert "_on_recent_applications_changed" not in model_methods
    assert "recent_applications" not in {
        argument.arg for argument in settings_init.args.kwonlyargs
    }


def test_removed_compatibility_symbols_do_not_return() -> None:
    package = Path(__file__).parents[3] / "docking"
    definitions: set[str] = set()
    for relative in (
        Path("applets/music/applet.py"),
        Path("search/services/recent_files.py"),
        Path("platform/backends/wayland/hyprland_ipc.py"),
    ):
        tree = ast.parse((package / relative).read_text(encoding="utf-8"))
        definitions.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        )

    assert definitions.isdisjoint(
        {
            "_find_media_app",
            "launch_default_media_app",
            "_launch_default_for_uri",
            "set_preview_handle_source",
        }
    )
