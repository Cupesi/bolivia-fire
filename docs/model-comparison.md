# Post-hoc comparison of alternative learners

This experiment checks whether the benchmark's conclusion is robust to the learner choice. It retains the published LightGBM model. The private source study and this repository rerun were designed after the primary test had been opened; all results here are exploratory. The first primary opening was 2026-10-31T10:30:00.578581+00:00.

The validation decision was frozen at 2026-10-31T19:35:50.785313+00:00, before its exploratory scoring began at 2026-10-31T19:36:43.128562+00:00. The decision is in [comparison_selection.json](../artifacts/evaluation/comparison_selection.json), SHA-256 `338e530a9eea33b4f7bd559c258c572ec1892905a6d92a58079b6ea89e5a0e00`. It uses validation evidence only.

## Models and information

XGBoost hist tests a different histogram tree implementation and depth-wise growth. CatBoost tests symmetric trees and regularization; no categorical predictors were added. The spatial CNN tests learned directional local interactions using the same strict feature types on weekly national grids. Neighboring values expand spatial context beyond each tabular row. LightGBM and logistic regression are read from the recorded selection artifact and fitted model files without refitting.

All models use the benchmark target, partitions, boundary purges, geographic holdout exclusion and `window_obs_complete_28d` eligibility through the repository's `PanelStore`. The 365-day completeness flag remains a feature. Held-out locations may supply past-feature context to the CNN; their labels never enter normalization, training, selection or calibration. Outside-country inputs are zero after train-only log1p transformation, imputation and scaling. Ineligible grid targets are masked.

| Partition | Eligible rows | Positives | Prevalence |
| --- | ---: | ---: | ---: |
| train | 11,979,436 | 255,339 | 0.021315 |
| val_select | 1,357,276 | 30,302 | 0.022326 |
| val_calib | 1,180,240 | 31,110 | 0.026359 |

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
| LightGBM (recorded) | 0.346855 [0.206830, 0.399072] | 0.929182 | 0.017539 | 0.069103 | 0.004706 | 0.385886 | 0.566827 |
| Logistic regression (recorded) | 0.326496 [0.182567, 0.381861] | 0.924399 | 0.017694 | 0.069925 | 0.002513 | 0.373706 | 0.551266 |
| XGBoost hist | 0.345404 [0.206813, 0.399745] | 0.928937 | 0.017621 | 0.069558 | 0.005613 | 0.385106 | 0.569674 |
| CatBoost | 0.348614 [0.211545, 0.404176] | 0.929784 | 0.017380 | 0.067864 | 0.002020 | 0.390153 | 0.571446 |
| Spatial CNN | 0.350462 [0.211483, 0.404733] | 0.932444 | 0.017434 | 0.067418 | 0.003659 | 0.389975 | 0.576957 |

### 2022 calibration, uncalibrated

| Model | AP [95% CI] | ROC-AUC | Brier | Log loss | ECE | Recall 5% | Recall 10% |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| LightGBM (recorded) | 0.319169 [0.227738, 0.374718] | 0.918242 | 0.021261 | 0.082471 | 0.006673 | 0.394379 | 0.573101 |
| Logistic regression (recorded) | 0.286024 [0.195610, 0.344419] | 0.912998 | 0.021728 | 0.084373 | 0.004822 | 0.380808 | 0.562342 |
| XGBoost hist | 0.313248 [0.225110, 0.368256] | 0.917260 | 0.021391 | 0.083462 | 0.008239 | 0.392232 | 0.570577 |
| CatBoost | 0.323372 [0.234115, 0.382338] | 0.918117 | 0.021039 | 0.081529 | 0.004043 | 0.396598 | 0.574931 |
| Spatial CNN | 0.317202 [0.231040, 0.371167] | 0.919792 | 0.021231 | 0.082347 | 0.007302 | 0.395960 | 0.577203 |

### 2022 calibration, isotonic calibrated

| Model | AP [95% CI] | ROC-AUC | Brier | Log loss | ECE | Recall 5% | Recall 10% |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| LightGBM (recorded) | 0.316166 [0.224760, 0.371130] | 0.918423 | 0.020989 | 0.080765 | 0.000071 | 0.395651 | 0.573677 |
| Logistic regression (recorded) | 0.282937 [0.193392, 0.340900] | 0.913217 | 0.021526 | 0.082922 | 0.000113 | 0.381270 | 0.563389 |
| XGBoost hist | 0.310469 [0.221893, 0.364531] | 0.917493 | 0.021039 | 0.081067 | 0.000072 | 0.392198 | 0.571058 |
| CatBoost | 0.319312 [0.230376, 0.377624] | 0.918329 | 0.020959 | 0.080764 | 0.000074 | 0.396794 | 0.574717 |
| Spatial CNN | 0.313585 [0.227954, 0.367153] | 0.920003 | 0.020994 | 0.080471 | 0.000051 | 0.396492 | 0.578875 |

