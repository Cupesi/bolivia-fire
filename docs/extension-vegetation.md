# Retrospective vegetation and land-cover extension

The contract is [extension_vegetation.yaml](../configs/extension_vegetation.yaml),
version 1.1, dated 2026-11-01, before evaluation of the new feature groups.
It supplements the frozen [protocol 1.1](../configs/protocol.yaml). It defines
an experiment, makes no promise of improvement, and does not replace the
published or deployed model.

## Registration before fitting or metrics

[extension_registration.json](../artifacts/evaluation/extension_registration.json)
records the UTC registration timestamp, SHA-256 of the exact amended YAML,
this document, frozen parent protocol and experiments configuration, the ten
planned fits, and the statement that no extension labels or metrics have been
accessed in this amendment and registration task. This attestation does not
replace the separate audit of external confirmation-window access history.
The coordinator must commit the exact YAML, document and registration record
together before any extension training, selection, early stopping,
calibration, validation metric or other label access. Registration is prepared;
this document does not assert that the coordinator commit already exists.

Experiment code must fail closed before loading labels or starting any of
those operations if the record is absent, malformed, lacks the no-access
attestation, or has a hash differing from the current YAML, document, parent
protocol or experiments file. It must also verify a retained Git commit
containing the exact YAML, document and registration blobs, and match the
requested run's features, lag, geography and comparators to the manifest.
Unlisted runs, unavailable groups, failed preflight or missing commit proof
must refuse execution. There is no automatic registration or override.
Demonstrated refusal for a missing record, changed YAML and uncommitted record
is a prerequisite to experiment execution; this contract amendment does not
verify the implementation of that guard.

Keep selected model/calibrator hashes and scores in separate selection and
evaluation artifacts linked to the registration and commit. Any substantive
amendment requires a new dated version and committed registration before new
fits or metrics. Retain exact prior YAML/document revisions, registrations,
selections and all their scores; preserve the prior registration under its
contract hash before replacing the current record. The YAML records the
version 1.0 hashes reviewed in V13. Do not write a self-referential contract
hash into the YAML.

## Questions and decision

| Hypothesis | Addition to the published retrospective baseline | Availability condition |
| --- | --- | --- |
| H3 | MOD13A2 NDVI/EVI condition, changes, seasonal anomalies, quality and age | Vegetation acquisition and provenance pass |
| H4 | Five MapBiomas level-1 land-cover fractions | Separate zero-cost/access/legend/licence verification and feature build pass; currently `not_evaluated` |
| H5 | Both groups | Both groups pass; currently `not_evaluated` |

For each available hypothesis, the primary extension decision uses calibrated
average precision (AP) on the common non-held-out `val_calib` rows. Subtract
the published retrospective model's calibrated AP from the extension's AP.
Use 500 paired calendar year-month block-bootstrap replicates, NumPy
`default_rng(7)`, and the 2.5th/97.5th percentiles, following the existing
bootstrap implementation. Models share rows and resampled month multiplicities.
Report represented months and valid replicates; jointly undefined AP draws are
omitted. Empty or single-class populations and intervals with no valid draws
are inconclusive. Only a finite interval entirely above zero supports
"adds information"; every other result is inconclusive/no detected gain,
never equivalence. Intervals are marginal for each hypothesis; there is no
family-wise superiority claim and no selection among groups using these scores.

This is a validation decision, not independent confirmation. Isotonic
calibrators fit these same 2022 labels, and the intervals condition on fixed
models and calibrators, omitting training/search uncertainty. Report raw AP
and the secondary parent-protocol metrics as diagnostics. A positive validation
decision alone cannot justify a future model proposal.

## Published comparator and estimation

The temporal comparator is exactly `retrospective/lightgbm_16`, with its saved
calibrator and ordered 26-feature list in
[selection_retrospective.json](../artifacts/evaluation/selection_retrospective.json).
It contains the 21 strict FIRMS features and five frozen CHIRPS inputs:
`rain_sum_7d`, `rain_sum_30d`, `rain_sum_90d`, `dry_days_30d`, and
`rain_anomaly_90d`. No MapBiomas feature occurs in that selection. Verify the
selection, model, calibrator and parent-configuration hashes pinned in the
extension contract; do not refit or recalibrate this comparator. Report the
saved `strict/lightgbm_08` on the same rows for context. The geographic
counterparts are `retrospective_geo/lightgbm_04` and
`strict_geo/lightgbm_04`, with their own pinned selection, model and calibrator
hashes. The YAML lists each comparator's exact ordered feature names.

