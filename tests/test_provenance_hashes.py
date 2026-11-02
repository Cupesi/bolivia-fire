from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from bolivia_fire.extension.amendment import (
    AMENDMENT_CONTRACT,
    AMENDMENT_DOCUMENT,
    AMENDMENT_REGISTRATION,
)
from bolivia_fire.extension.registration import REGISTERED_FILES, REGISTRATION

ROOT = Path(__file__).resolve().parents[1]
LEGACY_SOURCE = (
    "datasets/sources.yaml",
    "2cc470a1199c578be5e6707319170e216904828c390397d05761c6ed1418c168",
)
APPEEARS_INTERMEDIATE_MANIFESTS = {
    "datasets/manifests/appeears_download_322434e3-151a-4c8e-97a2-b87a710feedf.json":
        "7a2a495a5dbc1c0d56594d1805f2d492f7867d02615087f09fa03969fcf61aa7",
    "datasets/manifests/appeears_download_6e6d2561-6e0d-4d46-9f4c-2df864442742.json":
        "6b96765095c4273d3362d6ef073ecfc72dd431ee104f7509d8a0c0ed4d2e4915",
    "datasets/manifests/appeears_download_7b9b48fa-2459-453a-9d0e-494fc148017e.json":
        "79d4959e3316eab3666b4a810d8a1d5b5f29417ba1adea5ea9a804e4ab292228",
    "datasets/manifests/appeears_download_88cc0908-0d05-43e7-97d8-383e1b161d7c.json":
        "a1f5fdda98f597e6915eaa0b1a0292ec49106383d6acf9bfc3cef4c256f72b79",
    "datasets/manifests/appeears_download_f698da1c-da72-4e71-9351-5ea4d36944b4.json":
        "5624a3dc758167b00ef313e134d21ee1ec345034b41b369468bfadca65aa689a",
}
# Exact historical exceptions require a written explanation before any addition.
EXCEPTIONS = {
    **{
        (path, digest): (
            "The AppEEARS integrity check hashed this uncommitted manifest state before "
            "later resume passes rewrote retrieval timestamps; task, file count and bytes "
            "are checked against the committed manifest."
        )
        for path, digest in APPEEARS_INTERMEDIATE_MANIFESTS.items()
    },
    LEGACY_SOURCE: "The de457a9 registry was hashed with CRLF before Git normalized it.",
    (
        "datasets/sources.yaml",
        "395eea230b421c474dd20d5d73c2078d752705df372d9eaab77c84368d7ee697",
    ): "A land-cover audit hashed an intermediate registry that was not committed.",
    (
        "datasets/licenses/MapBiomas.md",
        "457ff09a68bc025876a62c589cf791ec6d73e2515b2dc30e754e191be498ec5f",
    ): "A land-cover audit hashed intermediate licence evidence that was not committed.",
    (
        "datasets/manifests/mapbiomas_raster_requests.jsonl",
        "5ab5771b88c63301d66f4cbf1dfffebc9503de4aeb8d455c6fae89d3fdd66d73",
    ): "A land-cover audit hashed an intermediate request manifest that was not committed.",
    (
        "datasets/schemas/landcover.yaml",
        "7d9443490bd1d594e8d9c4e5d4d402b9016a4ce600c6788ccb257e0748890309",
    ): "A land-cover audit hashed an intermediate schema that was not committed.",
    (
        "docs/covariates.md",
        "1ff2d452101e1aef1b7d3340fca27af928a5dc92ebceaf4049cdffc2d8349b63",
    ): "A land-cover audit hashed an intermediate covariate document that was not committed.",
    (
        "docs/covariates.md",
        "8949a32002e0cec3966fd1e5d85fa83a41ab1f129bc908863184b384b6b2ab62",
    ): "A land-cover audit hashed another uncommitted intermediate covariate document.",
    (
        "src/bolivia_fire/extension/inputs.py",
        "688c1826bb94d668704247921beb6024f9765064593c5a459b08f5b9b993923a",
    ): "Extension attempt 002 hashed an intermediate input implementation that was not committed.",
    (
        "tests/extension/test_inputs.py",
        "9210e8c7908e0e103bcc89218ae71986c8b94e2065ad2659cfb75d029e537f80",
    ): "Extension verification hashed an intermediate test revision that was not committed.",
    (
        "artifacts/evaluation/extension_inputs.json",
        "56f1da52aced3eaee8c090a7cfa60b0d591e3c0c4d7cdb321210b93fb2e9aefe",
    ): "A retained guard references an earlier input plan that was not committed.",
    (
        "artifacts/evaluation/extension_inputs.json",
        "9ef980d9c2e8eb4097f3d20eaa0cc24a4342f179479216d64f0ed48284a45ebe",
    ): "A retained record references another earlier input plan that was not committed.",
}
REGISTERED_HASH_PATHS = {
    **{f"{name}_sha256": path for name, path in REGISTERED_FILES.items()},
    "registration_sha256": REGISTRATION,
    "base_registration_sha256": REGISTRATION,
    "base_contract_sha256": REGISTERED_FILES["contract"],
    "base_document_sha256": REGISTERED_FILES["document"],
    "amendment_contract_sha256": AMENDMENT_CONTRACT,
    "amendment_document_sha256": AMENDMENT_DOCUMENT,
    "amendment_registration_sha256": AMENDMENT_REGISTRATION,
}


