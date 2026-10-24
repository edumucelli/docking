"""Gmail count semantics, isolated account failures, and lifecycle ownership."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from docking.applets import get_applet_catalog, load_applet_class
from docking.applets.gmail import applet as applet_mod
from docking.applets.gmail.api import GmailClient, response_json
from docking.applets.gmail.applet import GmailApplet
from docking.applets.gmail.auth import ClientConfig, GmailError, Operation
from docking.applets.gmail.secrets import RefreshCredential, SecretStore
from docking.applets.gmail.state import (
    Account,
    GmailPrefs,
    Snapshot,
    badge_count,
    inbox_url,
    prefs_from_mapping,
    prefs_payload,
    retry_delay,
    tooltip,
)
from docking.core.config import Config

A = Account("subject-a", "personal@example.test", "connection-a")
B = Account("subject-b", "work@example.test", "connection-b")
CLIENT = ClientConfig("test.apps.googleusercontent.com", "public-client-metadata")


class QueueWorker:
    def __init__(self):
        self.jobs = []

    def run(self, **job):
        self.jobs.append(job)

    def finish(self, index=0):
        job = self.jobs.pop(index)
        try:
            result = job["fn"]()
        except Exception as error:
            if job.get("on_error"):
                job["on_error"](error)
        else:
            if job.get("on_result"):
                job["on_result"](result)


@pytest.fixture
def applet(monkeypatch):
    client = Mock()
    client.poll.return_value = 4
    client.disconnect.return_value = True
    monkeypatch.setattr(applet_mod, "load_client_config", lambda: CLIENT)
    monkeypatch.setattr(applet_mod, "GmailClient", lambda *_args: client)
    config = Config()
    config.applet_prefs["gmail"] = prefs_payload(GmailPrefs((A, B), A.subject))
    instance = GmailApplet(48, config)
    instance._worker = QueueWorker()
    instance.start(lambda: None)
    yield instance, client
    instance.stop()


def test_catalog_discovers_gmail_without_special_registry_entries():
    assert get_applet_catalog()["gmail"].name == "Gmail"
    assert load_applet_class("gmail") is GmailApplet


def test_prefs_round_trip_discards_unrecognized_secrets_and_duplicate_accounts():
    raw = prefs_payload(GmailPrefs((A, B), B.subject))
    raw["refresh_token"] = "must-not-be-persisted"
    raw["accounts"].extend([raw["accounts"][0], {"subject": "broken"}])
    parsed = prefs_from_mapping(raw)
    assert parsed == GmailPrefs((A, B), B.subject)
    assert "must-not-be-persisted" not in json.dumps(prefs_payload(parsed))
    assert prefs_from_mapping({"accounts": None}) == GmailPrefs()
    assert prefs_from_mapping({"accounts": [False]}) == GmailPrefs()
    assert prefs_from_mapping({**raw, "selected": []}).selected == A.subject


def test_unread_counts_are_aggregated_without_turning_unknown_or_failure_into_zero():
    prefs = GmailPrefs((A, B), A.subject)
    snapshots = {A.subject: Snapshot(unread=7), B.subject: Snapshot(error="offline")}
    assert badge_count(prefs, snapshots) == 7
    assert "incomplete" in tooltip(prefs, snapshots)
    snapshots[B.subject] = Snapshot(unread=3, error="offline")
    assert badge_count(prefs, snapshots) == 10
    assert "last known count" in tooltip(prefs, snapshots)
    assert "unknown" in tooltip(prefs, {})


def test_account_url_uses_email_instead_of_browser_account_index():
    account = replace(A, email="personal+label@example.test")
    parsed = urlsplit(inbox_url(account))
    assert parse_qs(parsed.query) == {"authuser": [account.email]}
    assert parsed.fragment == "inbox"


def test_backoff_is_bounded_and_respects_longer_provider_delay():
    assert [retry_delay(i) for i in (1, 2, 3, 4, 5, 1000)] == [
        60,
        120,
        240,
        480,
        900,
        900,
    ]
    assert retry_delay(1, 3600) == 3600


def test_missing_managed_client_has_actionable_state_without_loading_keyring(
    monkeypatch,
):
    def missing():
        raise GmailError("Gmail sign-in is not configured in this Docking build")

    monkeypatch.setattr(applet_mod, "load_client_config", missing)
    instance = GmailApplet(32, Config())
    assert "not configured" in instance.item.name
    assert instance.item.badge_visible is False
    rows = instance.get_menu_items()
    assert any(
        r.get_label() == "Sign in with Google" and not r.get_sensitive() for r in rows
    )


def test_polling_is_single_flight_and_one_failure_does_not_stop_other_accounts(applet):
    instance, client = applet
    instance._snapshots[A.subject] = Snapshot(unread=9)
    instance._refresh_all()
    instance._refresh_all()
    assert len(instance._worker.jobs) == 2
    client.poll.side_effect = [GmailError("network unavailable"), 2]
    instance._worker.finish()
    instance._worker.finish()
    assert instance.item.badge_count == 11
    assert instance._snapshots[A.subject].unread == 9
    assert instance._snapshots[A.subject].failures == 1
    assert not instance._snapshots[B.subject].error
    instance._refresh_all()
    assert len(instance._worker.jobs) == 1  # A respects backoff even on manual refresh.


def test_revoked_account_waits_for_reconnect(applet):
    instance, client = applet
    client.poll.side_effect = GmailError("reconnect", reconnect=True)
    instance._refresh(A)
    instance._worker.finish()
    instance._refresh(A, manual=True)
    assert instance._worker.jobs == []
    assert any(
        "Reconnect personal" in row.get_label() for row in instance.get_menu_items()
    )


def test_stop_invalidates_pending_results_and_cancels_operations(applet):
    instance, _client = applet
    instance._refresh(A)
    operation = instance._operations[A.subject]
    instance.stop()
    instance._worker.finish()
    assert instance._snapshots == {}
    with pytest.raises(GmailError, match="cancelled"):
        operation.check()
    assert instance._timer == instance._startup_timer == 0


def test_disconnect_cannot_be_undone_by_a_late_poll(applet):
    instance, client = applet
    instance._refresh(A)
    instance._disconnect(A)
    instance._worker.finish(1)  # Disconnect finishes before the stale poll callback.
    instance._worker.finish()
    assert instance._prefs.accounts == (B,)
    assert instance._prefs.selected == B.subject
    assert A.subject not in instance._snapshots
    assert client.disconnect.call_args.args == (A,)


def test_explicit_disconnect_cleanup_finishes_after_stop(applet):
    instance, _client = applet
    instance._disconnect(A)
    instance.stop()
    instance._worker.finish()
    assert instance._prefs.accounts == (B,)
    assert len(instance.load_prefs()["accounts"]) == 1


def test_disconnect_keyring_failure_keeps_account_for_retry(applet):
    instance, client = applet
    client.disconnect.side_effect = GmailError("unlock keyring", paused=True)
    instance._disconnect(A)
    instance._worker.finish()
    assert A in instance._prefs.accounts
    assert instance._snapshots[A.subject].paused


def test_revocation_failure_still_disconnects_locally(applet):
    instance, client = applet
    client.disconnect.return_value = False
    instance._disconnect(A)
    instance._worker.finish()
    assert A not in instance._prefs.accounts
    assert "Disconnected locally" in instance.item.name


def test_reconnect_deduplicates_account_and_replaces_only_old_secret(applet):
    instance, client = applet
    replacement = replace(A, credential_id="new-connection")
    client.connect.return_value = replacement
    instance._sign_in()
    instance._worker.finish()
    assert instance._prefs.accounts == (replacement, B)
    instance._worker.finish()  # Remove the obsolete connection without revoking Google.
    client.forget.assert_called_once_with(A)
    assert not client.disconnect.called
    assert "refresh_token" not in json.dumps(instance.load_prefs())


def test_cancelled_sign_in_cannot_add_an_account(applet):
    instance, client = applet
    late = Account("late", "late@example.test", "late-connection")
    client.connect.return_value = late
    instance._sign_in()
    instance._cancel_sign_in()
    instance._worker.finish()
    instance._worker.finish()  # Discard credentials created before cancellation won.
    assert late not in instance._prefs.accounts
    client.forget.assert_called_once_with(late)


def test_opening_account_remembers_it_and_hides_verified_zero_badge(
    applet, monkeypatch
):
    instance, _client = applet
    opened = []
    monkeypatch.setattr(
        applet_mod.targets, "open_target", lambda url: opened.append(url) or True
    )
    instance._open_inbox(B)
    instance.on_clicked()
    assert opened == [inbox_url(B), inbox_url(B)]
    instance._snapshots = {A.subject: Snapshot(unread=0), B.subject: Snapshot(unread=0)}
    instance.present()
    assert not instance.item.badge_visible
    assert "incomplete" not in instance.item.name


@pytest.mark.parametrize(
    "status,body,reconnect,paused",
    [
        (401, {}, True, False),
        (403, {"error": {"errors": [{"reason": "domainPolicy"}]}}, False, True),
        (403, {"error": {"errors": [{"reason": "rateLimitExceeded"}]}}, False, False),
        (429, {}, False, False),
        (503, {}, False, False),
    ],
)
def test_provider_errors_are_sanitized_and_classified(status, body, reconnect, paused):
    response = SimpleNamespace(
        status_code=status, json=lambda: body, headers={"Retry-After": "3600"}
    )
    with pytest.raises(GmailError) as error:
        response_json(response)
    assert error.value.reconnect == reconnect
    assert error.value.paused == paused
    if status in (429, 503):
        assert error.value.retry_after == 3600


def test_keyring_payload_is_bound_to_client_and_connection(monkeypatch):
    store = SecretStore(CLIENT)
    secret = Mock()
    secret.password_store_sync.return_value = True
    monkeypatch.setattr(store, "_call", lambda op, fn: fn(secret, "schema", "cancel"))
    store.save(A, "synthetic-refresh-token", Operation())
    args = secret.password_store_sync.call_args.args
    assert args[1] == {
        "client_id": CLIENT.client_id,
        "subject": A.subject,
        "credential_id": A.credential_id,
    }
    secret.password_lookup_sync.return_value = args[4]
    assert store.load(A, Operation()) == RefreshCredential(
        CLIENT.client_id, "synthetic-refresh-token"
    )
    secret.password_lookup_sync.return_value = (
        '{"client_id":"other","refresh_token":"secret"}'
    )
    with pytest.raises(GmailError, match="reconnect"):
        store.load(A, Operation())
    assert "synthetic-refresh-token" not in repr(
        RefreshCredential(CLIENT.client_id, "synthetic-refresh-token")
    )


def test_missing_libsecret_never_falls_back_to_files(monkeypatch):
    import gi

    def unavailable(*_args):
        raise ValueError("unavailable")

    monkeypatch.setattr(gi, "require_version", unavailable)
    with pytest.raises(GmailError, match="keyring") as error:
        SecretStore(CLIENT).probe(Operation())
    assert error.value.paused


def test_sdk_debug_logging_does_not_expose_tokens(caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        GmailClient(CLIENT, lambda _url: True)
        logging.getLogger("requests_oauthlib.oauth2_session").debug(
            "synthetic-secret-token"
        )
        logging.getLogger("oauthlib.oauth2.rfc6749.clients.base").debug(
            "synthetic-secret-token"
        )
    assert "synthetic-secret-token" not in caplog.text


def test_manual_retry_can_unlock_a_paused_keyring_without_waiting(applet):
    instance, _client = applet
    instance._snapshots[A.subject] = Snapshot(
        error="locked", paused=True, next_refresh=float("inf")
    )
    instance._refresh(A)
    assert not instance._worker.jobs
    instance._refresh(A, manual=True)
    assert len(instance._worker.jobs) == 1


@pytest.mark.parametrize("value", [True, -1, "7", None])
def test_poll_rejects_invalid_counts(monkeypatch, value):
    client = GmailClient(CLIENT, lambda _url: True)
    client.store = Mock()
    client.store.load.return_value = RefreshCredential(CLIENT.client_id, "synthetic")
    monkeypatch.setattr(client, "_get", lambda *_args: {"threadsUnread": value})
    with pytest.raises(GmailError, match="invalid unread count"):
        client.poll(A, Operation())


def test_poll_uses_inbox_conversation_count_and_saves_rotated_token(monkeypatch):
    from google.oauth2.credentials import Credentials

    client = GmailClient(CLIENT, lambda _url: True)
    client.store = Mock()
    client.store.load.return_value = RefreshCredential(CLIENT.client_id, "old-token")
    credentials = Credentials("access-token", refresh_token="new-token")
    client._credentials[A.credential_id] = credentials
    requested = []
    monkeypatch.setattr(
        client,
        "_get",
        lambda _creds, url, _op: (
            requested.append(url) or {"threadsUnread": 3, "messagesUnread": 17}
        ),
    )
    assert client.poll(A, Operation()) == 3
    assert requested[0].endswith("/labels/INBOX")
    assert client.store.save.call_args.args[:2] == (A, "new-token")


def test_cancelled_poll_never_writes_rotated_credentials(monkeypatch):
    client = GmailClient(CLIENT, lambda _url: True)
    client.store = Mock()
    client.store.load.return_value = RefreshCredential(CLIENT.client_id, "synthetic")
    operation = Operation()

    def cancelled(*_args):
        operation.cancel()
        return {"threadsUnread": 3}

    monkeypatch.setattr(client, "_get", cancelled)
    with pytest.raises(GmailError, match="cancelled"):
        client.poll(A, operation)
    client.store.save.assert_not_called()
