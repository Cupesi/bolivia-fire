# Post-hoc comparison of alternative learners

This experiment checks whether the benchmark's conclusion is robust to the learner choice. It retains the published LightGBM model. The private source study and this repository rerun were designed after the primary test had been opened; all results here are exploratory. The first primary opening was 2026-10-31T10:30:00.578581+00:00.

The validation decision was frozen at 2026-10-31T19:35:50.785313+00:00, before its exploratory scoring began at 2026-10-31T19:36:43.128562+00:00. The decision is in [comparison_selection.json](../artifacts/evaluation/comparison_selection.json), SHA-256 `338e530a9eea33b4f7bd559c258c572ec1892905a6d92a58079b6ea89e5a0e00`. It uses validation evidence only.

Metrics and interval endpoints are rounded to three decimals; ECE and values below 0.001
retain two significant digits. Counts are exact. Exact values are in the cited evaluation
JSON artifacts.

## Models and information

XGBoost hist tests a different histogram tree implementation and depth-wise growth. CatBoost tests symmetric trees and regularization; no categorical predictors were added. The spatial CNN tests learned directional local interactions using the same strict feature types on weekly national grids. Neighboring values expand spatial context beyond each tabular row. LightGBM and logistic regression are read from the recorded selection artifact and fitted model files without refitting.

All models use the benchmark target, partitions, boundary purges, geographic holdout exclusion and `window_obs_complete_28d` eligibility through the repository's `PanelStore`. The 365-day completeness flag remains a feature. Held-out locations may supply past-feature context to the CNN; their labels never enter normalization, training, selection or calibration. Outside-country inputs are zero after train-only log1p transformation, imputation and scaling. Ineligible grid targets are masked.

| Partition | Eligible rows | Positives | Prevalence |
| --- | ---: | ---: | ---: |
| train | 11,979,436 | 255,339 | 0.021 |
| val_select | 1,357,276 | 30,302 | 0.022 |
| val_calib | 1,180,240 | 31,110 | 0.026 |

## Frozen configuration

[comparison.yaml](../configs/comparison.yaml) copies the source study's spaces, ordered draws and seeds. Each alternative has twenty configurations and a one-hour family backstop. Uncalibrated `val_select` AP selects trials and checkpoints; ties keep the first. Selected learners are never refitted on validation data. All seeds for the searches, models and bootstrap are 7. The frozen run uses CUDA for both tree learners and the CNN.

XGBoost uses histogram trees, 63 bins, at most 400 rounds and 30-round AP patience, with four CPU threads. CatBoost fits 400 trees per trial with 63 borders and Bayesian bootstrap; exact AP selects among ten-tree checkpoints before truncation. The CNN has a 1x1 stem, GELU, two or three 3x3 layers, a 1x1 head and linear center skip. It uses every eligible row in unweighted BCE, AdamW, gradient clipping at 5, four weeks per batch, at most twelve epochs and three-epoch AP patience. Mixed precision is disabled. CUDA deterministic algorithms are enabled, cuDNN benchmarking is disabled and `CUBLAS_WORKSPACE_CONFIG=:4096:8` is set.

| Learner | Selected trial (zero-based) | Trees / epoch | Configurations completed |
| --- | ---: | ---: | ---: |
| XGBoost hist | 9 | 65 | 20 |
| CatBoost | 19 | 200 | 20 |
| Spatial CNN | 3 | 12 | 20 |

Selected XGBoost hist parameters:

```json
{
  "tree_method": "hist",
  "device": "cuda",
  "max_bin": 63,
  "objective": "binary:logistic",
  "seed": 7,
  "nthread": 4,
  "disable_default_eval_metric": 1,
  "subsample": 1.0,
  "max_depth": 5,
  "eta": 0.03,
  "min_child_weight": 10,
  "colsample_bytree": 0.85,
  "lambda": 1
}
```

Selected CatBoost parameters:

