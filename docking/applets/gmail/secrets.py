"""Refresh credentials stored only through libsecret's desktop/portal backend."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from docking.applets.gmail.auth import ClientConfig, GmailError, Operation
from docking.applets.gmail.state import Account
from docking.i18n import _

T = TypeVar("T")
KEYRING_TIMEOUT = 30


@dataclass(frozen=True, slots=True)
class RefreshCredential:
    client_id: str
    refresh_token: str = field(repr=False)


class SecretStore:
    """Serialize keyring mutations so cancelled refreshes cannot undo deletion."""

    def __init__(self, config: ClientConfig) -> None:
        self._config = config
        self._lock = threading.RLock()

    def _call(self, operation: Operation, fn: Callable[..., T]) -> T:
        try:
            import gi

            gi.require_version("Secret", "1")
            from gi.repository import Gio, Secret

            schema = Secret.Schema.new(
                "org.docking.Gmail",
                Secret.SchemaFlags.NONE,
                {
                    "client_id": Secret.SchemaAttributeType.STRING,
                    "subject": Secret.SchemaAttributeType.STRING,
                    "credential_id": Secret.SchemaAttributeType.STRING,
                },
            )
            cancellable = Gio.Cancellable()
            unregister = operation.on_cancel(cancellable.cancel)
            timer = threading.Timer(KEYRING_TIMEOUT, cancellable.cancel)
            timer.daemon = True
            try:
                timer.start()
                with self._lock:
                    operation.check()
                    return fn(Secret, schema, cancellable)
            finally:
                timer.cancel()
                unregister()
        except GmailError:
            raise
        except Exception:
            operation.check()
            raise GmailError(
                _("Unlock or install a desktop keyring, then retry Gmail"), paused=True
            ) from None

    def _attributes(self, account: Account) -> dict[str, str]:
        return {
            "client_id": self._config.client_id,
            "subject": account.subject,
            "credential_id": account.credential_id,
        }

    def probe(self, operation: Operation) -> None:
        self.load(Account("probe", "", "probe"), operation)

    def load(self, account: Account, operation: Operation) -> RefreshCredential | None:
        raw = self._call(
            operation,
            lambda secret, schema, cancel: secret.password_lookup_sync(
                schema,
                self._attributes(account),
                cancel,
            ),
        )
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            if (
                data["client_id"] != self._config.client_id
                or not isinstance(data["refresh_token"], str)
                or not data["refresh_token"]
            ):
                raise ValueError
            return RefreshCredential(data["client_id"], data["refresh_token"])
        except (ValueError, KeyError, TypeError):
            raise GmailError(
                _("Stored Gmail credentials are unavailable; reconnect this account"),
                reconnect=True,
            ) from None

    def save(self, account: Account, token: str, operation: Operation) -> None:
        raw = json.dumps({"client_id": self._config.client_id, "refresh_token": token})
        success = self._call(
            operation,
            lambda secret, schema, cancel: secret.password_store_sync(
                schema,
                self._attributes(account),
                secret.COLLECTION_DEFAULT,
                "Gmail in Docking",
                raw,
                cancel,
            ),
        )
        if not success:
            raise GmailError(
                _("Could not save Gmail credentials in the desktop keyring"),
                paused=True,
            )

    def delete(self, account: Account, operation: Operation) -> None:
        # A missing record is already deleted; libsecret returns False for it.
        self._call(
            operation,
            lambda secret, schema, cancel: secret.password_clear_sync(
                schema,
                self._attributes(account),
                cancel,
            ),
        )
