"""Tests for the Docker applet."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import docking.applets.docker.applet as docker_applet_mod
import docking.applets.docker.state as docker_state_mod
from docking.applets.docker.applet import DockerApplet
from docking.applets.docker.render import render_icon
from docking.applets.docker.state import (
    DockerContainer,
    DockerState,
    _parse_docker_ps,
    docker_tooltip,
    query_docker_state,
    restart_container,
    stop_container,
)
from docking.core.config import Config


class _InlineWorker:
    def __init__(self, *_args, **_kwargs) -> None:
        self.calls: list[str] = []

    def run(self, *, name, fn, on_result=None, on_error=None):
        self.calls.append(name)
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return
        if on_result is not None:
            on_result(result)

    def run_guarded(self, *, key, name, fn, on_result=None, on_error=None):
        self.calls.append(key)
        self.run(name=name, fn=fn, on_result=on_result, on_error=on_error)
        return True


def _container(
    *,
    container_id: str = "abc123",
    name: str = "web",
    image: str = "nginx:latest",
    status: str = "Up 2 minutes",
) -> DockerContainer:
    return DockerContainer(
        container_id=container_id,
        name=name,
        image=image,
        status=status,
    )


class TestDockerState:
    def test_parse_docker_ps_json_lines(self):
        output = (
            '{"ID":"abc123","Names":"web","Image":"nginx","Status":"Up 2 minutes"}\n'
            '{"ID":"def456","Names":"db","Image":"postgres","Status":"Up 1 hour"}\n'
        )

        containers = _parse_docker_ps(output=output)

        assert containers == [
            DockerContainer("abc123", "web", "nginx", "Up 2 minutes"),
            DockerContainer("def456", "db", "postgres", "Up 1 hour"),
        ]

    def test_parse_docker_ps_ignores_bad_and_empty_lines(self):
        output = '\nnot json\n{"Names":"missing id"}\n'

        assert _parse_docker_ps(output=output) == []

    def test_query_docker_state_reports_missing_command(self, monkeypatch):
        monkeypatch.setattr(docker_state_mod.shutil, "which", lambda _cmd: None)

        state = query_docker_state()

        assert state.available is False
        assert "not found" in state.error

    def test_query_docker_state_reads_running_containers(self, monkeypatch):
        monkeypatch.setattr(
            docker_state_mod.shutil, "which", lambda _cmd: "/bin/docker"
        )

        def fake_run(*_args, **_kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout='{"ID":"abc123","Names":"web","Image":"nginx"}\n',
                stderr="",
            )

        monkeypatch.setattr(docker_state_mod.subprocess, "run", fake_run)

        state = query_docker_state()

        assert state.available is True
        assert state.containers == (
            DockerContainer("abc123", "web", "nginx", "running"),
        )

    def test_query_docker_state_reports_docker_error(self, monkeypatch):
        monkeypatch.setattr(
            docker_state_mod.shutil, "which", lambda _cmd: "/bin/docker"
        )
        monkeypatch.setattr(
            docker_state_mod.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="permission denied",
            ),
        )

        state = query_docker_state()

        assert state.available is False
        assert state.error == "Docker is unavailable"

    def test_query_docker_state_handles_timeout(self, monkeypatch):
        monkeypatch.setattr(
            docker_state_mod.shutil, "which", lambda _cmd: "/bin/docker"
        )

        def fail(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(cmd="docker", timeout=1)

        monkeypatch.setattr(docker_state_mod.subprocess, "run", fail)

        assert query_docker_state().available is False

    def test_stop_and_restart_run_docker_actions(self, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(docker_state_mod.subprocess, "run", fake_run)

        assert stop_container("abc123") is True
        assert restart_container("def456") is True
        assert calls == [
            ["docker", "stop", "abc123"],
            ["docker", "restart", "def456"],
        ]

    def test_action_failure_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            docker_state_mod.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="no such container",
            ),
        )

        assert stop_container("missing") is False

    def test_tooltips_cover_availability_and_count(self):
        assert "not found" in docker_tooltip(
            DockerState(available=False, error="not found")
        )
        assert docker_tooltip(DockerState(available=True)) == (
            "Docker: no running containers"
        )
        assert (
            docker_tooltip(DockerState(available=True, containers=(_container(),)))
            == "Docker: 1 running container"
        )


class TestDockerApplet:
    def test_presents_state_and_tooltip(self, monkeypatch):
        state = DockerState(available=True, containers=(_container(),))
        monkeypatch.setattr(docker_applet_mod, "query_docker_state", lambda: state)
        monkeypatch.setattr(
            docker_applet_mod, "render_icon", lambda **_kwargs: object()
        )

        applet = DockerApplet(icon_size=48, config=Config())

        assert applet.item.name == "Docker: 1 running container"
        assert applet.item.icon is not None

    def test_menu_for_unavailable_docker_has_status_and_refresh(self, monkeypatch):
        monkeypatch.setattr(
            docker_applet_mod,
            "query_docker_state",
            lambda: DockerState(available=False, error="Docker command not found"),
        )
        monkeypatch.setattr(
            docker_applet_mod, "render_icon", lambda **_kwargs: object()
        )
        monkeypatch.setattr(docker_applet_mod, "BackgroundWorker", _InlineWorker)

        applet = DockerApplet(icon_size=48, config=Config())
        items = applet.get_menu_items()

        labels = [item.get_label() for item in items]
        assert labels[0] == "Docker unavailable: Docker command not found"
        assert labels[-1] == "Refresh Now"

    def test_menu_builds_stop_and_restart_submenu(self, monkeypatch):
        container = _container()
        state = DockerState(available=True, containers=(container,))
        monkeypatch.setattr(docker_applet_mod, "query_docker_state", lambda: state)
        monkeypatch.setattr(
            docker_applet_mod, "render_icon", lambda **_kwargs: object()
        )
        monkeypatch.setattr(docker_applet_mod, "BackgroundWorker", _InlineWorker)

        applet = DockerApplet(icon_size=48, config=Config())
        items = applet.get_menu_items()
        container_item = next(
            item for item in items if item.get_label() == "web (nginx:latest)"
        )
        submenu = container_item.get_submenu()

        assert [child.get_label() for child in submenu.children] == [
            "nginx:latest",
            "Up 2 minutes",
            "",
            "Stop",
            "Restart",
        ]

    def test_stop_menu_action_runs_container_action_and_refreshes(self, monkeypatch):
        initial = DockerState(available=True, containers=(_container(),))
        refreshed = DockerState(available=True, containers=())
        states = iter([initial, refreshed])
        stopped: list[str] = []
        monkeypatch.setattr(
            docker_applet_mod, "query_docker_state", lambda: next(states)
        )
        monkeypatch.setattr(
            docker_applet_mod,
            "stop_container",
            lambda container_id: stopped.append(container_id) or True,
        )
        monkeypatch.setattr(
            docker_applet_mod, "render_icon", lambda **_kwargs: object()
        )
        monkeypatch.setattr(docker_applet_mod, "BackgroundWorker", _InlineWorker)

        applet = DockerApplet(icon_size=48, config=Config())
        container_item = next(
            item
            for item in applet.get_menu_items()
            if item.get_label() == "web (nginx:latest)"
        )
        stop_item = container_item.get_submenu().children[3]
        callback, args = stop_item._signals["activate"][0]
        callback(stop_item, *args)

        assert stopped == ["abc123"]
        assert applet.item.name == "Docker: no running containers"

    def test_click_refreshes_state(self, monkeypatch):
        initial = DockerState(available=True, containers=())
        refreshed = DockerState(available=True, containers=(_container(),))
        states = iter([initial, refreshed])
        monkeypatch.setattr(
            docker_applet_mod, "query_docker_state", lambda: next(states)
        )
        monkeypatch.setattr(
            docker_applet_mod, "render_icon", lambda **_kwargs: object()
        )
        monkeypatch.setattr(docker_applet_mod, "BackgroundWorker", _InlineWorker)

        applet = DockerApplet(icon_size=48, config=Config())
        applet.on_clicked()

        assert applet.item.name == "Docker: 1 running container"


class TestDockerRender:
    def test_render_icon_returns_pixbuf(self):
        pixbuf = render_icon(size=48, running_count=3, available=True)

        assert pixbuf is not None
        assert pixbuf.get_width() == 48
        assert pixbuf.get_height() == 48
