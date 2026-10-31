# Model card

## Target and model identity

These models estimate the probability of at least one selected NASA FIRMS VIIRS S-NPP
standard-processing vegetation-fire detection during the next weekly target interval in the
national portion of a fixed grid cell in Bolivia. Issues begin Monday at midnight UTC. Primary
labels use nominal or high confidence; an incomplete target interval has a missing label.
The [dataset card](../datasets/dataset_card.md) and [protocol](../configs/protocol.yaml) define
the spatial grid, observation rules and target precisely.

| Population | Selected model | Validation AP before calibration | Validation-selected baseline |
|---|---|---|---|
| Temporal | `strict/lightgbm_08` | 0.346855 | `train_climatology` |
| Geographic | `strict_geo/lightgbm_04` | 0.326202 | `persistence` |

Closed identities (SHA256):

- `strict/lightgbm_08` model: `ed72987f3dcbb1407601ee262b0e416d06a31f047a20e2386c1c4f4e7021b65b`.
- Its isotonic calibrator: `fb3886126c1910dd28f92b49f57eff3017fad9ae385471736caf3a906262bf8e`.
- `strict_geo/lightgbm_04` model: `dfe436de916d6984f317bdb64582d0f01847c15a0ec6d0cd02b19a82ff173887`.
- Its isotonic calibrator: `7594363d551b87800b543e40a7eafaec136bf7d18e56f19ab9bb1285d9a703c4`.

Sources: [selection_strict.json](../artifacts/evaluation/selection_strict.json) and
[selection_strict_geo.json](../artifacts/evaluation/selection_strict_geo.json),
`selected_model`, `candidates`, `model_ids` and `calibrator_ids`. The evaluated identities are
also recorded in [test_strict.json](../artifacts/evaluation/test_strict.json) and the primary
entry of [final_test_log.json](../artifacts/evaluation/final_test_log.json).

## Hyperparameters and selection

Both winners are LightGBM binary classifiers. The search evaluates
20 distinct configurations with seed 7;
uncalibrated AP on `val_select` chooses the candidate and its baseline. Shared settings are a
maximum of 400 boosting rounds,
30 early-stopping rounds,
`max_bin=63` and
`num_threads=4`. Fitting uses all eligible rows
for LightGBM, without negative sampling.

| Selected model | Leaves | Learning rate | Minimum leaf rows | Feature fraction | L2 penalty |
|---|---|---|---|---|---|
| `strict/lightgbm_08` | 15 | 0.03 | 1000 | 0.7 | 10 |
| `strict_geo/lightgbm_04` | 63 | 0.03 | 200 | 0.7 | 10 |

The displayed configuration values are reconstructed from each selection artifact's recorded
seed, ordered search space and selected model index using the deterministic
[`search_parameters`](../src/bolivia_fire/models/estimators.py) routine. This reconstruction
performs no fitting. The selection JSONs record the search cap, not the fitted `best_iteration`,
so an exact fitted tree count is not asserted here. Source: the `config.lightgbm_search`, `seed`
and `selected_model` fields in the two selection files linked above.

The comparison set also includes training-only climatology, empirical persistence, and logistic
regression. The [evaluation methods](evaluation.md) describe their fitting and transforms.

## Training observations and partitions

Inputs are FIRMS detections clipped to the pinned Bolivia boundary, aggregated into cell-week
labels and histories. Exact source duplicates are removed. Clouds, smoke, sensor outages and
overpass timing affect which detections are observed. A negative label means no selected
detection in a complete target interval, not proven absence of fire.

| Partition | Issue years | Eligible cell-weeks | Role |
|---|---|---|---|
| `train` | 2013-2020 | 11,979,436 | Parameter fitting and training-only statistics |
| `val_select` | 2021 | 1,357,276 | Selection and early stopping |
| `val_calib` | 2022 | 1,180,240 | Isotonic calibration only |
| Temporal test | 2023-2025 | 3,127,636 | Non-held-out cells |
| Geographic test | 2023-2025 | 809,840 | Held-out cells |

Rows crossing temporal partition boundaries are purged. Missing labels and incomplete recent
observation histories are excluded. Geographic blocks are excluded from all fitting, selection,
calibration and climatology estimation under the frozen holdout rule. Both strict selections
use identical training and validation populations. Dates and split rules follow the
[protocol](../configs/protocol.yaml); measured counts come from `exclusions` in
[selection_strict.json](../artifacts/evaluation/selection_strict.json),
[selection_strict_geo.json](../artifacts/evaluation/selection_strict_geo.json) and
[test_strict.json](../artifacts/evaluation/test_strict.json).

## Features and calibration

The strict selection records 21 inputs:

- Cell history and recency: `count_last_7d`, `count_last_14d`, `count_last_28d`,
  `count_last_91d`, `count_last_365d`, `any_last_7d`, `days_since_last`,
  `no_prior_detection`, `frp_sum_28d`, `same_week_last_year`.
- Neighborhood history: `neighborhood_3x3_last_7d`, `neighborhood_7x7_last_7d`,
  `neighborhood_3x3_last_28d`, `neighborhood_7x7_last_28d`.
