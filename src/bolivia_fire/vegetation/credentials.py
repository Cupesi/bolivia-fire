from __future__ import annotations

import os
import shlex
from pathlib import Path


def load_credentials(root: Path) -> tuple[str, str]:
    names = ("EARTHDATA_USERNAME", "EARTHDATA_PASSWORD")
    values = {name: os.environ.get(name, "").strip() for name in names}
    if not all(values.values()):
        try:
            with (root / ".env").open(encoding="utf-8-sig") as stream:
                for line in stream:
                    name, separator, raw = line.strip().removeprefix("export ").partition("=")
                    name = name.strip()
                    if separator and name in values and not values[name]:
                        try:
                            tokens = shlex.split(raw, comments=True, posix=True)
                        except ValueError:
                            raise ValueError("Malformed Earthdata credential assignment") from None
                        values[name] = tokens[0] if len(tokens) == 1 else ""
        except FileNotFoundError:
            pass
    if not all(values.values()):
        raise ValueError("Set EARTHDATA_USERNAME and EARTHDATA_PASSWORD")
    return values[names[0]], values[names[1]]
