# Bolivia Fire dataset card

## Population and intended use

The population is every Monday issue time paired with every 0.05° grid cell
having a positive-area intersection with the pinned Bolivia ADM0 polygon.
The target is at least one selected VIIRS S-NPP standard-processing detection
in the national part of the cell during `[issue, issue + 7 days)`, issued at
00:00 UTC. It supports retrospective evaluation of weekly detection probability.
It does not count fires, ignitions or burned area, and is not an official warning.

The strict dataset, CHIRPS acquisition and retrospective features are complete.
The [primary final-test results and all five pre-registered sensitivity analyses](../docs/results.md)
are published. The sensitivity artifacts cover
[all confidence classes](../artifacts/evaluation/test_strict_all_confidence.json),
[excluding low-coverage target weeks](../artifacts/evaluation/test_strict_exclude_low_coverage.json),
[removing neighborhood features](../artifacts/evaluation/test_strict_drop_neighborhood.json),
[removing climatology features](../artifacts/evaluation/test_strict_drop_climatology.json) and
[removing national activity features](../artifacts/evaluation/test_strict_drop_national.json).
Each sensitivity analysis has a separately logged test reopening and reason.
Model selection and calibration use validation years only. The original retrospective
comparison uses CHIRPS rainfall after anonymous MapBiomas raster requests failed.
Later experiments use MOD13A2 vegetation under the
[vegetation protocol](../docs/extension-vegetation.md) and authenticated MapBiomas
Collection 4 aggregates under the [land-cover amendment](../docs/extension-landcover-amendment.md).
Their test-year results are exploratory; no completed 2026 confirmation is supported.
See [the covariate guide](../docs/covariates.md) for acquisition evidence.

## Coverage and splits

Counts below come from the [descriptive summary](../artifacts/figures/dataset_summary.json)
and [figure documentation](../docs/figures.md); they describe observations and
labels, not predictive performance.

| Property | Observed snapshot |
| --- | --- |
| Eligible cells | 37,146 |
| Eligible national intersection area | 1,068,783.84 km² |
| Issue range | 2013-01-07 through 2025-12-29 |
| Issue weeks / cell-weeks | 678 / 25,184,988 |
| Raw observation range | 2012-01-20 through 2026-01-04, including feature warm-up |
| Primary / all-confidence national detections | 3,614,079 / 3,842,904 |
| Nonmissing primary labels, including purged rows | 24,367,776 |
| Geographic holdout | 24 of 124 eligible one-degree blocks; 7,640 cells |
| Held-out national area | 219,395.59 km² |

| Partition | Issue years | Cell-weeks | Primary labels present |
| --- | --- | ---: | ---: |
| `train` | 2013–2020 | 15,452,736 | 99.5192% |
| `val_select` | 2021 | 1,894,446 | 98.0392% |
| `val_calib` | 2022 | 1,894,446 | 92.1569% |
| `test` | 2023–2025 | 5,831,922 | 90.4459% |
| Purged, null partition | Target crosses the next partition boundary | 111,438 | 100% |

Purged rows remain descriptive records. Fitting also excludes missing labels,
geographic holdout and incomplete antecedent 28-day observation coverage. The
resulting eligible fitting populations are 11,979,436 training, 1,357,276
selection and 1,180,240 calibration rows. Geographic holdout is assigned by the
frozen seeded block-hash rule and excluded from all fitting, selection and
calibration. Its test-year population uses the geographic feature set, which
omits cell climatology. See [the protocol](../docs/protocol.md) and
[evaluation rules](../docs/evaluation.md).

## Variables and filters

| Table | Variables and contract |
| --- | --- |
| Raw observations | [FIRMS schema](schemas/firms_viirs_sp.yaml): source coordinates, UTC date/time, confidence, type, brightness temperatures, FRP and scan geometry |
| Cells | [Cell schema](schemas/cells.yaml): grid bounds and centers, geodesic national area, department, Santa Cruz and holdout flags |
| Detections and observation days | [Detection schema](schemas/detections.yaml): parsed observations, selection flags, source file/line, acquisition and documented outage coverage |
| Weekly panel | [Panel schema](schemas/panel.yaml): nullable labels, observed counts, history, recency, neighborhoods, seasonality, training climatology and completeness flags |
| Retrospective features | [Covariate schema](schemas/covariates.yaml): antecedent rainfall sums, dry days, training-reference anomalies and optional previous-year land-cover fractions |
| Later environmental features | [Vegetation schema](schemas/vegetation_features.yaml) and [land-cover schema](schemas/landcover.yaml): MOD13A2 lags, five Collection 4 fractions, availability flags and combined tables |

Raw bounding-box observations are retained without confidence or country
filtering. Exact duplicates across all original source columns are removed
before parsing. Canonical manifest-recorded request identities resolve overlaps;
source file and original data-line references are preserved. Labels and history
use points covered by ADM0. Primary selection is type 0 with nominal/high
confidence; the all-confidence sensitivity adds low-confidence type-0 rows.

Observation features use detections strictly before issue time. Training
climatology is an explicitly cross-fitted statistic: other training years,
including later training years, may contribute, while validation, test and
held-out labels never do. CHIRPS v3 is a retrospective vintage even when rainfall
windows end before issue time. Missing rainfall remains null. Full definitions
are in [dataset construction](../docs/dataset.md) and
[covariates](../docs/covariates.md).

