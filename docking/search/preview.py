"""Safe bounded text previews for local search targets."""

from __future__ import annotations

import mimetypes
import re
import stat
import tarfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from docking.i18n import _
from docking.search.types import SearchPreview

MAX_PREVIEW_BYTES = 8 * 1024
MAX_PREVIEW_FILE_SIZE = 2 * 1024 * 1024
MAX_IMAGE_PREVIEW_FILE_SIZE = 50 * 1024 * 1024
MAX_ARCHIVE_PREVIEW_FILE_SIZE = 100 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 30
_IMAGE_SUFFIXES = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
    ".xpm",
}
_SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".json",
    ".kt",
    ".lua",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".xml",
    ".yaml",
    ".yml",
}
_ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
)


def local_path_from_target(target: str) -> Path | None:
    parsed = urlparse(target)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if not parsed.scheme:
        return Path(target).expanduser()
    return None


def preview_local_target(
    *,
    target: str,
    title: str,
) -> SearchPreview:
    path = local_path_from_target(target)
    if path is None:
        return SearchPreview(title=title, body=target, kind="uri")
    try:
        if path.is_dir():
            children = []
            for index, child in enumerate(path.iterdir()):
                if index >= 200:
                    break
                children.append(child.name + ("/" if child.is_dir() else ""))
            children = sorted(children)[:20]
            body = "\n".join(children) or _("Empty folder")
            return SearchPreview(title=title, body=body, kind="directory")
        path_stat = path.stat()
        if not stat.S_ISREG(path_stat.st_mode):
            return SearchPreview(
                title=title,
                body=str(path),
                kind="special",
                target=str(path),
            )
        file_size = path_stat.st_size
        mime_type = mimetypes.guess_type(path.name)[0] or ""
        archive = _archive_preview(
            path=path,
            title=title,
            file_size=file_size,
        )
        if archive is not None:
            return archive
        if mime_type.startswith("image/") or path.suffix.casefold() in _IMAGE_SUFFIXES:
            if file_size <= MAX_IMAGE_PREVIEW_FILE_SIZE:
                return SearchPreview(
                    title=title,
                    body=str(path),
                    kind="image",
                    target=str(path),
                )
            return SearchPreview(
                title=title,
                body=_("{path}\nImage is too large to preview.").format(path=path),
                kind="image-too-large",
                target=str(path),
            )
        if file_size > MAX_PREVIEW_FILE_SIZE:
            return SearchPreview(
                title=title,
                body=_("{path}\nFile is too large to preview.").format(path=path),
                kind="file",
            )
        with path.open("rb") as handle:
            payload = handle.read(MAX_PREVIEW_BYTES)
    except OSError:
        return SearchPreview(title=title, body=str(path), kind="file")
    if b"\0" in payload:
        return SearchPreview(title=title, body=str(path), kind="binary")
    body = payload.decode("utf-8", errors="replace").strip()
    suffix = path.suffix.casefold()
    if suffix in {".md", ".markdown"}:
        return SearchPreview(
            title=title,
            body=_markdown_preview(body),
            kind="markdown",
            target=str(path),
        )
    if suffix in _SOURCE_SUFFIXES:
        return SearchPreview(
            title=title,
            body=_source_preview(body),
            kind="source",
            target=str(path),
        )
    return SearchPreview(
        title=title,
        body=body or str(path),
        kind="text",
    )


def preview_local_descriptor(
    *,
    target: str,
    title: str,
) -> SearchPreview:
    """Build cheap metadata for rows; expensive content stays lazy."""
    path = local_path_from_target(target)
    if path is None:
        return SearchPreview(
            title=title,
            body=target,
            kind="uri",
            target=target,
        )
    mime_type = mimetypes.guess_type(path.name)[0] or ""
    if mime_type.startswith("image/") or path.suffix.casefold() in _IMAGE_SUFFIXES:
        try:
            file_size = path.stat().st_size
        except OSError:
            file_size = 0
        return SearchPreview(
            title=title,
            body=str(path),
            kind=(
                "image"
                if file_size <= MAX_IMAGE_PREVIEW_FILE_SIZE
                else "image-too-large"
            ),
            target=str(path),
        )
    return SearchPreview(
        title=title,
        body=str(path),
        kind="local",
        target=target,
    )


def _archive_preview(
    *,
    path: Path,
    title: str,
    file_size: int,
) -> SearchPreview | None:
    lowered = path.name.casefold()
    if not lowered.endswith(_ARCHIVE_SUFFIXES):
        return None
    if file_size > MAX_ARCHIVE_PREVIEW_FILE_SIZE:
        return SearchPreview(
            title=title,
            body=_("{path}\nArchive is too large to preview.").format(path=path),
            kind="archive-too-large",
            target=str(path),
        )
    try:
        if lowered.endswith(".zip"):
            with zipfile.ZipFile(path) as archive:
                entries = [
                    info.filename for info in archive.infolist()[:MAX_ARCHIVE_ENTRIES]
                ]
        else:
            entries = []
            with tarfile.open(path, mode="r:*") as archive:
                for index, member in enumerate(archive):
                    if index >= MAX_ARCHIVE_ENTRIES:
                        break
                    entries.append(member.name + ("/" if member.isdir() else ""))
    except (OSError, tarfile.TarError, zipfile.BadZipFile):
        return None
    return SearchPreview(
        title=title,
        body="\n".join(entries) or _("Empty archive"),
        kind="archive",
        target=str(path),
    )


def _source_preview(body: str) -> str:
    lines = body.splitlines()[:120]
    width = len(str(max(1, len(lines))))
    return "\n".join(
        f"{index:>{width}}  {line}" for index, line in enumerate(lines, start=1)
    )


def _markdown_preview(body: str) -> str:
    rendered = []
    in_code = False
    for line in body.splitlines()[:120]:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            rendered.append(f"    {line}")
            continue
        heading = re.sub(r"^#{1,6}\s+", "", line)
        bullet = re.sub(r"^\s*[-*+]\s+", "• ", heading)
        links = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 — \2", bullet)
        rendered.append(links.replace("**", "").replace("__", ""))
    return "\n".join(rendered).strip()


__all__ = [
    "MAX_ARCHIVE_ENTRIES",
    "MAX_ARCHIVE_PREVIEW_FILE_SIZE",
    "MAX_IMAGE_PREVIEW_FILE_SIZE",
    "MAX_PREVIEW_BYTES",
    "MAX_PREVIEW_FILE_SIZE",
    "local_path_from_target",
    "preview_local_descriptor",
    "preview_local_target",
]