The following ordered groups define the compared feature sets; concatenation
means exactly the listed order without inferred or additional features:

- `T` (21 temporal strict inputs): `count_last_7d`, `count_last_14d`,
  `count_last_28d`, `count_last_91d`, `count_last_365d`, `any_last_7d`,
  `days_since_last`, `no_prior_detection`, `frp_sum_28d`, `same_week_last_year`,
  `neighborhood_3x3_last_7d`, `neighborhood_7x7_last_7d`,
  `neighborhood_3x3_last_28d`, `neighborhood_7x7_last_28d`,
  `national_last_7d`, `national_last_28d`, `week_of_year_sin`,
  `week_of_year_cos`, `train_climatology_cell`, `train_climatology_dept`,
  `window_obs_complete_365d`.
- `G` (20 strict geographic inputs): exactly `T` with only
  `train_climatology_cell` removed, preserving order and
  `train_climatology_dept`.
- `R` (five CHIRPS inputs): `rain_sum_7d`, `rain_sum_30d`, `rain_sum_90d`,
  `dry_days_30d`, `rain_anomaly_90d`.
- `V` (ten vegetation inputs): `veg_ndvi_latest`, `veg_evi_latest`,
  `veg_ndvi_change_previous`, `veg_evi_change_previous`,
  `veg_ndvi_change_48d`, `veg_evi_change_48d`, `veg_ndvi_seasonal_anomaly`,
  `veg_evi_seasonal_anomaly`, `veg_valid_pixel_fraction`,
  `veg_observation_age_days`.
- `L` (five land-cover inputs): `mapbiomas_forest_fraction`,
  `mapbiomas_nonforest_fraction`, `mapbiomas_farming_fraction`,
  `mapbiomas_nonvegetated_fraction`, `mapbiomas_water_fraction`.

| Compared variant | Exact ordered groups | Inputs | Comparator | Selection |
| --- | --- | --- | --- | --- |
| Published strict temporal context | `T` | 21 | Context only | Saved `strict/lightgbm_08` |
| Published retrospective temporal | `T + R` | 26 | Temporal baseline | Saved `retrospective/lightgbm_16` |
| H3 temporal | `T + R + V` | 36 | Temporal baseline | Separate 16- and 32-day fits |
| H4 temporal | `T + R + L` | 31 | Temporal baseline | Separate fit; currently `not_evaluated` |
| H5 temporal | `T + R + V + L` | 41 | Temporal baseline | Separate 16- and 32-day fits; currently `not_evaluated` |
| Published strict geographic context | `G` | 20 | Context only | Saved `strict_geo/lightgbm_04` |
| Published retrospective geographic | `G + R` | 25 | Geographic baseline | Saved `retrospective_geo/lightgbm_04` |
| H3 geographic | `G + R + V` | 35 | Geographic baseline | Separate 16- and 32-day fits |
| H4 geographic | `G + R + L` | 30 | Geographic baseline | Separate fit; currently `not_evaluated` |
| H5 geographic | `G + R + V + L` | 40 | Geographic baseline | Separate 16- and 32-day fits; currently `not_evaluated` |

The YAML expands the full ordered feature list for all six extension variants
and enumerates all ten fits in `run_manifest`. The registration repeats that
manifest with expanded features and comparator identities. Geographic
vegetation anomalies use department references built from non-held-out cells;
temporal anomalies use cell references. No geographic variant contains
`train_climatology_cell`. Every geographic fit excludes held-out blocks from
training, selection, calibration and reference statistics, and independently
selects and calibrates its model. Geographic comparisons remain exploratory;
they cannot establish the primary temporal H3-H5 decisions.

