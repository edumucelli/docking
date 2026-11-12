"""Release age and bootstrap integrity must be checked before pip executes."""

import hashlib
import io
import json
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from urllib.error import URLError

import pytest

from tools import dependency_policy as policy

BEFORE = datetime(2026, 11, 11, tzinfo=timezone.utc)


def metadata(uploaded="2026-11-02T20:34:25Z"):
    return {
        "urls": [
            {
                "filename": policy.PIP_FILENAME,
                "upload_time_iso_8601": uploaded,
                "yanked": False,
                "digests": {"sha256": policy.PIP_SHA256},
                "url": "https://files.pythonhosted.org/packages/pip.whl",
            }
        ]
    }


def test_cutoff_is_utc_and_reused(monkeypatch):
    monkeypatch.delenv("PIP_UPLOADED_PRIOR_TO", raising=False)
    now = datetime.now(timezone.utc)
    assert abs((policy.cutoff() - (now - timedelta(days=7))).total_seconds()) < 1
    saved = now - timedelta(days=8)
    monkeypatch.setenv("PIP_UPLOADED_PRIOR_TO", saved.isoformat())
    assert policy.cutoff() == saved


@pytest.mark.parametrize("value", ["2099-01-01T00:00:00Z", "2026-01-01", "P0D"])
def test_cutoff_cannot_relax_policy(monkeypatch, value):
    monkeypatch.setenv("PIP_UPLOADED_PRIOR_TO", value)
    with pytest.raises(ValueError):
        policy.cutoff()


def test_old_wheel_is_eligible():
    assert policy.validate_wheel(metadata(), BEFORE).startswith(
        "https://files.pythonhosted.org/"
    )


@pytest.mark.parametrize(
    "uploaded", ["2026-11-11T00:00:00Z", "2026-11-11T00:00:01Z", "2026-11-10T23:00:00"]
)
def test_fresh_or_timezone_free_wheel_is_rejected(uploaded):
    with pytest.raises(ValueError):
        policy.validate_wheel(metadata(uploaded), BEFORE)


@pytest.mark.parametrize("change", ["hash", "yanked", "url", "missing"])
def test_untrusted_metadata_is_rejected(change):
    data = metadata()
    if change == "hash":
        data["urls"][0]["digests"]["sha256"] = "bad"
    elif change == "yanked":
        data["urls"][0]["yanked"] = True
    elif change == "url":
        data["urls"][0]["url"] = "http://example.org/pip.whl"
    else:
        data["urls"] = []
    with pytest.raises(ValueError):
        policy.validate_wheel(data, BEFORE)


@pytest.mark.parametrize("valid", [True, False])
def test_download_hash_is_verified_before_install(monkeypatch, valid):
    payload = b"verified wheel"
    monkeypatch.setattr(policy, "PIP_SHA256", hashlib.sha256(payload).hexdigest())
    responses = iter(
        [
            io.BytesIO(json.dumps(metadata()).encode()),
            io.BytesIO(payload if valid else b"modified"),
        ]
    )
    monkeypatch.setattr(policy, "urlopen", lambda *args, **kwargs: next(responses))
    install = Mock()
    monkeypatch.setattr(policy.subprocess, "run", install)
    if valid:
        policy.bootstrap(BEFORE)
        command = install.call_args.args[0]
        assert "--no-index" in command and "--no-deps" in command
        assert install.call_args.kwargs["check"]
    else:
        with pytest.raises(ValueError, match="SHA256"):
            policy.bootstrap(BEFORE)
        install.assert_not_called()


def test_metadata_network_failure_never_installs(monkeypatch):
    monkeypatch.setattr(policy, "urlopen", Mock(side_effect=URLError("offline")))
    install = Mock()
    monkeypatch.setattr(policy.subprocess, "run", install)
    with pytest.raises(URLError):
        policy.bootstrap(BEFORE)
    install.assert_not_called()


def test_bootstrap_persists_cutoff_only_after_success(monkeypatch, tmp_path):
    output = tmp_path / "environment"
    monkeypatch.setattr(policy, "cutoff", lambda: BEFORE)
    monkeypatch.setenv("GITHUB_ENV", str(output))
    monkeypatch.setenv("PIP_UPLOADED_PRIOR_TO", BEFORE.isoformat())
    monkeypatch.setattr(policy.sys, "argv", ["dependency_policy.py", "bootstrap"])
    monkeypatch.setattr(policy, "bootstrap", Mock(side_effect=ValueError("bad hash")))
    with pytest.raises(ValueError):
        policy.main()
    assert not output.exists()
    monkeypatch.setattr(policy, "bootstrap", Mock())
    policy.main()
    assert output.read_text() == "PIP_UPLOADED_PRIOR_TO=2026-11-11T00:00:00Z\n"


def test_main_and_security_workflows_bootstrap_every_online_pip_job():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for name in ("ci.yml", "security.yml"):
        text = (root / ".github/workflows" / name).read_text()
        assert "pip install --upgrade pip" not in text
        assert "date -d '7 days ago'" not in text
        for job in re.split(r"\n  (?=\S)", text)[1:]:
            if "-m pip install" in job:
                assert "tools/dependency_policy.py bootstrap" in job
                assert job.index("tools/dependency_policy.py bootstrap") < job.index(
                    "-m pip install"
                )


def test_missing_upload_time_fails_closed():
    data = metadata()
    del data["urls"][0]["upload_time_iso_8601"]
    with pytest.raises(KeyError):
        policy.validate_wheel(data, BEFORE)


def test_wheel_immediately_before_cutoff_is_eligible():
    assert policy.validate_wheel(metadata("2026-11-10T23:59:59Z"), BEFORE)