```json
{
  "task_type": "GPU",
  "devices": "0",
  "iterations": 400,
  "loss_function": "Logloss",
  "border_count": 63,
  "random_seed": 7,
  "thread_count": 4,
  "bootstrap_type": "Bayesian",
  "allow_writing_files": false,
  "verbose": false,
  "use_best_model": false,
  "depth": 6,
  "learning_rate": 0.06,
  "l2_leaf_reg": 10,
  "random_strength": 1.0,
  "bagging_temperature": 0.0
}
```

Selected Spatial CNN parameters:

```json
{
  "width": 24,
  "depth": 2,
  "learning_rate": 0.003,
  "weight_decay": 0.0001,
  "dropout": 0.0
}
```

## Validation

Point metrics reuse `bolivia_fire.evaluation.metrics`; uncertainty reuses `bolivia_fire.evaluation.bootstrap.block_bootstrap`. Intervals are 95% percentile intervals from 500 paired calendar-month resamples, seed 7. Each comparison uses identical rows and the same resampled month weights for LightGBM and its alternative. Weekly area recall uses eligible national geodesic cell areas. As in the source study, area recall is omitted from the bootstrap; the tables report its point values.

### 2021 selection, uncalibrated

| Model | AP [95% CI] | ROC-AUC | Brier | Log loss | ECE | Recall 5% | Recall 10% |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| LightGBM (recorded) | 0.347 [0.207, 0.399] | 0.929 | 0.018 | 0.069 | 0.0047 | 0.386 | 0.567 |
| Logistic regression (recorded) | 0.326 [0.183, 0.382] | 0.924 | 0.018 | 0.070 | 0.0025 | 0.374 | 0.551 |
| XGBoost hist | 0.345 [0.207, 0.400] | 0.929 | 0.018 | 0.070 | 0.0056 | 0.385 | 0.570 |
| CatBoost | 0.349 [0.212, 0.404] | 0.930 | 0.017 | 0.068 | 0.0020 | 0.390 | 0.571 |
| Spatial CNN | 0.350 [0.211, 0.405] | 0.932 | 0.017 | 0.067 | 0.0037 | 0.390 | 0.577 |

### 2022 calibration, uncalibrated

| Model | AP [95% CI] | ROC-AUC | Brier | Log loss | ECE | Recall 5% | Recall 10% |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| LightGBM (recorded) | 0.319 [0.228, 0.375] | 0.918 | 0.021 | 0.082 | 0.0067 | 0.394 | 0.573 |
| Logistic regression (recorded) | 0.286 [0.196, 0.344] | 0.913 | 0.022 | 0.084 | 0.0048 | 0.381 | 0.562 |
| XGBoost hist | 0.313 [0.225, 0.368] | 0.917 | 0.021 | 0.083 | 0.0082 | 0.392 | 0.571 |
| CatBoost | 0.323 [0.234, 0.382] | 0.918 | 0.021 | 0.082 | 0.0040 | 0.397 | 0.575 |
| Spatial CNN | 0.317 [0.231, 0.371] | 0.920 | 0.021 | 0.082 | 0.0073 | 0.396 | 0.577 |

### 2022 calibration, isotonic calibrated

| Model | AP [95% CI] | ROC-AUC | Brier | Log loss | ECE | Recall 5% | Recall 10% |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| LightGBM (recorded) | 0.316 [0.225, 0.371] | 0.918 | 0.021 | 0.081 | 7.1e-05 | 0.396 | 0.574 |
| Logistic regression (recorded) | 0.283 [0.193, 0.341] | 0.913 | 0.022 | 0.083 | 0.00011 | 0.381 | 0.563 |
| XGBoost hist | 0.310 [0.222, 0.365] | 0.917 | 0.021 | 0.081 | 7.2e-05 | 0.392 | 0.571 |
| CatBoost | 0.319 [0.230, 0.378] | 0.918 | 0.021 | 0.081 | 7.4e-05 | 0.397 | 0.575 |
| Spatial CNN | 0.314 [0.228, 0.367] | 0.920 | 0.021 | 0.080 | 5.1e-05 | 0.396 | 0.579 |

### Paired 2022 AP differences versus LightGBM