def _git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        input=input_bytes,
        capture_output=True,
        check=True,
    ).stdout


def _references(value: Any, tracked: set[str]) -> Iterator[tuple[str, str]]:
    if isinstance(value, list):
        for item in value:
            yield from _references(item, tracked)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == "preceding_revision":
                continue
            if isinstance(item, str) and re.fullmatch(r"[0-9a-fA-F]{64}", item):
                candidates = [key, REGISTERED_HASH_PATHS.get(key)]
                if key.endswith("_sha256"):
                    stem = key.removesuffix("_sha256")
                    candidates.extend(value.get(name) for name in (stem, f"{stem}_path"))
                if key in {"sha256", "current_sha256", "previous_sha256"}:
                    candidates.extend(
                        value.get(name) for name in ("path", "file", "relative_path")
                    )
                    # Provider bundle filenames do not identify repository-root files.
                    filename = value.get("file_name")
                    if isinstance(filename, str) and "/" in filename:
                        candidates.append(filename)
                if (
                    key == "registration_sha256"
                    and "amendment_id" in value
                    and "amendment_registration_sha256" not in value
                ):
                    candidates = [AMENDMENT_REGISTRATION]
                for path in candidates:
                    if isinstance(path, str) and path in tracked:
                        yield path, item.lower()
            yield from _references(item, tracked)


def _record_references(
    value: Any, tracked: set[str], origin: str
) -> Iterator[tuple[str, str, str]]:
    parts = PurePosixPath(origin).parts
    if not (
        len(parts) == 5
        and parts[:3] == ("artifacts", "evaluation", "amendments")
        and parts[-1] == "extension_selection.json"
        and isinstance(value, dict)
        and "preserved_h3" in value
    ):
        for target, digest in _references(value, tracked):
            yield target, digest, origin
        return
    ordinary = {key: item for key, item in value.items() if key != "preserved_h3"}
    for target, digest in _references(ordinary, tracked):
        yield target, digest, origin
    copies = value["preserved_h3"]
    assert isinstance(copies, dict), f"Invalid preserved_h3 mapping in {origin}"
    for source, evidence in copies.items():
        message = f"Unverified preserved_h3 source {source} in {origin}"
        assert source in tracked and isinstance(evidence, dict), message
        content = _git(ROOT, "show", f"HEAD:{source}")
        digest = evidence.get("sha256")
        assert isinstance(digest, str) and digest.lower() == hashlib.sha256(content).hexdigest(), (
            message
        )
        assert "record" in evidence, message
        # JSON serialization distinguishes booleans from equal-valued integers.
        assert json.dumps(evidence["record"], sort_keys=True) == json.dumps(
            json.loads(content), sort_keys=True
        ), message
        yield source, digest.lower(), origin
        metadata = {key: item for key, item in evidence.items() if key != "record"}
        for target, metadata_digest in _references(metadata, tracked):
            yield target, metadata_digest, origin
        for target, nested_digest in _references(evidence["record"], tracked):
            yield target, nested_digest, source


