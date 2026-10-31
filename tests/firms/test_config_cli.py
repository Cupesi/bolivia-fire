from __future__ import annotations

from pathlib import Path

import pytest

from bolivia_fire.cli import main
from bolivia_fire.config import load_key, load_protocol


def test_environment_key_precedes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRMS_MAP_KEY", "synthetic-environment-key")
    # Synthetic environment file; never access the repository secret file in tests.
    (tmp_path / ".env").write_text("FIRMS_MAP_KEY=synthetic-file-key\n")
    assert load_key(tmp_path) == "synthetic-environment-key"


@pytest.mark.parametrize(
    "assignment",
    [
        "FIRMS_MAP_KEY=synthetic-file-key\n",
        "export FIRMS_MAP_KEY='synthetic-file-key' # comment\n",
        'FIRMS_MAP_KEY="synthetic-file-key"\n',
    ],
)
def test_synthetic_env_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    assignment: str,
) -> None:
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    (tmp_path / ".env").write_text(assignment)
    assert load_key(tmp_path) == "synthetic-file-key"


def test_env_is_not_executed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    (tmp_path / ".env").write_text("FIRMS_MAP_KEY=$(touch synthetic-marker)\n")
    with pytest.raises(ValueError, match="Set FIRMS_MAP_KEY"):
        load_key(tmp_path)
    assert not (tmp_path / "synthetic-marker").exists()


def test_protocol_loader() -> None:
    protocol = load_protocol()
    assert protocol["study_area"]["acquisition_bbox"] == [-70, -23, -57, -9]
    assert protocol["target"]["source_product"] == "VIIRS_SNPP_SP"


def test_dry_run_needs_no_key_or_network(tmp_path: Path, capsys) -> None:
    assert (
        main(
            [
                "acquire",
                "firms",
                "--start",
                "2024-09-01",
                "--end",
                "2024-09-10",
                "--dry-run",
            ],
            root=tmp_path,
        )
        == 0
    )
    assert capsys.readouterr().out.splitlines() == [
        "VIIRS_SNPP_SP_2024-09-01_5d.csv start=2024-09-01 day_range=5",
        "VIIRS_SNPP_SP_2024-09-06_5d.csv start=2024-09-06 day_range=5",
    ]
    assert not (tmp_path / "datasets").exists()
