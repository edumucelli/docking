"""GTK presentation and lifecycle for the optional multi-account Gmail applet."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.gmail import meta
from docking.applets.gmail.api import GmailClient
from docking.applets.gmail.auth import (
    CancelledError,
    GmailError,
    Operation,
    load_client_config,
)
from docking.applets.gmail.render import render_icon
from docking.applets.gmail.state import (
    REFRESH_SECONDS,
    Account,
    GmailPrefs,
    Snapshot,
    account_label,
    badge_count,
    inbox_url,
    prefs_from_mapping,
    prefs_payload,
    retry_delay,
    tooltip,
)
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.platform import targets

if TYPE_CHECKING:
    from docking.core.config import Config


class GmailApplet(Applet):
    id = meta.id
    name = _("Gmail")
    icon_name = "mail-unread"

    def __init__(self, icon_size: int, config: Config) -> None:
        self._prefs = prefs_from_mapping(config.applet_prefs.get(meta.id, {}))
        self._snapshots: dict[str, Snapshot] = {}
        self._operations: dict[str, Operation] = {}
        self._disconnecting: set[str] = set()
        self._auth: Operation | None = None
        self._running = False
        self._timer = 0
        self._startup_timer = 0
        self._worker = BackgroundWorker()
        self._client: GmailClient | None = None
        self._error = ""
        try:
            self._client = GmailClient(load_client_config(), targets.open_target)
        except GmailError as error:
            self._error = str(error)
        super().__init__(icon_size, config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(
            size,
            warning=bool(self._error) or any(s.error for s in self._snapshots.values()),
        )

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip(self._prefs, self._snapshots, self._error)
        self.item.badge_count = badge_count(self._prefs, self._snapshots)
        self.item.badge_visible = self.item.badge_count > 0

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify)
        self._running = True
        self._timer = GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)
        self._startup_timer = GLib.timeout_add_seconds(1, self._startup)

    def stop(self) -> None:
        self._running = False
        for timer in (self._timer, self._startup_timer):
            if timer:
                GLib.source_remove(timer)
        self._timer = self._startup_timer = 0
        self._cancel_auth()
        for operation in self._operations.values():
            operation.cancel()
        self._operations.clear()
        super().stop()

    def _startup(self) -> bool:
        self._startup_timer = 0
        self._tick()
        return False

    def _tick(self) -> bool:
        if self._running:
            for account in self._prefs.accounts:
                self._refresh(account)
        return self._running

    def on_clicked(self) -> None:
        if not self._prefs.accounts:
            self._sign_in()
        else:
            account = next(
                (a for a in self._prefs.accounts if a.subject == self._prefs.selected),
                self._prefs.accounts[0],
            )
            self._open_inbox(account)

    def _save(self) -> None:
        self.save_prefs(prefs_payload(self._prefs))

    def _open_inbox(self, account: Account) -> None:
        self._prefs = replace(self._prefs, selected=account.subject)
        self._save()
        if not targets.open_target(inbox_url(account)):
            self._error = _("Could not open the Gmail inbox in your browser")
            self.present()

    def _cancel_auth(self) -> None:
        if self._auth:
            self._auth.cancel()
            self._auth = None

    def _sign_in(self) -> None:
        if (
            not self._running
            or self._client is None
            or self._auth
            or self._disconnecting
        ):
            return
        client = self._client
        operation = Operation()
        self._auth = operation
        self._error = _("Complete Google sign-in in your browser")
        self.present()

        def connected(account: Account) -> None:
            if self._auth is not operation or not self._running:
                self._worker.run(
                    name="gmail-discard-sign-in", fn=lambda: client.forget(account)
                )
                return
            self._auth = None
            self._error = ""
            previous = next(
                (a for a in self._prefs.accounts if a.subject == account.subject), None
            )
            if previous:
                old_operation = self._operations.pop(previous.subject, None)
                if old_operation:
                    old_operation.cancel()
                accounts = tuple(
                    account if a.subject == account.subject else a
                    for a in self._prefs.accounts
                )
                self._worker.run(
                    name="gmail-replace-credentials", fn=lambda: client.forget(previous)
                )
            else:
                accounts = (*self._prefs.accounts, account)
            self._prefs = GmailPrefs(accounts, self._prefs.selected or account.subject)
            self._snapshots[account.subject] = Snapshot()
            self._save()
            self.present()
            self._refresh(account)

        def failed(error: Exception) -> None:
            if self._auth is operation and self._running:
                self._auth = None
                self._error = (
                    "" if isinstance(error, CancelledError) else self._safe_error(error)
                )
                self.present()

        self._worker.run(
            name="gmail-sign-in",
            fn=lambda: client.connect(operation),
            on_result=connected,
            on_error=failed,
        )

    @staticmethod
    def _safe_error(error: Exception) -> str:
        return (
            str(error)
            if isinstance(error, GmailError)
            else _("Gmail operation failed; try again")
        )

    def _refresh(self, account: Account, *, manual: bool = False) -> None:
        if (
            not self._running
            or self._client is None
            or account.subject in self._operations
            or account.subject in self._disconnecting
        ):
            return
        snapshot = self._snapshots.get(account.subject, Snapshot())
        if (
            snapshot.reconnect
            or (snapshot.paused and not manual)
            or (not snapshot.paused and time.monotonic() < snapshot.next_refresh)
        ):
            return
        client = self._client
        operation = Operation()
        self._operations[account.subject] = operation

        def current() -> bool:
            return (
                self._running
                and self._operations.get(account.subject) is operation
                and account in self._prefs.accounts
            )

        def updated(count: int) -> None:
            if current():
                self._operations.pop(account.subject)
                self._snapshots[account.subject] = Snapshot(unread=count)
                self.present()

        def failed(error: Exception) -> None:
            if not current():
                return
            self._operations.pop(account.subject)
            failures = snapshot.failures + 1
            provider = (
                error
                if isinstance(error, GmailError)
                else GmailError(self._safe_error(error))
            )
            self._snapshots[account.subject] = replace(
                snapshot,
                error=str(provider),
                failures=failures,
                reconnect=provider.reconnect,
                paused=provider.paused,
                next_refresh=time.monotonic()
                + retry_delay(failures, provider.retry_after),
            )
            self.present()

        self._worker.run(
            name="gmail-refresh",
            fn=lambda: client.poll(account, operation),
            on_result=updated,
            on_error=failed,
        )

    def _disconnect(self, account: Account) -> None:
        if self._client is None or account.subject in self._disconnecting:
            return
        client = self._client
        operation = self._operations.pop(account.subject, None)
        if operation:
            operation.cancel()
        self._cancel_auth()
        self._disconnecting.add(account.subject)

        def disconnected(revoked: bool) -> None:
            self._disconnecting.discard(account.subject)
            accounts = tuple(a for a in self._prefs.accounts if a != account)
            selected = self._prefs.selected
            if selected == account.subject:
                selected = accounts[0].subject if accounts else ""
            self._prefs = GmailPrefs(accounts, selected)
            self._snapshots.pop(account.subject, None)
            self._save()
            self._error = (
                ""
                if revoked
                else _(
                    "Disconnected locally; review Gmail access in your Google Account"
                )
            )
            if self._running:
                self.present()

        def failed(error: Exception) -> None:
            self._disconnecting.discard(account.subject)
            if self._running:
                self._snapshots[account.subject] = replace(
                    self._snapshots.get(account.subject, Snapshot()),
                    error=self._safe_error(error),
                    paused=True,
                )
                self.present()

        # Explicit disconnect cleanup must finish even if the applet is stopped.
        self._worker.run(
            name="gmail-disconnect",
            fn=lambda: client.disconnect(account),
            on_result=disconnected,
            on_error=failed,
        )
        self.present()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status = [disabled_menu_item(self._error, gtk=Gtk)] if self._error else []
        primary: list[Gtk.MenuItem] = []
        reconnect: list[Gtk.MenuItem] = []
        disconnect: list[Gtk.MenuItem] = []
        for account in self._prefs.accounts:
            snapshot = self._snapshots.get(account.subject, Snapshot())
            item = Gtk.MenuItem(label=account_label(account, snapshot))
            item.connect("activate", lambda _w, a=account: self._open_inbox(a))
            primary.append(item)
            if snapshot.reconnect:
                item = Gtk.MenuItem(
                    label=_("Reconnect {email}").format(email=account.email)
                )
                item.connect("activate", lambda _w: self._sign_in())
                item.set_sensitive(self._auth is None and not self._disconnecting)
                reconnect.append(item)
            item = Gtk.MenuItem(
                label=_("Disconnect {email}").format(email=account.email)
            )
            item.set_sensitive(account.subject not in self._disconnecting)
            item.connect("activate", lambda _w, a=account: self._disconnect(a))
            disconnect.append(item)
        add = Gtk.MenuItem(
            label=_("Add Account") if self._prefs.accounts else _("Sign in with Google")
        )
        add.set_sensitive(
            self._client is not None and self._auth is None and not self._disconnecting
        )
        add.connect("activate", lambda _w: self._sign_in())
        manage = [add, *reconnect]
        if self._auth:
            cancel = Gtk.MenuItem(label=_("Cancel Sign-in"))
            cancel.connect("activate", lambda _w: self._cancel_sign_in())
            manage.append(cancel)
        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.set_sensitive(self._client is not None and bool(self._prefs.accounts))
        refresh.connect("activate", lambda _w: self._refresh_all())
        if self._error == _(
            "Disconnected locally; review Gmail access in your Google Account"
        ):
            access = Gtk.MenuItem(label=_("Manage Google Account Access"))
            access.connect(
                "activate",
                lambda _w: targets.open_target(
                    "https://myaccount.google.com/connections"
                ),
            )
            manage.append(access)
        return menu_sections(
            status=status,
            primary=primary,
            refresh=[refresh],
            manage=manage,
            destructive=disconnect,
            gtk=Gtk,
        )

    def _cancel_sign_in(self) -> None:
        self._cancel_auth()
        self._error = ""
        self.present()

    def _refresh_all(self) -> None:
        for account in self._prefs.accounts:
            self._refresh(account, manual=True)
