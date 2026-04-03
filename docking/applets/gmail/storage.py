"""Secret storage helpers for the Gmail applet."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from docking.log import get_logger

log = get_logger("gmail.storage")

SECRET_SCHEMA_NAME = "com.docking.gmail"
SECRET_KIND_CLIENT = "client"
SECRET_KIND_CREDENTIALS = "credentials"


class SecretStorageError(RuntimeError):
    """Raised when libsecret is unavailable or rejects an operation."""


def secret_storage_available() -> bool:
    try:
        _secret_module()
    except SecretStorageError:
        return False
    return True


def load_client_config(*, applet_id: str) -> dict[str, Any] | None:
    return _load_json_secret(applet_id=applet_id, kind=SECRET_KIND_CLIENT)


def save_client_config(*, applet_id: str, client_config: Mapping[str, Any]) -> None:
    _save_json_secret(
        applet_id=applet_id,
        kind=SECRET_KIND_CLIENT,
        payload=client_config,
        label="Docking Gmail OAuth client",
    )


def load_credentials(*, applet_id: str) -> dict[str, Any] | None:
    return _load_json_secret(applet_id=applet_id, kind=SECRET_KIND_CREDENTIALS)


def save_credentials(*, applet_id: str, credentials: Mapping[str, Any]) -> None:
    _save_json_secret(
        applet_id=applet_id,
        kind=SECRET_KIND_CREDENTIALS,
        payload=credentials,
        label="Docking Gmail OAuth credentials",
    )


def delete_client_config(*, applet_id: str) -> None:
    _clear_secret(applet_id=applet_id, kind=SECRET_KIND_CLIENT)


def delete_credentials(*, applet_id: str) -> None:
    _clear_secret(applet_id=applet_id, kind=SECRET_KIND_CREDENTIALS)


def clear_all(*, applet_id: str) -> None:
    delete_credentials(applet_id=applet_id)
    delete_client_config(applet_id=applet_id)


def _load_json_secret(*, applet_id: str, kind: str) -> dict[str, Any] | None:
    secret = _lookup_secret(applet_id=applet_id, kind=kind)
    if not secret:
        return None
    try:
        parsed = json.loads(secret)
    except json.JSONDecodeError as exc:
        log.warning("Failed to parse stored Gmail %s secret: %s", kind, exc)
        return None
    if not isinstance(parsed, dict):
        log.warning("Stored Gmail %s secret is not a JSON object", kind)
        return None
    return parsed


def _save_json_secret(
    *,
    applet_id: str,
    kind: str,
    payload: Mapping[str, Any],
    label: str,
) -> None:
    secret = _secret_module()
    try:
        blob = json.dumps(payload, sort_keys=True)
        stored = secret.password_store_sync(
            _schema(secret=secret),
            _attrs(applet_id=applet_id, kind=kind),
            secret.COLLECTION_DEFAULT,
            label,
            blob,
            None,
        )
    except Exception as exc:
        log.warning("Failed to store Gmail %s secret: %s", kind, exc)
        raise SecretStorageError(str(exc)) from exc
    if not stored:
        raise SecretStorageError(f"Failed to store Gmail {kind} secret")


def _lookup_secret(*, applet_id: str, kind: str) -> str | None:
    secret = _secret_module()
    try:
        return secret.password_lookup_sync(
            _schema(secret=secret),
            _attrs(applet_id=applet_id, kind=kind),
            None,
        )
    except Exception as exc:
        log.warning("Failed to read Gmail %s secret: %s", kind, exc)
        raise SecretStorageError(str(exc)) from exc


def _clear_secret(*, applet_id: str, kind: str) -> None:
    secret = _secret_module()
    try:
        secret.password_clear_sync(
            _schema(secret=secret),
            _attrs(applet_id=applet_id, kind=kind),
            None,
        )
    except Exception as exc:
        log.warning("Failed to clear Gmail %s secret: %s", kind, exc)
        raise SecretStorageError(str(exc)) from exc


def _attrs(*, applet_id: str, kind: str) -> dict[str, str]:
    return {"applet_id": applet_id, "kind": kind}


def _schema(*, secret):
    return secret.Schema.new(
        SECRET_SCHEMA_NAME,
        secret.SchemaFlags.NONE,
        {
            "applet_id": secret.SchemaAttributeType.STRING,
            "kind": secret.SchemaAttributeType.STRING,
        },
    )


def _secret_module():
    try:
        import gi

        gi.require_version("Secret", "1")
        from gi.repository import Secret
    except Exception as exc:
        log.warning("libsecret is unavailable for Gmail applet storage: %s", exc)
        raise SecretStorageError(str(exc)) from exc
    return Secret
