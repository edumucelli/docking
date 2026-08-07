"""Dependency-boundary tests for the application foundation."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).parents[3] / "docking" / "platform" / "applications"
DOCKING = Path(__file__).parents[3] / "docking"
COMPATIBILITY_MODULES = {
    DOCKING / "applets" / "apps.py",
    DOCKING / "platform" / "app_matcher.py",
    DOCKING / "platform" / "desktop_entries.py",
    DOCKING / "platform" / "launcher.py",
    DOCKING / "platform" / "process_identity.py",
    DOCKING / "platform" / "running.py",
}
OLD_APPLICATION_MODULES = {
    "docking.applets.apps",
    "docking.platform.app_matcher",
    "docking.platform.desktop_entries",
    "docking.platform.launcher",
    "docking.platform.process_identity",
    "docking.platform.running",
}
APP_FACADE_MODULES = {
    "docking.platform.launcher",
    "docking.platform.process_identity",
}


def _tree(filename: str) -> ast.Module:
    return ast.parse((PACKAGE / filename).read_text(encoding="utf-8"))


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = list(
                    path.relative_to(DOCKING.parent).with_suffix("").parts[:-1]
                )
                package = package[: len(package) - node.level + 1]
                if node.module:
                    package.extend(node.module.split("."))
                module = ".".join(package)
            else:
                module = node.module or ""
            modules.add(module)
            modules.update(
                f"{module}.{alias.name}" if module else alias.name
                for alias in node.names
            )
    return modules


def test_package_initializer_is_docstring_only():
    tree = _tree("__init__.py")

    assert len(tree.body) == 1
    assert isinstance(tree.body[0], ast.Expr)
    assert isinstance(tree.body[0].value, ast.Constant)
    assert isinstance(tree.body[0].value.value, str)


def test_types_is_a_standard_library_leaf():
    tree = _tree("types.py")
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots <= {
        "__future__",
        "dataclasses",
        "enum",
        "pathlib",
    }


def test_application_modules_do_not_reach_consumer_layers():
    forbidden = (
        "docking.applets",
        "docking.platform.model",
        "docking.search",
        "docking.ui",
    )

    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            assert not any(
                module.startswith(prefix) for module in modules for prefix in forbidden
            ), path.name


def test_application_applets_do_not_import_legacy_apps_adapter():
    consumers = (
        DOCKING / "applets" / "applications" / "state.py",
        DOCKING / "applets" / "applications" / "applet.py",
        DOCKING / "applets" / "runcommand" / "state.py",
        DOCKING / "applets" / "runcommand" / "applet.py",
    )

    for path in consumers:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            assert "docking.applets.apps" not in modules, path


def test_internal_production_has_zero_old_application_imports():
    for path in DOCKING.rglob("*.py"):
        if path in COMPATIBILITY_MODULES:
            continue
        hits = _imported_modules(path) & OLD_APPLICATION_MODULES
        expected = APP_FACADE_MODULES if path == DOCKING / "app.py" else set()
        assert hits == expected, (path.relative_to(DOCKING), hits)


def test_app_uses_old_facades_only_for_explicit_binding_and_reset():
    tree = ast.parse((DOCKING / "app.py").read_text(encoding="utf-8"))
    references = {
        (node.value.id, node.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in {"launcher_facade", "process_identity_facade"}
    }

    assert references == {
        ("launcher_facade", "configure_application_launcher"),
        ("launcher_facade", "reset_application_launcher"),
        ("process_identity_facade", "configure_process_identity_service"),
        ("process_identity_facade", "reset_process_identity_service"),
    }


def test_legacy_free_launch_result_is_not_consumed_by_apps_adapter():
    tree = ast.parse((DOCKING / "applets" / "apps.py").read_text(encoding="utf-8"))
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "launch_desktop_id"
    ]

    assert len(calls) == 1
    assert isinstance(parents[calls[0]], ast.Expr)


def test_canonical_matcher_has_no_legacy_constructor_or_match_subclass():
    tree = _tree("matcher.py")
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    assert "AppMatch" not in classes
    matcher_init = next(
        node
        for node in classes["AppIdMatcher"].body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    argument_names = {
        argument.arg
        for argument in (
            *matcher_init.args.posonlyargs,
            *matcher_init.args.args,
            *matcher_init.args.kwonlyargs,
        )
    }
    assert "launcher" not in argument_names


def test_canonical_running_has_no_runtime_compatibility_constructor():
    tree = _tree("running.py")
    names = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    assert names == {"RunningAppInfo", "RunningWindowInfo"}
    assert "docking.platform.applications.entries" not in _imported_modules(
        PACKAGE / "running.py"
    )
    assert not any(
        isinstance(node, ast.Name | ast.Attribute)
        and getattr(node, "id", getattr(node, "attr", "")) == "DesktopInfo"
        for node in ast.walk(tree)
    )


def test_registry_and_matcher_do_not_construct_legacy_desktop_info():
    for filename in ("matcher.py", "registry.py"):
        tree = _tree(filename)
        assert not any(
            isinstance(node, ast.Name | ast.Attribute)
            and getattr(node, "id", getattr(node, "attr", "")) == "DesktopInfo"
            for node in ast.walk(tree)
        ), filename


def test_reduced_backend_uses_binding_free_application_constants():
    reduced = DOCKING / "platform" / "backends" / "reduced" / "services.py"
    imports = _imported_modules(reduced)
    assert "docking.platform.applications.constants" in imports
    assert "docking.platform.applications.entries" not in imports


def test_core_config_has_no_platform_application_imports():
    imports = _imported_modules(DOCKING / "core" / "config.py")
    assert not any(
        module.startswith("docking.platform.applications")
        or module in OLD_APPLICATION_MODULES
        for module in imports
    )


def test_application_listing_does_not_import_launching_services():
    imports = _imported_modules(PACKAGE / "listing.py")
    assert "docking.platform.launcher" not in imports
    assert "docking.platform.applications.launcher" not in imports


def test_targets_depend_on_icons_without_reverse_dependency():
    icons = _imported_modules(DOCKING / "platform" / "icons.py")
    targets = _imported_modules(DOCKING / "platform" / "targets.py")

    assert "docking.platform.targets" not in icons
    assert "docking.platform.icons" in targets
