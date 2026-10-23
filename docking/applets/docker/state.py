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

"""Docker CLI state and action helpers for the Docker applet."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass

from docking.applets.docker import meta
from docking.i18n import _
from docking.log import get_logger, with_context

log = with_context(get_logger(name="docker"), applet_id=meta.id)

POLL_INTERVAL_S = 5
DOCKER_TIMEOUT_S = 4
DOCKER_ACTION_TIMEOUT_S = 20


@dataclass(frozen=True, slots=True)
class DockerContainer:
    """One running Docker container shown by the applet."""

    container_id: str
    name: str
    image: str
    status: str


@dataclass(frozen=True, slots=True)
class DockerState:
    """Snapshot of Docker availability and running containers."""

    available: bool
    containers: tuple[DockerContainer, ...] = ()
    error: str = ""


def query_docker_state() -> DockerState:
    """Return currently running Docker containers from ``docker ps``."""
    if shutil.which("docker") is None:
        return DockerState(available=False, error=_("Docker command not found"))

    output = _run_docker_ps()
    if output is None:
        return DockerState(available=False, error=_("Docker is unavailable"))
    return DockerState(
        available=True,
        containers=tuple(_parse_docker_ps(output=output)),
    )


def stop_container(container_id: str) -> bool:
    """Stop one container by id."""
    return _run_docker_action(["docker", "stop", container_id], action="stop")


def restart_container(container_id: str) -> bool:
    """Restart one container by id."""
    return _run_docker_action(["docker", "restart", container_id], action="restart")


def docker_tooltip(state: DockerState) -> str:
    """User-facing tooltip for the current Docker state."""
    if not state.available:
        return _("Docker: {error}").format(error=state.error or _("unavailable"))
    count = len(state.containers)
    if count == 0:
        return _("Docker: no running containers")
    if count == 1:
        return _("Docker: 1 running container")
    return _("Docker: {count} running containers").format(count=count)


def _run_docker_ps() -> str | None:
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=DOCKER_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.bind(action="docker_ps").warning("Failed to run docker ps: %s", exc)
        return None
    if result.returncode != 0:
        log.bind(action="docker_ps").warning(
            "docker ps failed: %s", result.stderr.strip()
        )
        return None
    return result.stdout


def _parse_docker_ps(*, output: str) -> list[DockerContainer]:
    containers: list[DockerContainer] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            log.bind(action="parse_docker_ps").debug("Ignoring invalid JSON: %s", line)
            continue
        container_id = str(data.get("ID") or data.get("ID".lower()) or "").strip()
        if not container_id:
            continue
        name = str(data.get("Names") or container_id).strip()
        image = str(data.get("Image") or "").strip()
        status = str(data.get("Status") or _("running")).strip()
        containers.append(
            DockerContainer(
                container_id=container_id,
                name=name,
                image=image,
                status=status,
            )
        )
    return containers


def _run_docker_action(cmd: list[str], *, action: str) -> bool:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=DOCKER_ACTION_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.bind(action=action).warning("Failed to run %s: %s", cmd, exc)
        return False
    if result.returncode != 0:
        log.bind(action=action).warning("%s failed: %s", cmd, result.stderr.strip())
        return False
    return True