def _reference_paths(origin: str, target: str) -> tuple[str, ...]:
    record_parts = PurePosixPath(origin).parts
    target_parts = PurePosixPath(target).parts
    if (
        len(record_parts) >= 4
        and record_parts[:2] == ("artifacts", "evaluation")
        and record_parts[2].startswith("extension_archive_attempt_")
        and len(target_parts) == 3
        and target_parts[:2] == ("artifacts", "evaluation")
    ):
        archive = "/".join(record_parts[:3])
        name = target_parts[-1]
        return target, f"{archive}/{name}", f"{archive}/retired_active/{name}"
    return (target,)


def _historical_hashes(root: Path, paths: set[str]) -> dict[str, set[str]]:
    objects: dict[str, set[str]] = {}
    for path in sorted(paths):
        entries = _git(
            root, "rev-list", "--full-history", "--objects", "HEAD", "--", path
        ).decode().splitlines()
        objects[path] = {
            oid for line in entries for oid, _, name in [line.partition(" ")] if name == path
        }
    oids = sorted(set().union(*objects.values()))
    stream = io.BytesIO(_git(root, "cat-file", "--batch", input_bytes="\n".join(oids).encode()))
    hashes = {}
    for oid in oids:
        actual_oid, kind, size = stream.readline().split()
        assert actual_oid.decode() == oid and kind == b"blob"
        hashes[oid] = hashlib.sha256(stream.read(int(size))).hexdigest()
        assert stream.read(1) == b"\n"
    return {path: {hashes[oid] for oid in versions} for path, versions in objects.items()}


def _require_git_history() -> None:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("Recorded provenance requires a Git checkout with reachable history")
    if _git(ROOT, "rev-parse", "--is-shallow-repository").strip() == b"true":
        pytest.skip("Shallow clone lacks the history required to verify recorded provenance")


@pytest.mark.parametrize("path,digest", APPEEARS_INTERMEDIATE_MANIFESTS.items())
def test_appeears_intermediate_manifest_claim(path: str, digest: str) -> None:
    _require_git_history()
    baseline = json.loads(
        _git(ROOT, "show", "HEAD:datasets/manifests/appeears_resume_baseline.json")
    )
    bundles = [
        bundle for bundle in baseline["appeears_integrity.json"]["bundles"]
        if bundle["manifest"] == path and bundle["manifest_sha256"] == digest
    ]
    assert len(bundles) == 1, f"Expected one integrity-check bundle for {path} {digest}"
    bundle = bundles[0]
    manifest = json.loads(_git(ROOT, "show", f"HEAD:{path}"))
    assert bundle["task_id"] == manifest["task_id"]
    assert bundle["files_verified"] == len(manifest["files"])
    assert bundle["bytes_verified"] == sum(item["file_size"] for item in manifest["files"])