- National history: `national_last_7d`, `national_last_28d`.
- Seasonality and climatology: `week_of_year_sin`, `week_of_year_cos`,
  `train_climatology_cell`, `train_climatology_dept`.
- History completeness: `window_obs_complete_365d`.

`strict_geo` uses 20 inputs, removing only `train_climatology_cell`; its climatology
baseline uses the department statistic. Observation-derived histories end before issue time.
Training-row climatology uses leave-one-year-out cross-fitting over training years, explicitly
including later training years. Held-out labels never enter those statistics; past detections
from held-out locations may enter history features.

Each selected model has `calibration_status: fitted`. Isotonic regression is fitted only on
`val_calib`; candidate test metrics use its calibrated probabilities. The primary baseline
retains its uncalibrated probabilities. Calibration does not guarantee calibration in later
years or other locations. Sources: `features` and selected-candidate `calibration_status` in
[selection_strict.json](../artifacts/evaluation/selection_strict.json) and
[selection_strict_geo.json](../artifacts/evaluation/selection_strict_geo.json).

The separate retrospective selection adds `rain_sum_7d`, `rain_sum_30d`, `rain_sum_90d`,
`dry_days_30d` and `rain_anomaly_90d` from CHIRPS. These historical vintages were not necessarily
available at issue time. No MapBiomas features occur in the selected retrospective input list.
Source: [selection_retrospective.json](../artifacts/evaluation/selection_retrospective.json), `features`.

## Evaluation

The primary recorded evaluation gives:

| Metric | Temporal strict winner | Geographic strict_geo winner |
|---|---|---|
| AP | 0.323393 | 0.383912 |
| AP 95% CI | [0.221655, 0.382973] | [0.250128, 0.467940] |
| Brier score | 0.015533 | 0.010553 |
| Log loss | 0.062067 | 0.043102 |
| ROC-AUC | 0.927621 | 0.944192 |
| ECE | 0.003132 | 0.000917 |
| Recall at 5% area | 0.384318 | 0.469404 |
| Recall at 10% area | 0.557699 | 0.672829 |

AP is the primary metric. ECE uses the protocol's equal-frequency bins. Recall budgets apply to
eligible geodesic cell area per week, with fractional crossing-cell contributions. The AP
intervals use 500 block-bootstrap replicates, resampling calendar
months for the temporal population and geographic blocks for the spatial population.
Source: [test_strict.json](../artifacts/evaluation/test_strict.json), `models`,
`geographic_holdout` and `bootstrap`. See [results](results.md) for paired hypotheses,
annual and Santa Cruz breakdowns, calibration, exclusion counts, figures and sensitivity status.

## Intended and unsupported uses

Intended uses are a research benchmark, reproducible comparison of detection-probability models,
and retrospective estimates within the documented target and populations.

Unsupported uses include operational warnings or emergency response, estimation of fire-event
counts or burned area, and decisions affecting people or property. The outputs do not identify
ignitions, individual fire events, causes or damages.

## Known limitations

- Detection omissions and false positives differ across environments and observation conditions;
  the outage completeness rule does not establish cloud-free coverage.
- Strict histories assume standard-processing observations approximate the near-real-time
  observations available at issue time. This equivalence has not been validated.
- The cross-fitted climatology exception is a training statistic, not a fully causal historical
  replay. Historical detections from held-out locations remain allowed inputs.
- CHIRPS is retrospective; H2 applies to the tested input set and frozen interval rule, not to
  all weather information. MapBiomas was omitted after verified raster access failed.
- Missing labels and recent-history exclusions change the evaluation population and its annual
  composition. Reported performance does not cover excluded weeks or unsupported locations.
- Geographic cells differ in area; the boundary snapshot excludes large interior salt flats and
  lakes. Calibration and ranking may shift with future seasons, sensors or source revisions.

## Licences

Code is [MIT licensed](../LICENSE). Source observations retain their own terms:

- [FIRMS](../datasets/licenses/FIRMS.md): NASA open-data reuse basis with source attribution;
  no Creative Commons licence is asserted for the detections.
- [Bolivia geoBoundaries sources](../datasets/licenses/geoBoundaries.md): gbOpen BOL ADM0/ADM1,
  revision `9469f09`; both official `boundaryLicense` fields are `Public Domain`, verified
  2026-10-31. Retain GeoBolivia, geoBoundaries and Runfola et al. (2020) attribution.
- [CHIRPS](../datasets/licenses/CHIRPS.md): Creative Commons Attribution 4.0 International,
  with the provider's stated waiver. Credit Climate Hazards Center, CHIRPS3 Data Repository
  (2025), final daily `rnl` and ERA5 disaggregation; identify native-pixel mapping to cell
  centers and antecedent-rainfall aggregation when redistributing derived inputs.
- [MapBiomas](../datasets/licenses/MapBiomas.md): Creative Commons Attribution 4.0 International;
  no MapBiomas inputs are included in these fitted models.

The code licence does not replace source-specific terms. Preserve source pins, attribution and
processing descriptions when redistributing derived inputs or outputs.
