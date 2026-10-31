from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from bolivia_fire.redact import Redactor


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def durable_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def read_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError
        except ValueError:
            if index == len(lines) - 1 and not line.endswith("\n"):
                break
            raise RuntimeError(f"Corrupt manifest record {index + 1}: {path.name}") from None
        records.append(record)
    return records


def append_manifest(path: Path, record: dict[str, Any], redactor: Redactor) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(redactor.sanitize(record), ensure_ascii=True, allow_nan=False) + "\n"
    # Preserve a torn final line while keeping future records independently parseable.
    if path.exists():
        body = path.read_bytes()
        if body and not body.endswith(b"\n"):
            try:
                json.loads(body.splitlines()[-1])
            except ValueError:
                recovery = path.with_name(path.name + ".recovered.part")
                good = b"\n".join(body.splitlines()[:-1])
                durable_write(recovery, good + b"\n" if good else b"")
                archive = path.with_name(
                    path.name + ".torn-" + hashlib.sha256(body).hexdigest()[:16]
                )
                if not archive.exists():
                    durable_write(archive, body)
                os.replace(recovery, path)
            else:
                with path.open("ab") as stream:
                    stream.write(b"\n")
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
