"""Verify raw JSON preservation and visible archive read and write failures."""

import json

import pytest

from oslo_energy.ingestion.archive import Archive


def test_archive_preserves_response_and_creates_parents(tmp_path):
    """Writing creates parent directories and preserves the complete JSON payload."""
    payload = {"Production": [1.5, None], "Consumption": [2.0, None], "extra": {"raw": True}}
    archive = Archive(tmp_path / "nested" / "raw.json")

    archive.write(payload)

    assert archive.read() == payload
    assert json.loads(archive.file_path.read_text()) == payload


def test_existing_archive_cannot_be_overwritten(tmp_path):
    """An overwrite attempt raises and leaves the original payload intact."""
    archive = Archive(tmp_path / "raw.json")
    archive.write({"original": True})

    with pytest.raises(FileExistsError):
        archive.write({"replacement": True})

    assert archive.read() == {"original": True}


def test_missing_archive_is_an_error(tmp_path):
    """Reading a nonexistent archive propagates FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        Archive(tmp_path / "missing.json").read()


def test_invalid_archive_is_an_error(tmp_path):
    """Reading malformed JSON propagates JSONDecodeError."""
    path = tmp_path / "invalid.json"
    path.write_text("invalid", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        Archive(path).read()


def test_archive_path_failure_is_visible(tmp_path):
    """Writing beneath an existing file propagates a filesystem error."""
    path = tmp_path / "not-a-directory"
    path.write_text("existing file", encoding="utf-8")

    with pytest.raises(OSError):
        Archive(path / "raw.json").write({})


def test_invalid_json_value_does_not_create_archive(tmp_path):
    """A NaN value raises ValueError without creating an archive file."""
    path = tmp_path / "raw.json"

    with pytest.raises(ValueError):
        Archive(path).write({"invalid": float("nan")})

    assert not path.exists()