### Paired 2022 AP differences versus LightGBM

| Alternative | Uncalibrated difference [95% CI] | Calibrated difference [95% CI] |
| --- | --- | --- |
| XGBoost hist | -0.005921 [-0.009516, -0.000078] | -0.005697 [-0.009951, +0.000178] |
| CatBoost | +0.004203 [-0.001047, +0.011021] | +0.003145 [-0.001901, +0.009632] |
| Spatial CNN | -0.001967 [-0.006205, +0.003870] | -0.002581 [-0.007026, +0.003533] |

Isotonic regression is fit on `val_calib` only after every model/configuration/epoch choice is frozen. Calibrated 2022 diagnostics reuse those fitting labels; they are not an independent calibration assessment. Isotonic ties may reduce AP while improving probability error. Calibration is then applied unchanged to test.

Only 10 observed calendar-month blocks contribute to the 2022 bootstrap. Intervals condition on fixed models and calibrators and omit training/search uncertainty. No alternative has a validation AP advantage with a paired interval entirely above zero. CatBoost is a candidate for a future pre-registered comparison on a new untouched holdout; these results do not justify changing the reported model.

## Reproduction against the private source study

The source study's recorded values are preserved in [comparison_reference.json](../artifacts/evaluation/comparison_reference.json). The measured comparison is in [comparison_reproduction.json](../artifacts/evaluation/comparison_reproduction.json). The table compares uncalibrated AP; that artifact also checks calibrated AP.

| Model | Source 2021 AP | Rerun 2021 AP | Difference | Source 2022 AP | Rerun 2022 AP | Difference |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| LightGBM (recorded) | 0.346854928 | 0.346854928 | +0.000e+00 | 0.319169022 | 0.319169022 | -5.551e-17 |
| Logistic regression (recorded) | 0.326495523 | 0.326495523 | +0.000e+00 | 0.286024306 | 0.286024306 | +0.000e+00 |
| XGBoost hist | 0.345404347 | 0.345404347 | -5.551e-17 | 0.313248133 | 0.313248133 | +0.000e+00 |
| CatBoost | 0.348835811 | 0.348614079 | -2.217e-04 | 0.324293156 | 0.323371885 | -9.213e-04 |
| Spatial CNN | 0.350462066 | 0.350462066 | -5.551e-17 | 0.317202114 | 0.317202114 | +0.000e+00 |

AP differences beyond the 1e-6 alternative tolerance: catboost/val_select/uncalibrated: -2.217e-04; catboost/val_select/calibrated: -1.922e-03; catboost/val_calib/uncalibrated: -9.213e-04; catboost/val_calib/calibrated: -1.846e-03.

