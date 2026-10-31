from __future__ import annotations

import gc
import time
from typing import Any

import joblib
import numpy as np
from numpy.typing import NDArray
from sklearn.isotonic import IsotonicRegression
from threadpoolctl import threadpool_limits

from bolivia_fire.comparison.data import Split, load_split
from bolivia_fire.comparison.guard import freeze_selection, open_test
from bolivia_fire.comparison.runtime import (
    ALTERNATIVES,
    FAMILIES,
    MODEL_FILES,
    Resources,
    Study,
    average_precision,
    check_hashes,
    environment,
    input_hashes,
    log,
    machine,
    now,
    read_json,
)
from bolivia_fire.config import Paths
from bolivia_fire.evaluation.bootstrap import block_bootstrap
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.models.estimators import FeatureTransform


class Comparison:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths

    @property
    def study(self) -> Study:
        return Study(self.paths)

    def _baseline_name(self, family: str) -> str:
        reference = read_json(self.paths.root / "artifacts/evaluation/selection_strict.json")
        return reference["selected_lightgbm"] if family == "lightgbm" else family

    def _baseline(self, family: str) -> Any:
        name = self._baseline_name(family)
        return joblib.load(self.paths.root / f"artifacts/models/strict/{name}.joblib")

    def prepare(self) -> dict[str, Any]:
        study = self.study
        study.require_unfrozen()
        with Resources(study, "prepare") as monitor:
            hashes = input_hashes(self.paths)
            for family in FAMILIES[:2]:
                path = (
                    self.paths.root
                    / f"artifacts/models/strict/{self._baseline_name(family)}.joblib"
                )
                hashes[path.relative_to(self.paths.root).as_posix()] = sha256_file(path)
            if study.result_path("inputs").exists():
                previous = read_json(study.result_path("inputs"))
                if previous["input_hashes"] != hashes:
                    raise ValueError("Comparison inputs changed; use a separate checkout and cache")
            study.save(
                "inputs",
                {
                    "created_utc": now(),
                    "input_hashes": hashes,
                    "environment": environment(),
                    "hardware": machine(),
                    "status": (
                        "Post-hoc; primary results were already opened before this comparison"
                    ),
                },
            )
            train = load_split(study, "train")
            transform = FeatureTransform(study.features)
            transform.fit(train.x)
            joblib.dump(transform, study.models / "train_transform.joblib")
            summaries = {
                "train": {
                    "rows": len(train.y),
                    "positives": int(train.y.sum()),
                    "prevalence": float(train.y.mean()),
                    "exclusions": train.exclusions,
                }
            }
            del train
            gc.collect()
            recorded = read_json(self.paths.root / "artifacts/evaluation/selection_strict.json")
            verification: dict[str, Any] = {}
            for name in ("val_select", "val_calib"):
                split = load_split(study, name)
                summaries[name] = {
                    "rows": len(split.y),
                    "positives": int(split.y.sum()),
                    "prevalence": float(split.y.mean()),
                    "exclusions": split.exclusions,
                }
                verification[name] = {}
                for family in FAMILIES[:2]:
                    candidate = self._baseline(family)
                    verification[name][family] = {}
                    for calibrated, kind in ((False, "uncalibrated"), (True, "calibrated")):
                        p = candidate.predict(split.x, calibrated=calibrated)
                        study.save_prediction(family, name, p, calibrated)
                        observed = split.score(p)
                        expected = recorded["candidates"][self._baseline_name(family)][name][kind]
                        differences = {
                            metric: observed[metric] - expected[metric]
                            for metric in (
                                "average_precision",
                                "roc_auc",
                                "brier_score",
                                "log_loss",
                                "expected_calibration_error",
                                "recall_at_area_budget_5pct",
                                "recall_at_area_budget_10pct",
                            )
                        }
                        if max(abs(value) for value in differences.values()) >= 1e-8:
                            raise ValueError(
                                f"Recorded baseline predictions differ: {family} {name}"
                            )
                        verification[name][family][kind] = differences
                    log(f"Verified recorded {family} on {name}")
                del split
                gc.collect()
            result = {
                "partitions": summaries,
                "baseline_reproduction": verification,
                "resources": monitor.close(),
                "input_hashes": hashes,
            }
            study.save("prepare", result)
            return result

    def _verify_inputs(self) -> None:
        study = self.study
        manifest = read_json(study.result_path("inputs"))
        check_hashes(self.paths, manifest["input_hashes"])

    def train(self, family: str | None = None) -> dict[str, Any]:
        from bolivia_fire.comparison.trees import train_trees

        study = self.study
        study.require_unfrozen()
        if study.result_path("family_freeze").exists():
            raise ValueError("Family choices are frozen before calibration")
        self._verify_inputs()
        results = {}
        for name in ALTERNATIVES if family is None else (family,):
            if name not in ALTERNATIVES:
                raise ValueError("Only alternative learners may be trained by compare")
            if name == "spatial_cnn":
                from bolivia_fire.comparison.spatial import train_cnn

                results[name] = train_cnn(study)
            else:
                results[name] = train_trees(study, name)
        return results

    def predict(self, family: str, split: Split, *, calibrated: bool = False) -> NDArray:
        study = self.study
        if family in FAMILIES[:2]:
            return self._baseline(family).predict(split.x, calibrated=calibrated)
        if family == "spatial_cnn":
            from bolivia_fire.comparison.spatial import predict_cnn

            p = predict_cnn(study, split)
        else:
            from bolivia_fire.comparison.trees import predict_tree

            p = predict_tree(study, family, split)
        if calibrated:
            calibrator = joblib.load(study.models / f"{family}_calibrator.joblib")
            p = calibrator.predict(p).astype(np.float32)
        return p

    def audit(self) -> dict[str, Any]:
        from bolivia_fire.comparison.audit import cpu_check, determinism_audit

        self._verify_inputs()
        study = self.study
        study.require_unfrozen()
        determinism_audit(study)
        if study.result_path("cpu_check").exists():
            result = read_json(study.result_path("cpu_check"))
            if sha256_file(study.models / "catboost_cpu_check.cbm") != result["model_sha256"]:
                raise ValueError("Existing CPU audit model hash changed")
            return result
        return cpu_check(study)

    def _freeze_families(self) -> dict[str, Any]:
        study = self.study
        hashes = {
            (study.models / filename).relative_to(self.paths.root).as_posix(): sha256_file(
                study.models / filename
            )
            for filename in (*MODEL_FILES.values(), "train_transform.joblib")
        }
        path = study.result_path("family_freeze")
        if path.exists():
            result = read_json(path)
            check_hashes(self.paths, result["model_hashes"])
            return result
        selections = {
            name: read_json(study.result_path(f"{name}_selected")) for name in ALTERNATIVES
        }
        for name, selected in selections.items():
            if (
                selected["completed_configurations"] < 1
                or selected["selection_source"] != "val_select exact AP only"
            ):
                raise ValueError(f"Invalid comparison selection: {name}")
        result = {
            "frozen_utc": now(),
            "model_hashes": hashes,
            "selected_models": selections,
            "selection_rule": study.config["selection"],
            "calibration_rule": study.config["calibration"],
        }
        study.save("family_freeze", result)
        return result

    def _validation(self) -> dict[str, Any]:
        study = self.study
        with Resources(study, "validation_metrics_and_calibration") as monitor:
            selection, calibration = load_split(study, "val_select"), load_split(study, "val_calib")
            recorded = read_json(self.paths.root / "artifacts/evaluation/selection_strict.json")
            results, roundtrip = {}, {}
            for family in FAMILIES:
                if family in ALTERNATIVES:
                    restored = self.predict(family, selection)
                    stored = study.prediction(family, "val_select")
                    delta = float(np.max(np.abs(restored - stored)))
                    ap_delta = average_precision(selection.y, restored) - average_precision(
                        selection.y, stored
                    )
                    if (
                        not np.allclose(restored, stored, rtol=1e-6, atol=1e-7)
                        or abs(ap_delta) >= 1e-6
                    ):
                        raise ValueError(f"Saved model prediction roundtrip differs: {family}")
                    roundtrip[family] = {
                        "max_probability_difference": delta,
                        "ap_difference": ap_delta,
                    }
                    p = self.predict(family, calibration)
                    study.save_prediction(family, "val_calib", p)
                    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1)
                    calibrator.fit(p, calibration.y)
                    joblib.dump(calibrator, study.models / f"{family}_calibrator.joblib")
                    for split in (selection, calibration):
                        raw = study.prediction(family, split.name)
                        study.save_prediction(family, split.name, calibrator.predict(raw), True)
                results[family] = {}
                for split in (selection, calibration):
                    results[family][split.name] = {
                        "rows": len(split.y),
                        "positives": int(split.y.sum()),
                    }
                    for calibrated, key in ((False, "uncalibrated"), (True, "calibrated")):
                        if family in FAMILIES[:2]:
                            # Keep recorded point metrics; prepare audits reproduced predictions.
                            point = recorded["candidates"][self._baseline_name(family)][split.name][
                                key
                            ]
                        else:
                            point = split.score(study.prediction(family, split.name, calibrated))
                        results[family][split.name][key] = point
                study.save("validation", results)
                log(f"Validation complete: {family}")
                monitor.check()
            study.save("roundtrip", roundtrip)
            study.save("validation_resources", monitor.close())
            return results

    def _bootstrap(self) -> dict[str, Any]:
        study = self.study
        with Resources(study, "validation_bootstrap") as monitor, threadpool_limits(limits=1):
            destination = study.result_path("bootstrap")
            results = read_json(destination) if destination.exists() else {}
            for split_name in ("val_calib", "val_select"):
                split = load_split(study, split_name)
                blocks = split.issue.astype("datetime64[M]")
                results.setdefault(split_name, {})
                for family in FAMILIES:
                    results[split_name].setdefault(family, {})
                    for calibrated, key in ((False, "uncalibrated"), (True, "calibrated")):
                        if key in results[split_name][family]:
                            continue
                        monitor.check()
                        p = study.prediction(family, split_name, calibrated)
                        baseline = (
                            None
                            if family == "lightgbm"
                            else study.prediction("lightgbm", split_name, calibrated)
                        )
                        started = time.perf_counter()
                        interval = block_bootstrap(
                            split.y,
                            p,
                            blocks,
                            baseline=baseline,
                            replicates=study.config["uncertainty"]["replicates"],
                            seed=study.config["uncertainty"]["seed"],
                        )
                        interval.update(
                            runtime_seconds=time.perf_counter() - started,
                            paired_rows=len(split.y),
                            reference="recorded lightgbm " + key if baseline is not None else None,
                            note=(
                                "Area omitted from bootstrap, as in the private study; "
                                "weekly area point metrics use all eligible national areas"
                            ),
                        )
                        results[split_name][family][key] = interval
                        study.save("bootstrap", results)
                        log(
                            f"Bootstrap {split_name} {family} {key}: "
                            f"AP CI={interval['metrics']['average_precision']}"
                        )
                del split
                gc.collect()
            study.save("bootstrap_resources", monitor.close())
            return results

    def select(self) -> dict[str, Any]:
        study = self.study
        study.require_unfrozen()
        self._verify_inputs()
        families = self._freeze_families()
        validation = self._validation()
        bootstrap = self._bootstrap()
        differences = {}
        for family in ALTERNATIVES:
            point = validation[family]["val_calib"]["calibrated"]["average_precision"]
            reference = validation["lightgbm"]["val_calib"]["calibrated"]["average_precision"]
            interval = bootstrap["val_calib"][family]["calibrated"]["paired_difference"][
                "average_precision"
            ]
            differences[family] = {"point": point - reference, **interval}
        improved = [
            name
            for name, value in differences.items()
            if value["lower"] is not None and value["lower"] > 0
        ]
        decision = (
            "Retain the published LightGBM model; no alternative has a validation AP gain "
            "with a paired interval excluding zero."
            if not improved
            else "Keep the published model frozen; exploratory validation suggests a gain "
            "requiring evaluation on a new holdout."
        )
        hashes = dict(families["model_hashes"])
        for family in ALTERNATIVES:
            path = study.models / f"{family}_calibrator.joblib"
            hashes[path.relative_to(self.paths.root).as_posix()] = sha256_file(path)
        manifest = read_json(study.result_path("inputs"))
        for name, digest in manifest["input_hashes"].items():
            if name.startswith("artifacts/models/"):
                hashes[name] = digest
        for suffix in ("validation", "bootstrap", "family_freeze", "inputs"):
            path = study.result_path(suffix)
            hashes[path.relative_to(self.paths.root).as_posix()] = sha256_file(path)
        for suffix in ("cpu_check", "reference", "provenance", "determinism_audit"):
            path = study.result_path(suffix)
            if path.exists():
                hashes[path.relative_to(self.paths.root).as_posix()] = sha256_file(path)
        source_files = [
            *sorted(
                path
                for path in (self.paths.root / "src/bolivia_fire/comparison").glob("*.py")
                if path.name != "report.py"
            ),
            self.paths.root / "src/bolivia_fire/comparison_cli.py",
            self.paths.root / "src/bolivia_fire/models/data.py",
            self.paths.root / "src/bolivia_fire/models/estimators.py",
            self.paths.root / "src/bolivia_fire/evaluation/metrics.py",
            self.paths.root / "src/bolivia_fire/evaluation/bootstrap.py",
        ]
        source_hashes = {
            path.relative_to(self.paths.root).as_posix(): sha256_file(path) for path in source_files
        }
        primary_log = self.paths.root / "artifacts/evaluation/final_test_log.json"
        primary_entries = read_json(primary_log) if primary_log.exists() else []
        payload = {
            "status": "POST-HOC EXPLORATORY",
            "decision": decision,
            "future_evaluation": (
                "CatBoost is a candidate for a future pre-registered comparison "
                "on a new untouched holdout."
            ),
            "decision_basis": (
                "val_select chooses configurations; val_calib supplies calibration "
                "and paired validation diagnostics only"
            ),
            "paired_calibrated_ap_difference": differences,
            "selected_models": families["selected_models"],
            "artifact_hashes": hashes,
            "input_hashes": manifest["input_hashes"],
            "source_hashes": source_hashes,
            "primary_opening_utc": (
                primary_entries[0]["timestamp_utc"] if primary_entries else None
            ),
            "validation_artifact": "artifacts/evaluation/comparison_validation.json",
            "bootstrap_artifact": "artifacts/evaluation/comparison_bootstrap.json",
            "limitations": [
                "Calibration-year metrics reuse isotonic fitting labels",
                "Bootstrap fixes fitted learners and calibrators; "
                "it excludes search and initialization uncertainty",
                "The primary test had already been opened before the private study "
                "and this repository rerun",
            ],
        }
        result = freeze_selection(self.paths, payload)
        log(decision)
        return result

    def evaluate(
        self,
        *,
        post_hoc_test: bool = False,
        reason: str | None = None,
        reopen_reason: str | None = None,
    ) -> dict[str, Any]:
        opening = open_test(
            self.paths, post_hoc_test=post_hoc_test, reason=reason, reopen_reason=reopen_reason
        )
        return self._score_test(opening)

    def _score_test(self, opening: dict[str, Any]) -> dict[str, Any]:
        study = self.study
        with Resources(study, "post_hoc_temporal_test") as monitor:
            split = load_split(study, "test", opening=opening)
            results = {
                "status": "POST-HOC EXPLORATORY; NOT A NEW HOLDOUT; NEVER USED FOR CHOICES",
                "opening": opening,
                "models": {},
                "rows": len(split.y),
                "positives": int(split.y.sum()),
                "exclusions": split.exclusions,
            }
            years = split.issue.astype("datetime64[Y]").astype(int) + 1970
            for family in FAMILIES:
                raw = self.predict(family, split)
                if family in ALTERNATIVES:
                    calibrator = joblib.load(study.models / f"{family}_calibrator.joblib")
                    calibrated = calibrator.predict(raw).astype(np.float32)
                else:
                    calibrated = self.predict(family, split, calibrated=True)
                study.save_prediction(family, "test", raw)
                study.save_prediction(family, "test", calibrated, True)
                annual = {}
                for year in np.unique(years):
                    mask = years == year
                    annual[str(year)] = {
                        "rows": int(mask.sum()),
                        "prevalence": float(split.y[mask].mean()),
                        "uncalibrated_ap": average_precision(split.y[mask], raw[mask]),
                        "calibrated": Split(
                            "test",
                            split.x[mask],
                            split.y[mask],
                            split.metadata.filter(mask),
                            {},
                        ).score(calibrated[mask]),
                    }
                results["models"][family] = {
                    "uncalibrated": split.score(raw),
                    "calibrated": split.score(calibrated),
                    "annual": annual,
                }
                study.save("test", results)
                log(
                    f"Exploratory test {family}: calibrated "
                    f"AP={results['models'][family]['calibrated']['average_precision']:.8f}"
                )
                monitor.check()
            check_hashes(self.paths, opening["dataset_snapshot_hashes"])
            from bolivia_fire.comparison.guard import verify_selection

            verify_selection(self.paths)
            results["resources"] = monitor.close()
            study.save("test", results)
            return results
