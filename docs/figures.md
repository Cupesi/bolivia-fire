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
