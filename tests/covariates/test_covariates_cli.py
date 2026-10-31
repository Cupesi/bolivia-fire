import json
from argparse import ArgumentParser
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from bolivia_fire.cli import main
from bolivia_fire.config import REPO_ROOT, Paths
from bolivia_fire.covariates import join_retrospective
from bolivia_fire.covariates.chirps import extract_year
from bolivia_fire.covariates_cli import register


def test_synthetic_feature_build_and_parquet_join(tmp_path, capsys):
    root = tmp_path / "datasets" / "processed" / "covariates"
    config = tmp_path / "configs"
    config.mkdir()
    (config / "protocol.yaml").write_bytes((REPO_ROOT / "configs" / "protocol.yaml").read_bytes())
    cells = pd.DataFrame({"cell_id": ["a", "b"], "longitude": [-69.975, -69.925],
                          "latitude": [-9.025, -9.075]})
    cells_path = tmp_path / "datasets" / "processed" / "cells.parquet"
    cells_path.parent.mkdir(parents=True)
    cells.to_parquet(cells_path, index=False)

    def synthetic_reader(day, budget):
        return np.ones((2, 3), dtype=np.float32), {
            "url": "synthetic", "http_size": 1000,
            "transform": [0.05, 0, -70, 0, -0.05, -9],
        }

    extract_year(2024, root, reader=synthetic_reader, end=date(2024, 4, 7))
    parser = ArgumentParser()
    register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["covariates", "features", "--start-issue", "2024-04-08",
                              "--end-issue", "2024-04-08"])
    assert args.pipeline_handler(args, Paths(tmp_path)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["feature_rows"] == {"2024": 2}
    output = root / "chirps_features" / "year=2024" / "part.parquet"
    assert pq.ParquetFile(output).read().column("rain_sum_90d").to_pylist() == [90, 90]
    synthetic_panel = pa.table({"cell_id": ["b", "a"],
                                "issue_date": [date(2024, 4, 8), date(2024, 4, 8)]})
    joined = join_retrospective(synthetic_panel, root=root)
    assert joined.column("cell_id").to_pylist() == ["b", "a"]
    assert joined.column("rain_sum_7d").to_pylist() == [7, 7]
    assert joined.column("rain_complete_90d").to_pylist() == [True, True]
    assert joined.column("rain_anomaly_90d").null_count == 2


def test_synthetic_covariate_commands_require_no_firms_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    parser = ArgumentParser()
    register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["covariates", "summary"])
    assert args.pipeline_handler(args, Paths(Path(tmp_path))) == 0
    assert json.loads(capsys.readouterr().out)["bytes_transferred_total"] == 0


def test_synthetic_root_cli_registration(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    assert main(["covariates", "summary"], root=tmp_path) == 0
    assert json.loads(capsys.readouterr().out)["bytes_transferred_total"] == 0
