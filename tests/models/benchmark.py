from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from test_workflow import command, synthetic_panel

from bolivia_fire.evaluation.artifacts import atomic_json
from bolivia_fire.pipeline_cli import peak_ram_mb


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=2_000_000)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.rows < 1200 or args.rows % 4:
        raise ValueError("Synthetic row count must be at least 1200 and divisible by four")
    tick = time.perf_counter()
    phases = {}
    with tempfile.TemporaryDirectory(prefix="bolivia_fire_synthetic_") as directory:
        root = Path(directory)
        phase = time.perf_counter()
        synthetic_panel(root, rows_per_split=args.rows // 4)
        phases["fixture_seconds"] = time.perf_counter() - phase
        for name, options in (
            ("train", []),
            ("select", []),
            ("evaluate", ["--open-final-test"]),
            ("predict", ["--split", "test"]),
        ):
            phase = time.perf_counter()
            command(root, name, *options)
            phases[f"{name}_seconds"] = time.perf_counter() - phase
            print(
                json.dumps(
                    {
                        "phase": name,
                        "seconds": phases[f"{name}_seconds"],
                        "peak_ram_mb": peak_ram_mb(),
                    }
                ),
                flush=True,
            )
        training = json.loads((root / "artifacts/models/strict/run.json").read_text())
        geographic_training = json.loads(
            (root / "artifacts/models/strict_geo/run.json").read_text()
        )
        evaluation = json.loads((root / "artifacts/evaluation/test_strict.json").read_text())
        prediction = json.loads((root / "artifacts/predictions/strict/test.json").read_text())
        result = {
            "synthetic": True,
            "input_rows": args.rows,
            "phases": phases,
            "runtime_seconds": time.perf_counter() - tick,
            "peak_ram_mb": peak_ram_mb(),
            "training_exclusions": training["exclusions"],
            "test_exclusions": evaluation["exclusions"],
            "candidate_count": len(training["candidates"]),
            "geographic_candidate_count": len(geographic_training["candidates"]),
            "bootstrap_replicates": evaluation["bootstrap"]["temporal"]["replicates"],
            "prediction_rows": prediction["rows"],
            "environment": training["environment"],
            "hardware": training["hardware"],
            **{
                name: training[name]
                for name in (
                    "timestamp_utc",
                    "git_commit",
                    "dataset_snapshot_hashes",
                    "protocol_sha256",
                    "experiments_sha256",
                    "config",
                    "seed",
                )
            },
        }
    atomic_json(args.report, result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
