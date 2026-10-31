from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Paths:
    root: Path = REPO_ROOT

    @property
    def datasets(self) -> Path:
        return self.root / "datasets"

    @property
    def temporary(self) -> Path:
        return self.datasets / ".tmp"

    @property
    def quarantine(self) -> Path:
        return self.datasets / ".quarantine"

    @property
    def manifests(self) -> Path:
        return self.datasets / "manifests"

    @property
    def firms_raw(self) -> Path:
        return self.datasets / "raw" / "firms" / "VIIRS_SNPP_SP"

    @property
    def firms_manifest(self) -> Path:
        return self.manifests / "firms_VIIRS_SNPP_SP.jsonl"


def load_protocol(root: Path = REPO_ROOT) -> dict[str, Any]:
    with (root / "configs" / "protocol.yaml").open(encoding="utf-8") as stream:
        result = yaml.safe_load(stream)
    if not isinstance(result, dict):
        raise ValueError("Protocol must be a YAML mapping")
    return result


def load_key(root: Path = REPO_ROOT) -> str:
    value = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not value:
        try:
            with (root / ".env").open(encoding="utf-8-sig") as stream:
                for line in stream:
                    name, separator, raw = line.strip().removeprefix("export ").partition("=")
                    if separator and name.strip() == "FIRMS_MAP_KEY":
                        try:
                            tokens = shlex.split(raw, comments=True, posix=True)
                        except ValueError:
                            raise ValueError("Malformed FIRMS_MAP_KEY assignment") from None
                        value = tokens[0].strip() if len(tokens) == 1 else ""
                        break
        except FileNotFoundError:
            pass
    if not value:
        raise ValueError("Set FIRMS_MAP_KEY in the environment or repository .env")
    if any(character.isspace() for character in value) or any(c in value for c in "/?&#"):
        raise ValueError("FIRMS_MAP_KEY has invalid characters")
    return value
