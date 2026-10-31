from __future__ import annotations

import json
from typing import Any

from bolivia_fire.comparison.runtime import ALTERNATIVES, FAMILIES, Study, read_json

LABELS = {
    "lightgbm": "LightGBM (recorded)",
    "logistic_regression": "Logistic regression (recorded)",
    "xgboost": "XGBoost hist",
    "catboost": "CatBoost",
    "spatial_cnn": "Spatial CNN",
}


def reproduction(validation: dict, reference: dict) -> dict[str, Any]:
    results: dict[str, Any] = {"models": {}, "all_within_tolerance": True}
    for family in FAMILIES:
        results["models"][family] = {}
        for split in ("val_select", "val_calib"):
            results["models"][family][split] = {}
            for kind in ("uncalibrated", "calibrated"):
                observed = validation[family][split][kind]["average_precision"]
                expected = reference[family][split][kind]["average_precision"]
                tolerance = 1e-8 if family in FAMILIES[:2] else 1e-6
                difference = observed - expected
                within = abs(difference) <= tolerance
                results["models"][family][split][kind] = {
                    "reference_ap": expected,
                    "rerun_ap": observed,
                    "ap_difference": difference,
                    "tolerance": tolerance,
                    "within_tolerance": within,
                }
                results["all_within_tolerance"] &= within
    return results


def _interval(value: float, interval: dict) -> str:
    return f"{value:.6f} [{interval['lower']:.6f}, {interval['upper']:.6f}]"


