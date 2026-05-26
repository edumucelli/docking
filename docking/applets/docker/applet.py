# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""GTK lifecycle glue for the Docker applet."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.docker import meta
from docking.applets.docker.render import render_icon
from docking.applets.docker.state import (
    POLL_INTERVAL_S,
    DockerContainer,
    DockerState,
    docker_tooltip,
    query_docker_state,
    restart_container,
    stop_container,
)
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="docker"), applet_id=meta.id)


class DockerApplet(Applet):
    """Show running Docker containers and provide stop/restart actions."""

    id = meta.id
    name = _("Docker")
    icon_name = "docker"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._state: DockerState = query_docker_state()
        self._timer_id: int = 0
        self._worker = BackgroundWorker(logger=log)
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(
            size=size,
            running_count=len(self._state.containers),
            available=self._state.available,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = docker_tooltip(self._state)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def on_clicked(self) -> None:
        self._refresh_now()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status = self._status_menu_items()

        primary: list[Gtk.MenuItem] = []
        if self._state.available:
            for container in self._state.containers:
                primary.append(self._container_menu_item(container=container))

        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.connect("activate", lambda _widget: self._refresh_now())

        return menu_sections(
            status=status,
            primary=primary,
            refresh=[refresh],
            gtk=Gtk,
        )

    def _status_menu_items(self) -> list[Gtk.MenuItem]:
        if not self._state.available:
            return [
                disabled_menu_item(
                    _("Docker unavailable: {error}").format(
                        error=self._state.error or _("unknown error")
                    ),
                    gtk=Gtk,
                )
            ]
        if not self._state.containers:
            return [disabled_menu_item(_("No running containers"), gtk=Gtk)]
        return [
            disabled_menu_item(
                _("{count} running containers").format(
                    count=len(self._state.containers)
                ),
                gtk=Gtk,
            )
        ]

    def _container_menu_item(self, *, container: DockerContainer) -> Gtk.MenuItem:
        root = Gtk.MenuItem(label=_container_label(container=container))
        submenu = Gtk.Menu()

        image = container.image or _("unknown image")
        status = container.status or _("running")
        submenu.append(disabled_menu_item(image, gtk=Gtk))
        submenu.append(disabled_menu_item(status, gtk=Gtk))
        submenu.append(Gtk.SeparatorMenuItem())

        stop_item = Gtk.MenuItem(label=_("Stop"))
        stop_item.connect(
            "activate",
            lambda _widget, target=container: self._run_container_action(
                action="stop",
                container=target,
            ),
        )
        submenu.append(stop_item)

        restart_item = Gtk.MenuItem(label=_("Restart"))
        restart_item.connect(
            "activate",
            lambda _widget, target=container: self._run_container_action(
                action="restart",
                container=target,
            ),
        )
        submenu.append(restart_item)

        root.set_submenu(submenu)
        return root

    def _refresh_now(self) -> None:
        self._worker.run_guarded(
            key="docker-refresh",
            name="docker-refresh",
            fn=query_docker_state,
            on_result=self._on_state_result,
        )

    def _run_container_action(
        self,
        *,
        action: str,
        container: DockerContainer,
    ) -> None:
        fn = stop_container if action == "stop" else restart_container

        def task() -> DockerState:
            fn(container.container_id)
            return query_docker_state()

        self._worker.run(
            name=f"docker-{action}",
            fn=task,
            on_result=self._on_state_result,
        )

    def _tick(self) -> bool:
        self._worker.run_guarded(
            key="docker-poll",
            name="docker-poll",
            fn=query_docker_state,
            on_result=self._on_state_result,
        )
        return True

    def _on_state_result(self, state: DockerState) -> bool:
        if state != self._state:
            self._state = state
            self.present()
        return False


def _container_label(*, container: DockerContainer) -> str:
    if not container.image:
        return container.name
    return _("{name} ({image})").format(name=container.name, image=container.image)