Each registered fit uses its exact feature set and LightGBM with the
same ordered 20 distinct configurations from `search_parameters(seed=7)` and
the exact search space in [experiments.yaml](../configs/experiments.yaml).
Use all eligible training rows, 400 maximum rounds, 30-round early stopping
on uncalibrated `val_select` AP, 63 bins, four threads, deterministic column-wise
construction, and ascending model-name tie resolution. No learner-family,
feature, lag or budget selection follows the extension results. After selection
closes, fit isotonic calibration on `val_calib`, clipping out-of-range scores;
an identity fallback is recorded and cannot support a positive decision.

Retain the parent target, observation rules, grid, geographic flags and
boundary purges. Train is 2013-01-07 through 2020-12-31, `val_select` is 2021,
`val_calib` is 2022, and the previously opened test is 2023-2025. Held-out
1-degree blocks (fraction 0.2, hash seed 20261031) enter no fitting,
selection, calibration or training-reference statistics. Eligibility requires
a finite primary label, assigned partition, correct geography and
`window_obs_complete_28d`. Keep `window_obs_complete_365d` as a feature and
the primary low-coverage exclusion disabled. CHIRPS provenance and every
required completeness flag must pass.

Before metrics, freeze group availability from access and feature audits.
Each extension fits on its feature-complete eligible training rows; report
how this differs from the original comparator's fitting population. Selection
and calibration use the intersection across available primary variants and
both published comparators within its temporal or geographic family.
Evaluation uses identical ordered cell-issue rows
with complete required features and finite predictions across those variants.
This compares extensions against a fixed published model; differing training
support remains a limitation, not an isolated causal feature effect.
An unavailable group removes its hypotheses from evaluation, rather than
emptying the support of the remaining hypotheses.
Compute temporal and geographic common support separately. A temporal
selection is never scored against geographic comparators, and each 32-day
sensitivity repeats selection/calibration independently under the same
geographic exclusions and reports its intersection with the primary lag.

Record exclusions sequentially under the parent rules, then per-variant
feature failures and losses to the common intersection. For every stage and
reported population, give pre-intersection counts, paired rows, positives,
prevalence, represented blocks and ordered row-key hashes. Preserve
missingness as null: no failed QA, stale value, absent reference or missing
MapBiomas fraction is zero-filled.

## Vegetation extraction and quality

Request MOD13A2.061 through AppEEARS area sampling over the pinned Bolivia
ADM0 polygon buffered outward by 0.1 degree, verifying that the request covers
all intersecting border cells. Request 2011-12-01 through 2026-07-31 for NDVI,
EVI, VI Quality, pixel reliability and composite day of year. Verify service
layer identifiers before submitting. Use native sinusoidal GeoTIFFs and
sequential year requests if service limits require them. The buffer supplies
border pixels; aggregation clips every pixel to the national cell portion.

