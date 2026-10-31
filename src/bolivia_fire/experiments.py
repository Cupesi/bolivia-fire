from __future__ import annotations

import json
import time
import uuid
from typing import Any

import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from numpy.typing import NDArray

from bolivia_fire.config import Paths
from bolivia_fire.evaluation.artifacts import (
    atomic_json,
    provenance,
    record_final_opening,
    save_candidate,
    snapshot,
)
from bolivia_fire.evaluation.bootstrap import block_bootstrap
from bolivia_fire.evaluation.decisions import hypothesis_decision
from bolivia_fire.evaluation.metrics import metrics
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.models.baselines import Climatology, Persistence
from bolivia_fire.models.data import PanelData, PanelStore, experiment_features
from bolivia_fire.models.estimators import (
    Candidate,
    fit_lightgbm,
    fit_logistic,
    lightgbm_datasets,
    search_parameters,
)
from bolivia_fire.models.retrospective import RetrospectiveUnavailable


def run_name(
    variant: str,
    selection: str,
    drop_group: str | None,
    exclude_low_coverage: bool = False,
) -> str:
    return (
        variant
        + ("_all_confidence" if selection == "all_confidence" else "")
        + (f"_drop_{drop_group}" if drop_group else "")
        + ("_exclude_low_coverage" if exclude_low_coverage else "")
    )


def scored(data: PanelData, probability: NDArray, mask: NDArray | None = None) -> dict[str, Any]:
    valid = np.isfinite(probability) & np.isfinite(data.y)
    if mask is not None:
        valid &= mask
    result = metrics(
        data.y[valid],
        probability[valid],
        area=data.column("national_area_km2")[valid],
        issue=data.column("issue_date")[valid],
    )
    result["rows"] = int(valid.sum())
    result["missing_probability"] = int((~np.isfinite(probability)).sum())
    return result


