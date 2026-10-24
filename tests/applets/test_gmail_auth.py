"""Exercise the real localhost OAuth callback without contacting Google."""

from __future__ import annotations

import http.client
import json
import socket
import threading
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from google_auth_oauthlib.flow import Flow

from docking.applets.gmail import auth
from docking.applets.gmail.auth import (
    LABEL_SCOPE,
    SCOPES,
    ClientConfig,
    GmailError,
    Operation,
    authorize,
    load_client_config,
)

CLIENT = ClientConfig("test.apps.googleusercontent.com", "public-desktop-value")


@pytest.fixture(autouse=True)
def short_auth_deadline(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_TIMEOUT", 5)


@pytest.fixture
def token_exchange(monkeypatch):
    seen = []

    def fetch_token(flow, **kwargs):
        seen.append(kwargs)
        flow.oauth2session.token = {
            "access_token": "synthetic-access-token",
            "refresh_token": "synthetic-refresh-token",
            "token_type": "Bearer",
            "expires_at": 9999999999,
            "scope": list(SCOPES),
        }

    monkeypatch.setattr(Flow, "fetch_token", fetch_token)
    return seen


@pytest.mark.parametrize(
    "outcome", ["success", "denied", "missing-scope", "no-refresh-token"]
)
def test_callback_validates_state_and_consent(
    token_exchange, capsys, monkeypatch, outcome
):
    threads = []
    results = []
    redirects = []
    exchange = Flow.fetch_token

    def exchange_result(flow, **kwargs):
        exchange(flow, **kwargs)
        if outcome == "missing-scope":
            flow.oauth2session.token["scope"] = ["openid"]
        if outcome == "no-refresh-token":
            flow.oauth2session.token.pop("refresh_token")

    monkeypatch.setattr(Flow, "fetch_token", exchange_result)

    def open_url(url):
        query = parse_qs(urlsplit(url).query)
        assert query["code_challenge_method"] == ["S256"]
        assert len(query["code_challenge"][0]) > 40
        assert set(query["scope"][0].split()) == set(SCOPES)
        redirect = urlsplit(query["redirect_uri"][0])
        redirects.append(redirect)

        def callback():
            for state in ("wrong-state", "invalid-✓", query["state"][0]):
                connection = http.client.HTTPConnection(
                    "127.0.0.1", redirect.port, timeout=5
                )
                connection.request(
                    "GET",
                    redirect.path
                    + "?"
                    + urlencode(
                        {
                            "state": state,
                            **(
                                {"error": "access_denied"}
                                if outcome == "denied"
                                else {"code": "synthetic-authorization-code"}
                            ),
                        }
                    ),
                )
                response = connection.getresponse()
                results.append(response.status)
                response.read()
                connection.close()

        thread = threading.Thread(target=callback)
        threads.append(thread)
        thread.start()
        return True

    if outcome == "success":
        credentials = authorize(CLIENT, Operation(), open_url)
        assert credentials.refresh_token == "synthetic-refresh-token"
    else:
        with pytest.raises(GmailError) as error:
            authorize(CLIENT, Operation(), open_url)
        assert error.value.reconnect == (outcome != "denied")
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert results == [400, 400, 200]
    assert token_exchange == (
        []
        if outcome == "denied"
        else [{"code": "synthetic-authorization-code", "timeout": auth.HTTP_TIMEOUT}]
    )
    assert "synthetic-authorization-code" not in capsys.readouterr().err
    with socket.socket() as connection:
        assert connection.connect_ex(("127.0.0.1", redirects[0].port)) != 0


def test_cancellation_closes_listener_before_token_exchange(token_exchange):
    operation = Operation()

    def open_url(_url):
        operation.cancel()
        return True

    with pytest.raises(GmailError, match="cancelled"):
        authorize(CLIENT, operation, open_url)
    assert token_exchange == []


def test_timeout_does_not_exchange_tokens(monkeypatch, token_exchange):
    monkeypatch.setattr(auth, "AUTH_TIMEOUT", 0)
    with pytest.raises(GmailError, match="timed out"):
        authorize(CLIENT, Operation(), lambda _url: True)
    assert token_exchange == []


def test_browser_failure_is_actionable(token_exchange):
    with pytest.raises(GmailError, match="open the browser"):
        authorize(CLIENT, Operation(), lambda _url: False)
    assert token_exchange == []


def test_client_override_cannot_replace_google_endpoints(monkeypatch, tmp_path):
    path = tmp_path / "desktop-client.json"
    path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": CLIENT.client_id,
                    "client_secret": CLIENT.client_secret,
                    "auth_uri": "https://untrusted.test/auth",
                    "token_uri": "https://untrusted.test/token",
                }
            }
        )
    )
    monkeypatch.setenv("DOCKING_GMAIL_OAUTH_CLIENT_FILE", str(path))
    config = load_client_config().google_config()["installed"]
    assert config["auth_uri"] == auth.AUTH_URI
    assert config["token_uri"] == auth.TOKEN_URI
    assert LABEL_SCOPE in SCOPES


def test_oauth_errors_do_not_leak_response_tokens(monkeypatch):
    def fail(*_args, **_kwargs):
        raise ValueError("sensitive-access-token")

    monkeypatch.setattr(Flow, "from_client_config", fail)
    with pytest.raises(GmailError) as error:
        authorize(CLIENT, Operation(), lambda _url: True)
    assert "sensitive" not in str(error.value)
