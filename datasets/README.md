# Dataset storage

The dataset consists of eligible Bolivia cell-weeks, with nullable next-seven-day
VIIRS S-NPP detection labels and antecedent features. The observed strict snapshot
contains 37,146 cells, 678 issue weeks and 25,184,988 rows; see the
[dataset card](dataset_card.md) and [descriptive summary](../artifacts/figures/dataset_summary.json).

## Layout

| Path under `datasets/` | Contents | Storage |
| --- | --- | --- |
| `sources.yaml` | Provider URLs, product versions, boundary and outage pins | Git |
| `manifests/` | Retrieval identity, source hashes, availability and parsed outage intervals | Git |
| `schemas/` | Raw and derived variable contracts | Git |
| `licenses/` | Source-specific terms and required attribution | Git |
| `raw/firms/VIIRS_SNPP_SP/` | Validated, unchanged FIRMS CSV snapshots | Local |
| `raw/boundaries/`, `raw/outages/` | Hash-pinned GeoJSON and dated outage HTML | Local |
| `interim/detection_bundles/` | Versioned detections and acquisition/observation-day tables | Local |
| `interim/detection_bundle.json` | Atomic pointer to one complete detection bundle | Local |
| `processed/cells.parquet` | Eligible grid, national areas, departments and holdout flags | Local |
| `processed/panel/year=YYYY/part.parquet` | Labels and strict features, keyed by cell and issue | Local |
| `processed/covariates/` | CHIRPS native windows, extraction manifests and retrospective features | Local |
| `.tmp/`, `.quarantine/` | Transfer checkpoints, locks, quota state and rejected responses | Local |

Covariate extraction manifests are stored with their local outputs; preserve them
with any exported covariate snapshot. Models, prediction exports, evaluation
metadata and the final-test access log live under `artifacts/`, described in the
[artifact contract](../artifacts/README.md).

## Versions and provenance

Software version and dataset identity are separate. The frozen
[protocol](../configs/protocol.yaml), [source pins](sources.yaml), manifest bytes,
schema versions and input/output SHA256 hashes identify a dataset snapshot.
Retain the exact manifest used to build derived files: later acquisition appends
can change canonical partition selection. Do not compare snapshots as though
they were identical, or replace source pins to bypass a failed hash check.

Raw FIRMS bytes remain immutable; changed re-fetches receive distinct hash
suffixes. Detection and observation tables publish as one atomic versioned
bundle. Panel files carry the hashes of their exact inputs. Rebuilding selected
issues replaces the corresponding year's panel file, so use the full protocol
range to reproduce the complete snapshot. Schema or protocol changes require a
documented version change and rebuilt dependent artifacts.

## Variables and access

Use the [FIRMS source schema](schemas/firms_viirs_sp.yaml),
[cell schema](schemas/cells.yaml), [detection and coverage schema](schemas/detections.yaml),
[panel schema](schemas/panel.yaml) and [covariate schema](schemas/covariates.yaml).
The [dataset construction guide](../docs/dataset.md) defines filters, missingness,
feature windows and geographic membership. The [dataset card](dataset_card.md)
records splits, observation outages, detection biases and redistribution terms.

Git supplies metadata, not the observations. FIRMS acquisition needs a local
`FIRMS_MAP_KEY` configured from [the example file](../.env.example). Follow the
[reproduction sequence](../docs/reproduction.md) to acquire the source range,
restore pinned boundaries and outage HTML, build cells/detections/panel, extract
CHIRPS and build features. Retrospective extraction is still in progress;
verified public MapBiomas raster access returned HTTP 403, so no real land-cover
fractions are included. See [covariate status](../docs/covariates.md).

Code is [MIT licensed](../LICENSE); data remain under the source terms listed in
[the dataset card](dataset_card.md). Retain source attribution, pins and
processing descriptions with redistributed derivatives; never redistribute keys.
