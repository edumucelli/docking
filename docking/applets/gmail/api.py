"""Small Gmail API boundary; provider errors never expose response bodies."""

from __future__ import annotations

import datetime as dt
import email.utils
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from docking.applets.gmail.auth import (
    HTTP_TIMEOUT,
    SCOPES,
    TOKEN_URI,
    ClientConfig,
    GmailError,
    Operation,
    authorize,
    protect_auth_logs,
)
from docking.applets.gmail.secrets import SecretStore
from docking.applets.gmail.state import Account
from docking.i18n import _

if TYPE_CHECKING:
    from google.oauth2.credentials import Credentials

INBOX_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/labels/INBOX"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"


def _retry_after(value: str) -> float:
    try:
        return max(0, int(value))
    except ValueError:
        try:
            date = email.utils.parsedate_to_datetime(value)
            return max(0, (date - dt.datetime.now(dt.timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0


def response_json(response: Any) -> dict[str, Any]:
    status = response.status_code
    if status == 401:
        raise GmailError(
            _("Sign in again to reconnect this Gmail account"), reconnect=True
        )
    try:
        data = response.json()
    except (TypeError, ValueError):
        data = {}
    if status == 403:
        error = data.get("error", {}) if isinstance(data, dict) else {}
        reasons = (
            {
                item.get("reason")
                for item in error.get("errors", [])
                if isinstance(item, dict)
            }
            if isinstance(error, dict)
            else set()
        )
        if not reasons.intersection(
            {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}
        ):
            raise GmailError(
                _(
                    "Gmail access is unavailable; "
                    "check Google permissions or organization policy"
                ),
                paused=True,
            )
    if status == 403 or status == 429 or status >= 500:
        raise GmailError(
            _("Gmail is temporarily unavailable; retrying later"),
            retry_after=_retry_after(response.headers.get("Retry-After", "")),
        )
    if status != 200:
        raise GmailError(
            _("Gmail request failed; retry or reconnect this account"), paused=True
        )
    if not isinstance(data, dict):
        raise GmailError(_("Gmail returned an invalid response"))
    return data


class GmailClient:
    """Keep access tokens in memory and refresh tokens in SecretStore only."""

    def __init__(self, config: ClientConfig, open_url: Callable[[str], bool]) -> None:
        protect_auth_logs()
        self.config = config
        self.store = SecretStore(config)
        self._open_url = open_url
        self._credentials: dict[str, Credentials] = {}

    def _get(
        self, credentials: Credentials, url: str, operation: Operation
    ) -> dict[str, Any]:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import AuthorizedSession

        operation.check()
        try:
            with AuthorizedSession(
                credentials, refresh_timeout=HTTP_TIMEOUT, max_refresh_attempts=1
            ) as session:
                response = session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=False)
                operation.check()
                return response_json(response)
        except GmailError:
            raise
        except RefreshError as error:
            if error.retryable:
                raise GmailError(
                    _("Google sign-in service is temporarily unavailable")
                ) from None
            raise GmailError(
                _("Sign in again to reconnect this Gmail account"), reconnect=True
            ) from None
        except Exception:
            operation.check()
            raise GmailError(
                _("Could not reach Gmail; check your connection")
            ) from None

    def connect(self, operation: Operation) -> Account:
        self.store.probe(operation)
        credentials = authorize(self.config, operation, self._open_url)
        data = self._get(credentials, USERINFO_ENDPOINT, operation)
        subject, address = data.get("sub"), data.get("email")
        if (
            not isinstance(subject, str)
            or not 0 < len(subject) <= 255
            or not isinstance(address, str)
            or not 0 < len(address) <= 320
            or "@" not in address
            or data.get("email_verified") is not True
            or any(ord(c) < 32 or ord(c) == 127 for c in subject + address)
        ):
            raise GmailError(_("Google did not provide a verified account identity"))
        account = Account(subject, address, uuid.uuid4().hex)
        try:
            operation.check()
            self.store.save(account, credentials.refresh_token or "", operation)
            operation.check()
        except GmailError:
            self.forget(account)
            raise
        self._credentials[account.credential_id] = credentials
        return account

    def poll(self, account: Account, operation: Operation) -> int:
        from google.oauth2.credentials import Credentials

        record = self.store.load(account, operation)
        if record is None:
            raise GmailError(
                _("Stored Gmail credentials are unavailable; reconnect this account"),
                reconnect=True,
            )
        credentials = self._credentials.get(account.credential_id)
        if credentials is None:
            credentials = Credentials(
                None,
                refresh_token=record.refresh_token,
                token_uri=TOKEN_URI,
                client_id=self.config.client_id,
                client_secret=self.config.client_secret,
                scopes=SCOPES,
            )
        data = self._get(credentials, INBOX_ENDPOINT, operation)
        unread = data.get("threadsUnread")
        if isinstance(unread, bool) or not isinstance(unread, int) or unread < 0:
            raise GmailError(_("Gmail returned an invalid unread count"))
        operation.check()
        if (
            credentials.refresh_token
            and credentials.refresh_token != record.refresh_token
        ):
            self.store.save(account, credentials.refresh_token, operation)
        operation.check()
        self._credentials[account.credential_id] = credentials
        return unread

    def forget(self, account: Account) -> None:
        """Delete an abandoned/replaced connection without revoking a newer grant."""
        self._credentials.pop(account.credential_id, None)
        self.store.delete(account, Operation())

    def disconnect(self, account: Account) -> bool:
        import requests

        operation = Operation()
        record = self.store.load(account, operation)
        self.store.delete(account, operation)
        self._credentials.pop(account.credential_id, None)
        if record is None:
            return True
        try:
            with requests.Session() as session:
                response = session.post(
                    REVOKE_ENDPOINT,
                    data={"token": record.refresh_token},
                    timeout=HTTP_TIMEOUT,
                    allow_redirects=False,
                )
                return bool(response.status_code == 200)
        except requests.RequestException:
            return False