@pytest.mark.parametrize(
    "case",
    [
        "same_archive", "retired_active", "deleted_historical_copy", "outside_archive",
        "other_archive", "wrong_hash", "mixed_live_and_archived_origins",
    ],
)
def test_synthetic_archived_provenance_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    if shutil.which("git") is None:
        pytest.skip("Synthetic archive provenance requires Git")
    _git(tmp_path, "init")
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    evaluation = "artifacts/evaluation"
    archive = f"{evaluation}/extension_archive_attempt_007"
    target = f"{evaluation}/fit.json"
    old = b'{"synthetic": "archived"}\n'
    digest = hashlib.sha256(old).hexdigest()
    if case == "wrong_hash":
        digest = hashlib.sha256(b"synthetic absent bytes").hexdigest()
    destination = f"{archive}/fit.json"
    if case == "retired_active":
        destination = f"{archive}/retired_active/fit.json"
    elif case == "other_archive":
        destination = f"{evaluation}/extension_archive_attempt_008/fit.json"
    origins = [f"{archive}/nested/report.json"]
    if case == "outside_archive":
        origins = [f"{evaluation}/report.json"]
    elif case == "mixed_live_and_archived_origins":
        origins.append(f"{evaluation}/report.json")
    contents = {
        target: b'{"synthetic": "current"}\n',
        destination: old,
        **{
            origin: json.dumps({"path": target, "sha256": digest}).encode()
            for origin in origins
        },
    }
    for name, content in contents.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    _git(tmp_path, "add", ".")
    commit_args = (
        "-c", "user.name=Synthetic Test", "-c", "user.email=synthetic@example.invalid",
        "commit", "-m", "synthetic provenance fixture",
    )
    _git(tmp_path, *commit_args)
    if case == "deleted_historical_copy":
        (tmp_path / destination).unlink()
        _git(tmp_path, "add", "-u")
        _git(tmp_path, *commit_args)
    if case in {"same_archive", "retired_active", "deleted_historical_copy"}:
        test_recorded_hashes_match_reachable_git_blobs()
    else:
        with pytest.raises(AssertionError, match="Recorded hashes absent"):
            test_recorded_hashes_match_reachable_git_blobs()


@pytest.fixture
def synthetic_provenance_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if shutil.which("git") is None:
        pytest.skip("Synthetic provenance requires Git")
    _git(tmp_path, "init")
    _git(tmp_path, "config", "core.autocrlf", "false")
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    return tmp_path


def _commit_synthetic_records(root: Path, records: dict[str, Any]) -> None:
    for name, value in records.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json.dumps(value).encode("utf-8"))
    _git(root, "add", ".")
    _git(
        root, "-c", "user.name=Synthetic Test", "-c", "user.email=synthetic@example.invalid",
        "commit", "-m", "synthetic provenance fixture",
    )


@pytest.mark.parametrize(
    "case", ["base", "standalone_guard", "dual_metadata", "swapped_dual", "wrong_guard"]
)
def test_synthetic_registration_provenance(synthetic_provenance_repo: Path, case: str) -> None:
    base = {"synthetic": "base registration"}
    amendment = {"synthetic": "amendment registration"}
    base_digest = hashlib.sha256(json.dumps(base).encode()).hexdigest()
    amendment_digest = hashlib.sha256(json.dumps(amendment).encode()).hexdigest()
    records = {
        "base": {"registration_sha256": base_digest},
        "standalone_guard": {
            "amendment_id": "synthetic", "registration_sha256": amendment_digest,
        },
        "dual_metadata": {
            "amendment_id": "synthetic", "registration_sha256": base_digest,
            "amendment_registration_sha256": amendment_digest,
        },
        "swapped_dual": {
            "amendment_id": "synthetic", "registration_sha256": amendment_digest,
            "amendment_registration_sha256": base_digest,
        },
        "wrong_guard": {"amendment_id": "synthetic", "registration_sha256": base_digest},
    }
    _commit_synthetic_records(synthetic_provenance_repo, {
        REGISTRATION: base,
        AMENDMENT_REGISTRATION: amendment,
        "artifacts/evaluation/report.json": records[case],
    })
    if case in {"swapped_dual", "wrong_guard"}:
        with pytest.raises(AssertionError, match="Recorded hashes absent"):
            test_recorded_hashes_match_reachable_git_blobs()
    else:
        test_recorded_hashes_match_reachable_git_blobs()


