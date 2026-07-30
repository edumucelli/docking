"""Tests for bounded provider-neutral local previews."""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

from docking.search.preview import preview_local_descriptor, preview_local_target


def test_text_file_and_directory_previews(tmp_path) -> None:
    text_file = tmp_path / "notes.txt"
    text_file.write_text("First line\nSecond line")
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "child.txt").write_text("child")

    text_preview = preview_local_target(
        target=text_file.as_uri(),
        title="notes.txt",
    )
    folder_preview = preview_local_target(
        target=folder.as_uri(),
        title="folder",
    )

    assert text_preview.body == "First line\nSecond line"
    assert text_preview.kind == "text"
    assert folder_preview.body == "child.txt"
    assert folder_preview.kind == "directory"

    descriptor = preview_local_descriptor(
        target=text_file.as_uri(),
        title=text_file.name,
    )
    assert descriptor.kind == "local"
    assert descriptor.body == str(text_file)


def test_binary_preview_does_not_decode_payload(tmp_path) -> None:
    binary = tmp_path / "binary"
    binary.write_bytes(b"prefix\0secret")

    preview = preview_local_target(target=str(binary), title="binary")

    assert preview.kind == "binary"
    assert "secret" not in preview.body


def test_image_preview_retains_decode_target(tmp_path) -> None:
    image = tmp_path / "screenshot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    preview = preview_local_target(
        target=image.as_uri(),
        title=image.name,
    )

    assert preview.kind == "image"
    assert preview.target == str(image)
    assert preview.body == str(image)


def test_zip_and_tar_previews_list_entries_without_extracting(tmp_path) -> None:
    zip_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("docs/readme.txt", "hello")
        archive.writestr("src/main.py", "print('hello')")
    source = tmp_path / "source.txt"
    source.write_text("payload")
    tar_path = tmp_path / "bundle.tar"
    with tarfile.open(tar_path, "w") as archive:
        archive.add(source, arcname="source.txt")

    zip_preview = preview_local_target(target=str(zip_path), title=zip_path.name)
    tar_preview = preview_local_target(target=str(tar_path), title=tar_path.name)

    assert zip_preview.kind == "archive"
    assert "docs/readme.txt" in zip_preview.body
    assert tar_preview.kind == "archive"
    assert tar_preview.body == "source.txt"


def test_markdown_and_source_previews_are_readable(tmp_path) -> None:
    markdown = tmp_path / "README.md"
    markdown.write_text(
        "# Heading\n- Item\n[Docking](https://example.com)\n```py\nprint('x')\n```"
    )
    source = tmp_path / "main.py"
    source.write_text("def main():\n    return 0\n")

    markdown_preview = preview_local_target(
        target=str(markdown),
        title=markdown.name,
    )
    source_preview = preview_local_target(
        target=str(source),
        title=source.name,
    )

    assert markdown_preview.kind == "markdown"
    assert markdown_preview.body.startswith("Heading\n• Item")
    assert "Docking — https://example.com" in markdown_preview.body
    assert source_preview.kind == "source"
    assert "1  def main():" in source_preview.body
    assert "2      return 0" in source_preview.body


def test_special_device_preview_never_reads_until_eof() -> None:
    device = Path("/dev/zero")
    if not device.exists():
        return

    preview = preview_local_target(target=str(device), title="zero")

    assert preview.kind == "special"
    assert preview.body == str(device)
