# Evaluation protocol

The machine-readable protocol is [`configs/protocol.yaml`](../configs/protocol.yaml). This page gives the
rationale. The protocol was frozen on 2026-10-31, before any model was fitted.

## Question

How much skill remains over training-only climatology and persistence when forecasting next-week
satellite fire detections in Bolivia on future years and on geographic regions excluded from fitting,
and how much of any apparent gain depends on covariates whose historical versions were not available
at forecast issue time?

Two hypotheses are tested, and either may be rejected:

- **H1.** A model using only information available at issue time (strict variant) improves average
  precision over the better of climatology and persistence on the 2023–2025 test years.
- **H2.** Adding retrospective covariates (CHIRPS v3 rainfall, MapBiomas land cover) changes test
  average precision by less than the block-bootstrap uncertainty of the strict model.

## Target

For a 0.05° cell and an issue time `t` (Monday 00:00 UTC), the target is 1 when at least one selected
VIIRS S-NPP standard-processing detection falls in the national part of the cell during
`[t, t + 7 days)`. The target measures detections, not fires, ignitions, burned area, or official
warnings. A zero means that no selected detection occurred in a fully acquired interval; it does not
prove that no fire burned (clouds, smoke, overpass timing and small fires all reduce detection).
Intervals with any unacquired day have a missing label, never a zero.

Selection: `type = 0` (presumed vegetation fire) and confidence nominal or high. A sensitivity
analysis repeats the main evaluation with all confidence classes. FIRMS confidence classes are
detection-quality categories, not calibrated probabilities.

## Spatial unit

Cells have edges at multiples of 0.05° in WGS84 and are closed on the west and south edges. A cell is
eligible when it intersects the geoBoundaries ADM0 polygon of Bolivia with positive area. Cells are
not equal-area: each cell carries the geodesic area of its national part, and every territorial
quantity (area budgets, maps of coverage) is area-weighted. Detections outside the national polygon
are excluded from labels and features even when they fall in a border cell.

## Information availability

Three products are kept separate:

1. **Retrospective benchmark** — may use final or revised products regardless of release date.
2. **Availability-constrained simulation (strict)** — uses only FIRMS-derived features computed from
   detections acquired before `t`. Its one stated assumption is that the standard-processing (SP)
   detections used here approximate the near-real-time (NRT) detections an operational user would
   have had at `t`. The archive offers no overlap period to quantify that difference, so it is a
   limitation, not a validated equivalence.
3. **Operational forecast** — not claimed by this project.

CHIRPS v3 and MapBiomas collections were released after most of the study period and revise their
full history, so lagging them does not recreate what was known at `t`. They appear only in the
retrospective variant.

## Partitions

| Partition | Issue dates | Use |
|---|---|---|
| train | 2013-01-07 – 2020-12-31 | fitting |
| val_select | 2021 | model selection, early stopping |
| val_calib | 2022 | isotonic calibration only |
| test | 2023–2025 | final evaluation, opened once |

Rows whose target interval crosses into the next partition are purged. Twenty percent of 1° blocks,
chosen by a seeded hash, are excluded from all fitting, selection and calibration; they form a
spatiotemporal test on the test years. Santa Cruz department is reported separately.

## Models and selection

Baselines: training-only climatology per cell and week of year (leave-one-year-out for training rows)
and persistence (conditional detection frequency given recent detections). Candidates: logistic
regression and LightGBM, with a 20-configuration random search scored by average precision on
`val_select`. No neural models are planned unless these results justify them.

## Metrics and uncertainty

Primary: average precision over all eligible cell-weeks. Secondary: Brier score, log loss, ROC-AUC,
expected calibration error, and recall at 5% and 10% area budgets (per week, cells are taken in order
of predicted probability until their national area reaches the budget share of Bolivia). Confidence
intervals use block bootstraps over calendar months (temporal test) and over 1° blocks (geographic
test). Metrics undefined on a subset are reported as missing with the reason.

## Final-test access

Test metrics are produced only by the evaluation command with an explicit open flag, and each opening
is logged with the protocol hash in `artifacts/evaluation/final_test_log.json`.

## Amendments

None.