class Experiment:
    def __init__(
        self,
        paths: Paths,
        *,
        variant: str = "strict",
        selection: str = "primary",
        drop_group: str | None = None,
        exclude_low_coverage: bool = False,
    ) -> None:
        self.paths, self.variant, self.selection, self.drop_group = (
            paths,
            variant,
            selection,
            drop_group,
        )
        self.config = yaml.safe_load((paths.root / "configs/experiments.yaml").read_text())
        self.exclude_low_coverage = exclude_low_coverage
        if self.config["seed"] != 7 or self.config["bootstrap_replicates"] != 500:
            raise ValueError("Frozen experiment requires seed 7 and 500 bootstrap replicates")
        if self.config["lightgbm_search"]["configurations"] != 20:
            raise ValueError("Frozen experiment requires 20 LightGBM configurations")
        self.features = experiment_features(self.config, variant, drop_group)
        self.target = "y_allconf" if selection == "all_confidence" else "y_primary"
        self.name = run_name(variant, selection, drop_group, exclude_low_coverage)
        self.climatology_column = self.config["columns"][
            "climatology_department" if variant.endswith("_geo") else "climatology_cell"
        ]
        self.directory = paths.root / "artifacts/models" / self.name
        self.selection_path = paths.root / "artifacts/evaluation" / f"selection_{self.name}.json"

    def _metadata(self, tick: float, *, include_test: bool = False) -> dict[str, Any]:
        return {
            **provenance(self.paths, self.config, tick, include_test=include_test),
            "variant": self.variant,
            "selection": self.selection,
            "drop_group": self.drop_group,
            "features": self.features,
            "target": self.target,
            "exclude_low_coverage": self.exclude_low_coverage,
        }

    def _trained(self) -> dict[str, Any]:
        path = self.directory / "run.json"
        if not path.exists():
            raise ValueError("Train this experiment before selection or prediction")
        result = json.loads(path.read_text())
        if result["dataset_snapshot_hashes"] != snapshot(self.paths):
            raise ValueError("Training dataset or configuration changed; train and select again")
        return result

    def _selected(self) -> tuple[dict[str, Any], dict[str, Any]]:
        trained = self._trained()
        if not self.selection_path.exists():
            raise ValueError("Close selection before evaluation or prediction")
        selected = json.loads(self.selection_path.read_text())
        if selected["run_token"] != trained["run_token"]:
            raise ValueError("Training run changed; select again")
        for name, digest in selected["model_ids"].items():
            if sha256_file(self.directory / f"{name}.joblib") != digest:
                raise ValueError("Selected model artifact changed; select again")
        for name, digest in selected["calibrator_ids"].items():
            if sha256_file(self.directory / f"{name}.calibrator.joblib") != digest:
                raise ValueError("Selected calibrator artifact changed; select again")
        return trained, selected

    def _load(self, store: PanelStore, split: str, **options: Any) -> PanelData:
        return store.load(
            [split],
            self.features,
            self.target,
            exclude_low_coverage=self.exclude_low_coverage,
            retrospective=self._retrospective_features(),
            **options,
        )

    def _retrospective_features(self) -> list[str]:
        if not self.variant.startswith("retrospective"):
            return []
        strict = experiment_features(
            self.config, self.variant.replace("retrospective", "strict"), self.drop_group,
        )
        additional = [name for name in self.features if name not in strict]
        if not additional:
            raise RetrospectiveUnavailable(
                "retrospective_features_not_additional", "no additional features over strict",
            )
        return additional

    def geographic(self) -> Experiment:
        variant = self.variant if self.variant.endswith("_geo") else self.variant + "_geo"
        return Experiment(
            self.paths,
            variant=variant,
            selection=self.selection,
            drop_group=self.drop_group,
            exclude_low_coverage=self.exclude_low_coverage,
        )

    def train(self) -> dict[str, Any]:
        self._retrospective_features()
        tick = time.perf_counter()
        self.directory.mkdir(parents=True, exist_ok=True)
        initial_snapshot = snapshot(self.paths)
        with PanelStore(self.paths) as store:
            train = self._load(store, "train")
            validation = self._load(store, "val_select")
            if len(np.unique(train.y)) < 2 or len(np.unique(validation.y)) < 2:
                raise ValueError("Training and selection partitions each require both classes")
            persistence = Persistence().fit(train.column("count_last_7d"), train.y)
            candidates = [
                Candidate("train_climatology", Climatology(0), [self.climatology_column]),
                Candidate("persistence", persistence, ["count_last_7d"]),
                fit_logistic(train.x, train.y, self.features, seed=self.config["seed"]),
            ]
            records: dict[str, Any] = {}

            def save(candidate: Candidate) -> None:
                save_candidate(self.directory / f"{candidate.name}.joblib", candidate)
                records[candidate.name] = {
                    "parameters": candidate.parameters,
                    "features": candidate.features,
                }

            for candidate in candidates:
                save(candidate)
            del candidates
            datasets = lightgbm_datasets(
                train.x, train.y, validation.x, validation.y, self.features
            )
            for index, parameters in enumerate(
                search_parameters(
                    seed=self.config["seed"],
                    space=self.config["lightgbm_search"]["space"],
                )
            ):
                candidate = fit_lightgbm(
                    *datasets,
                    self.features,
                    parameters,
                    seed=self.config["seed"],
                    index=index,
                    settings=self.config["lightgbm_search"],
                )
                save(candidate)
            del datasets, candidate
            report = {
                "run_token": uuid.uuid4().hex,
                "candidates": records,
                "exclusions": {"train": train.exclusions, "val_select": validation.exclusions},
            }
        if snapshot(self.paths) != initial_snapshot:
            raise ValueError("Panel changed during training; discard this run and retry")
        report.update(self._metadata(tick))
        atomic_json(self.directory / "run.json", report)
        return report

    def select(self) -> dict[str, Any]:
        self._retrospective_features()
        tick = time.perf_counter()
        records: dict[str, Any] = {}
        with PanelStore(self.paths) as store:
            selection = self._load(store, "val_select")
            trained = self._trained()
            for name in trained["candidates"]:
                candidate: Candidate = joblib.load(self.directory / f"{name}.joblib")
                records[name] = {
                    "val_select": {
                        "uncalibrated": scored(
                            selection, candidate.predict(selection.inputs(candidate.features))
                        ),
                        "calibrated": None,
                    },
                    "calibration_status": "not_selected",
                }

            def rank(name: str) -> tuple[float, str]:
                ap = records[name]["val_select"]["uncalibrated"]["average_precision"]
                return (ap if ap is not None else -1, name)

            best_lightgbm = max(
                (name for name in records if name.startswith("lightgbm_")), key=rank
            )
            best_baseline = max(("train_climatology", "persistence"), key=rank)
            chosen = max(("logistic_regression", best_lightgbm), key=rank)
            if rank(chosen)[0] < 0 or rank(best_baseline)[0] < 0:
                raise ValueError("Selection average precision is undefined")
            final = ["train_climatology", "persistence", "logistic_regression", best_lightgbm]
            calibration = self._load(store, "val_calib")
            if not len(calibration.y):
                raise ValueError("No eligible calibration rows")
            for name in final:
                candidate = joblib.load(self.directory / f"{name}.joblib")
                candidate.calibrate(calibration.inputs(candidate.features), calibration.y)
                values = records[name]
                for split, data in (("val_select", selection), ("val_calib", calibration)):
                    x = data.inputs(candidate.features)
                    values[split] = {
                        "uncalibrated": scored(data, candidate.predict(x)),
                        "calibrated": scored(data, candidate.predict(x, calibrated=True)),
                    }
                values["calibration_status"] = candidate.calibration_status
                records[name] = values
                save_candidate(self.directory / f"{name}.joblib", candidate)
                save_candidate(self.directory / f"{name}.calibrator.joblib", candidate.calibrator)
            report = {
                "exclusions": {
                    **trained["exclusions"],
                    "val_select": selection.exclusions,
                    "val_calib": calibration.exclusions,
                }
            }

        report.update(
            {
                "candidates": records,
                "selected_lightgbm": best_lightgbm,
                "selected_model": chosen,
                "best_baseline": best_baseline,
                "final_candidates": final,
                "run_token": trained["run_token"],
                "selection_rule": "uncalibrated val_select average_precision",
                "model_ids": {
                    name: sha256_file(self.directory / f"{name}.joblib") for name in final
                },
                "calibrator_ids": {
                    name: sha256_file(self.directory / f"{name}.calibrator.joblib")
                    for name in final
                },
                **self._metadata(tick),
            }
        )
        if report["dataset_snapshot_hashes"] != trained["dataset_snapshot_hashes"]:
            raise ValueError("Panel changed during selection; repeat training and selection")
        atomic_json(self.selection_path, report)
        return report

    def _bootstrap(
        self,
        data: PanelData,
        probability: NDArray,
        reference: NDArray,
        *,
        geographic: bool,
    ) -> dict[str, Any]:
        valid = np.isfinite(probability) & np.isfinite(reference) & np.isfinite(data.y)
        blocks = (
            data.column("block_id")
            if geographic
            else (data.column("issue_date").astype("datetime64[M]").astype(str))
        )
        result = block_bootstrap(
            data.y[valid],
            probability[valid],
            blocks[valid],
            baseline=reference[valid],
            area=data.column("national_area_km2")[valid],
            issue=data.column("issue_date")[valid],
            replicates=self.config["bootstrap_replicates"],
            seed=self.config["seed"],
        )
        result["paired_rows"] = int(valid.sum())
        return result

    def evaluate(
        self,
        *,
        split: str = "test",
        open_final_test: bool = False,
        reopen_reason: str | None = None,
    ) -> dict[str, Any]:
        tick = time.perf_counter()
        if self.variant.endswith("_geo"):
            raise ValueError(
                "Evaluate the base variant to report both temporal and geographic populations"
            )
        if split == "test" and not open_final_test:
            raise ValueError("Test metrics require evaluate --open-final-test")
        _, selected = self._selected()
        geographic = self.geographic()
        _, geo_selected = geographic._selected()
        other = Experiment(
            self.paths,
            variant="strict" if self.variant.startswith("retrospective") else "retrospective",
            selection=self.selection,
            drop_group=self.drop_group,
            exclude_low_coverage=self.exclude_low_coverage,
        )
        other_selected = None
        retrospective = self if self.variant.startswith("retrospective") else other
        h2_reason = None
        try:
            retrospective._retrospective_features()
        except RetrospectiveUnavailable as exc:
            h2_reason = exc.reason
        if other.selection_path.exists() and (
            h2_reason is None or self.variant.startswith("retrospective")
        ):
            _, other_selected = other._selected()
        elif self.variant.startswith("retrospective"):
            raise ValueError(
                "Select the matching strict experiment before retrospective evaluation"
            )
        identities = {
            f"{self.variant}/{name}": value for name, value in selected["model_ids"].items()
        }
        calibrators = {
            f"{self.variant}/{name}": value for name, value in selected["calibrator_ids"].items()
        }
        for experiment, selection in ((geographic, geo_selected), (other, other_selected)):
            if selection is not None:
                identities.update(
                    {
                        f"{experiment.variant}/{name}": value
                        for name, value in selection["model_ids"].items()
                    }
                )
                calibrators.update(
                    {
                        f"{experiment.variant}/{name}": value
                        for name, value in selection["calibrator_ids"].items()
                    }
                )
        evaluation_snapshot = snapshot(self.paths, include_test=split == "test")
        opening = None
        if split == "test":
            opening = record_final_opening(
                self.paths, identities, calibrators, self.config, reopen_reason
            )

        def publish(report: dict[str, Any]) -> dict[str, Any]:
            report.update(
                {
                    "model_ids": identities,
                    "calibrator_ids": calibrators,
                    "final_test_opening": opening,
                    **self._metadata(tick, include_test=split == "test"),
                }
            )
            if report["dataset_snapshot_hashes"] != evaluation_snapshot:
                raise ValueError(
                    "Panel changed during evaluation; this opening is logged but not published"
                )
            atomic_json(
                self.paths.root / "artifacts/evaluation" / f"{split}_{self.name}.json", report,
            )
            return report

        models = {
            name: joblib.load(self.directory / f"{name}.joblib")
            for name in selected["final_candidates"]
        }
        geo_models = {
            name: joblib.load(geographic.directory / f"{name}.joblib")
            for name in geo_selected["final_candidates"]
        }
        with PanelStore(self.paths) as store:
            try:
                data = self._load(store, split)
                held = geographic._load(store, split, holdout=True)
            except RetrospectiveUnavailable as exc:
                return publish({
                    "split": split,
                    "status": "unavailable",
                    "reason": exc.reason,
                    "models": {},
                    "hypotheses": {
                        name: {"supported": None, "reason": exc.reason}
                        for name in ("H1", "H2")
                    },
                })
            probabilities = {
                name: {
                    kind: model.predict(data.inputs(model.features), calibrated=calibrated)
                    for kind, calibrated in (("uncalibrated", False), ("calibrated", True))
                }
                for name, model in models.items()
            }
            held_probabilities = {
                name: {
                    kind: model.predict(held.inputs(model.features), calibrated=calibrated)
                    for kind, calibrated in (("uncalibrated", False), ("calibrated", True))
                }
                for name, model in geo_models.items()
            }

            def tables(
                panel: PanelData,
                values: dict[str, dict[str, NDArray]],
                mask: NDArray | None = None,
            ) -> dict[str, Any]:
                return {
                    name: {kind: scored(panel, p, mask) for kind, p in variants.items()}
                    for name, variants in values.items()
                }

            years = pd.DatetimeIndex(data.column("issue_date")).year
            chosen, baseline = selected["selected_model"], selected["best_baseline"]
            geo_chosen, geo_baseline = geo_selected["selected_model"], geo_selected["best_baseline"]
            intervals = {
                "temporal": {
                    name: {
                        kind: self._bootstrap(
                            data, p, probabilities[baseline]["uncalibrated"], geographic=False
                        )
                        for kind, p in versions.items()
                    }
                    for name, versions in probabilities.items()
                },
                "geographic": {
                    name: {
                        kind: self._bootstrap(
                            held,
                            p,
                            held_probabilities[geo_baseline]["uncalibrated"],
                            geographic=True,
                        )
                        for kind, p in versions.items()
                    }
                    for name, versions in held_probabilities.items()
                },
            }
            report = {
                "split": split,
                "selected_model": chosen,
                "best_baseline": baseline,
                "models": tables(data, probabilities),
                "per_year": {
                    str(year): tables(data, probabilities, years == year)
                    for year in sorted(set(years))
                },
                "santa_cruz": tables(data, probabilities, data.column("santa_cruz").astype(bool)),
                "geographic_holdout": {
                    "rows": len(held.y),
                    "feature_set": geographic.variant,
                    "features": geographic.features,
                    "selected_model": geo_chosen,
                    "best_baseline": geo_baseline,
                    "models": tables(held, held_probabilities),
                },
                "exclusions": {split: data.exclusions, "geographic_holdout": held.exclusions},
                "bootstrap": {
                    "temporal": intervals["temporal"][chosen]["calibrated"],
                    "geographic": intervals["geographic"][geo_chosen]["calibrated"],
                },
                "candidate_bootstrap": intervals,
                "baseline_comparison": "uncalibrated best baseline selected by val_select AP",
                "calibrated_baseline_sensitivity": self._bootstrap(
                    data,
                    probabilities[chosen]["calibrated"],
                    probabilities[baseline]["calibrated"],
                    geographic=False,
                ),
            }
            strict_interval = report["bootstrap"]["temporal"]["paired_difference"][
                "average_precision"
            ]
            h2: dict[str, Any] = {
                "supported": None, "reason": h2_reason or "retrospective_selection_missing",
            }
            other_data = None
            if other_selected is not None and h2_reason is None:
                try:
                    other_data = other._load(store, split)
                except RetrospectiveUnavailable as exc:
                    h2 = {"supported": None, "reason": exc.reason}
            if other_data is not None:
                if not (
                    np.array_equal(data.column("cell_id"), other_data.column("cell_id"))
                    and np.array_equal(data.column("issue_date"), other_data.column("issue_date"))
                ):
                    raise ValueError("Strict and retrospective evaluation populations do not align")
                comparison_model: Candidate = joblib.load(
                    other.directory / f"{other_selected['selected_model']}.joblib"
                )
                other_p = comparison_model.predict(
                    other_data.inputs(comparison_model.features), calibrated=True
                )
                current_p = probabilities[chosen]["calibrated"]
                retro_p, strict_p = (
                    (current_p, other_p)
                    if self.variant.startswith("retrospective")
                    else (other_p, current_p)
                )
                paired = self._bootstrap(data, retro_p, strict_p, geographic=False)
                h2 = hypothesis_decision(
                    paired["paired_difference"]["average_precision"], contains_zero=True
                )
                report["strict_retrospective_bootstrap"] = paired
                if self.variant.startswith("retrospective"):
                    strict_baseline: Candidate = joblib.load(
                        other.directory / f"{other_selected['best_baseline']}.joblib"
                    )
                    baseline_p = strict_baseline.predict(
                        other_data.inputs(strict_baseline.features), calibrated=False
                    )
                    strict_interval = self._bootstrap(data, strict_p, baseline_p, geographic=False)[
                        "paired_difference"
                    ]["average_precision"]
            report["hypotheses"] = (
                {
                    "H1": hypothesis_decision(strict_interval),
                    "H2": h2,
                }
                if split == "test"
                else {
                    name: {"supported": None, "reason": "not_final_test"} for name in ("H1", "H2")
                }
            )
        return publish(report)

    def predict(self, *, split: str = "test") -> dict[str, Any]:
        tick = time.perf_counter()
        _, selected = self._selected()
        geographic = self.geographic()
        _, geo_selected = geographic._selected()
        output = self.paths.root / "artifacts/predictions" / self.name / f"{split}.parquet"
        output.parent.mkdir(parents=True, exist_ok=True)
        pending = output.with_suffix(".parquet.tmp")
        rows = 0
        with PanelStore(self.paths) as store:
            schema = pa.schema(
                [
                    ("cell_id", pa.string()),
                    ("issue_date", pa.timestamp("ns")),
                    ("model", pa.string()),
                    ("p", pa.float32()),
                    ("y", pa.float32()),
                ]
            )
            try:
                with pq.ParquetWriter(pending, schema, compression="zstd") as writer:
                    for is_held, experiment, selection in (
                        (False, self, selected),
                        (True, geographic, geo_selected),
                    ):
                        data = experiment._load(store, split, holdout=is_held, require_labels=False)
                        for name in selection["final_candidates"]:
                            model: Candidate = joblib.load(experiment.directory / f"{name}.joblib")
                            p = model.predict(data.inputs(model.features), calibrated=True)
                            for start in range(0, len(p), 100_000):
                                stop = min(len(p), start + 100_000)
                                batch = pa.table(
                                    {
                                        "cell_id": data.metadata["cell_id"].slice(
                                            start, stop - start
                                        ),
                                        "issue_date": data.metadata["issue_date"].slice(
                                            start, stop - start
                                        ),
                                        "model": [f"{experiment.variant}/{name}"] * (stop - start),
                                        "p": pa.array(p[start:stop], from_pandas=True),
                                        "y": pa.array(data.y[start:stop], from_pandas=True),
                                    },
                                    schema=schema,
                                )
                                writer.write_table(batch)
                                rows += len(batch)
                pending.replace(output)
            finally:
                pending.unlink(missing_ok=True)
        report = {
            "rows": rows,
            "split": split,
            "path": output.relative_to(self.paths.root).as_posix(),
            "prediction_sha256": sha256_file(output),
            "model_ids": {
                **{
                    f"{self.variant}/{name}": digest
                    for name, digest in selected["model_ids"].items()
                },
                **{
                    f"{geographic.variant}/{name}": digest
                    for name, digest in geo_selected["model_ids"].items()
                },
            },
            **self._metadata(tick, include_test=split == "test"),
        }
        atomic_json(output.with_suffix(".json"), report)
        return report
