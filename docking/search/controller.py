"""Process-wide search orchestration and GTK presentation lifecycle."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from docking.core.items import APPLET_KIND
from docking.i18n import _
from docking.log import get_logger
from docking.search.app_identity import application_id
from docking.search.coordinator import SearchCoordinator, SearchRequest, SearchSnapshot
from docking.search.intents import (
    QueryIntent,
    QueryIntentKind,
    parse_query_intent,
)
from docking.search.preview import preview_local_target
from docking.search.providers import (
    ApplicationSearchProvider,
    CalculatorSearchProvider,
    ConverterSearchProvider,
    DockSearchProvider,
    InvokableSearchProvider,
    PathSearchProvider,
    RecentFilesSearchProvider,
    TemporalSearchProvider,
    WebSearchProvider,
    WindowSearchProvider,
)
from docking.search.services.application_catalog import ApplicationCatalog
from docking.search.services.currency_rates import CurrencyRatesCatalog
from docking.search.services.global_shortcuts import (
    GlobalShortcutActivation,
    GlobalShortcutsService,
    GlobalShortcutsState,
    GlobalShortcutsStatus,
)
from docking.search.services.recent_files import RecentFilesCatalog
from docking.search.services.x11_shortcuts import (
    ShortcutFallback,
    X11GlobalShortcutService,
    is_x11_session,
)
from docking.search.types import (
    SearchAction,
    SearchIdentity,
    SearchPreview,
    SearchQuery,
    SearchResult,
)
from docking.search.ui.shortcut_capture import shortcut_label
from docking.search.ui.thumbnails import LoadedSearchImage
from docking.search.ui.window import SearchWindow
from docking.search.usage import SearchUsageStore

WEB_FALLBACK_STRONG_SCORE = 300
log = get_logger("search.controller")

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.backends.base import PreviewService, WindowService
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel


class GlobalSearchController:
    """Own catalogs, providers, shortcut registration, and the search window."""

    def __init__(
        self,
        *,
        config: Config,
        launcher: Launcher,
        model: DockModel,
        windows: WindowService,
        preview_service: PreviewService,
    ) -> None:
        self._config = config
        self._model = model
        self._windows = windows
        self._preview_service = preview_service
        self._application_catalog = ApplicationCatalog()
        self._recent_files = RecentFilesCatalog()
        self._started = False
        self._current_query = ""
        self._selected_identity: SearchIdentity | None = None
        self._shortcut_status: GlobalShortcutsStatus | None = None
        self._shortcut_status_listeners: list[Callable[[], None]] = []
        self._shortcut_suspended = False
        self._model_signature: tuple[tuple[object, ...], ...] = ()
        self._schedule_idle = GLib.idle_add
        self._currency_rates = CurrencyRatesCatalog(schedule_idle=self._schedule_idle)
        self._usage_store = SearchUsageStore()
        self._window_preview_cache: dict[
            tuple[str, int, int],
            tuple[float, LoadedSearchImage],
        ] = {}

        copy_text = self._copy_text
        providers: tuple[InvokableSearchProvider, ...] = (
            ApplicationSearchProvider(
                catalog=self._application_catalog,
                launcher=launcher,
                model=model,
                windows=windows,
                recent_docs_limit=config.recent_docs_max,
            ),
            DockSearchProvider(model=model),
            WindowSearchProvider(windows=windows),
            CalculatorSearchProvider(copy_text=copy_text),
            ConverterSearchProvider(
                copy_text=copy_text,
                currency_rates=self._currency_rates,
            ),
            RecentFilesSearchProvider(catalog=self._recent_files),
            TemporalSearchProvider(copy_text=copy_text),
            PathSearchProvider(
                launcher=launcher,
                copy_text=copy_text,
                icon_size=config.icon_size,
            ),
            WebSearchProvider(copy_text=copy_text),
        )
        self._provider_by_id = {
            provider.provider_id: provider for provider in providers
        }
        self._enabled_provider_ids: tuple[str, ...] = ()
        self._coordinator = self._new_coordinator()
        self.window = SearchWindow(
            launcher=launcher,
            on_query_changed=self._search,
            on_result_selected=self._select,
            on_result_activated=self._activate_primary,
            on_action_activated=self._activate_action,
            on_hidden=self._on_hidden,
            on_refine_requested=self._refine_result,
            dynamic_preview_loader=self._load_dynamic_preview,
            preview_resolver=self._resolve_result_preview,
        )
        self._global_shortcuts = self._new_shortcut_service()
        self._shortcut_fallback = self._new_shortcut_fallback()
        self._shortcut_preferences = self._current_shortcut_preferences()

    @property
    def visible(self) -> bool:
        return self.window.visible

    @property
    def shortcut_status(self) -> GlobalShortcutsStatus | None:
        return self._shortcut_status

    def shortcut_status_text(self) -> str:
        if self._shortcut_fallback is not None and self._shortcut_fallback.active:
            return _("Active: {shortcut} (X11)").format(
                shortcut=shortcut_label(self._config.global_search_shortcut)
            )
        if self._shortcut_fallback is not None and self._shortcut_fallback.error:
            if self._shortcut_fallback.error == "shortcut is already in use":
                return _("Shortcut already in use")
            return _("Registration failed")
        status = self._shortcut_status
        if status is None:
            return _("Not active")
        trigger = status.binding.trigger_description if status.binding else None
        if status.state in {
            GlobalShortcutsState.STARTING,
            GlobalShortcutsState.CREATING,
            GlobalShortcutsState.BINDING,
        }:
            return _("Connecting...")
        if status.state in {
            GlobalShortcutsState.BOUND,
            GlobalShortcutsState.REASSIGNED,
        }:
            if trigger:
                return _("Assigned: {shortcut}").format(shortcut=trigger)
            return _("Assigned by desktop")
        if status.state is GlobalShortcutsState.UNAVAILABLE:
            return _("Unavailable on this desktop")
        if status.state is GlobalShortcutsState.DENIED:
            return _("Permission denied")
        if status.state is GlobalShortcutsState.CANCELLED:
            return _("Setup cancelled")
        if status.state is GlobalShortcutsState.ERROR:
            return _("Registration failed")
        return _("Not active")

    def shortcut_status_summary(self) -> str:
        if self._shortcut_fallback is not None:
            if self._shortcut_fallback.active:
                return _("Active")
            if self._shortcut_fallback.error:
                if self._shortcut_fallback.error == "shortcut is already in use":
                    return _("Conflict")
                return _("Failed")
        status = self._shortcut_status
        if status is None:
            return _("Inactive")
        if status.state in {
            GlobalShortcutsState.STARTING,
            GlobalShortcutsState.CREATING,
            GlobalShortcutsState.BINDING,
        }:
            return _("Connecting")
        if status.state in {
            GlobalShortcutsState.BOUND,
            GlobalShortcutsState.REASSIGNED,
        }:
            return _("Active")
        if status.state is GlobalShortcutsState.UNAVAILABLE:
            return _("Unavailable")
        if status.state is GlobalShortcutsState.DENIED:
            return _("Denied")
        if status.state is GlobalShortcutsState.CANCELLED:
            return _("Cancelled")
        if status.state is GlobalShortcutsState.ERROR:
            return _("Failed")
        return _("Inactive")

    def add_shortcut_status_listener(
        self,
        listener: Callable[[], None],
    ) -> Callable[[], None]:
        if listener not in self._shortcut_status_listeners:
            self._shortcut_status_listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._shortcut_status_listeners:
                self._shortcut_status_listeners.remove(listener)

        return unsubscribe

    def suspend_shortcuts(self) -> None:
        """Temporarily release global grabs while a new sequence is captured."""
        self._shortcut_suspended = True
        self._stop_shortcut_services()
        self._notify_shortcut_status()

    def resume_shortcuts(self) -> None:
        self._shortcut_suspended = False
        if self._started and self._config.global_search_enabled:
            self._start_shortcut_services()

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._shortcut_suspended = False
        self._application_catalog.start()
        self._recent_files.start()
        self._model_signature = self._searchable_model_signature()
        self._application_catalog.add_listener(self._refresh_visible)
        self._recent_files.add_listener(self._refresh_visible)
        self._currency_rates.add_listener(self._refresh_visible)
        self._model.add_change_listener(self._refresh_for_model_change)
        if self._config.global_search_enabled:
            self._start_shortcut_services()

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        if self._coordinator.request is not None:
            self._coordinator.cancel()
        self._shortcut_suspended = True
        self._stop_shortcut_services()
        self._model.remove_change_listener(self._refresh_for_model_change)
        self._application_catalog.remove_listener(self._refresh_visible)
        self._recent_files.remove_listener(self._refresh_visible)
        self._currency_rates.remove_listener(self._refresh_visible)
        self._currency_rates.stop()
        self._application_catalog.stop()
        self._recent_files.stop()
        self.window.destroy()

    def show(
        self,
        initial_query: str = "",
        activation_context: dict[str, object] | None = None,
    ) -> None:
        if not self._config.global_search_enabled:
            return
        self._current_query = initial_query
        self.window.present(
            initial_query=initial_query,
            activation_context=activation_context,
        )
        self._search(initial_query)

    def hide(self) -> None:
        self.window.hide()

    def toggle(
        self,
        activation_context: dict[str, object] | None = None,
    ) -> None:
        if self.visible:
            self.hide()
        else:
            self.show(activation_context=activation_context)

    def refresh_settings(self) -> None:
        """Rebuild provider routing after settings change."""
        self._coordinator = self._new_coordinator()
        shortcut_preferences = self._current_shortcut_preferences()
        if self._started and shortcut_preferences != self._shortcut_preferences:
            self._stop_shortcut_services()
            self._global_shortcuts = self._new_shortcut_service()
            self._shortcut_fallback = self._new_shortcut_fallback()
            if self._config.global_search_enabled and not self._shortcut_suspended:
                self._start_shortcut_services()
            else:
                self.hide()
        self._shortcut_preferences = shortcut_preferences
        if self.visible:
            self._search(self._current_query)

    def _new_shortcut_service(self) -> GlobalShortcutsService:
        return GlobalShortcutsService(
            app_id=application_id(),
            preferred_trigger=self._config.global_search_shortcut,
            on_activated=self._on_global_shortcut,
            on_status_changed=self._on_shortcut_status,
        )

    def _new_shortcut_fallback(self) -> ShortcutFallback | None:
        if not is_x11_session():
            return None
        return X11GlobalShortcutService(
            shortcut=self._config.global_search_shortcut,
            on_activated=self._on_fallback_shortcut,
            schedule_idle=self._schedule_idle,
        )

    def _start_shortcut_services(self) -> None:
        self._global_shortcuts.start()

    def _stop_shortcut_services(self) -> None:
        self._global_shortcuts.stop()
        if self._shortcut_fallback is not None:
            self._shortcut_fallback.stop()

    def _start_shortcut_fallback(self) -> None:
        if self._shortcut_fallback is not None:
            self._shortcut_fallback.start()

    def _current_shortcut_preferences(self) -> tuple[bool, str]:
        return (
            bool(self._config.global_search_enabled),
            str(self._config.global_search_shortcut),
        )

    def _new_coordinator(
        self,
        provider_ids: tuple[str, ...] | None = None,
    ) -> SearchCoordinator:
        previous = getattr(self, "_coordinator", None)
        if previous is not None and previous.request is not None:
            previous.cancel()
        if provider_ids is None:
            enabled = set(self._config.global_search_providers)
            provider_ids = tuple(
                provider_id
                for provider_id in self._provider_by_id
                if provider_id in enabled
            )
        self._enabled_provider_ids = provider_ids
        return SearchCoordinator(
            tuple(
                self._provider_by_id[provider_id]
                for provider_id in provider_ids
                if provider_id in self._provider_by_id
            ),
            rank_adjuster=self._usage_store.boost,
        )

    def _search(self, text: str) -> None:
        if not self.visible:
            return
        self._current_query = text
        intent = parse_query_intent(text)
        provider_ids = self._provider_ids_for_intent(intent)
        if provider_ids != self._enabled_provider_ids:
            self._coordinator = self._new_coordinator(provider_ids)
        query = SearchQuery(
            text=intent.search_text,
            limit=self._config.global_search_max_results,
            context=(
                ("intent_kind", intent.kind.value),
                ("web_engine", self._config.global_search_web_engine),
                ("question_like", "true" if intent.question_like else "false"),
            ),
        )
        request = self._coordinator.begin(
            query,
            selected_identity=self._selected_identity,
            recognized=intent.recognized,
        )
        self._publish_snapshot(
            self._coordinator.snapshot(),
            coordinator=self._coordinator,
        )
        provider_ids = tuple(
            provider.provider_id for provider in self._coordinator.providers
        )
        if provider_ids:
            self._schedule_idle(
                self._run_provider,
                self._coordinator,
                request,
                provider_ids,
                0,
            )

    def _provider_ids_for_intent(self, intent: QueryIntent) -> tuple[str, ...]:
        enabled = set(self._config.global_search_providers)

        def available(provider_id: str) -> bool:
            if provider_id == "converter":
                return "calculator" in enabled
            if provider_id == "web":
                return True
            if provider_id == "datetime":
                return True
            return provider_id in enabled

        if intent.provider_ids:
            return tuple(
                provider_id
                for provider_id in intent.provider_ids
                if available(provider_id)
            )
        provider_ids = [
            provider_id
            for provider_id in self._provider_by_id
            if provider_id in enabled
        ]
        if (
            intent.search_text
            and self._config.global_search_web_fallback
            and "web" not in provider_ids
        ):
            provider_ids.append("web")
        return tuple(provider_ids)

    def _publish_snapshot(
        self,
        snapshot: SearchSnapshot,
        *,
        coordinator: SearchCoordinator | None = None,
    ) -> None:
        if coordinator is not None and (
            coordinator is not self._coordinator
            or snapshot.generation != coordinator.generation
        ):
            return
        snapshot = replace(
            snapshot,
            results=tuple(
                replace(
                    result,
                    actions=self._usage_store.rank_actions(result.actions),
                )
                for result in snapshot.results
            ),
        )
        if snapshot.selected_identity is not None or snapshot.is_final:
            self._selected_identity = snapshot.selected_identity
        self.window.update(snapshot)

    def _run_provider(
        self,
        coordinator: SearchCoordinator,
        request: SearchRequest,
        provider_ids: tuple[str, ...],
        index: int,
    ) -> bool:
        if coordinator is not self._coordinator or not coordinator.is_current(
            request.generation
        ):
            return False
        provider_id = provider_ids[index]
        is_web_fallback = (
            provider_id == "web"
            and request.query.context_value("intent_kind")
            == QueryIntentKind.GLOBAL.value
        )
        has_strong_local_result = any(
            result.score >= WEB_FALLBACK_STRONG_SCORE
            for result in coordinator.snapshot().results
        )
        if is_web_fallback and has_strong_local_result:
            coordinator.finish_provider(
                provider_id=provider_id,
                generation=request.generation,
            )
            self._publish_snapshot(
                coordinator.snapshot(),
                coordinator=coordinator,
            )
        else:
            coordinator.run_provider(
                provider_id=provider_id,
                request=request,
                on_update=lambda snapshot: self._publish_snapshot(
                    snapshot,
                    coordinator=coordinator,
                ),
            )
        next_index = index + 1
        if next_index < len(provider_ids) and coordinator.is_current(
            request.generation
        ):
            self._schedule_idle(
                self._run_provider,
                coordinator,
                request,
                provider_ids,
                next_index,
            )
        return False

    def _select(self, identity: SearchIdentity | None) -> None:
        if self._coordinator.request is None:
            self._selected_identity = identity
            return
        if self._coordinator.select(identity):
            self._selected_identity = identity

    def _activate_primary(self, result: SearchResult) -> None:
        if not result.actions:
            return
        self._activate_action(result, result.actions[0])

    def _refine_result(self, result: SearchResult | None) -> None:
        if result is None:
            return
        provider = self._provider_by_id.get(result.identity.provider_id)
        refine = getattr(provider, "refine", None)
        try:
            refined = refine(result) if callable(refine) else result
        except Exception as exc:
            log.warning(
                "Failed to refine Search result from %s: %s",
                result.identity.provider_id,
                exc,
            )
            return
        if not isinstance(refined, SearchResult):
            log.warning(
                "Search provider %s returned an invalid refinement",
                result.identity.provider_id,
            )
            return
        refined = replace(
            refined,
            actions=self._usage_store.rank_actions(refined.actions),
        )
        if refined.actions:
            self.window.show_actions_for(refined)

    def _activate_action(
        self,
        result: SearchResult,
        result_action: SearchAction,
    ) -> None:
        provider = self._provider_by_id.get(result.identity.provider_id)
        if provider is None:
            return
        if result_action.verb in {"close", "remove"}:
            coordinator = self._coordinator
            request = coordinator.request
            generation = request.generation if request is not None else 0
            if not self._confirm_action(result_action) or not self._action_is_current(
                coordinator=coordinator,
                generation=generation,
                result_identity=result.identity,
                action_identity=result_action.identity,
            ):
                return
        try:
            invoked = provider.invoke(
                result_identity=result.identity,
                action_identity=result_action.identity,
            )
        except Exception as exc:
            log.warning(
                "Failed to invoke Search action from %s: %s",
                result.identity.provider_id,
                exc,
            )
            return
        if invoked:
            current_request = self._coordinator.request
            self._usage_store.record(
                query=(
                    current_request.query.text
                    if current_request is not None
                    else self._current_query
                ),
                result=result,
                action=result_action,
            )
            self.hide()

    def _action_is_current(
        self,
        *,
        coordinator: SearchCoordinator,
        generation: int,
        result_identity: SearchIdentity,
        action_identity: SearchIdentity,
    ) -> bool:
        if coordinator is not self._coordinator or not coordinator.is_current(
            generation
        ):
            return False
        current_result = coordinator.snapshot().result_for(result_identity)
        return current_result is not None and any(
            action.identity == action_identity for action in current_result.actions
        )

    def _confirm_action(self, result_action: SearchAction) -> bool:
        dialog = Gtk.MessageDialog(
            transient_for=self.window.window,
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=result_action.label,
        )
        try:
            return dialog.run() == Gtk.ResponseType.OK
        finally:
            dialog.destroy()

    def _load_dynamic_preview(
        self,
        preview: SearchPreview,
        width: int,
        height: int,
    ) -> LoadedSearchImage | None:
        service = self._preview_service
        if preview.kind != "window":
            return None
        cache_key = (preview.target, width, height)
        cached = self._window_preview_cache.get(cache_key)
        now = time.monotonic()
        if cached is not None and now - cached[0] <= 1.0:
            return cached[1]
        window = next(
            (
                candidate
                for candidate in self._windows.list_all_windows()
                if str(candidate.id) == preview.target
            ),
            None,
        )
        if window is None:
            return None
        try:
            captured = service.capture(window.id, width=width, height=height)
        except Exception as exc:
            log.warning("Failed to capture Search window preview: %s", exc)
            return None
        if captured is None or not isinstance(captured.image, GdkPixbuf.Pixbuf):
            return None
        loaded = LoadedSearchImage(
            pixbuf=captured.image,
            width=captured.width,
            height=captured.height,
            format_name=_("Live Window"),
            file_size=-1,
        )
        self._window_preview_cache[cache_key] = (now, loaded)
        return loaded

    def _resolve_result_preview(
        self,
        result: SearchResult,
    ) -> SearchPreview | None:
        provider = self._provider_by_id.get(result.identity.provider_id)
        resolver = getattr(provider, "build_preview", None)
        if callable(resolver):
            try:
                preview = resolver(result)
            except Exception as exc:
                log.warning(
                    "Failed to build Search preview for %s: %s",
                    result.identity.provider_id,
                    exc,
                )
                return None
            return preview if isinstance(preview, SearchPreview) else None
        if result.preview is not None and result.preview.kind == "local":
            return preview_local_target(
                target=result.preview.target,
                title=result.preview.title,
            )
        return None

    def _refresh_visible(self) -> None:
        if self.visible:
            self._search(self._current_query)

    def _refresh_for_model_change(self) -> None:
        signature = self._searchable_model_signature()
        if signature == self._model_signature:
            return
        self._model_signature = signature
        self._refresh_visible()

    def _searchable_model_signature(self) -> tuple[tuple[object, ...], ...]:
        return tuple(
            (
                item.desktop_id,
                item.kind,
                None if item.kind == APPLET_KIND else item.name,
                item.target,
                None if item.kind == APPLET_KIND else item.icon_name,
                item.is_pinned,
                item.is_running,
                item.is_recent,
            )
            for item in self._model.visible_items()
        )

    def _on_hidden(self) -> None:
        if self._coordinator.request is not None:
            self._coordinator.cancel()

    def _on_global_shortcut(self, activation: GlobalShortcutActivation) -> None:
        context: dict[str, object] = {"timestamp": activation.timestamp}
        if activation.activation_token:
            context["XDG_ACTIVATION_TOKEN"] = activation.activation_token
        self.toggle(activation_context=context)

    def _on_shortcut_status(self, status: GlobalShortcutsStatus) -> None:
        self._shortcut_status = status
        if (
            not self._started
            or self._shortcut_suspended
            or not self._config.global_search_enabled
        ):
            return
        if status.state is GlobalShortcutsState.UNAVAILABLE:
            self._start_shortcut_fallback()
        elif self._shortcut_fallback is not None:
            self._shortcut_fallback.stop()
        self._notify_shortcut_status()

    def _on_fallback_shortcut(self, timestamp: int) -> None:
        self.toggle(activation_context={"timestamp": timestamp})

    def _notify_shortcut_status(self) -> None:
        for listener in tuple(self._shortcut_status_listeners):
            listener()

    @staticmethod
    def _copy_text(text: str) -> None:
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)


__all__ = ["GlobalSearchController"]
