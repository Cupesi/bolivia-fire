# Media inventory

Rendered documentation media and static scientific figures are listed below. Dimensions
are width × height in pixels; sizes are exact bytes. Live browser views use the snapshot
updated 2026-10-31T18:16:52 UTC. Historical views use the prepared benchmark panel and
prediction export. The [figure guide](figures.md) describes sources and regeneration.

## Screenshots and animations

| File | What it shows | Source / issue or date | Language | Dimensions (px) | Size (bytes) |
|---|---|---|---|---:|---:|
| [live-forecast-desktop.png](assets/screenshots/live-forecast-desktop.png) | National live estimate and selected San José cell history. | Live snapshot; issue 2026-10-26 | English | 1,440 × 900 | 68,858 |
| [live-cell-detail.png](assets/screenshots/live-cell-detail.png) | Zoomed estimate, administrative outlines, town labels and selected-cell places. | Live snapshot; issue 2026-10-26 | English | 1,440 × 900 | 68,803 |
| [table-desktop.png](assets/screenshots/table-desktop.png) | Estimate table filtered to Campesina el Soto, place columns and cell history. | Live snapshot; issue 2026-10-26 | English | 1,440 × 900 | 36,620 |
| [estimate-mobile.png](assets/screenshots/estimate-mobile.png) | National weekly estimate and probability legend on mobile. | Live snapshot; issue 2026-10-26 | English | 390 × 844 | 40,772 |
| [live-spanish-desktop.png](assets/screenshots/live-spanish-desktop.png) | National weekly estimate and selected-cell history in Spanish. | Live snapshot; issue 2026-10-26 | Spanish | 1,440 × 900 | 70,928 |
| [estimate-desktop.png](assets/screenshots/estimate-desktop.png) | Historical temporal/geographic estimates, holdout outlines and model identity. | Historical export; issue 2024-09-02 | English | 1,440 × 900 | 79,577 |
| [observed-desktop.png](assets/screenshots/observed-desktop.png) | Historical observed indicators and complete-label legend. | Historical panel; issue 2024-09-02 | English | 1,440 × 900 | 45,112 |
| [track-record-desktop.png](assets/screenshots/track-record-desktop.png) | Backfill scores and an empty prospective track record. | Live snapshot; backfill issue 2026-10-19 | English | 1,440 × 900 | 39,325 |
| [week_navigation.gif](assets/screenshots/week_navigation.gif) | Four complete historical estimate weeks. | Historical export; issues 2024-09-02, 09-09, 09-16 and 09-23 | English | 1,476 × 936 | 327,531 |
| [national_weekly.gif](assets/charts/national_weekly.gif) | National weekly detection counts and observation outages. | Detection bundle; 2012-2025 | English | 1,026 × 443 | 60,601 |
| [seasonal_cycle.gif](assets/charts/seasonal_cycle.gif) | Annual detection cycles by week of year. | Detection bundle; 2012-2025 | English | 960 × 476 | 511,164 |
| [dataset_map.gif](assets/charts/dataset_map.gif) | Complete training-label frequency and held-out geographic blocks. | Processed cells and panel; training issues 2013-2020 | English | 575 × 575 | 1,621,140 |
| [fire_season_2024.gif](assets/charts/fire_season_2024.gif) | Weekly estimates and observed satellite detections during the 2024 fire season. | Strict test export and panel; issues 2024-07-01 through 2024-10-28 | English | 982 × 608 | 2,242,133 |
| [temporal_ap.gif](assets/charts/temporal_ap.gif) | Temporal AP and paired differences against the selected baseline. | [test_strict.json](../artifacts/evaluation/test_strict.json); test issues 2023-2025 | English | 1,092 × 487 | 67,650 |
| [geographic_ap.gif](assets/charts/geographic_ap.gif) | Geographic-holdout AP and paired differences. | [test_strict.json](../artifacts/evaluation/test_strict.json); test issues 2023-2025 | English | 1,092 × 487 | 74,586 |
| [reliability.gif](assets/charts/reliability.gif) | Temporal-test reliability bins with a low-probability detail panel. | [test_strict.json](../artifacts/evaluation/test_strict.json); test issues 2023-2025 | English | 982 × 476 | 264,944 |
| [per_year_ap.gif](assets/charts/per_year_ap.gif) | Temporal AP for each test issue year. | [test_strict.json](../artifacts/evaluation/test_strict.json); 2023, 2024 and 2025 | English | 850 × 531 | 370,797 |
| [area_budget.gif](assets/charts/area_budget.gif) | Recall at 5% and 10% eligible area in both test populations. | [test_strict.json](../artifacts/evaluation/test_strict.json); test issues 2023-2025 | English | 1,136 × 564 | 387,334 |
| [h2_interval.gif](assets/charts/h2_interval.gif) | Paired retrospective-minus-strict AP interval for H2. | [test_strict.json](../artifacts/evaluation/test_strict.json); test issues 2023-2025 | English | 982 × 377 | 118,720 |
| [pipeline.gif](assets/diagrams/pipeline.gif) | Acquisition, labels/features, selection, calibration, evaluation and delivery. | [Protocol 1.1](../configs/protocol.yaml); frozen 2026-10-31 | English | 1,156 × 596 | 93,709 |
| [information_products.gif](assets/diagrams/information_products.gif) | Retrospective analysis, availability-constrained simulation and operational use. | [Information rules](protocol.md); protocol frozen 2026-10-31 | English | 1,356 × 376 | 227,133 |
| [partitions.gif](assets/diagrams/partitions.gif) | Training, selection, calibration and test dates, purges and geographic holdout. | [Protocol 1.1](../configs/protocol.yaml); issue calendar 2013-2025 | English | 1,356 × 586 | 107,918 |

