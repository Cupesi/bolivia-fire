# Evaluation results

The primary final-test opening was `2026-10-31T10:30:00.578581+00:00`, recorded with `reopening: false` in
[final_test_log.json](../artifacts/evaluation/final_test_log.json). Later sensitivity openings
are logged separately with their reasons; they do not replace the primary results below.

Measured values come from [test_strict.json](../artifacts/evaluation/test_strict.json), the
[closed selections](../artifacts/evaluation/selection_strict.json), and
[results_summary.json](../artifacts/figures/results_summary.json). Metrics and interval endpoints
are rounded to six decimals; counts are exact. AP means average precision over eligible
cell-weeks, rather than a mean of annual scores.

## Protocol and comparator

The [frozen protocol](../configs/protocol.yaml) defines a weekly binary target: at least one
selected VIIRS S-NPP standard-processing vegetation-fire detection in the national portion of a
cell. Primary labels use nominal or high confidence. Incomplete target weeks have null labels;
ordinary eligibility also requires complete recent observation history.

Model choice and early stopping use `val_select`; isotonic calibration uses `val_calib` only.
The best baseline and fitted candidate are selected by **uncalibrated validation AP**. Primary
test comparisons use **uncalibrated baselines versus calibrated candidates**. Calibrated-baseline
comparisons are a separate sensitivity result, not the primary comparator.

The temporal winner is `strict/lightgbm_08` and its comparator is
`train_climatology`; the geographic winner is `strict_geo/lightgbm_04` and its
comparator is `persistence`. These choices were closed in
[selection_strict.json](../artifacts/evaluation/selection_strict.json) and
[selection_strict_geo.json](../artifacts/evaluation/selection_strict_geo.json).

The test issue years are 2023-2025.
Temporal intervals resample the 28 represented calendar-month blocks;
geographic intervals resample 24 represented one-degree blocks.
Both use 500 seeded replicates and 95% percentile intervals.
Paired comparisons use the same rows and block multiplicities. Source:
[test_strict.json](../artifacts/evaluation/test_strict.json), `bootstrap` and `candidate_bootstrap`.

## Temporal test

The temporal population contains 3,127,636 eligible cell-weeks outside held-out blocks.
The table follows the primary probability rule above; losses and ECE are better when smaller.

| Model (probabilities) | AP [95% CI] | Brier | Log loss | ROC-AUC | ECE |
|---|---|---|---|---|---|
| `train_climatology` (uncalibrated) | 0.166677 [0.105385, 0.214274] | 0.017580 | 0.107625 | 0.829648 | 0.007013 |
| `persistence` (uncalibrated) | 0.143290 [0.085345, 0.186614] | 0.017225 | 0.082888 | 0.648148 | 0.010927 |
| `logistic_regression` (calibrated) | 0.293042 [0.195122, 0.353043] | 0.015900 | 0.063911 | 0.923338 | 0.003878 |
| `lightgbm_08` (calibrated) | 0.323393 [0.221655, 0.382973] | 0.015533 | 0.062067 | 0.927621 | 0.003132 |

Source: [test_strict.json](../artifacts/evaluation/test_strict.json), `models` and
`candidate_bootstrap.temporal`; the AP values and intervals are also copied in
[results_summary.json](../artifacts/figures/results_summary.json), `figures.temporal`.

![Temporal test AP and paired differences](assets/charts/temporal_ap.gif)

Temporal-test AP and paired intervals reveal the recorded models from `artifacts/evaluation/test_strict.json`; the best baseline remains validation-selected.

## H1 and H2

| Hypothesis | Paired AP comparison | 95% interval | Frozen decision |
|---|---|---|---|
| H1 | Calibrated strict winner minus uncalibrated climatology | [0.099838, 0.217886] | Supported: interval entirely above zero |
| H2 | Calibrated retrospective winner minus calibrated strict winner | [-0.004107, 0.001889] | Supported: interval contains zero |

