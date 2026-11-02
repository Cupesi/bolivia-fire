from __future__ import annotations

import hashlib
import platform
import re
import shutil
import stat
import zipfile
from importlib.metadata import version
from pathlib import Path
from typing import Any

from bolivia_fire.release.build import (
    GROUPS,
    RESERVE,
    TIMESTAMP,
    atomic_bytes,
    filesystem_path,
    git,
    json_bytes,
    member_path,
    read_json,
    sha256,
)
from bolivia_fire.release.security import scan_text, security_report


def extract_archive(archive_path: Path, destination: Path,
                    entries: list[dict[str, Any]],
                    internal_notes: re.Pattern[bytes] | None = None) -> dict[str, Any]:
    destination = filesystem_path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Independent extraction requires an empty directory")
    destination.mkdir(parents=True, exist_ok=True)
    expected = {e["path"]: e for e in entries}
    if len(expected) != len(entries):
        raise ValueError("Duplicate inventory member")
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError("Archive member set differs from inventory")
        if names != sorted(names):
            raise ValueError("Archive member order is not sorted")
        findings = []
        if counts := scan_text(archive.comment, internal_notes):
            findings.append({"path": archive_path.name, "counts": counts})
        for info in archive.infolist():
            name = member_path(info.filename)
            if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("Directory or symbolic link archive member")
            if info.date_time != TIMESTAMP:
                raise ValueError("Unexpected archive timestamp")
            if counts := scan_text(info.filename.encode() + info.comment, internal_notes):
                findings.append({"path": name, "counts": counts})
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with archive.open(info) as source, target.open("wb") as output:
                while block := source.read(4 * 1024**2):
                    output.write(block)
                    digest.update(block)
                    size += len(block)
            item = expected[name]
            if digest.hexdigest() != item["sha256"] or size != item["bytes"]:
                raise ValueError(f"Extracted member hash mismatch: {name}")
    return {"passed": not findings, "members": len(entries),
            "bytes": sum(e["bytes"] for e in entries), "metadata_findings": findings,
            "sha256": sha256(archive_path), "all_member_hashes_match": True}


def verify_checksums(stage: Path, expected: set[str]) -> dict[str, Any]:
    checks = []
    seen = set()
    for line in (stage / "SHA256SUMS").read_text("utf-8").splitlines():
        digest, name = line.split("  ", 1)
        member_path(name)
        if name in seen or name == "SHA256SUMS":
            raise ValueError("Invalid checksum member")
        seen.add(name)
        path = stage / name
        checks.append({"path": name, "passed": path.is_file() and sha256(path) == digest})
    return {"passed": seen == expected and all(c["passed"] for c in checks),
            "files": checks, "missing": sorted(expected - seen),
            "unexpected": sorted(seen - expected)}


def access_log_hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha256(p)
            for p in sorted((root / "artifacts/evaluation").rglob("*test*log*.json"))}


def write_report(stage: Path, report: dict[str, Any]) -> None:
    checks = report.get("checks", {})
    report["passed"] = bool(checks) and all(c.get("passed", False) for c in checks.values())
    atomic_bytes(stage / "validation_report.json", json_bytes(report))
    lines = ["# Release validation", "", f"Passed: {report['passed']}", "",
             report["internal_notes_scan"], "",
             "No final-test metric opening occurred. Test rows were checked only for schema, "
             "counts, keys, partition assignments and label nullness.", "",
             "| Check | Passed |", "|---|---:|"]
    lines.extend(f"| {name} | {check.get('passed', False)} |" for name, check in checks.items())
    lines.extend(["", "| Archive | Bytes | Members | SHA-256 |", "|---|---:|---:|---|"])
    for archive in report["archives"]:
        lines.append(f"| {archive['name']} | {archive['bytes']} | {archive['members']} "
                     f"| {archive['sha256']} |")
    lines.extend(["", "Models were loaded only from trusted local release files. "
                  "Joblib/pickle can execute code and is unsafe for untrusted inputs.", "",
                  "The JSON report records individual checks, counts, tolerances, "
                  "environment versions and retained limitations.", ""])
    atomic_bytes(stage / "validation_report.md", "\n".join(lines).encode())


