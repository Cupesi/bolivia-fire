# Bolivia Fire dataset card

## Population and intended use

The population is every Monday issue time paired with every 0.05° grid cell
having a positive-area intersection with the pinned Bolivia ADM0 polygon.
The target is at least one selected VIIRS S-NPP standard-processing detection
in the national part of the cell during `[issue, issue + 7 days)`, issued at
00:00 UTC. It supports retrospective evaluation of weekly detection probability.
It does not count fires, ignitions or burned area, and is not an official warning.

The strict dataset and pipeline are complete; model selection uses validation
years, and final-test results are not yet published. CHIRPS extraction is in
progress. MapBiomas fractions are absent because verified anonymous raster
requests returned HTTP 403. These product states are described in
[the covariate guide](../docs/covariates.md).

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
| [NASA FIRMS](licenses/FIRMS.md) | NASA open-data reuse basis with source attribution; retain product, processing tier and dates; no Creative Commons licence is assigned here |
| [geoBoundaries / GeoBolivia](licenses/geoBoundaries.md) | Pinned Bolivia boundaries are Public Domain; retain source pins and attribution with derived intersections |
| [CHIRPS v3](licenses/CHIRPS.md) | CC BY 4.0 with the provider's copyright-waiver statement; attribute final daily `rnl`, ERA5 disaggregation and aggregation changes |
| [MapBiomas Bolivia](licenses/MapBiomas.md) | CC BY 4.0 permits attributed derivatives, but no real fractions were acquired |

There is no blanket MIT licence for these data. Code uses [MIT](../LICENSE).
Licensed derivatives may be redistributed under the applicable source terms,
with attribution and processing descriptions. Raw and processed data are not
bundled in Git. Follow [reproduction](../docs/reproduction.md) to acquire pinned
sources and rebuild; preserve manifests and hashes with any distributed snapshot.
Cite the software using [CITATION.cff](../CITATION.cff) and credit each data source.