H1's recorded AP difference is 0.156716,
derived in the figure summary by subtracting recorded APs on identical support. H2 compares
`retrospective/lightgbm_16` with `strict/lightgbm_08` on
3,127,636 paired rows. Its point estimate is
not recorded, so only the interval is reported. H2's rule is not an equivalence test and does not
establish an absence of useful rainfall information. The retrospective addition is CHIRPS
rainfall; the selected feature list contains no MapBiomas inputs.

Sources: [test_strict.json](../artifacts/evaluation/test_strict.json), `hypotheses` and
`strict_retrospective_bootstrap`;
[selection_retrospective.json](../artifacts/evaluation/selection_retrospective.json), `features`;
[results_summary.json](../artifacts/figures/results_summary.json), `figures.temporal` and `figures.h2`.

![H2 retrospective-minus-strict paired AP interval](assets/charts/h2_interval.gif)

The H2 animation shows the recorded paired interval from `strict_retrospective_bootstrap`; it supplies no unrecorded point estimate.

## Geographic holdout

The geographic test contains 809,840 eligible cell-weeks in blocks excluded
from fitting, selection and calibration. It uses `strict_geo`, which removes cell climatology
and retains department climatology. Historical detections from held-out locations may still
contribute history features under the frozen information rule.

| Model (probabilities) | AP [95% CI] | Brier | Log loss | ROC-AUC | ECE |
|---|---|---|---|---|---|
| `train_climatology` (uncalibrated) | 0.070585 [0.030861, 0.134639] | 0.013395 | 0.061348 | 0.850038 | 0.002280 |
| `persistence` (uncalibrated) | 0.180757 [0.102250, 0.243008] | 0.012134 | 0.062331 | 0.671958 | 0.009641 |
| `logistic_regression` (calibrated) | 0.350909 [0.218061, 0.436205] | 0.010939 | 0.045585 | 0.940450 | 0.002792 |
| `lightgbm_04` (calibrated) | 0.383912 [0.250128, 0.467940] | 0.010553 | 0.043102 | 0.944192 | 0.000917 |

The calibrated geographic winner's paired AP-gain interval against uncalibrated persistence is
[0.145437, 0.232513].
This population differs from the temporal test, so its higher AP is not evidence that spatial
transfer is easier.

Source: [test_strict.json](../artifacts/evaluation/test_strict.json), `geographic_holdout` and
`candidate_bootstrap.geographic`; [results_summary.json](../artifacts/figures/results_summary.json),
`figures.geographic`.

![Geographic holdout AP and paired differences](assets/charts/geographic_ap.gif)

Geographic AP and paired intervals use the recorded strict_geo population and spatial-block intervals in `artifacts/evaluation/test_strict.json`.

## Per-year results and Santa Cruz

Annual temporal AP uses the same models and probability versions as the pooled temporal table;
no model is reselected within a year.

| Issue year | Eligible rows | Climatology AP | Persistence AP | Logistic AP | LightGBM AP |
|---|---|---|---|---|---|
| 2023 | 1,239,252 | 0.208612 | 0.155952 | 0.332315 | 0.346671 |
| 2024 | 678,638 | 0.099630 | 0.208314 | 0.318806 | 0.373736 |
| 2025 | 1,209,746 | 0.166328 | 0.083709 | 0.228523 | 0.246341 |

Santa Cruz contains 1,095,934 eligible temporal-test
cell-weeks. Its area-budget denominator is the eligible national cell area within the department
for each issue week.

| Model (probabilities) | AP | Brier | ECE | Recall at 5% area | Recall at 10% area |
|---|---|---|---|---|---|
| `train_climatology` (uncalibrated) | 0.161291 | 0.022136 | 0.011712 | 0.215629 | 0.353170 |
| `persistence` (uncalibrated) | 0.201772 | 0.020462 | 0.012715 | 0.294971 | 0.366773 |
| `logistic_regression` (calibrated) | 0.332745 | 0.019132 | 0.004489 | 0.372504 | 0.557984 |
| `lightgbm_08` (calibrated) | 0.380798 | 0.018364 | 0.003777 | 0.388074 | 0.572590 |