def write_report(study: Study) -> dict[str, Any]:
    validation = read_json(study.result_path("validation"))
    bootstrap = read_json(study.result_path("bootstrap"))
    selection = read_json(study.result_path("selection"))
    selection_hash = read_json(study.result_path("selection_sha256"))["sha256"]
    reference = read_json(study.result_path("reference"))
    inputs = read_json(study.result_path("inputs"))
    prepared = read_json(study.result_path("prepare"))
    test = read_json(study.result_path("test"))
    determinism = read_json(study.result_path("determinism_audit"))
    reproduced = reproduction(validation, reference["validation"])
    catboost_trials = read_json(study.result_path("catboost_trials"))
    reproduced["catboost_trials"] = [
        {
            "index": observed["index"],
            "source_ap": expected["val_select_ap"],
            "rerun_ap": observed["val_select_ap"],
            "ap_difference": observed["val_select_ap"] - expected["val_select_ap"],
            "source_trees": expected["best_round"],
            "rerun_trees": observed["best_round"],
            "parameters_identical": observed["parameters"] == expected["parameters"],
        }
        for observed, expected in zip(catboost_trials, reference["catboost_trials"], strict=True)
    ]
    source_trial = reference["selected"]["catboost"]["selected_trial"]["index"]
    rerun_trial = selection["selected_models"]["catboost"]["selected_trial"]["index"]
    source_ci = reference["bootstrap"]["val_calib"]["catboost"]["calibrated"]["paired_difference"][
        "average_precision"
    ]
    rerun_ci = bootstrap["val_calib"]["catboost"]["calibrated"]["paired_difference"][
        "average_precision"
    ]
    reproduced["catboost_selection"] = {
        "source_trial": source_trial,
        "rerun_trial": rerun_trial,
        "selected_trial_changed": source_trial != rerun_trial,
        "source_paired_calibrated_ap_interval": source_ci,
        "rerun_paired_calibrated_ap_interval": rerun_ci,
        "paired_conclusion_changed": (source_ci["lower"] > 0) != (rerun_ci["lower"] > 0),
    }
    study.save("reproduction", reproduced)
    lines = [
        "# Post-hoc comparison of alternative learners",
        "",
        "This experiment checks whether the benchmark's conclusion is robust to the learner "
        "choice. It retains the published LightGBM model. The private source study and this "
        "repository rerun were designed after the primary test had been opened; all results "
        f"here are exploratory. The first primary opening was {selection['primary_opening_utc']}.",
        "",
        f"The validation decision was frozen at {selection['frozen_utc']}, before its "
        f"exploratory scoring began at {test['opening']['timestamp_utc']}. The decision is in "
        "[comparison_selection.json](../artifacts/evaluation/comparison_selection.json), "
        f"SHA-256 `{selection_hash}`. It uses validation evidence only.",
        "",
        "## Models and information",
        "",
        "XGBoost hist tests a different histogram tree implementation and depth-wise growth. "
        "CatBoost tests symmetric trees and regularization; no categorical predictors were "
        "added. The spatial CNN tests learned directional local interactions using the same "
        "strict feature types on weekly national grids. Neighboring values expand spatial "
        "context beyond each tabular row. LightGBM and logistic regression are read from the "
        "recorded selection artifact and fitted model files without refitting.",
        "",
        "All models use the benchmark target, partitions, boundary purges, geographic "
        "holdout exclusion and `window_obs_complete_28d` eligibility through the repository's "
        "`PanelStore`. The 365-day completeness flag remains a feature. Held-out locations "
        "may supply past-feature context to the CNN; their labels never enter normalization, "
        "training, selection or calibration. Outside-country inputs are zero after train-only "
        "log1p transformation, imputation and scaling. Ineligible grid targets are masked.",
        "",
        "| Partition | Eligible rows | Positives | Prevalence |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, item in prepared["partitions"].items():
        lines.append(
            f"| {name} | {item['rows']:,} | {item['positives']:,} | {item['prevalence']:.6f} |"
        )
    lines += [
        "",
        "## Frozen configuration",
        "",
        "[comparison.yaml](../configs/comparison.yaml) copies the source study's spaces, "
        "ordered draws and seeds. Each alternative has twenty configurations and a one-hour "
        "family backstop. Uncalibrated `val_select` AP selects trials and checkpoints; ties "
        "keep the first. Selected learners are never refitted on validation data. All seeds "
        "for the searches, models and bootstrap are 7. The frozen run uses CUDA "
        "for both tree learners and the CNN.",
        "",
        "XGBoost uses histogram trees, 63 bins, at most 400 rounds and 30-round AP patience, "
        "with four CPU threads. CatBoost fits 400 trees per trial with 63 borders and Bayesian "
        "bootstrap; exact AP selects among ten-tree checkpoints before truncation. The CNN "
        "has a 1x1 stem, GELU, two or three 3x3 layers, a 1x1 head and linear center skip. "
        "It uses every eligible row in unweighted BCE, AdamW, gradient clipping at 5, four "
        "weeks per batch, at most twelve epochs and three-epoch AP patience. Mixed precision "
        "is disabled. CUDA deterministic algorithms are enabled, cuDNN benchmarking is "
        "disabled and `CUBLAS_WORKSPACE_CONFIG=:4096:8` is set.",
        "",
        "| Learner | Selected trial (zero-based) | Trees / epoch | Configurations completed |",
        "| --- | ---: | ---: | ---: |",
    ]
    for family in ALTERNATIVES:
        item = selection["selected_models"][family]
        trial = item["selected_trial"]
        lines.append(
            f"| {LABELS[family]} | {trial['index']} | "
            f"{trial.get('best_round', trial.get('best_epoch'))} | "
            f"{item['completed_configurations']} |"
        )
    for family in ALTERNATIVES:
        parameters = selection["selected_models"][family]["selected_trial"]["parameters"]
        lines += [
            "",
            f"Selected {LABELS[family]} parameters:",
            "",
            "```json",
            json.dumps(parameters, indent=2),
            "```",
        ]
    lines += [
        "",
        "## Validation",
        "",
        "Point metrics reuse `bolivia_fire.evaluation.metrics`; uncertainty reuses "
        "`bolivia_fire.evaluation.bootstrap.block_bootstrap`. Intervals are 95% percentile "
        "intervals from 500 paired calendar-month resamples, seed 7. Each comparison uses "
        "identical rows and the same resampled month weights for LightGBM and its alternative. "
        "Weekly area recall uses eligible national geodesic cell areas. As in the source "
        "study, area recall is omitted from the bootstrap; the tables report its point values.",
        "",
    ]
    for split, kind, title in (
        ("val_select", "uncalibrated", "2021 selection, uncalibrated"),
        ("val_calib", "uncalibrated", "2022 calibration, uncalibrated"),
        ("val_calib", "calibrated", "2022 calibration, isotonic calibrated"),
    ):
        lines += [
            f"### {title}",
            "",
            "| Model | AP [95% CI] | ROC-AUC | Brier | Log loss | ECE | Recall 5% | Recall 10% |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for family in FAMILIES:
            point = validation[family][split][kind]
            ci = bootstrap[split][family][kind]["metrics"]["average_precision"]
            values = " | ".join(
                f"{point[name]:.6f}"
                for name in (
                    "roc_auc",
                    "brier_score",
                    "log_loss",
                    "expected_calibration_error",
                    "recall_at_area_budget_5pct",
                    "recall_at_area_budget_10pct",
                )
            )
            lines.append(
                f"| {LABELS[family]} | {_interval(point['average_precision'], ci)} | {values} |"
            )
        lines.append("")
    lines += [
        "### Paired 2022 AP differences versus LightGBM",
        "",
        "| Alternative | Uncalibrated difference [95% CI] | Calibrated difference [95% CI] |",
        "| --- | --- | --- |",
    ]
    for family in ALTERNATIVES:
        cells = []
        for kind in ("uncalibrated", "calibrated"):
            point = (
                validation[family]["val_calib"][kind]["average_precision"]
                - validation["lightgbm"]["val_calib"][kind]["average_precision"]
            )
            ci = bootstrap["val_calib"][family][kind]["paired_difference"]["average_precision"]
            cells.append(f"{point:+.6f} [{ci['lower']:+.6f}, {ci['upper']:+.6f}]")
        lines.append(f"| {LABELS[family]} | {' | '.join(cells)} |")
    blocks = bootstrap["val_calib"]["lightgbm"]["calibrated"]["blocks"]
    lines += [
        "",
        "Isotonic regression is fit on `val_calib` only after every model/configuration/"
        "epoch choice is frozen. Calibrated 2022 diagnostics reuse those fitting labels; "
        "they are not an independent calibration assessment. Isotonic ties may reduce AP "
        "while improving probability error. Calibration is then applied unchanged to test.",
        "",
        f"Only {blocks} observed calendar-month blocks contribute to the 2022 bootstrap. "
        "Intervals condition on fixed models and calibrators and omit training/search "
        "uncertainty. No alternative has a validation AP advantage with a paired interval "
        "entirely above zero. CatBoost is a candidate for a future pre-registered comparison "
        "on a new untouched holdout; these results do not justify changing the reported model.",
        "",
        "## Reproduction against the private source study",
        "",
        "The source study's recorded values are preserved in "
        "[comparison_reference.json](../artifacts/evaluation/comparison_reference.json). "
        "The measured comparison is in "
        "[comparison_reproduction.json](../artifacts/evaluation/comparison_reproduction.json). "
        "The table compares uncalibrated AP; that artifact also checks calibrated AP.",
        "",
        "| Model | Source 2021 AP | Rerun 2021 AP | Difference | "
        "Source 2022 AP | Rerun 2022 AP | Difference |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for family in FAMILIES:
        values = []
        for split in ("val_select", "val_calib"):
            item = reproduced["models"][family][split]["uncalibrated"]
            values.extend(
                (
                    f"{item['reference_ap']:.9f}",
                    f"{item['rerun_ap']:.9f}",
                    f"{item['ap_difference']:+.3e}",
                )
            )
        lines.append(f"| {LABELS[family]} | {' | '.join(values)} |")
    failures = [
        f"{family}/{split}/{kind}: {item['ap_difference']:+.3e}"
        for family, splits in reproduced["models"].items()
        for split, kinds in splits.items()
        for kind, item in kinds.items()
        if not item["within_tolerance"]
    ]
    if failures:
        lines += [
            "",
            "AP differences beyond the 1e-6 alternative tolerance: " + "; ".join(failures) + ".",
        ]
    else:
        lines += [
            "",
            "All alternative validation AP values reproduce within 1e-6; "
            "recorded baselines agree within 1e-8.",
        ]
    lines += [
        "",
        "[CatBoost documents nondeterministic GPU floating-point summations]"
        "(https://catboost.ai/docs/en/features/training-on-gpu). The measured "
        "differences above are retained rather than changing its search or selecting a rerun. "
        "The CNN uses deterministic flags and the source seed. XGBoost prediction rebuilds "
        "the train-referenced QuantileDMatrix, preserving float-level ties from selection. "
        "Selected model roundtrip differences are recorded in "
        "[comparison_roundtrip.json](../artifacts/evaluation/comparison_roundtrip.json).",
        "",
        "### CatBoost trial-level reproduction",
        "",
        "Training/selection arrays "
        + (
            "are"
            if all(item["identical"] for item in determinism["array_identity"].values())
            else "are not"
        )
        + " byte-identical to the source study. The CatBoost binary "
        + ("matches" if determinism["catboost_binary_identical"] else "differs from")
        + " the source binary. Trial parameter identity and AP differences are recorded in "
        "[comparison_determinism_audit.json]"
        "(../artifacts/evaluation/comparison_determinism_audit.json). GPU floating-point "
        "reduction nondeterminism prevents exact reproduction here.",
        "",
        f"The source selected trial {source_trial}; this rerun selected trial {rerun_trial}. "
        f"The 2022 calibrated paired interval was [{source_ci['lower']:+.6f}, "
        f"{source_ci['upper']:+.6f}] in the source and is [{rerun_ci['lower']:+.6f}, "
        f"{rerun_ci['upper']:+.6f}] here. The superiority conclusion "
        + (
            "changed."
            if reproduced["catboost_selection"]["paired_conclusion_changed"]
            else "did not change."
        ),
        "",
        "| Trial | Source AP | Rerun AP | Difference | Source trees | Rerun trees |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in reproduced["catboost_trials"]:
        lines.append(
            f"| {item['index']} | {item['source_ap']:.9f} | {item['rerun_ap']:.9f} | "
            f"{item['ap_difference']:+.3e} | {item['source_trees']} | {item['rerun_trees']} |"
        )
    if study.result_path("cpu_check").exists():
        cpu = read_json(study.result_path("cpu_check"))
        lines += [
            "",
            "### Separate CPU reproducibility check",
            "",
            f"One CPU refit uses GPU-selected trial {cpu['source_gpu_trial']} at the fixed "
            f"GPU-selected tree count {cpu['fixed_gpu_tree_count']}; it fitted "
            f"{cpu['fitted_trees']} trees in {cpu['resources']['runtime_seconds']:.1f} seconds "
            f"with {cpu['resources']['peak_process_tree_rss_gib']:.2f} GiB peak RAM. Its "
            f"uncalibrated selection AP is {cpu['val_select']['average_precision']:.6f}. "
            "This single refit does not establish between-fit identity. Its saved-model "
            "prediction roundtrip is exact. It is not calibrated, scored on test, or used "
            "to select a learner; the frozen GPU search remains the reference. "
            "[comparison_cpu_check.json](../artifacts/evaluation/comparison_cpu_check.json) "
            "records its settings, completion status and model hash.",
            "",
        ]
    lines += [
        "## Exploratory post-hoc test: previously opened 2023-2025 holdout",
        "",
        "This is an explicitly exploratory temporal addendum on the previously opened test, "
        "not confirmatory evidence or a new holdout. Test scores did not select models, "
        "calibrators, configurations or the recommendation. The opening was appended to "
        "[final_test_log.json](../artifacts/evaluation/final_test_log.json) with type "
        "`post_hoc_comparison` before labels were loaded. A repeated comparison opening "
        "requires `--reopen-reason`.",
        "",
        f"The test population contains {test['rows']:,} eligible non-held-out cell-weeks "
        f"and {test['positives']:,} positives. Geographic holdout scoring is outside this "
        "temporal comparison.",
        "",
        "| Model | Raw AP | Calibrated AP | ROC-AUC | Brier | Log loss | "
        "ECE | Recall 5% | Recall 10% |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for family in FAMILIES:
        item = test["models"][family]
        values = [f"{item['uncalibrated']['average_precision']:.6f}"]
        values.extend(
            f"{item['calibrated'][name]:.6f}"
            for name in (
                "average_precision",
                "roc_auc",
                "brier_score",
                "log_loss",
                "expected_calibration_error",
                "recall_at_area_budget_5pct",
                "recall_at_area_budget_10pct",
            )
        )
        lines.append(f"| {LABELS[family]} | {' | '.join(values)} |")
    lines += [
        "",
        "## Resource costs and environment",
        "",
        "| Search | Configurations | Wall seconds | Peak process-tree RAM (GiB) | "
        "Minimum free disk (GiB) |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for family in ALTERNATIVES:
        item = selection["selected_models"][family]
        cost = item["resources"]
        lines.append(
            f"| {LABELS[family]} | {item['completed_configurations']} | "
            f"{cost['runtime_seconds']:.1f} | {cost['peak_process_tree_rss_gib']:.2f} | "
            f"{cost['minimum_free_disk_gib']:.2f} |"
        )
    lines += [
        "",
        "| Additional stage | Wall seconds | Peak process-tree RAM (GiB) |",
        "| --- | ---: | ---: |",
    ]
    stage_costs = {
        "Preparation and recorded-baseline verification": prepared["resources"],
        "Calibration and validation metrics": read_json(study.result_path("validation_resources")),
        "Validation bootstrap": read_json(study.result_path("bootstrap_resources")),
        "Exploratory test scoring": test["resources"],
    }
    for label, cost in stage_costs.items():
        lines.append(
            f"| {label} | {cost['runtime_seconds']:.1f} | {cost['peak_process_tree_rss_gib']:.2f} |"
        )
    lines += [
        "",
        "Search costs include loading and, for the CNN, initial grid construction; they "
        "exclude installation and later calibration/bootstrap. Process-tree RSS is sampled "
        "every 0.5 seconds, and mapped pages contribute. GPU searches run sequentially; "
        "the separate CPU audit overlaps part of the CNN search, so these are practical "
        "costs rather than isolated hardware benchmarks. The RAM "
        "limit is 12 GiB and the free disk reserve is 55 GiB. Recorded primary models were "
        "not retrained, so their fitting costs are not measured here.",
        "",
        "Measured environment and hardware:",
        "",
        "```json",
        json.dumps(
            {"environment": inputs["environment"], "hardware": inputs["hardware"]}, indent=2
        ),
        "```",
        "",
        "## Reproduce",
        "",
        "Use a local checkout of the live-data branch with the processed panel and frozen "
        "primary fitted artifacts. Create a fresh checkout/cache for a new search because "
        "the selection cannot be overwritten. In that disposable checkout, archive the "
        "existing comparison outputs except `comparison_reference.json` and "
        "`comparison_provenance.json`, and start with empty comparison model/cache directories. "
        "Keep `final_test_log.json`; a reproduction of an already logged comparison requires "
        "an explicit `--reopen-reason` on its evaluate command. Install the optional dependencies; "
        "they are excluded from the core package. The project declares a platform-neutral "
        "PyTorch requirement. For the recorded GPU environment install its CUDA wheel "
        "explicitly after syncing, and preserve that wheel with `--no-sync`:",
        "",
        "```sh",
        "uv sync --all-extras",
        "uv pip install torch==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128",
        "uv run --no-sync bolivia-fire compare prepare",
        "uv run --no-sync bolivia-fire compare train",
        "uv run --no-sync bolivia-fire compare audit",
        "uv run --no-sync bolivia-fire compare select",
        "uv run --no-sync bolivia-fire compare evaluate --post-hoc-test \\",
        '  --reason "post-hoc exploratory comparison of alternative learners; '
        'does not change the reported model" \\',
        '  --reopen-reason "independent reproduction of the post-hoc comparison"',
        "uv run --no-sync bolivia-fire compare report",
        "uv run --no-sync ruff check .",
        'uv run --no-sync pytest -m "not live and not local_data"',
        "```",
        "",
        "`compare train --family xgboost`, `--family catboost` and `--family spatial_cnn` "
        "permit separate sequential stages. `select` records family choices before "
        "calibration, then computes validation intervals and writes the immutable decision "
        "and SHA-256 sidecar. It can resume an interrupted bootstrap. No primary "
        "train/select/evaluate command is part of this workflow.",
        "",
        "Models are stored in ignored `artifacts/models/comparison/`; regenerable arrays "
        "and grids are in ignored `artifacts/.cache/comparison/`. Durable outputs are the "
        "`artifacts/evaluation/comparison_*.json` files. Model, calibrator, panel and "
        "configuration hashes bind the logged test opening to the frozen selection.",
        "",
        "## Evidence",
        "",
        "All measured values in this document are generated from "
        "[comparison_validation.json](../artifacts/evaluation/comparison_validation.json), "
        "[comparison_bootstrap.json](../artifacts/evaluation/comparison_bootstrap.json), "
        "[comparison_prepare.json](../artifacts/evaluation/comparison_prepare.json), "
        "[comparison_selection.json](../artifacts/evaluation/comparison_selection.json), "
        "[comparison_inputs.json](../artifacts/evaluation/comparison_inputs.json), "
        "[comparison_reproduction.json](../artifacts/evaluation/comparison_reproduction.json), "
        "[comparison_cpu_check.json](../artifacts/evaluation/comparison_cpu_check.json), "
        "[comparison_reference.json](../artifacts/evaluation/comparison_reference.json), "
        "and [comparison_test.json](../artifacts/evaluation/comparison_test.json).",
        "",
    ]
    destination = study.paths.root / "docs/model-comparison.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")
    return {"path": destination.relative_to(study.paths.root).as_posix(), **reproduced}
