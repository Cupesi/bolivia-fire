from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

import pytest

from bolivia_fire.release.build import TIMESTAMP
from bolivia_fire.release.verify import extract_archive, verify_checksums


def test_synthetic_extraction_checks_hashes_and_requires_empty_directory(tmp_path: Path) -> None:
    archive = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr(zipfile.ZipInfo("sample.txt", TIMESTAMP), b"synthetic")
    entry = {"path": "sample.txt", "bytes": 9,
             "sha256": hashlib.sha256(b"synthetic").hexdigest()}
    result = extract_archive(archive, tmp_path / "clean", [entry])
    assert result["passed"] and result["members"] == 1
    with pytest.raises(ValueError, match="empty"):
        extract_archive(archive, tmp_path / "clean", [entry])
    with pytest.raises(ValueError, match="hash"):
        extract_archive(archive, tmp_path / "other", [{**entry, "sha256": "0" * 64}])


def test_synthetic_checksums_require_complete_expected_set(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"synthetic")
    (tmp_path / "SHA256SUMS").write_text(
        hashlib.sha256(b"synthetic").hexdigest() + "  a.txt\n", encoding="utf-8")
    assert verify_checksums(tmp_path, {"a.txt"})["passed"]
    assert not verify_checksums(tmp_path, {"a.txt", "missing.txt"})["passed"]


def test_synthetic_extraction_supports_long_windows_paths(tmp_path: Path) -> None:
    archive = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr(zipfile.ZipInfo("sample.txt", TIMESTAMP), b"synthetic")
    destination = tmp_path / ("a" * 80) / ("b" * 80)
    entry = {"path": "sample.txt", "bytes": 9,
             "sha256": hashlib.sha256(b"synthetic").hexdigest()}
    result = extract_archive(archive, destination, [entry])
    assert result["passed"]
    target = destination / "sample.txt"
    if os.name == "nt":
        target = Path("\\\\?\\" + str(target.resolve()))
    assert target.read_bytes() == b"synthetic"
