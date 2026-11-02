from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

from bolivia_fire.config import Paths
from bolivia_fire.release.build import (
    atomic_bytes,
    package_release,
    sanitize,
    select_entries,
    sha256,
    write_checksums,
    write_zip,
)
from bolivia_fire.release.cli import register, run


@pytest.mark.parametrize("operation", ["stage", "package"])
def test_synthetic_release_requires_inputs_before_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    monkeypatch.delenv("BOLIVIA_FIRE_RELEASE_INPUTS", raising=False)
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    args = parser.parse_args(["release", operation, "--version", "0.1.0"])
    with pytest.raises(ValueError, match="BOLIVIA_FIRE_RELEASE_INPUTS"):
        run(args, Paths(tmp_path))
    assert not list(tmp_path.iterdir())


def test_synthetic_release_inputs_option_overrides_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bolivia_fire.release import build

    monkeypatch.setenv("BOLIVIA_FIRE_RELEASE_INPUTS", str(tmp_path / "environment"))
    assert build.inputs_directory() == tmp_path / "environment"
    assert build.inputs_directory(tmp_path / "explicit") == tmp_path / "explicit"


@pytest.mark.parametrize("source", ["environment", "option", "direct"])
def test_synthetic_package_uses_explicit_documentation_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str,
) -> None:
    monkeypatch.delenv("BOLIVIA_FIRE_RELEASE_INPUTS", raising=False)
    stage = tmp_path / "datasets/releases/0.1.0"
    stage.mkdir(parents=True)
    plan = {"version": "0.1.0", "packaging_commit": "synthetic", "entries": []}
    (stage / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    inputs = tmp_path / "synthetic_inputs"
    docs = inputs / "archive_docs"
    docs.mkdir(parents=True)
    for name in ("README.md", "LICENSES.md", "DATA_DICTIONARY.md"):
        (docs / name).write_bytes(b"synthetic public documentation")
    options = []
    if source == "environment":
        monkeypatch.setenv("BOLIVIA_FIRE_RELEASE_INPUTS", str(inputs))
    elif source == "option":
        monkeypatch.setenv("BOLIVIA_FIRE_RELEASE_INPUTS", str(tmp_path / "unused"))
        options = ["--inputs-dir", str(inputs)]
    else:
        options = ["--docs-dir", str(docs)]
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    args = parser.parse_args(["release", "package", "--version", "0.1.0", *options])
    assert run(args, Paths(tmp_path)) == 0
    assert (stage / "README.md").read_bytes() == b"synthetic public documentation"


def synthetic_entry(root: Path, name: str, body: bytes, group: str) -> dict:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return {"path": name, "bytes": len(body), "sha256": sha256(path),
            "proposed_inclusion": group, "role": "synthetic", "licence": "CC0"}


def test_select_synthetic_inventory_and_reject_unsafe_names(tmp_path: Path) -> None:
    entries = [synthetic_entry(tmp_path, "a.json", b"{}", "benchmark-data"),
               synthetic_entry(tmp_path, "b.txt", b"omit", "exclude")]
    assert [e["path"] for e in select_entries({"entries": entries})] == ["a.json"]
    with pytest.raises(ValueError, match="member path"):
        select_entries({"entries": [{**entries[0], "path": "../escape"}]})


def test_sanitize_synthetic_request_preserves_original_mapping() -> None:
    original = b'{"user": "synthetic-private-account", "task_id": "abc"}\n'
    public, changes = sanitize("datasets/raw/vegetation/abc/demo-request.json", original)
    assert json.loads(public) == {"user": "<redacted-account>", "task_id": "abc"}
    assert changes == ["JSON /user replaced with <redacted-account>"]
    assert hashlib.sha256(public).hexdigest() != hashlib.sha256(original).hexdigest()
    assert b"synthetic-private-account" in original
    html = b'<a href="/home/public/products">Provider page</a>'
    assert sanitize("datasets/raw/outages/demo.html", html) == (html, [])


def test_synthetic_zip_is_deterministic_sorted_and_uses_policy(tmp_path: Path) -> None:
    a = synthetic_entry(tmp_path, "z.parquet", b"synthetic parquet", "benchmark-data")
    b = synthetic_entry(tmp_path, "a.json", b"{}", "benchmark-data")
    for entry in (a, b):
        entry["source"] = entry["path"]
    write_zip(tmp_path / "first.zip", [a, b], tmp_path, tmp_path)
    write_zip(tmp_path / "second.zip", [b, a], tmp_path, tmp_path)
    assert (tmp_path / "first.zip").read_bytes() == (tmp_path / "second.zip").read_bytes()
    with zipfile.ZipFile(tmp_path / "first.zip") as archive:
        assert archive.namelist() == ["a.json", "z.parquet"]
        assert [i.compress_type for i in archive.infolist()] == [8, 0]
        assert all(i.date_time == (2026, 1, 1, 0, 0, 0) for i in archive.infolist())
        assert archive.comment == b""


def test_synthetic_checksums_exclude_self_and_report(tmp_path: Path) -> None:
    (tmp_path / "inventory.json").write_text("{}", encoding="utf-8")
    (tmp_path / "SHA256SUMS").write_text("old", encoding="utf-8")
    (tmp_path / "validation_report.json").write_text("{}", encoding="utf-8")
    write_checksums(tmp_path, ["inventory.json", "SHA256SUMS", "validation_report.json"])
    assert (tmp_path / "SHA256SUMS").read_text() == (
        sha256(tmp_path / "inventory.json") + "  inventory.json\n"
    )


def test_synthetic_package_restarts_and_rechecks_corrupt_output(tmp_path: Path) -> None:
    entry = synthetic_entry(tmp_path, "a.json", b"{}", "benchmark-data")
    entry.update(source="a.json", original_sha256=entry["sha256"], changes=[])
    stage = tmp_path / "release"
    stage.mkdir()
    plan = {"version": "0.1.0", "packaging_commit": "synthetic", "entries": [entry]}
    (stage / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    first = package_release(tmp_path, stage, minimum_free_bytes=0)
    inventory_bytes = (stage / "inventory.json").read_bytes()
    second = package_release(tmp_path, stage, minimum_free_bytes=0)
    assert (stage / "inventory.json").read_bytes() == inventory_bytes
    assert all(not a["skipped"] for a in first["archives"])
    assert all(a["skipped"] for a in second["archives"])
    target = stage / first["archives"][0]["name"]
    target.write_bytes(b"corrupt")
    third = package_release(tmp_path, stage, minimum_free_bytes=0)
    assert third["archives"][0]["skipped"] is False
    assert sha256(target) == first["archives"][0]["sha256"]


def test_synthetic_zip_rejects_changed_payload(tmp_path: Path) -> None:
    entry = synthetic_entry(tmp_path, "a.json", b"{}", "benchmark-data")
    entry["source"] = "a.json"
    (tmp_path / "a.json").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Payload hash"):
        write_zip(tmp_path / "output.zip", [entry], tmp_path, tmp_path)
    assert not (tmp_path / "output.zip").exists()


def test_synthetic_atomic_write_retries_transient_windows_lock(tmp_path, monkeypatch) -> None:
    calls = []
    original = os.replace

    def transient(source, target):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("synthetic transient sharing lock")
        original(source, target)

    monkeypatch.setattr(os, "replace", transient)
    atomic_bytes(tmp_path / "synthetic.json", b"{}")
    assert len(calls) == 3
    assert (tmp_path / "synthetic.json").read_bytes() == b"{}"


def test_synthetic_restart_rejects_escape_before_writing_documentation(tmp_path: Path) -> None:
    root = tmp_path / "root" / "nested"
    root.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"synthetic source")
    stage = tmp_path / "release" / "v1"
    stage.mkdir(parents=True)
    entry = {"path": "../../outside.md", "source": "../../outside.md",
             "proposed_inclusion": "benchmark-data", "original_sha256": sha256(outside),
             "sha256": sha256(outside), "bytes": outside.stat().st_size}
    plan = {"version": "0.1.0", "packaging_commit": "synthetic", "entries": [entry]}
    (stage / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsafe member path"):
        package_release(root, stage, minimum_free_bytes=0)
    assert not (tmp_path / "release" / "outside.md").exists()


def test_synthetic_restart_rejects_unsafe_version_before_writes(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    plan = {"version": "../outside", "packaging_commit": "synthetic", "entries": []}
    (stage / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    before = (stage / "plan.json").read_bytes()
    with pytest.raises(ValueError, match="Release version"):
        package_release(tmp_path, stage, minimum_free_bytes=0)
    assert (stage / "plan.json").read_bytes() == before


def test_synthetic_restart_rejects_secret_source_before_reading(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    secret = synthetic_entry(tmp_path, ".env", b"synthetic private content", "benchmark-data")
    secret.update(path="public.txt", source=".env", original_sha256=secret["sha256"])
    plan = {"version": "0.1.0", "packaging_commit": "synthetic", "entries": [secret]}
    (stage / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="Secret file"):
        package_release(tmp_path, stage, minimum_free_bytes=0)
    assert not list(stage.glob("*.zip"))
