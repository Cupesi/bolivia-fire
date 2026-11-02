from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bolivia_fire.release import security
from bolivia_fire.release.security import scan_file, scan_text


def test_synthetic_unconfigured_notes_scan_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BOLIVIA_FIRE_RELEASE_TERMS", raising=False)
    assert security.load_internal_notes() is None
    result = security.security_report([])
    assert result["passed"]
    assert result["internal_notes_scan"] == "internal-notes scan: not configured"


def test_synthetic_external_terms_use_one_regex_per_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    terms = tmp_path / "synthetic_terms.txt"
    terms.write_text(r"\bsynthetic[_-]memo\b" + "\n" + r"synthetic\s+draft" + "\n",
                     encoding="utf-8")
    monkeypatch.setenv("BOLIVIA_FIRE_RELEASE_TERMS", str(terms))
    pattern = security.load_internal_notes()
    assert scan_text(b"SYNTHETIC_MEMO synthetic draft", pattern) == {"internal_notes": 2}
    public = tmp_path / "public.txt"
    public.write_bytes(b"synthetic draft")
    assert scan_file(public, pattern) == {"internal_notes": 1}
    assert security.security_report([("public.txt", public)], pattern)["findings"] == [
        {"path": "public.txt", "counts": {"internal_notes": 1}}]
    other = tmp_path / "synthetic_other.txt"
    other.write_text("synthetic_other\n", encoding="utf-8")
    explicit = security.load_internal_notes(other)
    assert scan_text(b"synthetic_other synthetic draft", explicit) == {"internal_notes": 1}


@pytest.mark.parametrize("body", ["[\n", "\n\n"])
def test_synthetic_invalid_terms_fail_without_disclosing_values(tmp_path: Path, body: str) -> None:
    terms = tmp_path / "synthetic_invalid.txt"
    terms.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="Internal-notes terms") as error:
        security.load_internal_notes(terms)
    assert str(terms) not in str(error.value)


def test_synthetic_external_terms_cover_stream_boundaries_and_zip_metadata(tmp_path: Path) -> None:
    terms = tmp_path / "synthetic_terms.txt"
    terms.write_text(r"synthetic_memo" + "\n", encoding="utf-8")
    pattern = security.load_internal_notes(terms)
    body = b" " * (1024**2 - 5) + b"synthetic_memo"
    assert security.scan_stream(io.BytesIO(body), pattern) == {"internal_notes": 1}
    path = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.comment = b"synthetic_memo"
        archive.writestr("synthetic_memo.txt", "synthetic_memo")
    assert scan_file(path, pattern) == {"internal_notes": 3}


@pytest.mark.parametrize("valid", [True, False])
def test_synthetic_opaque_identifiers_exempt_only_structural_records(
    tmp_path: Path, valid: bool,
) -> None:
    terms = tmp_path / "synthetic_terms.txt"
    terms.write_text("synthetic_reference\nsynthetic_memo\n", encoding="utf-8")
    pattern = security.load_internal_notes(terms)
    record = {"execution": {"synthetic_reference": "run_123456abcdef",
                            "synthetic_attempt": "ref_abcdef123456"},
              "note": "synthetic_memo"}
    if not valid:
        record["execution"]["synthetic_reference"] = "readable-private-note"
    path = tmp_path / "record.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    name = "artifacts/evaluation/extension_archive_attempt_002/extension_execution_report.json"
    result = security.security_report([(name, path)], pattern)
    assert result["findings"] == [
        {"path": name, "counts": {"internal_notes": 1 if valid else 2}}]
    assert len(result["documented_exemptions"]) == int(valid)


@pytest.mark.parametrize("field", ["user", "account", "username", "account_id"])
def test_synthetic_account_fields_require_redaction(field: str) -> None:
    body = ('{"' + field + '": "synthetic-private-account"}').encode()
    assert scan_text(body) == {"account": 1}
    assert scan_text(body.replace(b"synthetic-private-account", b"<redacted-account>")) == {}


def test_synthetic_security_patterns_and_public_url() -> None:
    assert scan_text(b'https://www.nasa.gov/home/public.html') == {}
    assert scan_text(b'{"user": "<redacted-account>"}') == {}
    assert scan_text(b'{"username": "synthetic-account"}') == {"account": 1}
    assert scan_text(b'https://example.org/?X-Amz-Signature=synthetic') == {"signed_url": 1}
    assert scan_text(b'Signature=synthetic') == {"signed_url": 1}
    assert scan_text(b'/api/area/csv/synthetic0000000000000000/2013-01-01') == {"credential": 1}
    assert scan_text(b'"D:/synthetic/private/file.txt"') == {"absolute_local_path": 1}


def test_synthetic_parquet_metadata_and_zip_comment_are_scanned(tmp_path: Path) -> None:
    table = pa.table({"synthetic": [1]}).replace_schema_metadata(
        {b"origin": b"D:/synthetic/private/file.txt"})
    path = tmp_path / "synthetic.parquet"
    pq.write_table(table, path)
    assert scan_file(path)["absolute_local_path"] == 1
    path = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("safe.txt", "public")
        archive.comment = b"D:/synthetic/private/file.txt"
    assert scan_file(path)["absolute_local_path"] == 1
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("data.txt", "https://example.org/?token=synthetic")
    path.write_bytes(payload.getvalue())
    assert scan_file(path)["signed_url"] == 1
