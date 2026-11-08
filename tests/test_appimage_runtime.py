from __future__ import annotations

import hashlib
import importlib.util
import io
from pathlib import Path
from urllib.error import HTTPError

import pytest


@pytest.fixture
def apprun(monkeypatch):
    script = Path(__file__).resolve().parents[1] / "packaging/appimage/fetch_apprun.py"
    spec = importlib.util.spec_from_file_location("fetch_apprun", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assets = {
        arch: tuple(
            (name, asset_id, hashlib.sha256(str(asset_id).encode()).hexdigest())
            for name, asset_id, _checksum in entries
        )
        for arch, entries in module.ASSETS.items()
    }
    monkeypatch.setattr(module, "ASSETS", assets)
    return module


@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
def test_caches_architecture_runtime_from_asset_api(
    apprun, arch, tmp_path, monkeypatch
):
    requests = []

    def download(request, *, timeout):
        assert request.get_header("Accept") == "application/octet-stream"
        assert timeout == 60
        requests.append(request.full_url)
        return io.BytesIO(request.full_url.rsplit("/", 1)[1].encode())

    monkeypatch.setattr(apprun, "urlopen", download)
    apprun.cache_apprun(arch=arch, build_dir=tmp_path)

    for name, asset_id, _checksum in apprun.ASSETS[arch]:
        assert (
            f"https://api.github.com/repos/AppImageCrafters/AppRun/releases/assets/{asset_id}"
            in requests
        )
        assert (tmp_path / "AppRun" / apprun.VERSION / name).read_bytes() == str(
            asset_id
        ).encode()
    assert len(requests) == 2


def test_reuses_only_verified_cached_runtime(apprun, tmp_path, monkeypatch):
    cache = tmp_path / "AppRun" / apprun.VERSION
    cache.mkdir(parents=True)
    entries = apprun.ASSETS["x86_64"]
    for name, asset_id, _checksum in entries:
        (cache / name).write_bytes(str(asset_id).encode())

    def download(request, *, timeout):
        return io.BytesIO(request.full_url.rsplit("/", 1)[1].encode())

    monkeypatch.setattr(apprun, "urlopen", download)
    # Verified entries never need the network.
    with monkeypatch.context() as patch:
        patch.setattr(
            apprun, "urlopen", lambda *_args, **_kwargs: pytest.fail("download")
        )
        apprun.cache_apprun(arch="x86_64", build_dir=tmp_path)

    # A partial or corrupt cached asset must be replaced before the builder runs.
    name, asset_id, _checksum = entries[0]
    (cache / name).write_bytes(b"partial")
    apprun.cache_apprun(arch="x86_64", build_dir=tmp_path)
    assert (cache / name).read_bytes() == str(asset_id).encode()


@pytest.mark.parametrize("failure", ["checksum", "http"])
def test_failed_download_never_populates_cache(apprun, failure, tmp_path, monkeypatch):
    def download(request, *, timeout):
        if failure == "http":
            raise HTTPError(request.full_url, 504, "Gateway timeout", None, None)
        return io.BytesIO(b"unexpected asset contents")

    monkeypatch.setattr(apprun, "urlopen", download)
    with pytest.raises(HTTPError if failure == "http" else ValueError):
        apprun.cache_apprun(arch="aarch64", build_dir=tmp_path)
    assert not list((tmp_path / "AppRun" / apprun.VERSION).iterdir())