@pytest.mark.parametrize(
    "case",
    [
        "verified_archive", "verified_retired_active", "wrong_source_digest",
        "missing_source", "tampered_record", "tampered_type", "cross_archive",
        "live_source", "outside_selection", "mixed_live_origin", "historical_source",
        "uncommitted_source", "unverified_wrapper_reference",
    ],
)
def test_synthetic_preserved_h3_provenance(synthetic_provenance_repo: Path, case: str) -> None:
    evaluation = "artifacts/evaluation"
    archive = f"{evaluation}/extension_archive_attempt_007"
    target = f"{evaluation}/fit.json"
    destination = f"{archive}/fit.json"
    source = f"{archive}/report.json"
    selection_path = f"{evaluation}/amendments/synthetic/extension_selection.json"
    if case == "verified_retired_active":
        destination = f"{archive}/retired_active/fit.json"
    elif case == "cross_archive":
        destination = f"{evaluation}/extension_archive_attempt_008/fit.json"
    elif case == "live_source":
        source = f"{evaluation}/report.json"
    elif case == "outside_selection":
        selection_path = f"{evaluation}/amendments/synthetic/report.json"
    old = {"synthetic": "archived fit"}
    digest = hashlib.sha256(json.dumps(old).encode()).hexdigest()
    record = {"path": target, "sha256": digest, "synthetic_version": 1}
    source_digest = hashlib.sha256(json.dumps(record).encode()).hexdigest()
    embedded = dict(record)
    if case == "wrong_source_digest":
        source_digest = "0" * 64
    elif case == "tampered_record":
        embedded["synthetic_version"] = 2
    elif case == "tampered_type":
        embedded["synthetic_version"] = True
    selection = {"preserved_h3": {source: {"sha256": source_digest, "record": embedded}}}
    if case == "mixed_live_origin":
        selection["live_reference"] = {"path": target, "sha256": digest}
    elif case == "unverified_wrapper_reference":
        selection["preserved_h3"][source]["extra"] = {"path": target, "sha256": digest}
    contents = {
        target: {"synthetic": "current fit"},
        destination: old,
        selection_path: selection,
    }
    if case not in {"missing_source", "uncommitted_source"}:
        contents[source] = record
    _commit_synthetic_records(synthetic_provenance_repo, contents)
    if case == "historical_source":
        _commit_synthetic_records(
            synthetic_provenance_repo, {source: {"synthetic": "replacement report"}}
        )
    elif case == "uncommitted_source":
        path = synthetic_provenance_repo / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json.dumps(record).encode())
    if case in {"verified_archive", "verified_retired_active"}:
        test_recorded_hashes_match_reachable_git_blobs()
    else:
        with pytest.raises(AssertionError):
            test_recorded_hashes_match_reachable_git_blobs()


def test_recorded_hashes_match_reachable_git_blobs() -> None:
    _require_git_history()
    tracked = set(_git(ROOT, "ls-tree", "-r", "--name-only", "HEAD").decode().splitlines())
    records = sorted(
        path for path in tracked
        if path.startswith(("artifacts/", "datasets/manifests/"))
        and Path(path).suffix in {".json", ".jsonl"}
    )
    references: dict[tuple[str, str], set[str]] = {}
    for path in records:
        content = _git(ROOT, "show", f"HEAD:{path}").decode("utf-8")
        values = [json.loads(line) for line in content.splitlines() if line.strip()] \
            if path.endswith(".jsonl") else [json.loads(content)]
        for value in values:
            for target, digest, origin in _record_references(value, tracked, path):
                references.setdefault((target, digest), set()).add(origin)
    assert references, "No tracked provenance references were collected"
    candidates = {
        (path, origin): _reference_paths(origin, path)
        for (path, _), origins in references.items()
        for origin in origins
    }
    historical = _historical_hashes(
        ROOT, {path for paths in candidates.values() for path in paths}
    )
    missing = []
    for (path, digest), origins in sorted(references.items()):
        unmatched = {
            origin for origin in origins
            if not any(digest in historical[name] for name in candidates[path, origin])
        }
        if not unmatched:
            continue
        if (path, digest) == LEGACY_SOURCE:
            old = _git(ROOT, "show", "de457a9:datasets/sources.yaml")
            assert b"\r\n" not in old
            assert hashlib.sha256(old.replace(b"\n", b"\r\n")).hexdigest() == digest
        elif (path, digest) not in EXCEPTIONS:
            missing.append(f"{path} {digest} referenced by {', '.join(sorted(unmatched))}")
    assert not missing, "Recorded hashes absent from reachable Git blobs:\n" + "\n".join(missing)
