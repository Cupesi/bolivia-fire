from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.mark.parametrize("partial", [False, True])
def test_synthetic_optional_comparison_cells_skip_missing_artifacts(tmp_path, partial) -> None:
    if partial:
        # An incomplete synthetic group must not be read, even if a file is malformed.
        (tmp_path / "comparison_validation.json").write_text("incomplete", encoding="utf-8")
    notebook = json.loads(
        (Path(__file__).parents[1] / "notebooks/04_evaluation.ipynb").read_text(encoding="utf-8")
    )
    messages: list[str] = []
    namespace: dict[str, Any] = {
        "directory": tmp_path,
        "json": json,
        "display": lambda value: messages.append(str(value)),
        "Markdown": str,
    }
    for index in (19, 21, 23):
        exec("".join(notebook["cells"][index]["source"]), namespace)
    assert any("comparison section skipped" in message.lower() for message in messages)
    assert "comparison_validation" not in namespace
    assert "comparison_audit" not in namespace
    assert "comparison_test" not in namespace
