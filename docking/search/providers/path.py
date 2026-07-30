"""Direct existing-path results without owning a filesystem index."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlparse

from docking.i18n import _
from docking.platform.launcher import Launcher, normalize_file_target, open_target
from docking.search.coordinator import SearchRequest
from docking.search.preview import preview_local_descriptor
from docking.search.providers.base import action, action_parts, metadata
from docking.search.types import SearchBatch, SearchIdentity, SearchResult


class PathSearchProvider:
    provider_id = "path"

    def __init__(
        self,
        *,
        launcher: Launcher,
        copy_text: Callable[[str], None],
        icon_size: int,
    ) -> None:
        self._launcher = launcher
        self._copy_text = copy_text
        self._icon_size = icon_size

    def search(self, request: SearchRequest):
        text = request.query.text.strip()
        target = _path_target(text)
        if target is None:
            yield SearchBatch.replace(self.provider_id, request.generation)
            return
        info = self._launcher.resolve_file(target=target, size=self._icon_size)
        if info is None:
            yield SearchBatch.replace(self.provider_id, request.generation)
            return
        result = SearchResult(
            identity=SearchIdentity(self.provider_id, info.target),
            title=info.name,
            description=info.target,
            score=1_100,
            icon_name=info.icon_name,
            source=_("Path"),
            state=_("Folder") if info.is_dir else _("File"),
            actions=(
                action(
                    provider_id=self.provider_id,
                    entity_id=info.target,
                    action_id="open",
                    label=_("Open Folder") if info.is_dir else _("Open File"),
                ),
                action(
                    provider_id=self.provider_id,
                    entity_id=info.target,
                    action_id="copy",
                    label=_("Copy Path"),
                ),
            ),
            metadata=metadata(target=info.target),
            preview=preview_local_descriptor(
                target=info.target,
                title=info.name,
            ),
            canonical_key=f"target:{info.target}",
        )
        yield SearchBatch.replace(
            self.provider_id,
            request.generation,
            (result,),
        )

    def invoke(
        self,
        *,
        result_identity: SearchIdentity,
        action_identity: SearchIdentity,
    ) -> bool:
        parts = action_parts(action_identity)
        if (
            result_identity.provider_id != self.provider_id
            or parts is None
            or parts[0] != result_identity.key
        ):
            return False
        if parts[1] == "open":
            return open_target(parts[0])
        if parts[1] == "copy":
            parsed = urlparse(parts[0])
            value = unquote(parsed.path) if parsed.scheme == "file" else parts[0]
            self._copy_text(value)
            return True
        return False


def _path_target(text: str) -> str | None:
    if text.startswith("file://"):
        return normalize_file_target(text)
    if not text.startswith(("/", "~/", "./", "../")):
        return None
    path = Path(text).expanduser()
    if not path.exists():
        return None
    return normalize_file_target(str(path))


__all__ = ["PathSearchProvider"]