def verify_release(root: Path, stage: Path, extract_to: Path, *,
                   internal_notes: re.Pattern[bytes] | None = None) -> dict[str, Any]:
    destination = extract_to.resolve()
    if not destination.is_relative_to((stage / "verify").resolve()):
        raise ValueError("Clean extraction must be under this release's verify directory")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Clean extraction directory must be empty")
    inventory = read_json(stage / "inventory.json")
    needed = sum(e["bytes"] for e in inventory["entries"])
    if shutil.disk_usage(stage).free - needed < RESERVE:
        raise ValueError("Clean extraction would violate the free disk reserve")
    before = access_log_hashes(root)
    report = {"version": inventory["version"], "packaging_commit": inventory["packaging_commit"],
              "verification_commit": git(root, "rev-parse", "HEAD").decode().strip(),
              "environment": {"python": platform.python_version(),
                              **{n: version(n) for n in ("lightgbm", "numpy", "pandas", "pyarrow",
                                                        "scikit-learn", "xgboost", "joblib")}},
              "archives": inventory["archives"], "checks": {}, "test_opening": False,
              "internal_notes_scan": ("internal-notes scan: " + (
                  "configured" if internal_notes is not None else "not configured")),
              "test_access_log_hashes_before": before, "errors": [],
              "known_limitations": [
                  "The older cell_places manifest hash differs from the retained display table; "
                  "the older bytes were not retained",
                  "Fourteen documented intermediate states are unretained; see provenance records",
                  "The MOD13A2 acquisition buffer was 0.05 degrees versus registered 0.1 degrees",
                  "CatBoost GPU fits are not digit reproducible; no new fitting was performed",
                  "Some frozen source-registry states require the archived historical blobs; "
                  "one recorded intermediate registry remains unretained",
                  "Year 2026 environmental features are confirmation-support inputs outside "
                  "the registered panel domain; no 2026 confirmation study exists",
              ],
              "sanitization": [{"path": e["path"], "original_sha256": e["original_sha256"],
                                "public_sha256": e["sha256"], "changes": e["changes"]}
                               for e in inventory["entries"] if e.get("changes")]}
    checks = report["checks"]
    roots = {}
    try:
        expected = {a["name"] for a in inventory["archives"]}
        expected.update({"inventory.json", *(f["path"] for f in inventory["top_level_files"])})
        checks["SHA256SUMS"] = verify_checksums(stage, expected)
        for group, archive in zip(GROUPS, inventory["archives"], strict=True):
            name = archive["name"]
            roots[group] = destination / group
            entries = [e for e in inventory["entries"] if e["archive"] == name]
            extraction = extract_archive(stage / name, roots[group], entries, internal_notes)
            extraction["passed"] &= (extraction["sha256"] == archive["sha256"]
                                      and extraction["members"] == archive["members"])
            checks[f"extract_{group}"] = extraction
            write_report(stage, report)
        public_files = [(e["archive"] + "/" + e["path"],
                         destination / e["proposed_inclusion"] / e["path"])
                        for e in inventory["entries"]]
        checks["archive_member_security"] = security_report(public_files, internal_notes)
        staged_files = [(p.relative_to(stage).as_posix(), p) for p in sorted(stage.rglob("*"))
                        if p.is_file() and not p.is_relative_to(stage / "verify")
                        and p.suffix != ".zip" and not p.name.startswith("validation_report")]
        checks["staged_security"] = security_report(staged_files, internal_notes)
        checks["archive_documentation"] = {"passed": not inventory["missing_documentation"],
                                            "missing": inventory["missing_documentation"]}
        write_report(stage, report)
        from bolivia_fire.release.validation import validate_data

        checks["extracted_data"] = validate_data(roots["benchmark-data"],
                                                 roots["reproduction-artifacts"])
        write_report(stage, report)
        from bolivia_fire.release.roundtrip import validate_models

        checks["validation_model_roundtrip"] = validate_models(
            roots["benchmark-data"], roots["reproduction-artifacts"], destination / "scratch")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        # Report exception types only; third-party messages may contain local paths.
        report["errors"].append({"error_type": type(exc).__name__})
        checks["execution"] = {"passed": False, "error_type": type(exc).__name__}
    after = access_log_hashes(root)
    checks["no_test_opening"] = {"passed": before == after, "metric_test_access": False,
                                  "access_logs_unchanged": before == after}
    report["test_access_log_hashes_after"] = after
    report["free_disk_bytes_after"] = shutil.disk_usage(stage).free
    write_report(stage, report)
    checks["report_security"] = security_report([
        ("validation_report.json", stage / "validation_report.json"),
        ("validation_report.md", stage / "validation_report.md"),
    ], internal_notes)
    write_report(stage, report)
    return {"passed": report["passed"], "checks": {n: c["passed"] for n, c in checks.items()},
            "report": "datasets/releases/" + inventory["version"] + "/validation_report.json"}