[CatBoost documents nondeterministic GPU floating-point summations](https://catboost.ai/docs/en/features/training-on-gpu). The measured differences above are retained rather than changing its search or selecting a rerun. The CNN uses deterministic flags and the source seed. XGBoost prediction rebuilds the train-referenced QuantileDMatrix, preserving float-level ties from selection. Selected model roundtrip differences are recorded in [comparison_roundtrip.json](../artifacts/evaluation/comparison_roundtrip.json).

### CatBoost trial-level reproduction

Training/selection arrays are byte-identical to the source study. The CatBoost binary matches the source binary. Trial parameter identity and AP differences are recorded in [comparison_determinism_audit.json](../artifacts/evaluation/comparison_determinism_audit.json). GPU floating-point reduction nondeterminism prevents exact reproduction here.

The source selected trial 1; this rerun selected trial 19. The 2022 calibrated paired interval was [-0.001438, +0.010196] in the source and is [-0.001901, +0.009632] here. The superiority conclusion did not change.

| Trial | Source AP | Rerun AP | Difference | Source trees | Rerun trees |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 0.347661656 | 0.347518760 | -1.429e-04 | 150 | 160 |
| 1 | 0.348835811 | 0.347382186 | -1.454e-03 | 90 | 40 |
| 2 | 0.345147904 | 0.345145044 | -2.860e-06 | 120 | 120 |
| 3 | 0.346171609 | 0.346532632 | +3.610e-04 | 300 | 340 |
| 4 | 0.344811744 | 0.345186440 | +3.747e-04 | 250 | 270 |
| 5 | 0.345997240 | 0.345863169 | -1.341e-04 | 180 | 110 |
| 6 | 0.347453908 | 0.347456511 | +2.603e-06 | 160 | 160 |
| 7 | 0.343902007 | 0.343657716 | -2.443e-04 | 90 | 130 |
| 8 | 0.345694191 | 0.345731676 | +3.749e-05 | 380 | 320 |
| 9 | 0.347072738 | 0.346606307 | -4.664e-04 | 350 | 340 |
| 10 | 0.344020160 | 0.344370860 | +3.507e-04 | 190 | 230 |
| 11 | 0.346813794 | 0.346813068 | -7.256e-07 | 100 | 100 |
| 12 | 0.344563110 | 0.343514703 | -1.048e-03 | 170 | 270 |
| 13 | 0.344708779 | 0.344703780 | -4.999e-06 | 400 | 400 |
| 14 | 0.343139870 | 0.343015402 | -1.245e-04 | 160 | 210 |
| 15 | 0.346107897 | 0.345665879 | -4.420e-04 | 240 | 310 |
| 16 | 0.346888804 | 0.346450792 | -4.380e-04 | 140 | 250 |
| 17 | 0.342134980 | 0.341720153 | -4.148e-04 | 60 | 30 |
| 18 | 0.346532579 | 0.345274957 | -1.258e-03 | 100 | 100 |
| 19 | 0.348513805 | 0.348614079 | +1.003e-04 | 100 | 200 |

### Separate CPU reproducibility check

One CPU refit uses GPU-selected trial 19 at the fixed GPU-selected tree count 200; it fitted 200 trees in 131.0 seconds with 5.29 GiB peak RAM. Its uncalibrated selection AP is 0.347266. This single refit does not establish between-fit identity. Its saved-model prediction roundtrip is exact. It is not calibrated, scored on test, or used to select a learner; the frozen GPU search remains the reference. [comparison_cpu_check.json](../artifacts/evaluation/comparison_cpu_check.json) records its settings, completion status and model hash.

## Exploratory post-hoc test: previously opened 2023-2025 holdout

This is an explicitly exploratory temporal addendum on the previously opened test, not confirmatory evidence or a new holdout. Test scores did not select models, calibrators, configurations or the recommendation. The opening was appended to [final_test_log.json](../artifacts/evaluation/final_test_log.json) with type `post_hoc_comparison` before labels were loaded. A repeated comparison opening requires `--reopen-reason`.

The test population contains 3,127,636 eligible non-held-out cell-weeks and 61,119 positives. Geographic holdout scoring is outside this temporal comparison.

| Model | Raw AP | Calibrated AP | ROC-AUC | Brier | Log loss | ECE | Recall 5% | Recall 10% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| LightGBM (recorded) | 0.327991 | 0.323393 | 0.927621 | 0.015533 | 0.062067 | 0.003132 | 0.384318 | 0.557699 |
| Logistic regression (recorded) | 0.297390 | 0.293042 | 0.923338 | 0.015900 | 0.063911 | 0.003878 | 0.369967 | 0.544833 |
| XGBoost hist | 0.323951 | 0.318529 | 0.926856 | 0.015570 | 0.062220 | 0.003249 | 0.383617 | 0.557492 |
| CatBoost | 0.336571 | 0.330587 | 0.928048 | 0.015483 | 0.062047 | 0.003805 | 0.386857 | 0.561787 |
| Spatial CNN | 0.323695 | 0.318591 | 0.929615 | 0.015554 | 0.061903 | 0.003246 | 0.386150 | 0.567649 |

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

Use a local checkout of the live-data branch with the processed panel and frozen primary fitted artifacts. Create a fresh checkout/cache for a new search because the selection cannot be overwritten. In that disposable checkout, archive the existing comparison outputs except `comparison_reference.json` and `comparison_provenance.json`, and start with empty comparison model/cache directories. Keep `final_test_log.json`; a reproduction of an already logged comparison requires an explicit `--reopen-reason` on its evaluate command. Install the optional dependencies; they are excluded from the core package. The project declares a platform-neutral PyTorch requirement. For the recorded GPU environment install its CUDA wheel explicitly after syncing, and preserve that wheel with `--no-sync`:

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
