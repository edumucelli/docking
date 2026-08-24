"""Small canonical application-service fakes shared by backend tests."""

from __future__ import annotations

from collections.abc import Iterable

from docking.platform.applications.identity import (
    LaunchProvenanceStore,
    ProcessIdentityService,
)
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)


class _Icon:
    def __init__(self, value: str) -> None:
        self._value = value

    def to_string(self) -> str:
        return self._value


class GioApplicationFake:
    """Small Gio desktop application double accepted by real discovery code."""

    def __init__(
        self,
        desktop_id: str,
        *,
        name: str | None = None,
        icon: str = "application-x-executable",
        commandline: str | None = None,
        categories: str = "Utility;",
        filename: str = "",
        should_show: bool = True,
    ) -> None:
        self.desktop_id = desktop_id
        self._name = name or desktop_id.removesuffix(".desktop")
        self._icon = icon
        self._commandline = commandline or desktop_id.removesuffix(".desktop")
        self._categories = categories
        self._filename = filename
        self._should_show = should_show

    def get_id(self) -> str:
        return self.desktop_id

    def get_display_name(self) -> str:
        return self._name

    def get_filename(self) -> str:
        return self._filename

    def get_icon(self) -> _Icon:
        return _Icon(self._icon)

    def get_startup_wm_class(self) -> str:
        return self.desktop_id.removesuffix(".desktop")

    def get_commandline(self) -> str:
        return self._commandline

    def get_categories(self) -> str:
        return self._categories

    def get_generic_name(self) -> str:
        return ""

    def get_description(self) -> str:
        return ""

    def get_keywords(self) -> tuple[str, ...]:
        return ()

    def list_actions(self) -> tuple[str, ...]:
        return ()

    def get_is_hidden(self) -> bool:
        return False

    def get_nodisplay(self) -> bool:
        return False

    def should_show(self) -> bool:
        return self._should_show


class ApplicationRegistryHarness:
    """Mutable source driving the real application registry in integration tests."""

    def __init__(self, applications: Iterable[object] = ()) -> None:
        self.applications = list(applications)
        self.registry = ApplicationRegistry(
            application_source=lambda: tuple(self.applications),
            desktop_directories_source=lambda: (),
        )
        self.registry._desktop_app_info_for_id = lambda _desktop_id: None
        self.registry._desktop_app_info_from_filename = lambda _path: None
        assert self.registry.refresh()

    def publish(self, applications: Iterable[object]) -> None:
        self.applications[:] = applications
        assert self.registry.refresh()


def application(
    desktop_id: str,
    *,
    wm_class: str | None = None,
) -> ApplicationInfo:
    stem = desktop_id.removesuffix(".desktop")
    runtime_class = wm_class or stem.rsplit(".", 1)[-1]
    return ApplicationInfo(
        desktop_id=desktop_id,
        name=stem,
        declared_icon=stem,
        wm_class=runtime_class,
        exec_line=stem,
        origin=ApplicationOrigin.INSTALLED,
        location=ApplicationLocation.SANDBOX,
        desktop_file=None,
        executable_path=None,
        aliases=tuple(
            dict.fromkeys(
                (
                    stem.casefold(),
                    runtime_class.casefold(),
                    desktop_id.casefold(),
                )
            )
        ),
        visible=True,
        has_gio_source=False,
    )


class ApplicationRegistryStub:
    """Registry lookup surface needed by the canonical matcher."""

    generation = 1

    def __init__(self, applications: tuple[ApplicationInfo, ...]) -> None:
        self.publish(*applications)

    def publish(self, *applications: ApplicationInfo) -> None:
        self._applications = {
            application.desktop_id: application for application in applications
        }
        aliases: dict[str, list[ApplicationInfo]] = {}
        for candidate in applications:
            for alias in candidate.aliases:
                aliases.setdefault(alias.casefold(), []).append(candidate)
        self._aliases = {
            alias: tuple(candidates) for alias, candidates in aliases.items()
        }
        self.generation += 1

    def get(self, desktop_id: str) -> ApplicationInfo | None:
        return self._applications.get(desktop_id)

    def resolve_all_by_wm_class(
        self,
        alias: str,
    ) -> tuple[ApplicationInfo, ...]:
        return self._aliases.get(alias.casefold().strip(), ())


def identity_services(
    *applications: ApplicationInfo,
) -> dict[str, object]:
    """Return canonical matcher dependencies for backend constructors."""
    if not applications:
        applications = (
            application("Alacritty.desktop", wm_class="Alacritty"),
            application("firefox.desktop", wm_class="firefox"),
            application("org.gnome.Nautilus.desktop", wm_class="Nautilus"),
        )
    registry = ApplicationRegistryStub(tuple(applications))
    process_identity_service = ProcessIdentityService(
        LaunchProvenanceStore(),
        executable_resolver=lambda _pid: None,
    )
    return {
        "application_registry": registry,
        "process_identity_service": process_identity_service,
    }