The chart sources are summarized in [results_summary.json](../artifacts/figures/results_summary.json)
and [dataset_summary.json](../artifacts/figures/dataset_summary.json). The machine-readable
animation inventory is [media.json](assets/media.json).

## Diagram sources

| File | What it shows | Source / date | Language | Dimensions | Size (bytes) |
|---|---|---|---|---|---:|
| [pipeline.mmd](assets/diagrams/src/pipeline.mmd) | Acquisition, benchmark and delivery stages. | [Protocol 1.1](../configs/protocol.yaml); frozen 2026-10-31 | English | Text source | 751 |
| [information_products.mmd](assets/diagrams/src/information_products.mmd) | Information availability for analysis, simulation and operations. | [Protocol 1.1](../configs/protocol.yaml); frozen 2026-10-31 | English | Text source | 422 |
| [partitions.mmd](assets/diagrams/src/partitions.mmd) | Training, validation and final-test partition dates. | [Protocol 1.1](../configs/protocol.yaml); frozen 2026-10-31 | English | Text source | 487 |

## Static scientific figures

| File | What it shows | Source / issue or date | Language | Dimensions (px) | Size (bytes) |
|---|---|---|---|---:|---:|
| [dataset_weekly.png](../artifacts/figures/dataset_weekly.png) | National weekly detection counts and observation coverage. | [Dataset summary](../artifacts/figures/dataset_summary.json); 2012-2025 | English | 2,729 × 1,067 | 185,330 |
| [dataset_seasonal.png](../artifacts/figures/dataset_seasonal.png) | Detection cycles by week of year. | [Dataset summary](../artifacts/figures/dataset_summary.json); 2012-2025 | English | 2,190 × 1,140 | 233,832 |
| [dataset_map.png](../artifacts/figures/dataset_map.png) | Training-label frequency and held-out geographic blocks. | [Dataset summary](../artifacts/figures/dataset_summary.json); training issues 2013-2020 | English | 1,350 × 1,474 | 260,577 |
| [dataset_partitions.png](../artifacts/figures/dataset_partitions.png) | Issue partitions, label prevalence and geographic holdout. | [Dataset summary](../artifacts/figures/dataset_summary.json); issues 2013-2025 | English | 2,730 × 1,235 | 175,608 |
| [results_temporal.png](../artifacts/figures/results_temporal.png) | Temporal AP and paired baseline differences. | [Test evaluation](../artifacts/evaluation/test_strict.json); test issues 2023-2025 | English | 3,030 × 1,350 | 170,411 |
| [results_geographic.png](../artifacts/figures/results_geographic.png) | Geographic-holdout AP and paired baseline differences. | [Test evaluation](../artifacts/evaluation/test_strict.json); test issues 2023-2025 | English | 3,029 × 1,350 | 171,389 |
| [results_reliability.png](../artifacts/figures/results_reliability.png) | Temporal-test reliability curves and low-probability detail. | [Test evaluation](../artifacts/evaluation/test_strict.json); test issues 2023-2025 | English | 2,409 × 1,410 | 168,669 |
| [results_per_year.png](../artifacts/figures/results_per_year.png) | Temporal AP by issue year. | [Test evaluation](../artifacts/evaluation/test_strict.json); 2023, 2024 and 2025 | English | 2,250 × 1,380 | 174,129 |
| [results_area_budget.png](../artifacts/figures/results_area_budget.png) | Recall at 5% and 10% eligible area. | [Test evaluation](../artifacts/evaluation/test_strict.json); test issues 2023-2025 | English | 3,030 × 1,470 | 180,994 |
| [results_h2.png](../artifacts/figures/results_h2.png) | Paired retrospective-minus-strict AP interval. | [Test evaluation](../artifacts/evaluation/test_strict.json); test issues 2023-2025 | English | 2,610 × 960 | 92,249 |
| [results_map.png](../artifacts/figures/results_map.png) | Example weekly probabilities and observed satellite detections. | [Result summary](../artifacts/figures/results_summary.json); issue 2024-09-02 | English | 2,916 × 1,830 | 414,942 |
