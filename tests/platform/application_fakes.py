"""Small canonical application-service fakes shared by backend tests."""

from __future__ import annotations

from docking.platform.applications.identity import (
    LaunchProvenanceStore,
    ProcessIdentityService,
)
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)


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

    def resolve(
        self,
        desktop_id: str,
        *,
        log_failures: bool = True,
    ) -> ApplicationInfo | None:
        del log_failures
        return self.get(desktop_id)

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