| Alternative | Uncalibrated difference [95% CI] | Calibrated difference [95% CI] |
| --- | --- | --- |
| XGBoost hist | -0.006 [-0.010, -7.8e-05] | -0.006 [-0.010, +0.00018] |
| CatBoost | +0.004 [-0.001, +0.011] | +0.003 [-0.002, +0.010] |
| Spatial CNN | -0.002 [-0.006, +0.004] | -0.003 [-0.007, +0.004] |

Isotonic regression is fit on `val_calib` only after every model/configuration/epoch choice is frozen. Calibrated 2022 diagnostics reuse those fitting labels; they are not an independent calibration assessment. Isotonic ties may reduce AP while improving probability error. Calibration is then applied unchanged to test.

Only 10 observed calendar-month blocks contribute to the 2022 bootstrap. Intervals condition on fixed models and calibrators and omit training/search uncertainty. No alternative has a validation AP advantage with a paired interval entirely above zero. CatBoost is a candidate for a future pre-registered comparison on a new untouched holdout; these results do not justify changing the reported model.

## Reproduction against the private source study

The source study's recorded values are preserved in [comparison_reference.json](../artifacts/evaluation/comparison_reference.json). The measured comparison is in [comparison_reproduction.json](../artifacts/evaluation/comparison_reproduction.json). The table compares uncalibrated AP; that artifact also checks calibrated AP.

| Model | Source 2021 AP | Rerun 2021 AP | Difference | Source 2022 AP | Rerun 2022 AP | Difference |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| LightGBM (recorded) | 0.347 | 0.347 | +0.000 | 0.319 | 0.319 | -5.6e-17 |
| Logistic regression (recorded) | 0.326 | 0.326 | +0.000 | 0.286 | 0.286 | +0.000 |
| XGBoost hist | 0.345 | 0.345 | -5.6e-17 | 0.313 | 0.313 | +0.000 |
| CatBoost | 0.349 | 0.349 | -0.00022 | 0.324 | 0.323 | -0.00092 |
| Spatial CNN | 0.350 | 0.350 | -5.6e-17 | 0.317 | 0.317 | +0.000 |

AP differences beyond the 1e-6 alternative tolerance: catboost/val_select/uncalibrated: -0.00022; catboost/val_select/calibrated: -0.002; catboost/val_calib/uncalibrated: -0.00092; catboost/val_calib/calibrated: -0.002.