## Observation coverage and known biases

All dates in the source range were acquired. The 64 unobserved days are acquired
dates with at least six hours in the union of documented S-NPP outages.
Unobserved days by year are 2012: 8, 2017: 1, 2019: 1, 2021: 2, 2022: 17,
2023: 4, 2024: 25 and 2025: 6; other acquisition years have none. Endpoint years
are partial. Source comments and annual counts are retained in the
[summary](../artifacts/figures/dataset_summary.json).

A target label is null when any target day is unobserved. Detection counts
remain the observed partial counts. A zero is a complete interval with no
selected detections, not evidence that no fire burned. The independent
`target_low_coverage` flag identifies June–November days with zero bounding-box
detections; it is a sensitivity heuristic, not the primary outage rule.

Clouds, smoke, overpass timing, small or short-lived fires and repeated
detections of the same event affect the target. The outage table captures
documented major instrument interruptions, not per-cell cloud-free coverage.
Confidence categories are detection-quality codes rather than probabilities.
Strict simulation assumes standard-processing detections approximate historical
near-real-time availability; that equivalence is unvalidated.

Cells have unequal ground areas and border cells contain partial national
territory. Area-weighted evaluation uses geodesic intersections. The pinned ADM0
excludes large interior lakes and salt flats, net 10,780.55 km² after retained
islands; see [the verified boundary-area note](../docs/dataset.md). Its eligible
area is below the commonly cited national total and should not be interpreted
as a replacement estimate of that total.

## Provenance, licences and rebuilding

The [source registry](sources.yaml), [FIRMS manifest](manifests/firms_VIIRS_SNPP_SP.jsonl),
[boundary manifest](manifests/boundaries.jsonl), schemas and
protocol/configuration hashes identify the inputs. Raw observations and derived
tables are local; manifests record their identity and checksums. Detection and
coverage tables publish as one versioned bundle, and panel metadata records
its exact inputs. The [storage guide](README.md) explains snapshot versioning.

| Source | Terms and redistribution status |
| --- | --- |
| [NASA FIRMS](licenses/FIRMS.md) | NASA open-data reuse basis; retain product, processing tier, dates and NASA FIRMS/LANCE/ESDIS acknowledgement |
| [geoBoundaries / GeoBolivia](licenses/geoBoundaries.md) | gbOpen BOL ADM0/ADM1 revision `9469f09`: official `boundaryLicense` is `Public Domain` for both, verified 2026-10-31; retain GeoBolivia, geoBoundaries and Runfola et al. (2020) attribution and source pins with derived intersections |
| [CHIRPS v3](licenses/CHIRPS.md) | CC BY 4.0 with the provider's copyright-waiver statement; credit Climate Hazards Center and CHIRPS3 Data Repository (2025), final daily `rnl`, ERA5 disaggregation, native-pixel mapping to cell centers and antecedent-rainfall aggregation |
| [MOD13A2](licenses/MODIS.md) | NASA open data; cite Didan (2021) and acknowledge LP DAAC and AppEEARS |
| [MapBiomas Bolivia](licenses/MapBiomas.md) | CC BY 4.0; Collection 4 aggregates and derived H4/H5 features are local |
| [GeoNames](licenses/GeoNames.md) | CC BY 4.0; place metadata is for display, not fitting |

Compiled and derived release data use CC BY 4.0; upstream inputs retain their source
terms. Code uses [MIT](../LICENSE). Preserve attribution, processing descriptions,
manifests and hashes when redistributing. Retained raw inputs and processed data are
available in the versioned dataset record rather than bundled in Git.

Dataset version 0.1.0 is available at
[10.5281/zenodo.23895316](https://doi.org/10.5281/zenodo.23895316). The record contains
the benchmark-data, reproduction-artifacts and raw-inputs archives described in
[the release format](README.md#versioned-data-release), plus the README, licence notices, data dictionary,
member inventory and SHA-256 checksums. Operational live forecasts, serving derivatives
and regenerable caches remain excluded; global CHIRPS rasters, MODIS HDF granules and
native MapBiomas 30 m maps are not supplied. Dataset reuse is supported;
prediction-based recalculation and raw-input rebuilding remain partially supported.

Concept DOI (all versions):
[10.5281/zenodo.23895315](https://doi.org/10.5281/zenodo.23895315). Cite the
version DOI above to identify these exact files.

[Reproduction limits](../docs/reproduction.md#release-reproduction-limits) record absent
prediction families, historical hash discrepancies and the test-access policy.
Cite the software using [CITATION.cff](../CITATION.cff) and credit each data source.

## How to cite

Dataset: Cuellar Flores, Victor Hugo. *Bolivia Fire benchmark data and reproduction
artifacts*, version 0.1.0. https://doi.org/10.5281/zenodo.23895316.

Software: Cuellar Flores, Victor Hugo. *Bolivia Fire: weekly VIIRS fire-detection
benchmark software*, version 0.1.0.
[Repository](https://github.com/Cupesi/bolivia-fire);
[v0.1.0 release](https://github.com/Cupesi/bolivia-fire/releases/tag/v0.1.0).

Cite the dataset and software separately and retain upstream source attributions.
This version cites the software through its repository and release URLs.
