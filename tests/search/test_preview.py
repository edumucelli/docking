"""Tests for bounded provider-neutral local previews."""

from __future__ import annotations

import gzip
import lzma
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

import docking.search.preview as preview_mod
from docking.search.preview import preview_local_descriptor, preview_local_target


@pytest.mark.parametrize("extension", ["tar", "zip"])
def test_archive_member_names_cannot_exceed_the_display_budget(tmp_path, extension):
    path = tmp_path / f"long-name.{extension}"
    name = "x" * (preview_mod.MAX_PREVIEW_BYTES + 1)
    if extension == "tar":
        with tarfile.open(path, "w") as archive:
            archive.addfile(tarfile.TarInfo(name), BytesIO())
    else:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(name, b"")

    result = preview_local_target(target=str(path), title="archive")

    assert result.kind == "archive"
    assert len(result.body) == preview_mod.MAX_PREVIEW_BYTES


def test_xz_preview_rejects_a_decoder_dictionary_above_the_memory_budget(
    tmp_path, monkeypatch
):
    tar = BytesIO()
    with tarfile.open(fileobj=tar, mode="w") as archive:
        archive.addfile(tarfile.TarInfo("small.txt"), BytesIO())
    path = tmp_path / "dictionary.tar.xz"
    path.write_bytes(
        lzma.compress(
            tar.getvalue(),
            filters=[{"id": lzma.FILTER_LZMA2, "dict_size": 1024 * 1024}],
        )
    )
    monkeypatch.setattr(preview_mod, "MAX_ARCHIVE_DECODER_BYTES", 512 * 1024)

    result = preview_local_target(target=str(path), title="archive")

    assert result.kind != "archive"


@pytest.mark.parametrize("padding", [b"", bytes(8)])
def test_xz_memory_limit_retains_concatenated_stream_support(tmp_path, padding):
    tar = BytesIO()
    with tarfile.open(fileobj=tar, mode="w") as archive:
        archive.addfile(tarfile.TarInfo("small.txt"), BytesIO())
    payload = tar.getvalue()
    path = tmp_path / "concatenated.tar.xz"
    path.write_bytes(
        lzma.compress(payload[:512]) + padding + lzma.compress(payload[512:])
    )

    result = preview_local_target(target=str(path), title="archive")

    assert result.kind == "archive" and result.body == "small.txt"


@pytest.mark.parametrize("compression", ["gz", "bz2", "xz"])
def test_compressed_archive_preview_limits_decompressed_bytes(
    tmp_path, monkeypatch, compression
):
    path = tmp_path / f"large.tar.{compression}"
    with tarfile.open(path, f"w:{compression}") as archive:
        entry = tarfile.TarInfo("large.bin")
        entry.size = 64 * 1024
        archive.addfile(entry, BytesIO(bytes(entry.size)))
    assert path.stat().st_size < 2048
    monkeypatch.setattr(preview_mod, "MAX_ARCHIVE_PREVIEW_BYTES", 2048)

    result = preview_local_target(target=str(path), title="archive")

    assert result.kind == "archive-too-large"
    assert "large.bin" not in result.body


@pytest.mark.parametrize("compression", ["gz", "bz2", "xz"])
def test_small_compressed_archives_keep_their_listing(tmp_path, compression):
    path = tmp_path / f"small.tar.{compression}"
    with tarfile.open(path, f"w:{compression}") as archive:
        archive.addfile(tarfile.TarInfo("small.txt"), BytesIO())
    result = preview_local_target(target=str(path), title="archive")
    assert result.kind == "archive" and result.body == "small.txt"


def test_corrupt_gzip_deflate_body_uses_the_binary_fallback(tmp_path):
    path = tmp_path / "corrupt.tar.gz"
    compressed = bytearray(gzip.compress(b"archive", mtime=0))
    compressed[10] = 0x07  # Reserved deflate block type after the gzip header.
    path.write_bytes(compressed)

    result = preview_local_target(target=str(path), title="archive")

    assert result.kind == "binary"
    assert result.body == str(path)


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
    assert "Docking - https://example.com" in markdown_preview.body
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


def test_windows_line_endings_are_displayed_as_normal_preview_lines(tmp_path):
    path = tmp_path / "windows-notes.txt"
    path.write_bytes(b"First line\r\nSecond line\r\n")
    preview = preview_local_target(target=path.as_uri(), title=path.name)
    assert preview.kind == "text"
    assert preview.body == "First line\nSecond line"