[CatBoost documents nondeterministic GPU floating-point summations](https://catboost.ai/docs/en/features/training-on-gpu). The measured differences above are retained rather than changing its search or selecting a rerun. The CNN uses deterministic flags and the source seed. XGBoost prediction rebuilds the train-referenced QuantileDMatrix, preserving float-level ties from selection. Selected model roundtrip differences are recorded in [comparison_roundtrip.json](../artifacts/evaluation/comparison_roundtrip.json).

### CatBoost trial-level reproduction

Training/selection arrays are byte-identical to the source study. The CatBoost binary matches the source binary. Trial parameter identity and AP differences are recorded in [comparison_determinism_audit.json](../artifacts/evaluation/comparison_determinism_audit.json). GPU floating-point reduction nondeterminism prevents exact reproduction here.

The source selected trial 1; this rerun selected trial 19. The 2022 calibrated paired interval was [-0.001, +0.010] in the source and is [-0.002, +0.010] here. The superiority conclusion did not change.

| Trial | Source AP | Rerun AP | Difference | Source trees | Rerun trees |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 0.348 | 0.348 | -0.00014 | 150 | 160 |
| 1 | 0.349 | 0.347 | -0.001 | 90 | 40 |
| 2 | 0.345 | 0.345 | -2.9e-06 | 120 | 120 |
| 3 | 0.346 | 0.347 | +0.00036 | 300 | 340 |
| 4 | 0.345 | 0.345 | +0.00037 | 250 | 270 |
| 5 | 0.346 | 0.346 | -0.00013 | 180 | 110 |
| 6 | 0.347 | 0.347 | +2.6e-06 | 160 | 160 |
| 7 | 0.344 | 0.344 | -0.00024 | 90 | 130 |
| 8 | 0.346 | 0.346 | +3.7e-05 | 380 | 320 |
| 9 | 0.347 | 0.347 | -0.00047 | 350 | 340 |
| 10 | 0.344 | 0.344 | +0.00035 | 190 | 230 |
| 11 | 0.347 | 0.347 | -7.3e-07 | 100 | 100 |
| 12 | 0.345 | 0.344 | -0.001 | 170 | 270 |
| 13 | 0.345 | 0.345 | -5.0e-06 | 400 | 400 |
| 14 | 0.343 | 0.343 | -0.00012 | 160 | 210 |
| 15 | 0.346 | 0.346 | -0.00044 | 240 | 310 |
| 16 | 0.347 | 0.346 | -0.00044 | 140 | 250 |
| 17 | 0.342 | 0.342 | -0.00041 | 60 | 30 |
| 18 | 0.347 | 0.345 | -0.001 | 100 | 100 |
| 19 | 0.349 | 0.349 | +0.00010 | 100 | 200 |

### Separate CPU reproducibility check

One CPU refit uses GPU-selected trial 19 at the fixed GPU-selected tree count 200; it fitted 200 trees in 131.0 seconds with 5.29 GiB peak RAM. Its uncalibrated selection AP is 0.347. This single refit does not establish between-fit identity. Its saved-model prediction roundtrip is exact. It is not calibrated, scored on test, or used to select a learner; the frozen GPU search remains the reference. [comparison_cpu_check.json](../artifacts/evaluation/comparison_cpu_check.json) records its settings, completion status and model hash.

## Exploratory post-hoc test: previously opened 2023-2025 holdout

This is an explicitly exploratory temporal addendum on the previously opened test, not confirmatory evidence or a new holdout. Test scores did not select models, calibrators, configurations or the recommendation. The opening was appended to [final_test_log.json](../artifacts/evaluation/final_test_log.json) with type `post_hoc_comparison` before labels were loaded. A repeated comparison opening requires `--reopen-reason`.

The test population contains 3,127,636 eligible non-held-out cell-weeks and 61,119 positives. Geographic holdout scoring is outside this temporal comparison.

| Model | Raw AP | Calibrated AP | ROC-AUC | Brier | Log loss | ECE | Recall 5% | Recall 10% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| LightGBM (recorded) | 0.328 | 0.323 | 0.928 | 0.016 | 0.062 | 0.0031 | 0.384 | 0.558 |
| Logistic regression (recorded) | 0.297 | 0.293 | 0.923 | 0.016 | 0.064 | 0.0039 | 0.370 | 0.545 |
| XGBoost hist | 0.324 | 0.319 | 0.927 | 0.016 | 0.062 | 0.0032 | 0.384 | 0.557 |
| CatBoost | 0.337 | 0.331 | 0.928 | 0.015 | 0.062 | 0.0038 | 0.387 | 0.562 |
| Spatial CNN | 0.324 | 0.319 | 0.930 | 0.016 | 0.062 | 0.0032 | 0.386 | 0.568 |

## Resource costs and environment

| Search | Configurations | Wall seconds | Peak process-tree RAM (GiB) | Minimum free disk (GiB) |
| --- | ---: | ---: | ---: | ---: |
| XGBoost hist | 20 | 189.5 | 3.38 | 94.10 |
| CatBoost | 20 | 771.7 | 4.44 | 94.03 |
| Spatial CNN | 20 | 396.2 | 5.46 | 91.50 |

| Additional stage | Wall seconds | Peak process-tree RAM (GiB) |
| --- | ---: | ---: |
| Preparation and recorded-baseline verification | 51.0 | 2.95 |
| Calibration and validation metrics | 18.8 | 3.36 |
| Validation bootstrap | 748.0 | 1.61 |
| Exploratory test scoring | 32.2 | 3.55 |

Search costs include loading and, for the CNN, initial grid construction; they exclude installation and later calibration/bootstrap. Process-tree RSS is sampled every 0.5 seconds, and mapped pages contribute. GPU searches run sequentially; the separate CPU audit overlaps part of the CNN search, so these are practical costs rather than isolated hardware benchmarks. The RAM limit is 12 GiB and the free disk reserve is 55 GiB. Recorded primary models were not retrained, so their fitting costs are not measured here.

Measured environment and hardware:

```json
{
  "environment": {
    "python": "3.13.15",
    "platform": "Windows-11-10.0.26200-SP0",
    "packages": {
      "numpy": "2.5.4",
      "pandas": "3.1.0",
      "pyarrow": "26.0.0",
      "scikit-learn": "1.9.1",
      "lightgbm": "4.7.0",
      "joblib": "1.6.0",
      "xgboost": "3.4.1",
      "catboost": "1.2.10",
      "torch": "2.11.0+cu128",
      "psutil": "7.2.2"
    }
  },
  "hardware": {
    "cpu": "AMD64 Family 26 Model 68 Stepping 0, AuthenticAMD",
    "logical_cpus": 8,
    "ram_bytes": 33408688128,
    "cuda_available": true,
    "cuda_version": "12.8",
    "cudnn_version": 91900,
    "gpu": "NVIDIA GeForce RTX 4060 Ti"
  }
}
```

## Reproduce

Run these commands from a separate `main` source checkout with the original processed panel and matching frozen primary fitted artifacts. The `live-data` branch contains exported operational data and models, not the application or complete benchmark inputs. Use a fresh checkout/cache for a new search because the selection cannot be overwritten. In that disposable checkout, archive the existing comparison outputs except `comparison_reference.json` and `comparison_provenance.json`, and start with empty comparison model/cache directories. Keep `final_test_log.json`; a reproduction of an already logged comparison requires an explicit `--reopen-reason` on its evaluate command. Install the optional dependencies; they are excluded from the core package. The project declares a platform-neutral PyTorch requirement. For the recorded GPU environment install its CUDA wheel explicitly after syncing, and preserve that wheel with `--no-sync`:

```sh
uv sync --all-extras
uv pip install torch==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128
uv run --no-sync bolivia-fire compare prepare
uv run --no-sync bolivia-fire compare train
uv run --no-sync bolivia-fire compare audit
uv run --no-sync bolivia-fire compare select
uv run --no-sync bolivia-fire compare evaluate --post-hoc-test \
  --reason "post-hoc exploratory comparison of alternative learners; does not change the reported model" \
  --reopen-reason "independent reproduction of the post-hoc comparison"
uv run --no-sync bolivia-fire compare report
uv run --no-sync ruff check .
uv run --no-sync pytest -m "not live and not local_data"
```

`compare train --family xgboost`, `--family catboost` and `--family spatial_cnn` permit separate sequential stages. `select` records family choices before calibration, then computes validation intervals and writes the immutable decision and SHA-256 sidecar. It can resume an interrupted bootstrap. No primary train/select/evaluate command is part of this workflow.

Models are stored in ignored `artifacts/models/comparison/`; regenerable arrays and grids are in ignored `artifacts/.cache/comparison/`. Durable outputs are the `artifacts/evaluation/comparison_*.json` files. Model, calibrator, panel and configuration hashes bind the logged test opening to the frozen selection.

## Evidence

All measured values in this document are generated from [comparison_validation.json](../artifacts/evaluation/comparison_validation.json), [comparison_bootstrap.json](../artifacts/evaluation/comparison_bootstrap.json), [comparison_prepare.json](../artifacts/evaluation/comparison_prepare.json), [comparison_selection.json](../artifacts/evaluation/comparison_selection.json), [comparison_inputs.json](../artifacts/evaluation/comparison_inputs.json), [comparison_reproduction.json](../artifacts/evaluation/comparison_reproduction.json), [comparison_cpu_check.json](../artifacts/evaluation/comparison_cpu_check.json), [comparison_reference.json](../artifacts/evaluation/comparison_reference.json), and [comparison_test.json](../artifacts/evaluation/comparison_test.json).
