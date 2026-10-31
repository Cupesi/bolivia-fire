# Dataset figures

`notebooks/01_dataset.ipynb` calls `bolivia_fire.figures` to rebuild four
300 dpi PNGs in `artifacts/figures/`, PDF copies in the ignored `data/figures/`,
and `artifacts/figures/dataset_summary.json`. Reproduction is local and offline:

```powershell
uv sync --all-extras
uv run jupyter nbconvert --to notebook --execute notebooks/01_dataset.ipynb --output 01_dataset.ipynb
uv run ruff check src tests
uv run pytest -q -p no:cacheprovider
```

The JSON records input hashes, annual observations and outage causes, label
prevalence, and sequential fitting exclusions. Aggregation uses UTC and a
512 MB DuckDB memory limit with two query threads. No model is fitted or
evaluated. Test-year counts describe observations and label prevalence.

## Local snapshot

The panel contains 37,146 eligible cells, 678 issue weeks, and 25,184,988 rows.
National cell intersections sum to 1,068,783.8407 km². The geographic holdout
contains 24 of 124 blocks, 7,640 cells, and 219,395.5882 km² (20.5276% of area).
There are 24,367,776 nonmissing primary labels, including purged rows.

| Partition | Rows | Labelled (%) | Positive, non-held-out (%) | Positive, held-out (%) |
|---|---:|---:|---:|---:|
| train | 15,452,736 | 99.5192 | 2.1838 | 1.5277 |
| val_select | 1,894,446 | 98.0392 | 2.3782 | 1.6285 |
| val_calib | 1,894,446 | 92.1569 | 3.0375 | 2.2942 |
| test | 5,831,922 | 90.4459 | 2.4563 | 1.7143 |
| purged | 111,438 | 100.0000 | 0.6383 | 0.6195 |

Rates use nonmissing labels before fitting filters. Geographic populations
have the same labelled share because observation completeness is national.
The map uses the share of complete, non-purged train weeks with `n_primary > 0`
per cell; held-out observations enter this descriptive map. The seasonal cycle
weights each labelled cell-week equally and uses the frozen 0–51 week index.

| Acquisition year | Selected detections | All-confidence detections | Unobserved days |
|---|---:|---:|---:|
| 2012 | 148,690 | 159,564 | 8 |
| 2013 | 108,320 | 115,389 | 0 |
| 2014 | 94,273 | 101,421 | 0 |
| 2015 | 136,293 | 146,122 | 0 |
| 2016 | 232,902 | 247,815 | 0 |
| 2017 | 186,020 | 199,273 | 1 |
| 2018 | 163,615 | 173,590 | 0 |
| 2019 | 355,701 | 380,029 | 1 |
| 2020 | 285,126 | 308,495 | 0 |
| 2021 | 285,926 | 303,009 | 2 |
| 2022 | 308,129 | 329,048 | 17 |
| 2023 | 332,558 | 355,236 | 4 |
| 2024 | 844,864 | 884,419 | 25 |
| 2025 | 131,484 | 139,306 | 6 |
| 2026 | 178 | 188 | 0 |

The acquisition span is January 20, 2012–January 4, 2026, so its endpoint years
are partial. Totals are 3,614,079 selected and 3,842,904 all-confidence national
detections. All 64 unobserved days are acquired dates with at least six hours
of documented outages. Per-cause day counts can overlap; the JSON retains the
source comments. Weekly counts shade documented outage intervals and retain
partial observations; the weekly figure stops at December 31, 2025.

Fitting exclusions are disjoint in the listed order within each partition's
issue-date bounds; candidate counts include the boundary week subsequently purged.

| Partition | Candidates | Purged | Missing label | Held out | Incomplete 28-day history | Eligible |
|---|---:|---:|---:|---:|---:|---:|
| train | 15,489,882 | 37,146 | 74,292 | 3,162,960 | 236,048 | 11,979,436 |
| val_select | 1,931,592 | 37,146 | 37,146 | 382,000 | 118,024 | 1,357,276 |
| val_calib | 1,931,592 | 37,146 | 148,584 | 359,080 | 206,542 | 1,180,240 |

The low-coverage sensitivity excludes zero additional otherwise eligible rows
in each of these partitions. Test rows are not treated as fitting candidates.

| Figure | PNG bytes |
|---|---:|
| `dataset_map.png` | 288,382 |
| `dataset_weekly.png` | 195,906 |
| `dataset_seasonal.png` | 253,674 |
| `dataset_partitions.png` | 182,959 |

