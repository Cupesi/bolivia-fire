from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bolivia_fire.config import REPO_ROOT, Paths
from bolivia_fire.firms.storage import sha256_file


@pytest.fixture
def synthetic_registration(tmp_path, monkeypatch):
    from bolivia_fire.extension import registration

    for name in (
        "configs/extension_vegetation.yaml",
        "configs/protocol.yaml",
        "configs/experiments.yaml",
        "docs/extension-vegetation.md",
        "artifacts/evaluation/extension_registration.json",
    ):
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / name, destination)
    blobs = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    def synthetic_git(root: Path, *args: str) -> bytes:
        if args[0] == "rev-parse":
            return b"a" * 40
        if args[0] == "ls-files":
            if args[-1] not in blobs:
                raise ValueError("Registration file is uncommitted")
            return args[-1].encode()
        if args[0] == "show":
            return blobs[args[-1].split(":", 1)[1]]
        raise AssertionError(args)

    monkeypatch.setattr(registration, "git_bytes", synthetic_git)
    return Paths(tmp_path), blobs


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def refresh_synthetic_registration(paths, blobs):
    path = paths.root / "artifacts/evaluation/extension_registration.json"
    entry = json.loads(path.read_text("utf-8"))
    for field, name in (
        ("contract", "configs/extension_vegetation.yaml"),
        ("document", "docs/extension-vegetation.md"),
        ("protocol", "configs/protocol.yaml"),
        ("experiments", "configs/experiments.yaml"),
    ):
        entry[f"{field}_sha256"] = sha256_file(paths.root / name)
    write_json(path, entry)
    for name in blobs:
        blobs[name] = (paths.root / name).read_bytes()
