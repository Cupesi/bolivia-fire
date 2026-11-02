from __future__ import annotations

import gc
import os
import time
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from filelock import FileLock

from bolivia_fire.comparison.runtime import average_precision, environment
from bolivia_fire.config import Paths
from bolivia_fire.evaluation.artifacts import atomic_json, hardware, save_candidate
from bolivia_fire.evaluation.metrics import metrics
from bolivia_fire.extension.access import (
    Opening,
    check_hashes,
    finish_opening,
    now,
    open_batch,
)
from bolivia_fire.extension.inference import (
    common_support,
    compare_lags,
    decision,
    paired_report,
    row_hash,
    subset,
)
from bolivia_fire.extension.inputs import FrozenInputs
from bolivia_fire.extension.registration import local_path, read_json, verify_registration
from bolivia_fire.extension.resources import Resources, implementation_hashes
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.models.data import PanelData
from bolivia_fire.models.estimators import Candidate, lightgbm_datasets, search_parameters


class Extension:
    def __init__(
        self, paths: Paths, plan_path: str, *, memory_limit_gib: float | None = None
    ) -> None:
        self.registration = verify_registration(paths)
        self.paths = paths
        self.contract = self.registration.contract
        override = os.environ.get("BOLIVIA_FIRE_EXTENSION_MEMORY_LIMIT_GIB")
        self.memory_limit_gib = (
            memory_limit_gib
            if memory_limit_gib is not None
            else float(override)
            if override is not None
            else self.contract["resources"]["memory_limit_gib"]
        )
        self.memory_limit_source = (
            "runner_argument"
            if memory_limit_gib is not None
            else "BOLIVIA_FIRE_EXTENSION_MEMORY_LIMIT_GIB"
            if override is not None
            else "registered_contract"
        )
        # Validate the override before loading any panels.
        Resources(self.registration, "configuration", memory_limit_gib=self.memory_limit_gib)
        self.source = FrozenInputs(self.registration, plan_path)
        self.runs = self.registration.runs
        self.configurations = search_parameters(
            seed=7, space=self.contract["estimation"]["search"]["space"]
        )
        self.comparators: dict[str, Candidate] = {}
        self.comparator_hashes: dict[str, str] = {}
        self.comparator_training_counts: dict[str, Any] = {}
        self.models = paths.root / "artifacts/models/extension"
        self.selection_path = paths.root / "artifacts/evaluation/extension_selection.json"
        self.tick = time.perf_counter()

    def close(self) -> None:
        self.source.close()

    def metadata(self) -> dict[str, Any]:
        return {
            **self.registration.provenance(),
            "timestamp_utc": now(),
            "seed": 7,
            "input_hashes": self.source.hashes,
            "environment": environment(),
            "hardware": hardware(),
            "runtime_seconds": time.perf_counter() - self.tick,
            "registered_memory_limit_gib": self.contract["resources"]["memory_limit_gib"],
            "effective_memory_limit_gib": self.memory_limit_gib,
            "memory_limit_source": self.memory_limit_source,
            "implementation_hashes": implementation_hashes(),
        }

    def _resources(self, stage: str) -> Resources:
        return Resources(
            self.registration,
            stage,
            input_bytes=self.source.input_bytes,
            memory_limit_gib=self.memory_limit_gib,
            memory_limit_source=self.memory_limit_source,
        )

    def _load_comparators(self) -> None:
        for name, entry in self.contract["comparators"].items():
            selection = local_path(self.paths, entry["selection"])
            directory = selection.stem.removeprefix("selection_")
            prefix = f"artifacts/models/{directory}/{entry['selected_model']}"
            hashes = {
                entry["selection"]: entry["selection_sha256"],
                f"{prefix}.joblib": entry["model_sha256"],
                f"{prefix}.calibrator.joblib": entry["calibrator_sha256"],
            }
            check_hashes(self.registration, hashes)
            selected = read_json(selection)
            if selected.get("selected_model") != entry["selected_model"]:
                raise ValueError("Published comparator selection changed")
            candidate = joblib.load(self.paths.root / f"{prefix}.joblib")
            if not isinstance(candidate, Candidate) or candidate.features != entry["features"]:
                raise ValueError("Published comparator feature order changed")
            candidate.calibrator = joblib.load(self.paths.root / f"{prefix}.calibrator.joblib")
            self.comparators[name] = candidate
            self.comparator_training_counts[name] = selected.get("exclusions", {}).get("train")
            self.comparator_hashes.update(hashes)

    def _active(self, geography: str) -> list[dict[str, Any]]:
        return [
            run
            for run in self.runs
            if run["geography"] == geography
            and self.source.available[run["run_id"]]["status"] == "evaluated_eligible"
        ]

    def _family(
        self,
        geography: str,
        stage: str,
        resources: Resources,
        *,
        opening: Opening | None = None,
        labels: bool = True,
        holdout: bool = False,
    ) -> tuple[dict[str, dict[str, PanelData]], dict[str, Any]]:
        active = self._active(geography)
        if not active:
            return {}, {}

        def load(run: dict[str, Any]) -> PanelData:
            if not labels:
                return self.source.plan_support(run, stage, resources, holdout=holdout)
            return self.source.load(run, stage, resources, opening=opening, holdout=holdout)

        panels = {run["run_id"]: load(run) for run in active}
        comparator_names = [active[0]["comparator"], active[0]["contextual_comparator"]]
        for name in comparator_names:
            panels[name] = load(
                {
                    "run_id": name,
                    "ordered_features": self.contract["comparators"][name]["features"],
                },
            )
        try:
            resources.check(projected_ram=sum(data.x.nbytes * 2 for data in panels.values()))
            primary = [run["run_id"] for run in active if run["availability_lag_days"] != 32]
            aligned, counts = common_support(
                {name: panels[name] for name in primary + comparator_names}
            )
            families = {
                name: {key: aligned[key] for key in [name, *comparator_names]} for name in primary
            }
            support = {
                name: {key: counts[key] for key in [name, *comparator_names]} for name in primary
            }
            for run in active:
                name = run["run_id"]
                if run["availability_lag_days"] == 32:
                    families[name], support[name] = common_support(
                        {key: panels[key] for key in [name, *comparator_names]}
                    )
            return families, support
        finally:
            self.source.release(*panels.values())

    def _fit(
        self,
        run: dict[str, Any],
        train: PanelData,
        selection: PanelData,
        calibration: PanelData,
        resources: Resources,
        support: dict[str, Any],
    ) -> dict[str, Any]:
        identity = run["run_id"]
        support_record = {
            key: value for key, value in support.items() if key != "calibration_panels"
        }
        destination = self.paths.root / f"artifacts/evaluation/extension_fit_{identity}.json"
        if destination.exists():
            previous = read_json(destination)
            if previous.get("implementation_hashes") != implementation_hashes():
                raise ValueError("Archive extension checkpoint from a different implementation")
            if (
                previous.get("registration_sha256") != self.registration.digest
                or previous.get("input_hashes") != self.source.hashes
                or previous.get("support") != support_record
            ):
                raise ValueError("Extension checkpoint belongs to different frozen inputs")
            check_hashes(self.registration, previous["artifact_hashes"])
            return previous
        for data in (train, selection):
            if len(np.unique(data.y)) != 2:
                raise ValueError("Extension train and val_select each require both classes")
        # Float32 raw input, up to two byte-wide bin buffers, and float64 labels.
        # The raw matrix term reserves pages of the mapped training input.
        allocation = train.x.nbytes + (train.x.size + selection.x.size) * 2
        allocation += (train.y.size + selection.y.size) * 8
        resources.check(projected_ram=allocation)
        datasets = lightgbm_datasets(train.x, train.y, selection.x, selection.y, train.features)
        for dataset in datasets:
            dataset.free_raw_data = True
        del dataset
        settings = self.contract["estimation"]["search"]
        best: Candidate | None = None
        best_key: tuple[float, str] | None = None
        results = []

        def enforce_resources(_environment: Any) -> None:
            resources.check()

        enforce_resources.order = 5
        for index, configuration in enumerate(self.configurations):
            resources.check()
            parameters = {
                "objective": "binary",
                "metric": "average_precision",
                "verbosity": -1,
                "seed": 7,
                "deterministic": True,
                "force_col_wise": True,
                "num_threads": settings["num_threads"],
                "max_bin": settings["max_bin"],
                "feature_pre_filter": False,
                **configuration,
            }
            estimator = lgb.train(
                parameters,
                datasets[0],
                num_boost_round=settings["boosting_rounds"],
                valid_sets=[datasets[1]],
                callbacks=[
                    lgb.early_stopping(settings["early_stopping_rounds"], verbose=False),
                    enforce_resources,
                ],
            )
            estimator.free_dataset()
            candidate = Candidate(
                f"lightgbm_{index:02d}",
                estimator,
                train.features,
                parameters={**parameters, "best_iteration": estimator.best_iteration},
            )
            ap = average_precision(selection.y, candidate.predict(selection.x))
            results.append(
                {
                    "name": candidate.name,
                    "parameters": candidate.parameters,
                    "val_select_uncalibrated_ap": ap,
                }
            )
            key = (-ap, candidate.name)
            if best_key is None or key < best_key:
                best, best_key = candidate, key
            atomic_json(
                self.paths.root / f"artifacts/evaluation/extension_checkpoint_{identity}.json",
                {
                    "run_id": identity,
                    "stage": "search",
                    "completed_configurations": len(results),
                    "ordered_configurations": self.configurations,
                    "results": results,
                    "selected_so_far": best.name,
                    "resources": resources.record("running"),
                    **self.metadata(),
                },
            )
        if best is None:
            raise ValueError("Extension search produced no candidate")
        del datasets, estimator, candidate
        gc.collect()
        resources.check(projected_ram=calibration.x.nbytes * 2)
        best.calibrate(calibration.x, calibration.y)
        model = self.models / identity / f"{best.name}.joblib"
        calibrator = self.models / identity / f"{best.name}.calibrator.joblib"
        resources.check(projected_disk=train.x.shape[1] * 400 * 63 * 128)
        save_candidate(model, best)
        save_candidate(calibrator, best.calibrator)
        artifacts = {
            path.relative_to(self.paths.root).as_posix(): sha256_file(path)
            for path in (model, calibrator)
        }
        probability = best.predict(calibration.x, calibrated=True)
        baseline = self.comparators[run["comparator"]].predict(
            support["calibration_panels"][run["comparator"]].x, calibrated=True
        )
        resources.check(projected_ram=len(calibration.y) * 1024)
        report = {
            "run": run,
            "status": "evaluated_eligible",
            "selected_model": best.name,
            "model_path": model.relative_to(self.paths.root).as_posix(),
            "calibration_status": best.calibration_status,
            "selection_rule": "uncalibrated val_select average_precision",
            "search": results,
            "support": support_record,
            "train_support": {
                "rows": len(train.y),
                "exclusions": train.exclusions,
                "row_key_sha256": row_hash(train),
                "published_comparator": self.comparator_training_counts.get(run["comparator"]),
            },
            "artifact_hashes": artifacts,
            "validation": paired_report(
                calibration, probability, baseline, calibration_status=best.calibration_status
            ),
            "raw_val_calib": metrics(calibration.y, best.predict(calibration.x)),
            "contextual_comparator_val_calib": metrics(
                calibration.y,
                self.comparators[run["contextual_comparator"]].predict(
                    support["calibration_panels"][run["contextual_comparator"]].x, calibrated=True
                ),
            ),
            "feature_audit": self.source.available[identity].get("audit_evidence"),
            "projected_fit_allocation_bytes": allocation,
            "resources": resources.record("completed"),
            **self.metadata(),
        }
        self.source.validate()
        resources.check()
        atomic_json(destination, report)
        return report

    def _select_geography(self, geography: str, reports: dict[str, Any]) -> dict[str, Any]:
        with self._resources(f"{geography}_support") as resources:
            selection, selection_counts = self._family(geography, "val_select", resources)
            calibration, calibration_counts = self._family(geography, "val_calib", resources)
        try:
            for run in self._active(geography):
                identity = run["run_id"]
                with self._resources(identity) as resources:
                    train = self.source.load(run, "train", resources)
                    try:
                        reports[identity] = self._fit(
                            run,
                            train,
                            selection[identity][identity],
                            calibration[identity][identity],
                            resources,
                            {
                                "val_select": selection_counts[identity],
                                "val_calib": calibration_counts[identity],
                                "calibration_panels": calibration[identity],
                            },
                        )
                    finally:
                        self.source.release(train)
                        del train
                        gc.collect()
            lag_sensitivity = {}
            with self._resources(f"{geography}_lag_comparison") as resources:
                for run in self._active(geography):
                    identity = run["run_id"]
                    if run["availability_lag_days"] != 16:
                        continue
                    sensitivity = identity.replace("lag16", "lag32")
                    if (
                        reports.get(sensitivity, {}).get("status") != "evaluated_eligible"
                        or identity not in calibration
                        or identity not in calibration[identity]
                        or sensitivity not in calibration
                        or sensitivity not in calibration[sensitivity]
                    ):
                        lag_sensitivity[identity] = {
                            "status": "not_evaluated",
                            "reason": "sensitivity_calibration_panel_unavailable",
                        }
                        continue
                    left = calibration[identity][identity]
                    right = calibration[sensitivity][sensitivity]
                    left_model = joblib.load(
                        local_path(self.paths, reports[identity]["model_path"])
                    )
                    right_model = joblib.load(
                        local_path(self.paths, reports[sensitivity]["model_path"])
                    )
                    resources.check(projected_ram=left.x.nbytes + right.x.nbytes)
                    lag_sensitivity[identity] = compare_lags(
                        left,
                        left_model.predict(left.x, calibrated=True),
                        right,
                        right_model.predict(right.x, calibrated=True),
                        calibration_status=reports[sensitivity]["calibration_status"],
                    )
                    del left, right, left_model, right_model
            return lag_sensitivity
        finally:
            del selection, calibration
            gc.collect()

    def select(self) -> dict[str, Any]:
        self.selection_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.selection_path.with_suffix(".lock"))):
            if self.selection_path.exists():
                raise ValueError("Extension selection is already frozen")
            self._load_comparators()
            reports = {
                run["run_id"]: {"run": run, **self.source.available[run["run_id"]]}
                for run in self.runs
                if self.source.available[run["run_id"]]["status"] != "evaluated_eligible"
            }
            lag_sensitivity = {}
            for geography in ("temporal", "geographic"):
                lag_sensitivity.update(self._select_geography(geography, reports))
                gc.collect()
            hashes = dict(self.comparator_hashes)
            for report in reports.values():
                hashes.update(report.get("artifact_hashes", {}))
            hypotheses = {}
            for hypothesis in ("H3", "H4", "H5"):
                primary = next(
                    run
                    for run in self.runs
                    if run["geography"] == "temporal"
                    and run["hypothesis"] == hypothesis
                    and run["availability_lag_days"] != 32
                )
                report = reports[primary["run_id"]]
                hypotheses[hypothesis] = (
                    report["validation"]["decision"]
                    if report["status"] == "evaluated_eligible"
                    else decision({}, available=False, reason=report.get("reason"))
                )
            self.source.validate()
            report = {
                "runs": reports,
                "hypotheses": hypotheses,
                "artifact_hashes": hashes,
                "ordered_configurations": self.configurations,
                "lag_sensitivity": lag_sensitivity,
                "frozen_at_utc": now(),
                **self.metadata(),
            }
            atomic_json(self.selection_path, report)
            atomic_json(
                self.selection_path.with_name("extension_selection_sha256.json"),
                {"sha256": sha256_file(self.selection_path), **self.registration.provenance()},
            )
            return report

    def _frozen(self) -> dict[str, Any]:
        sidecar = self.selection_path.with_name("extension_selection_sha256.json")
        if not self.selection_path.is_file() or not sidecar.is_file():
            raise ValueError("Freeze extension selection before scoring")
        if read_json(sidecar).get("sha256") != sha256_file(self.selection_path):
            raise ValueError("Frozen extension selection hash mismatch")
        frozen = read_json(self.selection_path)
        if frozen["registration_sha256"] != self.registration.digest:
            raise ValueError("Extension selection belongs to another registration")
        if frozen["input_hashes"] != self.source.hashes:
            raise ValueError("Scoring inputs differ from the frozen extension selection")
        check_hashes(self.registration, frozen["input_hashes"])
        check_hashes(self.registration, frozen["artifact_hashes"])
        frozen["selection_sha256"] = sha256_file(self.selection_path)
        return frozen

    def score(
        self,
        *,
        scope: str,
        explicit: bool,
        reason: str | None,
        confirmation_evidence: str | None = None,
    ) -> dict[str, Any]:
        if not explicit or not reason or not reason.strip():
            raise ValueError("Extension scoring requires an explicit opening and nonempty reason")
        frozen = self._frozen()
        self._load_comparators()
        with self._resources(f"{scope}_support") as resources:
            planned_hashes = {}
            for geography in (
                ("temporal",) if scope == "confirmation" else ("temporal", "geographic")
            ):
                panels, _ = self._family(
                    geography, scope, resources, labels=False, holdout=geography == "geographic"
                )
                planned_hashes.update(
                    {name: row_hash(values[name]) for name, values in panels.items()}
                )
        opening = open_batch(
            self.registration,
            frozen,
            scope=scope,
            explicit=explicit,
            reason=reason,
            row_hashes=planned_hashes,
            confirmation_evidence=confirmation_evidence,
        )
        try:
            reports, lag_reports = {}, {}
            for geography in (
                ("temporal",) if scope == "confirmation" else ("temporal", "geographic")
            ):
                with self._resources(f"{scope}_{geography}") as resources:
                    families, support = self._family(
                        geography,
                        scope,
                        resources,
                        opening=opening,
                        holdout=geography == "geographic",
                    )
                    predictions = {}
                    for identity, panels in families.items():
                        record = frozen["runs"][identity]
                        candidate = joblib.load(local_path(self.paths, record["model_path"]))
                        run = record["run"]
                        data = panels[identity]
                        predictions[identity] = (data, candidate.predict(data.x, calibrated=True))
                    # Every compared primary shares the intersection of finite predictions.
                    primary = [
                        name
                        for name in families
                        if frozen["runs"][name]["run"]["availability_lag_days"] != 32
                    ]
                    finite = None
                    for identity in primary:
                        data, p = predictions[identity]
                        mask = np.isfinite(p)
                        for name in (
                            frozen["runs"][identity]["run"]["comparator"],
                            frozen["runs"][identity]["run"]["contextual_comparator"],
                        ):
                            mask &= np.isfinite(
                                self.comparators[name].predict(
                                    families[identity][name].x, calibrated=True
                                )
                            )
                        finite = mask if finite is None else finite & mask
                    for identity, panels in families.items():
                        record = frozen["runs"][identity]
                        run, (data, p) = record["run"], predictions[identity]
                        baseline = self.comparators[run["comparator"]].predict(
                            panels[run["comparator"]].x, calibrated=True
                        )
                        context = self.comparators[run["contextual_comparator"]].predict(
                            panels[run["contextual_comparator"]].x, calibrated=True
                        )
                        mask = (
                            finite
                            if identity in primary
                            else np.isfinite(p) & np.isfinite(baseline) & np.isfinite(context)
                        )
                        indices = np.flatnonzero(mask)
                        data = subset(data, indices)
                        p, baseline, context = p[indices], baseline[indices], context[indices]
                        resources.check(projected_ram=len(data.y) * 1024)
                        report = paired_report(
                            data,
                            p,
                            baseline,
                            calibration_status=record["calibration_status"],
                            spatial=geography == "geographic",
                        )
                        report["contextual_comparator"] = metrics(data.y, context)
                        report["support"] = support[identity]
                        report["excluded_nonfinite_predictions"] = int((~mask).sum())
                        dates = pd.to_datetime(data.column("issue_date"), utc=True)
                        santa = data.column("santa_cruz").astype(bool)
                        dry = np.isin(dates.month, [7, 8, 9, 10])
                        report["exploratory_subgroups"] = {}
                        for name, subgroup in (
                            ("santa_cruz", santa),
                            ("dry_season", dry),
                            ("santa_cruz_dry_season", santa & dry),
                        ):
                            selected = np.flatnonzero(subgroup)
                            report["exploratory_subgroups"][name] = paired_report(
                                subset(data, selected),
                                p[selected],
                                baseline[selected],
                                calibration_status=record["calibration_status"],
                                spatial=geography == "geographic",
                            )
                        reports[identity] = report
                        resources.check()
                        predictions[identity] = (data, p)
                    for identity in primary:
                        if "lag16" not in identity:
                            continue
                        sensitivity = identity.replace("lag16", "lag32")
                        if sensitivity not in predictions:
                            continue
                        left, left_p = predictions[identity]
                        right, right_p = predictions[sensitivity]
                        lag_reports[identity] = compare_lags(
                            left,
                            left_p,
                            right,
                            right_p,
                            calibration_status=frozen["runs"][sensitivity]["calibration_status"],
                            spatial=geography == "geographic",
                        )
            for identity, record in frozen["runs"].items():
                if record["status"] == "not_evaluated":
                    reports[identity] = {"status": "not_evaluated", "reason": record.get("reason")}
            result = {
                "scope": scope,
                "classification": "exploratory"
                if scope == "test"
                else "supplementary_confirmation",
                "runs": reports,
                "lag_sensitivity": lag_reports,
                "opening_id": opening.identifier,
                "selection_sha256": sha256_file(self.selection_path),
                "artifact_hashes": frozen["artifact_hashes"],
                **self.metadata(),
            }
            if scope == "confirmation":
                result["hypotheses"] = {}
                for hypothesis, validation in frozen["hypotheses"].items():
                    identity = next(
                        run["run_id"]
                        for run in self.runs
                        if run["geography"] == "temporal"
                        and run["hypothesis"] == hypothesis
                        and run["availability_lag_days"] != 32
                    )
                    agreement = reports.get(identity, {}).get(
                        "decision",
                        {"status": "not_evaluated", "reason": "required_group_unavailable"},
                    )
                    result["hypotheses"][hypothesis] = {
                        "validation": validation,
                        "confirmation": agreement,
                        "future_study_proposal": validation["status"] == "adds_information"
                        and agreement["status"] == "adds_information",
                    }
            self.source.validate()
            destination = (
                self.paths.root
                / f"artifacts/evaluation/extension_{scope}_{opening.identifier}.json"
            )
            atomic_json(destination, result)
            finish_opening(self.registration, opening, "completed")
            return result
        except BaseException as exc:
            finish_opening(self.registration, opening, "failed", type(exc).__name__)
            raise