The MOD13 [user guide](https://landweb.modaps.eosdis.nasa.gov/data/userguide/MODISC6VIUserguide.pdf)
(Tables 4, 5, 8 and 11) defines the reliability and detailed QA fields. The
registered screen requires reliability 0; MODLAND bits 0-1 = 0; usefulness
bits 2-5 in {0, 1, 2, 4}; aerosol bits 6-7 in {1, 2}; bits 8, 10, 14 and
15 = 0; and land/water bits 11-13 = 1. Bit 9 may be 0 or 1. These are
deliberately strict study choices, rather than a claim that every other
product value is unusable. The [Collection 6.1 guide](https://lpdaac.usgs.gov/documents/621/MOD13_User_Guide_V61.pdf)
is also pinned in the contract; the accessible Collection 6 tables are
cross-checked against the Collection 6.1 file specification.

The [MOD13A2 file specification](https://ladsweb.modaps.eosdis.nasa.gov/filespec/MODIS/61/MOD13A2)
gives NDVI/EVI raw range [-2000, 10000], fill -3000, VI Quality range
[0, 65534], fill 65535, reliability values 0-3, fill -1, and observation DOY
1-366, fill -1. Decode the indices to [-0.2, 1.0] by multiplying by 0.0001
once, with zero offset. The native specification expresses its scale as a
divisor of 10000. Never scale QA, reliability or DOY. Require a real observation
date inside the metadata period; reject impossible day 366 in a non-leap year.

AppEEARS area GeoTIFF samples are expected to store unscaled integers.
[LP DAAC's explanation](https://forum.earthdata.nasa.gov/viewtopic.php?t=3934)
also documents band Scale/Offset metadata that some readers apply
automatically. Before feature extraction, record dtype, nodata, metadata and
reader decoding mode; read without automatic scaling, verify raw ranges and
fill masks, and compare with metadata-decoded physical values to float32
precision. Apply conversion only when the reader has not already applied it.
Range magnitudes alone do not prove correct scaling; ambiguous or conflicting
metadata blocks extraction. This verification remains required on the actual
request outputs and has not been replaced by a documentation assumption.

Use one accepted-pixel mask for both indices. Compute geodesic WGS84 areas of
native pixel/cell/ADM0 intersections. A mean divides the weighted index sum
by accepted intersection area. `veg_valid_pixel_fraction` divides accepted area
by the entire national cell area, including rejected and missing pixels.
At least 0.5 is required; otherwise all vegetation inputs are missing. This
threshold and pure-land QA screen can exclude mixed or persistently cloudy
cells; report their coverage loss.

## Time, features and retrospective status

For the product's inclusive metadata period `[start, end]`, implement
`end_boundary` as midnight immediately after its final UTC day. A composite is
eligible only when `end_boundary + 16 days <= issue_time`. This convention is
conservative at the inclusive endpoint. Do not treat its nominal start date
or an early selected pixel date as its publication time.

The [MODAPS production description](https://modaps.modaps.eosdis.nasa.gov/services/about/)
describes roughly 24-hour forward production for L1/L2 atmosphere products;
it does not establish MOD13A2 delivery latency. The reviewed LP DAAC/MODAPS
pages do not establish a product-specific guaranteed latency. Consequently,
16 days after composite end is a conservative registered assumption, not a
measured service guarantee. The longer-lag sensitivity uses 32 days,
repeating the same 20-configuration search and calibration procedure. Keep
all other rules, report its own support and additionally compare lags on
their intersection; it cannot replace the primary lag after seeing scores.

The [Collection 6.1 changes](https://landweb.modaps.eosdis.nasa.gov/data/userguide/MODIS_Land_C61_Changes.pdf)
describe full-mission land reprocessing, revised calibration and VI corrections
that had entered C6 forward production in September 2017. The
[reprocessing status](https://landweb.modaps.eosdis.nasa.gov/data/userguide/C61MODISLandStatus.pdf)
is a processing-history source, not a latency guarantee. The
[LP DAAC collection record](https://cmr.earthdata.nasa.gov/search/concepts/C2565788905-LPCLOUD.html)
identifies the V061 release as 2021-02-16 and distinguishes acquisition from
production timestamps. Historical .061 vintages were reprocessed; an issue lag
cannot reconstruct their original published versions. Vegetation variants
remain retrospective, as do CHIRPS and MapBiomas variants.

For each cell, choose the most recent available composite passing quality,
coverage and freshness. Every contributing observation must be at most 64 days
old. The age feature is days from issue time to the latest contributing pixel
observation; retain the oldest date too for the age gate. Never interpolate
forward or use a later composite to fill an earlier issue. Generic forward
fill and backward fill of failed feature rows are prohibited. A registered
as-of composite search may use only candidates satisfying every availability,
QA, coverage, age and required-reference gate; it does not relax those gates.

The ten vegetation features are:

- Latest area-weighted NDVI and EVI.
- Each latest value minus its immediately previous valid available composite,
  requiring a start-date gap at most 32 days.
- Each latest value minus the valid available composite nearest 48 days before
  its start, within 8 days, breaking ties toward the earlier start.
- Each latest value minus its training-only composite-of-year climatology.
- Accepted pixel-area fraction and observation age.

Change references must also pass QA and availability; their contributing
observations may be at most 128 days old at issue time. This separate limit
allows the 48-day comparison while retaining the 64-day limit on the current
condition. A missing reference makes the required change missing.

Climatology uses only 2013-2020 observations, the same geographic exclusion
and LOYO exception as the frozen label climatology: exclude the issue calendar
year for train rows; later rows use all training years. Other training years
may be later than a train issue, but validation, test, confirmation and
warm-up-only years never enter the reference. The slot is
`min((DOY(composite_start)-1)//16, 22)`; use an arithmetic mean of valid annual
cell composites in that slot, with at least four distinct reference years.
For geographic models, use a department reference built from non-held-out
cells, equally weighting cells within a year and then years. Historical
reference composites pass QA/coverage in their own periods; they are not
subject to the current issue's age gate. An anomaly is a difference, not a
standardized score; missing climatology remains missing.

## Conditional MapBiomas group

[MapBiomas Bolivia](https://bolivia.mapbiomas.org/iniciativas-y-productos/cobertura-y-uso-del-suelo/cobertura/)
describes Collection 4 annual 30 m maps for 1985-2025 and evolving historical
collections. The retained external report `research_mapbiomas_access.md`,
dated **2026-11-01**, records anonymous ranged GET HTTP 403 for 2012, 2018,
2024 and 2025; its SHA-256 is pinned in the YAML. It supersedes the earlier
2026-10-31 anonymous-access evidence for the current access decision. A public
raster link does not establish accessible data.

The owner has authorized authenticated Google Earth Engine use under strict
**zero-cost Community Tier** conditions. The official
[GEE asset route](https://bolivia.mapbiomas.org/downloads/assets-en-google-earth-engine/)
for `projects/mapbiomas-public/assets/bolivia/lulc/collection4/mapbiomas_bolivia_collection4_coverage_v1`
is conditional. A separate verification task must establish zero cost for
compute, export and destination storage, authenticated access, exact
collection/year identity, the legend, licence and attribution. No paid tier,
billing activation or billable fallback is authorized; stop if zero cost
cannot be established. This amendment performs no GEE access or export and
does not assert that verification has succeeded.

H4/H5 persist as **`not_evaluated`** until that separate verification succeeds
and land-cover features are built, audited and frozen. Freeze availability
before any extension metric; adding the group after scores requires a new
dated committed registration. Record access mode, official provider URL or
asset, zero-cost evidence, metadata, checksum and verification date. Failed
access retains the status and reason. Do not substitute private assets or
another collection.

"Previous-available-year" means the latest map year accessible at extraction
and at most issue year minus one, with a
maximum age of two calendar years, and record nominal and actual years.
Extraction-time access and this one-year lag do not establish release or
availability at the historical issue time. Collection 4 gap filling and
multi-year temporal smoothing can use future years; lagging the map by one
year does not remove that information. Collection 4 remains retrospective
regardless of this lag. The five fractions
are forest, non-forest natural formation, farming, non-vegetated land, and
water, using the explicit code groups in the YAML and checking the
[Collection 4 legend](https://bolivia.mapbiomas.org/wp-content/uploads/sites/5/2026/09/Leyenda_Code_Bolivia_Col4.pdf).
They preserve the repository's five class groups, including rocky outcrop 29
under non-forest formations, glacier 34 under water, and salt flat 61 under
non-vegetated land.

Use national pixel-cell intersection areas. Nodata, unobserved class 27 and
unmapped codes are excluded from mapped area; require mapped area at least
95% of the national cell area. The five mapped-area fractions must be in
[0, 1] and sum to one within 1e-6. This extension deliberately treats
unobserved/unknown coverage as missing, rather than the existing descriptive
`other_fraction`; no sixth model input is added. Absent or invalid coverage
is missing, never a zero-valued land-cover vector.

## Test access, confirmation and promotion

[final_test_log.json](../artifacts/evaluation/final_test_log.json) has seven
entries at registration: the primary opening at
2026-10-31T10:30:00.578581+00:00, five sensitivity reopenings, and one post-hoc
learner comparison. The latter has `type: post_hoc_comparison` and
`reopening: false` in its own namespace; that flag does not make 2023-2025
untouched again. Any extension test scoring is an **exploratory logged
reopening**, requiring an explicit open flag, nonempty reason and frozen
artifact hashes, registration hash/commit and ordered row-key hashes durably
logged before labels are loaded **or scoring begins**, for temporal,
geographic and sensitivity runs. Abort if logging fails. Append completion
or failure to the retained attempt; never delete an opening. Test results cannot tune
anything or change the published choice.

Pre-register issues 2026-01-05 through 2026-07-20 inclusive as **supplementary
once-only temporal confirmation**. Use every eligible non-held-out national
cell on the frozen grid and common support, with unchanged seven-day targets;
the last target ends 2026-07-27 exclusive. Obtain FIRMS SP and documented
outage coverage through 2026-07-31 and extend unchanged CHIRPS antecedent
features through 2026-07-19. Require source MOD13A2 granules evidenced as
produced and archived by July 31, as well as the issue-specific lag. Missing
archive evidence makes features unavailable rather than proving timely
publication. Neither models nor calibrators learn from 2026 rows.

Before opening these labels, document an access audit confirming that no
2026 confirmation labels, outcome summaries or scores were inspected for
development. This document selects the window but does not assert that an
external access history has already been verified. A failed freshness audit
marks confirmation unavailable; it cannot be replaced by another window
chosen after scoring. Freeze variants, selection, calibrators, manifests and
support rules before one durably logged batch containing all available
temporal hypotheses, their longer-lag sensitivities and fixed temporal
subgroups. Geographic runs remain exploratory on the registered holdout.
Log before label access or scoring and abort if logging fails. A failure after label access
consumes that opening; any retry is exploratory.

Confirmation agreement requires the corresponding calibrated paired AP
interval to be entirely above zero under the same 500 month-block replicates
and seed. Undefined or nonpositive intervals are inconclusive. January to
July 20 excludes August-October and most of the full July-October subgroup;
prevalence may be lower and is not yet measured. AP depends on prevalence,
so cross-window AP levels are not directly comparable. This window cannot
establish full fire-season performance and is supplementary to the validation
decision, despite supplying independent temporal evidence if the audit passes.

Exploratory subgroups fixed now are Santa Cruz under the existing cell flag,
issue months July-October, their intersection, and geographic holdout with
the corresponding geo models. Report support and positives, with null metrics
and reasons when undefined. Temporal subgroup intervals use calendar months;
geographic intervals use 1-degree spatial blocks, both 500 replicates and
seed 7. No subgroup-specific fitting or confirmatory claims are allowed.

The deployed live model remains unchanged. An extension can only be proposed
for a future study when its validation rule is met **and** its fresh-window
interval agrees. An unavailable or inconclusive fresh window does not pass
this gate. Full seasonal confirmation requires a future separately registered
untouched study.

## Resources and remaining gates

Keep at least 55 GiB free on the data volume and cap extension peak storage
at 30 GiB, using the existing guard's `1024^3` byte convention. Keep RAM under
12 GiB across the entire process tree, including child processes for download,
aggregation, fitting and bootstrap. Sample summed resident RAM at least once
per second and retain per-stage peaks in bytes and timestamps for every
temporal, geographic and sensitivity fit. Check projected allocations and
measured process-tree RAM; checkpoint and stop before exceeding the cap, and
stop if monitoring fails. Process one download and one fit at a time, and use
batches of at most 100,000 rows. Check projected post-allocation free space
and extension peak disk before starting allocations. Retain compact
cell features, metadata and checksums, and remove only verified intermediates
owned by the extension. If service output or working storage exceeds the
budget, checkpoint and stop; do not shorten dates or drop layers silently.

Planning estimates, **not measured runtimes**, are 6-72 hours for the AppEEARS
queue/extraction, 2-24 hours for transfer/aggregation, and 1-12 hours per variant
for search/calibration/bootstrap. Queue delay and transfer throughput are
unknown. Replace estimates with measured bytes, stage runtimes, peak RAM and
minimum free disk in acquisition and selection records.

The remaining gates are coordinator commitment of this registration,
demonstrated runtime registration refusal, actual AppEEARS output/scaling
verification, product-specific archive-time evidence for the fresh window,
separate zero-cost GEE/access/legend/licence verification and land-cover
feature construction, fresh-window access history, complete 2026
FIRMS/outage/CHIRPS coverage, and measured resource use. None is an
observed extension result. No new labels, feature-group metrics or raster
downloads are needed to register this contract.
