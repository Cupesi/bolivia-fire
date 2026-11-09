# Dataset storage

The dataset consists of eligible Bolivia cell-weeks, with nullable next-seven-day VIIRS S-NPP detection labels and antecedent features. The observed strict snapshot contains 37,146 cells, 678 issue weeks and 25,184,988 rows; see the [dataset card](dataset_card.md) and [descriptive summary](../artifacts/figures/dataset_summary.json).

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
| `raw/vegetation/`, `processed/vegetation/` | MOD13A2 subset, composites, weights and lagged vegetation features | Local |
| `processed/landcover/` | MapBiomas aggregates, issue features and combined vegetation/land-cover tables | Local |
| `.tmp/`, `.quarantine/` | Transfer checkpoints, locks, quota state and rejected responses | Local |

Covariate extraction manifests are stored with their local outputs; preserve them with any exported covariate snapshot. Models, prediction exports, evaluation metadata and the final-test access log live under `artifacts/`, described in the [artifact contract](../artifacts/README.md).

## Versions and provenance

Software version and dataset identity are separate. The frozen [protocol](../configs/protocol.yaml), [source pins](sources.yaml), manifest bytes, schema versions and input/output SHA256 hashes identify a dataset snapshot. Retain the exact manifest used to build derived files: later acquisition appends can change canonical partition selection. Do not compare snapshots as though they were identical, or replace source pins to bypass a failed hash check.

Raw FIRMS bytes remain immutable; changed re-fetches receive distinct hash suffixes. Detection and observation tables publish as one atomic versioned bundle. Panel files carry the hashes of their exact inputs. Rebuilding selected issues replaces the corresponding year's panel file, so use the full protocol range to reproduce the complete snapshot. Schema or protocol changes require a documented version change and rebuilt dependent artifacts.

## Variables and access

Use the [FIRMS source schema](schemas/firms_viirs_sp.yaml), [cell schema](schemas/cells.yaml), [detection and coverage schema](schemas/detections.yaml), [panel schema](schemas/panel.yaml) and [covariate schema](schemas/covariates.yaml). The [dataset construction guide](../docs/dataset.md) defines filters, missingness, feature windows and geographic membership. The [dataset card](dataset_card.md) records splits, observation outages, detection biases and redistribution terms.

Git supplies metadata, not the observations. FIRMS acquisition needs a local `FIRMS_MAP_KEY` configured from [the example file](../.env.example). Follow the [reproduction sequence](../docs/reproduction.md) to acquire the source range, restore pinned boundaries and outage HTML, build cells/detections/panel, extract CHIRPS and build features. CHIRPS acquisition and retrospective features are complete, and [primary final-test and sensitivity results](../docs/results.md) are recorded. The original retrospective comparison uses rainfall only after anonymous MapBiomas raster access failed. Later MOD13A2 vegetation and authenticated MapBiomas Collection 4 features support the registered H3 and amended H4/H5 experiments. See [covariate status](../docs/covariates.md) and [environmental results](../docs/results.md#environmental-covariates).

Code is [MIT licensed](../LICENSE); compiled and derived release data use CC BY 4.0, and upstream inputs retain the source terms listed in [the dataset card](dataset_card.md). Retain source attribution, pins and processing descriptions with redistributed derivatives; never redistribute keys.

## Versioned data release

Dataset version 0.1.0 is available at [10.5281/zenodo.23895316](https://doi.org/10.5281/zenodo.23895316).

Concept DOI (all versions): [10.5281/zenodo.23895315](https://doi.org/10.5281/zenodo.23895315). Cite the version DOI above to identify these exact files.

The format uses three independently extractable ZIP64 archives with repository-relative paths:

| Archive | Contents |
| --- | --- |
| `bolivia-fire-0.1.0-benchmark-data.zip` | Panel, cells, rainfall, vegetation and land-cover features, schemas and interpretation manifests |
| `bolivia-fire-0.1.0-reproduction-artifacts.zip` | Configurations, environment lockfile, fitted models and calibrators, saved predictions including comparison vectors, evaluation history, figure JSON, producing-code revisions and historical source registries |
| `bolivia-fire-0.1.0-raw-inputs.zip` | FIRMS SP snapshots, MOD13A2 AppEEARS subset, CHIRPS native windows, MapBiomas aggregate exports, boundaries, GeoNames and outage HTML |

The record-level format includes `README.md`, `LICENSES.md`, `DATA_DICTIONARY.md`, `inventory.json` and `SHA256SUMS`. Exclusions are operational live forecasts, serving derivatives, regenerable caches, temporary files and logs, deleted global CHIRPS rasters, MODIS HDF granules and native MapBiomas 30 m maps. The retained comparison prediction vectors under `artifacts/.cache/comparison/` are reproduction evidence and are included.

1. **Reuse the dataset: supported.** Tables retain keys, nullable labels, observation flags and partition/holdout assignments; see the [dataset card](dataset_card.md).
2. **Recalculate reported results from saved predictions: partially supported.** Only strict primary `val_select`/`val_calib`/`test` exports and alternative-learner cached vectors are retained. Rainfall, sensitivity and H3/H4/H5 per-row predictions were not persisted; their result records and fitted models are retained. Regenerating those predictions requires the logged test-access procedure. Every final-test metric recalculation is a logged opening under [the frozen policy](../configs/protocol.yaml#final_test_access). Release verification does not open the test.
3. **Rebuild from raw inputs: partially supported.** FIRMS, boundaries, outages, GeoNames, the MOD13A2 subset and CHIRPS native windows are retained. Global CHIRPS rasters, MODIS HDF granules and native MapBiomas maps are absent; MapBiomas re-extraction needs an Earth Engine account.

Use family-specific producing revisions and historical `datasets/sources.yaml` bytes; the current merged registry cannot satisfy every frozen experiment hash. See [reproduction limits and discrepancies](../docs/reproduction.md#release-reproduction-limits).

## How to cite

Dataset: Cuellar Flores, Victor Hugo. *Bolivia Fire benchmark data and reproduction artifacts*, version 0.1.0. https://doi.org/10.5281/zenodo.23895316.

Software: Cuellar Flores, Victor Hugo. *Bolivia Fire: weekly VIIRS fire-detection benchmark software*, version 0.2.0. [10.5281/zenodo.24090358](https://doi.org/10.5281/zenodo.24090358). [Repository](https://github.com/Cupesi/bolivia-fire); [v0.2.0 release](https://github.com/Cupesi/bolivia-fire/releases/tag/v0.2.0).

Software concept DOI (all versions): [10.5281/zenodo.23898297](https://doi.org/10.5281/zenodo.23898297). The software archive preserves tag `v0.2.0` at commit `0f3cd83`. Later documentation changes are outside that archive.

Cite the dataset and software separately and retain upstream source attributions.