Source: [test_strict.json](../artifacts/evaluation/test_strict.json), `per_year` and `santa_cruz`.
Annual differences reflect the evaluated populations, including observation exclusions; the
artifact supplies no annual or Santa Cruz bootstrap intervals. The temporal comparator remains
the validation-selected climatology even where another baseline scores higher in a subset.

![Temporal AP by issue year](assets/charts/per_year_ap.gif)

Annual AP uses the three recorded issue-year subsets in `artifacts/evaluation/test_strict.json`, with no annual model reselection.

## Calibration

ECE uses ten equal-frequency bins under the [protocol](../configs/protocol.yaml). The following
values compare each model before and after isotonic calibration. Baseline calibration is shown
for sensitivity only; primary baseline comparisons retain their original probabilities.

| Population and model | ECE uncalibrated | ECE calibrated |
|---|---|---|
| Temporal: `train_climatology` | 0.007013 | 0.008773 |
| Temporal: `persistence` | 0.010927 | 0.010346 |
| Temporal: `logistic_regression` | 0.002279 | 0.003878 |
| Temporal: `lightgbm_08` | 0.003985 | 0.003132 |
| Geographic: `train_climatology` | 0.002280 | 0.006658 |
| Geographic: `persistence` | 0.009641 | 0.010429 |
| Geographic: `logistic_regression` | 0.002803 | 0.002792 |
| Geographic: `lightgbm_04` | 0.010395 | 0.000917 |

Calibration lowers the selected LightGBM's pooled ECE in both populations, but does not improve
every model or every annual subset. In particular, temporal logistic-regression ECE increases.
Calibration is fitted only on the calibration partition; these test values do not guide fitting.

Source: [test_strict.json](../artifacts/evaluation/test_strict.json), `models` and
`geographic_holdout.models`; plotted reliability-bin values are copied in
[results_summary.json](../artifacts/figures/results_summary.json), `figures.reliability`.

The reliability figure uses the temporal test population only; its second panel
zooms into low predicted probabilities.

![Temporal-test reliability curves with a low-probability zoom panel](assets/charts/reliability.gif)

Recorded temporal reliability bins accumulate from `artifacts/evaluation/test_strict.json`; the detail panel omits bins outside its stated range.

## Recall at area budgets

For each issue week, cells are ranked by probability and accumulated to the budget share of the
eligible population's national cell area. The crossing cell contributes fractionally; selected
positive contributions are pooled across weeks. Equal probabilities follow input row order.
These are shares of positive cell-weeks captured, not shares of detected fires or burned area.

| Population and model (probabilities) | Recall at 5% area | Recall at 10% area |
|---|---|---|
| Temporal: `train_climatology` (uncalibrated) | 0.251441 | 0.397374 |
| Temporal: `persistence` (uncalibrated) | 0.265362 | 0.322522 |
| Temporal: `logistic_regression` (calibrated) | 0.369967 | 0.544833 |
| Temporal: `lightgbm_08` (calibrated) | 0.384318 | 0.557699 |
| Geographic: `train_climatology` (uncalibrated) | 0.103675 | 0.121960 |
| Geographic: `persistence` (uncalibrated) | 0.299788 | 0.361417 |
| Geographic: `logistic_regression` (calibrated) | 0.452851 | 0.651184 |
| Geographic: `lightgbm_04` (calibrated) | 0.469404 | 0.672829 |

Source: [test_strict.json](../artifacts/evaluation/test_strict.json),
`recall_at_area_budget_5pct` and `recall_at_area_budget_10pct` in each population;
[results_summary.json](../artifacts/figures/results_summary.json), `figures.area_budget`.

![Recall at eligible area budgets](assets/charts/area_budget.gif)

Area-budget recall uses the recorded temporal and geographic summaries in `artifacts/evaluation/test_strict.json` at 5% and 10% of eligible area.