All figures were visually inspected. The executed notebook contains six code
cells and is 1,240,771 bytes, with no errors or absolute paths in cell outputs.
All 13 panel files match the bundle, protocol, outage, and cell provenance.

Verification: dependency sync, repository Ruff checks, and clean-kernel notebook
execution passed. Pytest passed 319 tests with one live test deselected; all six
figure tests passed. The eight warnings are DuckDB's pandas deprecation warnings.

## Recorded results figures

`notebooks/02_results.ipynb` rebuilds seven result figures from the recorded
`artifacts/evaluation/test_strict.json`, closed `selection_*.json` files, and
`final_test_log.json`. It does not invoke any fitting, selection, evaluation,
or prediction stage. The notebook uses no network requests.

```powershell
uv run jupyter nbconvert --to notebook --execute --inplace notebooks/02_results.ipynb --ExecutePreprocessor.timeout=180
uv run ruff check src tests
uv run pytest tests -m "not live and not local_data" -q -p no:cacheprovider
```

Performance values, reliability bins, annual AP, and confidence endpoints are
copied from evaluation JSON. Baselines retain original probabilities; logistic
regression and the selected LightGBM use calibrated probabilities. The temporal
and geographic best baselines are selected on validation AP independently.
Paired point differences subtract the two recorded APs only when the recorded
row counts prove identical support; paired intervals are copied directly.
No bootstrap or performance metric is recomputed.

The example map reads only the existing strict test prediction export, the
2024 panel, cells, and pinned ADM0/ADM1 boundaries. The prediction checksum and
model identities must agree with the export sidecar and closed selections.
The temporal model is used outside held-out blocks and the geographic model
inside them. Complete 2024 weeks with national prediction coverage are ranked
by observed primary detection count, breaking ties by the earliest issue date.
The derived week, per-cell probabilities, observed counts and labels are saved
in the summary JSON and identified as derived in the map caption. The right
map uses a log1p color scale in detection counts per cell.

The selected issue week is 2024-09-02, with target interval
`[2024-09-02, 2024-09-09)` UTC: 131,310 detections, 3,352 positive cells,
and predictions for all 37,146 cells. This descriptive example combines
`strict/lightgbm_08` and `strict_geo/lightgbm_04`.

| Population | Selected model | Recorded AP | 95% AP CI | Best baseline | Baseline AP |
|---|---|---:|---|---|---:|
| temporal | `lightgbm_08` | 0.323393 | [0.221655, 0.382973] | `train_climatology` | 0.166677 |
| geographic | `lightgbm_04` | 0.383912 | [0.250128, 0.467940] | `persistence` | 0.180757 |

H2 has a recorded 95% paired AP-difference interval of
`[-0.004107230965515116, 0.0018890130209346604]` for retrospective minus strict.
The artifact has no paired point estimate, so the H2 figure shows the interval
alone. Its inclusion of zero satisfies the frozen H2 rule; it is not evidence
from an equivalence test.

All PNGs use 300 dpi. PNG and PDF copies are also written to the ignored
`data/figures/` directory. `artifacts/figures/results_summary.json` records the
plotted values, relative source paths and JSON keys, source SHA-256 checksums,
probability versions, captions, and output sizes. Map values use column arrays
with a common cell order.

| Figure in `artifacts/figures/` | PNG bytes | PDF bytes in `data/figures/` |
|---|---:|---:|
| `results_temporal.png` | 178,907 | 20,928 |
| `results_reliability.png` | 272,336 | 21,694 |
| `results_per_year.png` | 183,214 | 18,946 |
| `results_geographic.png` | 183,582 | 20,934 |
| `results_area_budget.png` | 192,222 | 21,026 |
| `results_map.png` | 565,683 | 526,210 |
| `results_h2.png` | 97,256 | 17,842 |

All seven figures were visually inspected. The notebook executes nine code
cells from a clean kernel, contains no error outputs, and has no absolute local
paths in its text outputs. Ruff passed across `src` and `tests`. The non-live,
non-local-data suite passed 356 tests with one deselected; the eight warnings
are DuckDB pandas deprecation warnings. Notebook execution also emits the
existing Windows Jupyter transport and event-loop notices.

The executed notebook is 2,248,653 bytes. The plotted-value summary is
5,254,569 bytes; the seven artifact PNGs total 1,673,200 bytes.
