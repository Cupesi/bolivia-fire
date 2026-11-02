from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

GROUPS = ("benchmark-data", "reproduction-artifacts", "raw-inputs")
DOCS = ("README.md", "LICENSES.md", "DATA_DICTIONARY.md")
STORED = {".parquet", ".tif", ".tiff", ".npz", ".zip", ".gz", ".nc", ".png", ".gif",
          ".joblib", ".pt"}
TIMESTAMP = (2026, 1, 1, 0, 0, 0)
RESERVE = 55 * 1024**3
JSON_SERIALIZATION = "UTF-8; sorted object keys; two-space indentation; LF newlines"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def filesystem_path(path: Path) -> Path:
    resolved = path.resolve()
    if os.name == "nt" and not str(resolved).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(resolved))
    return resolved


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def atomic_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(body)
        replace_file(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def replace_file(source: Path, target: Path) -> None:
    for attempt in range(21):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 20:
                raise
            time.sleep(0.5)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def member_path(name: str) -> str:
    path = PurePosixPath(name)
    if (path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
            or not path.parts or name != path.as_posix()):
        raise ValueError("Unsafe member path")
    if path.name.lower() in {"thumbs.db", "desktop.ini", ".ds_store"}:
        raise ValueError("OS file in member path")
    if any(part.casefold() == ".env" for part in path.parts):
        raise ValueError("Secret file in member path")
    return name


def validate_version(version: str) -> None:
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Release version must contain three numeric components")


def select_entries(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in inventory["entries"]:
        group = item["proposed_inclusion"]
        if group == "exclude":
            continue
        if group not in GROUPS:
            raise ValueError("Unknown archive group")
        name = member_path(item["path"])
        if name.casefold() in seen:
            raise ValueError("Duplicate member path")
        seen.add(name.casefold())
        result.append(dict(item))
    return sorted(result, key=lambda item: item["path"])


def documentation_member(name: str) -> bool:
    return Path(name).suffix.lower() == ".md" or name in {"CITATION.cff", ".zenodo.json"}


def refresh_documentation(root: Path, entries: list[dict[str, Any]], stage: Path) -> None:
    for item in entries:
        member_path(item["path"])
        if not documentation_member(item["path"]):
            continue
        path = root / item["path"]
        frozen = item.get("audit_sha256", item["original_sha256"])
        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        source = "documentation/" + item["path"]
        atomic_bytes(stage / source, body)
        item.update(audit_sha256=frozen, original_sha256=frozen, sha256=digest,
                    bytes=len(body), source=source, source_location="staging")
        if digest != frozen:
            item.update(role="documentation_updated_for_release",
                        changes=["Current release documentation replaces frozen documentation"])


def sanitize(name: str, body: bytes) -> tuple[bytes, list[str]]:
    changes: list[str] = []
    if name.startswith("datasets/raw/vegetation/") and name.endswith("-request.json"):
        value = json.loads(body)

        def redact(node: Any, pointer: str = "") -> None:
            if isinstance(node, dict):
                for key, child in node.items():
                    location = pointer + "/" + key
                    if key.lower() in {"user", "username", "user_name", "user_id", "email",
                                       "account", "account_id"} and isinstance(child, str):
                        node[key] = "<redacted-account>"
                        changes.append(f"JSON {location} replaced with <redacted-account>")
                    else:
                        redact(child, location)
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    redact(child, f"{pointer}/{index}")

        redact(value)
        if changes:
            return json_bytes(value), changes
    return body, changes


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True).stdout


def inputs_directory(path: Path | None = None) -> Path:
    if path is not None:
        return path
    configured = os.environ.get("BOLIVIA_FIRE_RELEASE_INPUTS", "").strip()
    if configured:
        return Path(configured)
    raise ValueError("Release inputs require --inputs-dir, an explicit input directory option, "
                     "or BOLIVIA_FIRE_RELEASE_INPUTS")


def generated_entry(stage: Path, name: str, role: str) -> dict[str, Any]:
    source = stage / name
    return {"path": name, "source": name, "source_location": "staging",
            "bytes": source.stat().st_size, "sha256": sha256(source),
            "original_sha256": sha256(source), "changes": [], "role": role,
            "licence": "CC-BY-4.0", "proposed_inclusion": "reproduction-artifacts"}


def historical_sources(root: Path, stage: Path, audit: Path,
                       entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    revisions = read_json(audit / "producing_revisions.json")
    producing = []
    for item in entries:
        commits = item.get("producing_code_revisions", [])
        if commits:
            producing.append({"path": item["path"], "revisions": commits,
                              "tree_urls": [f"https://github.com/Cupesi/bolivia-fire/tree/{c}"
                                            for c in commits],
                              "evidence": item.get("revision_evidence", [])})
    expected: dict[str, list[str]] = {}
    classification = read_json(audit / "provenance_classification.json")
    for item in classification["mismatches"]:
        if item["target"] == "datasets/sources.yaml":
            expected[item["expected_sha256"]] = item["origins"]
    blobs: dict[str, dict[str, Any]] = {}
    commits = git(root, "log", "--all", "--format=%H", "--", "datasets/sources.yaml")
    for commit in commits.decode().splitlines():
        blob = git(root, "rev-parse", f"{commit}:datasets/sources.yaml").decode().strip()
        if blob in blobs:
            blobs[blob]["revisions"].append(commit)
            continue
        raw = git(root, "cat-file", "blob", blob)
        digest = hashlib.sha256(raw).hexdigest()
        restored = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        crlf_hash = hashlib.sha256(restored).hexdigest()
        if digest not in expected and crlf_hash not in expected:
            continue
        name = f"release/historical_sources/{blob}.yaml"
        atomic_bytes(stage / name, raw)
        record = {"path": name, "git_blob": blob, "sha256": digest, "revisions": [commit],
                  "satisfies": expected.get(digest, [])}
        if crlf_hash in expected and crlf_hash != digest:
            restored_name = f"release/historical_sources/{blob}.crlf.yaml"
            atomic_bytes(stage / restored_name, restored)
            record["newline_reconstruction"] = {
                "path": restored_name, "sha256": crlf_hash,
                "transformation": "LF to CRLF", "satisfies": expected[crlf_hash]}
        blobs[blob] = record
    satisfied = {r["sha256"] for r in blobs.values()}
    satisfied.update(r["newline_reconstruction"]["sha256"] for r in blobs.values()
                     if "newline_reconstruction" in r)
    missing = [{"sha256": digest, "references": origins,
                "status": "unretained intermediate registry"}
               for digest, origins in expected.items() if digest not in satisfied]
    document = {"packaging_commit": git(root, "rev-parse", "HEAD").decode().strip(),
                "producing_records": revisions, "producing_files": producing,
                "historical_sources": list(blobs.values()), "unretained_sources": missing}
    atomic_bytes(stage / "release/code_revisions.json", json_bytes(document))
    names = ["release/code_revisions.json"]
    names.extend(r["path"] for r in blobs.values())
    names.extend(r["newline_reconstruction"]["path"] for r in blobs.values()
                 if "newline_reconstruction" in r)
    return [generated_entry(stage, name, "provenance") for name in names]


def stage_release(root: Path, stage: Path, version: str, audit: Path) -> dict[str, Any]:
    validate_version(version)
    inventory_path = audit / "inventory.json"
    inventory = read_json(inventory_path)
    entries = select_entries(inventory)
    stage.mkdir(parents=True, exist_ok=True)
    sanitized = 0
    for item in entries:
        path = root / item["path"]
        digest = sha256(path)
        if ((digest != item["sha256"] or path.stat().st_size != item["bytes"])
                and not documentation_member(item["path"])):
            raise ValueError(f"Frozen inventory mismatch: {item['path']}")
        item["source"] = item["path"]
        item["source_location"] = "repository"
        item["original_sha256"] = item["sha256"]
        item["changes"] = []
        item["licence"] = ("upstream terms; see LICENSES.md"
                           if item["proposed_inclusion"] == "raw-inputs" else "CC-BY-4.0")
        if item["path"].endswith("-request.json"):
            body, changes = sanitize(item["path"], path.read_bytes())
            if changes:
                name = "sanitized/" + item["path"]
                atomic_bytes(stage / name, body)
                item.update(source=name, source_location="staging", bytes=len(body),
                            sha256=hashlib.sha256(body).hexdigest(), changes=changes)
                sanitized += 1
    refresh_documentation(root, entries, stage)
    entries.extend(historical_sources(root, stage, audit, entries))
    plan = {"version": version, "packaging_commit": git(root, "rev-parse", "HEAD").decode().strip(),
            "audit_commit": inventory["head"], "audit_inventory_sha256": sha256(inventory_path),
            "sanitized_files": sanitized, "entries": sorted(entries, key=lambda e: e["path"]),
            "sanitized_json_serialization": JSON_SERIALIZATION,
            "outage_review": "Path-shaped match is a public NASA URL; unchanged"}
    atomic_bytes(stage / "plan.json", json_bytes(plan))
    return {"members": len(entries), "sanitized_files": sanitized,
            "groups": {g: sum(e["proposed_inclusion"] == g for e in entries) for g in GROUPS}}


def payload_path(item: dict[str, Any], root: Path, stage: Path) -> Path:
    base = stage if item.get("source_location") == "staging" else root
    return base / member_path(item["source"])


def write_zip(target: Path, entries: list[dict[str, Any]], root: Path, stage: Path) -> None:
    temporary = target.with_name(target.name + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True, compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            for item in sorted(entries, key=lambda e: e["path"]):
                info = zipfile.ZipInfo(member_path(item["path"]), TIMESTAMP)
                info.compress_type = (zipfile.ZIP_STORED if Path(info.filename).suffix.lower()
                                      in STORED else zipfile.ZIP_DEFLATED)
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                digest = hashlib.sha256()
                size = 0
                with (payload_path(item, root, stage).open("rb") as source,
                      archive.open(info, "w", force_zip64=True) as destination):
                    while block := source.read(4 * 1024**2):
                        destination.write(block)
                        digest.update(block)
                        size += len(block)
                if digest.hexdigest() != item["sha256"] or size != item["bytes"]:
                    raise ValueError(f"Payload hash mismatch: {item['path']}")
        replace_file(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def write_checksums(stage: Path, names: list[str],
                    verified_hashes: dict[str, str] | None = None) -> None:
    omitted = {"SHA256SUMS", "validation_report.json", "validation_report.md"}
    digests = verified_hashes or {}
    body = "".join(f"{digests[name] if name in digests else sha256(stage / name)}  {name}\n"
                   for name in sorted(set(names) - omitted))
    atomic_bytes(stage / "SHA256SUMS", body.encode())


def package_release(root: Path, stage: Path, *, docs: Path | None = None,
                    minimum_free_bytes: int = RESERVE) -> dict[str, Any]:
    plan = read_json(stage / "plan.json")
    validate_version(plan["version"])
    names = set()
    for entry in plan["entries"]:
        name = member_path(entry["path"]).casefold()
        member_path(entry["source"])
        if name in names or entry["proposed_inclusion"] not in GROUPS:
            raise ValueError("Invalid or duplicate archive member")
        names.add(name)
    plan.setdefault("sanitized_json_serialization", JSON_SERIALIZATION)
    refresh_documentation(root, plan["entries"], stage)
    atomic_bytes(stage / "plan.json", json_bytes(plan))
    state_path = stage / "package_state.json"
    old = read_json(state_path) if state_path.exists() else {}
    archives = []
    public_entries = []
    for group in GROUPS:
        members = [e for e in plan["entries"] if e["proposed_inclusion"] == group]
        policy = {"timestamp": TIMESTAMP, "deflate_level": 6,
                  "compression": [(e["path"], 0 if Path(e["path"]).suffix.lower() in STORED
                                    else 8) for e in members]}
        plan_hash = hashlib.sha256(json_bytes({"members": members, "policy": policy})).hexdigest()
        name = f"bolivia-fire-{plan['version']}-{group}.zip"
        target = stage / name
        previous = old.get(group, {})
        existing_hash = (sha256(target) if previous.get("plan_sha256") == plan_hash
                         and target.is_file() else None)
        skip = existing_hash is not None and existing_hash == previous.get("sha256")
        if not skip:
            budget = sum(e["bytes"] for e in members) + len(members) * 1024 + 1024**2
            if shutil.disk_usage(stage).free - budget < minimum_free_bytes:
                raise ValueError("Archive would violate the free disk reserve")
            write_zip(target, members, root, stage)
        digest = existing_hash if skip else sha256(target)
        record = {"name": name, "bytes": target.stat().st_size, "sha256": digest,
                  "members": len(members), "plan_sha256": plan_hash, "skipped": skip}
        archives.append(record)
        old[group] = {k: v for k, v in record.items() if k != "skipped"}
        atomic_bytes(state_path, json_bytes(old))
        for entry in members:
            public_entries.append({k: v for k, v in entry.items()
                                   if k not in {"source", "source_location", "tracked_in_git"}} |
                                  {"archive": name, "public_sha256": entry["sha256"]})
    top_files = []
    if docs is not None:
        for name in DOCS:
            if (docs / name).is_file():
                atomic_bytes(stage / name, (docs / name).read_bytes())
    for name in DOCS:
        if (stage / name).is_file():
            top_files.append({"path": name, "bytes": (stage / name).stat().st_size,
                              "sha256": sha256(stage / name), "licence": "CC-BY-4.0"})
    result = {"version": plan["version"], "packaging_commit": plan["packaging_commit"],
              "archives": [{k: v for k, v in a.items() if k != "skipped"} for a in archives],
              "entries": public_entries, "top_level_files": top_files,
              "sanitized_json_serialization": plan["sanitized_json_serialization"],
              "missing_documentation": sorted(set(DOCS) - {e["path"] for e in top_files})}
    atomic_bytes(stage / "inventory.json", json_bytes(result))
    write_checksums(stage, [*(a["name"] for a in archives), "inventory.json",
                            *(e["path"] for e in top_files)],
                    {a["name"]: a["sha256"] for a in archives})
    return {"archives": archives, "missing_documentation": result["missing_documentation"]}
