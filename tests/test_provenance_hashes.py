from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
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
                if key == "registration_sha256" and "amendment_id" in value:
                    candidates = [AMENDMENT_REGISTRATION]
                for path in candidates:
                    if isinstance(path, str) and path in tracked:
                        yield path, item.lower()
            yield from _references(item, tracked)


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
            for pair in _references(value, tracked):
                references.setdefault(pair, set()).add(path)
    assert references, "No tracked provenance references were collected"
    historical = _historical_hashes(ROOT, {path for path, _ in references})
    missing = []
    for (path, digest), origins in sorted(references.items()):
        if digest in historical[path]:
            continue
        if (path, digest) == LEGACY_SOURCE:
            old = _git(ROOT, "show", "de457a9:datasets/sources.yaml")
            assert b"\r\n" not in old
            assert hashlib.sha256(old.replace(b"\n", b"\r\n")).hexdigest() == digest
        elif (path, digest) not in EXCEPTIONS:
            missing.append(f"{path} {digest} referenced by {', '.join(sorted(origins))}")
    assert not missing, "Recorded hashes absent from reachable Git blobs:\n" + "\n".join(missing)