## Example spatial distribution

The map is a descriptive example for issue `2024-09-02`, with target ending
`2024-09-09` (exclusive). It was chosen as the largest observed
detection total among complete target weeks in the example year with national prediction
coverage; ties choose the earliest issue date. Predictions use the strict model outside held-out
blocks and the geographic model inside them. The observed panel displays satellite detections,
not fire-event counts. This selected example is not an additional performance test.

Source: [results_summary.json](../artifacts/figures/results_summary.json), `figures.map`, including
the recorded selection rule, input provenance and figure caption.

![Example weekly predictions and observed satellite detections](assets/charts/fire_season_2024.gif)

The July?October 2024 map sequence joins the strict test prediction export to panel observations, retaining partial counts, null labels and unavailable estimates.

## Exclusions

Counts follow the sequential eligibility filters. `Wrong geography` means held-out rows for
fitting and temporal evaluation, and non-held-out rows for geographic evaluation. `Purged` is an
independent count across the panel, repeated in each report; `Scanned` includes that count and
the requested partition. Do not sum these repeated counts across partitions.

| Population | Scanned | Purged | Missing label | Wrong geography | Incomplete recent history | Eligible |
|---|---|---|---|---|---|---|
| train | 15,564,174 | 111,438 | 74,292 | 3,162,960 | 236,048 | 11,979,436 |
| val_select | 2,005,884 | 111,438 | 37,146 | 382,000 | 118,024 | 1,357,276 |
| val_calib | 2,005,884 | 111,438 | 148,584 | 359,080 | 206,542 | 1,180,240 |
| Temporal test | 5,943,360 | 111,438 | 557,190 | 1,084,880 | 1,062,216 | 3,127,636 |
| Geographic test | 5,943,360 | 111,438 | 557,190 | 4,189,852 | 275,040 | 809,840 |

The primary run has `exclude_low_coverage: false`; its `target_low_coverage` exclusion counter
is 0, which reports no exclusion by that optional filter,
not absence of flagged weeks. All primary table models report
0 missing probabilities on their
eligible populations. Paired temporal and geographic comparisons contain
3,127,636 and 809,840
rows respectively.

Sources: [selection_strict.json](../artifacts/evaluation/selection_strict.json), `exclusions`;
[selection_strict_geo.json](../artifacts/evaluation/selection_strict_geo.json), matching fitting
and validation eligibility; [test_strict.json](../artifacts/evaluation/test_strict.json),
`exclusions`, model `missing_probability` and bootstrap `paired_rows`.

## Sensitivity analyses

The pre-registered runs below supplement the primary evaluation. Results remain pending until
each named evaluation file exists and contains a completed report. Closed selection artifacts
and access-log entries alone do not establish sensitivity results. Every later final-test
opening must appear as a reopening with its reason in
[final_test_log.json](../artifacts/evaluation/final_test_log.json).

| Run | Change | Expected evaluation file | Status |
|---|---|---|---|
| All confidence classes | Use all FIRMS confidence classes for the target, retaining primary-selection history features | `artifacts/evaluation/test_strict_all_confidence.json` | Pending |
| Exclude low coverage | Exclude rows flagged `target_low_coverage` | `artifacts/evaluation/test_strict_exclude_low_coverage.json` | Pending |
| Neighborhood ablation | Remove neighborhood history features | `artifacts/evaluation/test_strict_drop_neighborhood.json` | Pending |
| Climatology ablation | Remove cell and department climatology features | `artifacts/evaluation/test_strict_drop_climatology.json` | Pending |
| National ablation | Remove national history features | `artifacts/evaluation/test_strict_drop_national.json` | Pending |

Each run includes its corresponding geographic feature set under the same selection and
calibration procedure. The already recorded calibrated-baseline sensitivity is separate from
these pending runs and remains in `test_strict.json` under `calibrated_baseline_sensitivity`.
Primary tables and figures above continue to describe the original primary report.
