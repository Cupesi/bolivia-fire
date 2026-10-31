# Artifact API

Run from the repository root:

```powershell
uv sync --all-extras
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
uv run pytest -q tests backend/tests -p no:cacheprovider
uv run ruff check src tests backend
```

`BOLIVIA_FIRE_DATA_ROOT` selects a repository-shaped data root; it must contain
`configs/protocol.yaml`. The default is the repository root. The service reads
prepared artifacts locally. It does not fit models or fetch source data.
Restart after publishing new panel, prediction, evaluation, or boundary files.
Startup discovers files and caches grid, week coverage, metadata, evaluation
summaries, and boundaries. Boundaries must match the SHA256 pins in
`datasets/sources.yaml`; simplification uses 0.005 degrees in EPSG:4326 with
topology preservation. Department borders are simplified independently.

Requests use Arrow column projection and predicates. A layer or CSV contains at
most one issue's eligible cells. A series contains at most 700 weeks. No request
materializes the full panel. JSON null labels remain null; CSV nulls are empty.
`n_primary` remains the observed detection count even when the label is missing.
`cell_index` refers to the zero-based position in `/grid` arrays, ordered by cell ID.
Dates use exact `YYYY-MM-DD`; layer/export dates must be available issue dates.
Range endpoints are inclusive and must fall within the prepared date range.
Unknown parameters, repeated parameters, malformed IDs, invalid enums, and
invalid ranges receive 422. Missing predictions and unavailable metrics receive
404; missing panel/grid data receives 503. Unexpected failures return a generic
500 without exception text or filesystem paths.

`/weeks` accepts `start`, `end`, `offset` (default 0), and `limit` (default and
maximum 700). `outage` means a documented outage interval overlaps the target
week, including short outages. `target_low_coverage` is the separate zero-bbox
detection heuristic. `label_complete_share` is the fraction of non-null labels;
neither outage flag independently establishes label completeness.

Prediction files are `artifacts/predictions/{variant}/{split}.parquet`, with
`cell_id`, `issue_date`, `model`, and `p`; `y` is optional and is not used to replace
panel observations. Model names match the producer's `strict/lightgbm` form.
Metadata lists available runs and model names. If `model` is omitted, the first
model in lexicographic order is used; this ordering does not rank model skill.
Without predictions, a series returns null probabilities and an estimate returns
404. `variant` defaults to `strict`. Known base variants and discovered run names
are accepted; filesystem paths are never constructed from request parameters.

Metrics return the prepared `artifacts/evaluation/{split}_{variant}.json`
unchanged. Only existing summaries are listed. Test summaries are hidden unless
`final_test_log.json` contains an opening timestamp matching the current protocol
SHA256. Prediction visibility does not open the final test or compute metrics.

GET responses have ETags and `Cache-Control: public, max-age=300, must-revalidate`;
matching `If-None-Match` receives 304. Health and errors use `no-store`. Gzip is
enabled for bodies of at least 1,000 bytes. CORS defaults to same-origin access.
`BOLIVIA_FIRE_CORS_ORIGINS` is a comma-separated list of explicit HTTP/HTTPS
origins, for example `http://localhost:5173`; wildcard origins are rejected.
Only GET/HEAD and the `If-None-Match` request header are allowed cross-origin.

When `frontend/dist/index.html` exists at startup, StaticFiles serves the built
frontend at `/`, with SPA fallback for extensionless routes. Unknown API routes,
missing asset filenames, encoded traversal, backslashes, and drive paths receive
404. OpenAPI is at `/api/v1/openapi.json` and interactive documentation is at
`/api/v1/docs`.

## Compact series index

```powershell
uv run python -m backend.app.prepare
```

This optional offline step projects the panel to `cell_id`, `issue_date`,
`y_primary`, and `n_primary`, sorted by cell and date, with 8,192-row Parquet
groups. It writes `artifacts/serving/series.parquet` and a manifest atomically.
The directory is generated serving data. DuckDB uses two threads, a 512 MB
memory limit, and at most 4 GB of temporary spill storage. Preparation requires
60 GiB free to preserve a 55 GiB reserve. It verifies that inputs did not change
during the build.

Startup verifies the protocol, cell file, panel file, and index SHA256 hashes.
An absent, stale, or corrupt index is ignored and requests use the original
panel. Only series requests use this index; layers retain the panel's weekly
row-group layout. Restart the service after preparing the index.
`BOLIVIA_FIRE_DISABLE_SERVING=1` forces the unprepared path for comparison.

```powershell
uv run python -m backend.benchmark --requests 50 --unprepared --output backend/measurements_unprepared.json
uv run python -m backend.benchmark --requests 50 --output backend/measurements.json
```

The benchmark starts its own uvicorn process on an available loopback port,
waits for a successful health response, measures the actual uvicorn process RSS
(the Windows virtual-environment launcher has a separate PID), and stops that
server after the workload. It records full source snapshot IDs and workload
dates/cells in the JSON files.

## Local measurements, 2026-10-31

Windows 11 build 26200, AMD Ryzen 7 9800X3D, eight logical processors reported
by Windows, 33,408,688,128 bytes physical RAM, Python 3.13.15. The real panel had
37,146 cells, 678 issue weeks from 2013-01-07 through 2025-12-29, and 25,184,988
cell-weeks. Predictions and evaluation summaries were absent during measurement.

Each run started one uvicorn worker and used a persistent HTTP client over
loopback, sequentially requesting 50 observed layers followed by 50 cell series.
Layer dates were evenly spaced across all prepared weeks. Series requests
rotated five evenly spaced grid cells and requested all 678 weeks. Timing
includes response download, gzip decompression, and JSON decoding; no endpoint
warm-up requests were made. Startup includes process launch through the first
successful health response, with readiness polling every 50 ms. OS caches were
not flushed, and other repository tasks were active.

| Measurement | Original panel | Compact index |
|---|---:|---:|
| Startup (seconds) | 2.398 | 2.332 |
| RSS after startup (bytes) | 184,811,520 | 184,893,440 |
| RSS after requests (bytes) | 267,681,792 | 235,769,856 |
| Layer p50 (ms) | 88.25 | 80.77 |
| Layer p95 (ms) | 110.89 | 97.12 |
| Series p50 (ms) | 2,501.01 | 68.00 |
| Series p95 (ms) | 2,814.11 | 105.41 |

The source snapshot IDs and request dates/cells matched between the two runs.
The compact index built in 8.044 seconds and occupied 22,930,369 bytes. The
largest decoded layer response was 471,925 bytes; the largest series response
was 15,086 bytes. D: had 108,723,605,504 bytes free after the indexed workload.
Full measurements are in `backend/measurements_unprepared.json` (06:21:12 UTC)
and `backend/measurements.json` (06:21:43 UTC).

Verification on the same date:

| Command | Outcome |
|---|---|
| `uv run ruff check src tests backend` | Passed |
| `uv run pytest -q tests backend/tests -p no:cacheprovider` | 263 passed; one live test deselected |
| `uv run python -m backend.app.prepare` | Passed; row count and size recorded above |
| Both 50-request benchmark commands above | Passed; all HTTP responses successful |
| `uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8765 --no-access-log` | Startup successful; frontend, health, observed layer, and series returned 200 |

The test run emitted one upstream Starlette deprecation warning for its httpx
TestClient adapter. The `local_data` test ran against the prepared local panel;
it skips when the cell or panel artifacts are absent.

## Exact response examples

The responses below were obtained from the explicitly synthetic fixtures in
`backend/tests/conftest.py`: three cells and three issue weeks. They describe
the wire contract, not scientific measurements. JSON objects have no outer
`data` envelope. The metrics fixture is returned verbatim.

`GET /api/v1/health`

```json
{
  "status": "ok",
  "data_ready": {
    "cells": true,
    "panel": true,
    "predictions": true,
    "evaluation": true
  }
}
```

`GET /api/v1/meta`

```json
{
  "project_name": "Bolivia Fire",
  "protocol": {
    "version": "1.1",
    "sha256": "661b841c5dade7b8f0dab0787cc6916888b498ca13ef94f6b1272055c1644fbf"
  },
  "dataset_snapshot_ids": {
    "protocol_sha256": "661b841c5dade7b8f0dab0787cc6916888b498ca13ef94f6b1272055c1644fbf",
    "panel": {
      "2024": {
        "protocol_sha256": "661b841c5dade7b8f0dab0787cc6916888b498ca13ef94f6b1272055c1644fbf",
        "firms_manifest_sha256": "synthetic-manifest"
      }
    },
    "sources_sha256": "df72c4b8f44443252eda6464b299727f95d1983bd80b13e45408be2ae8113e1a",
    "cells": {
      "protocol_sha256": "661b841c5dade7b8f0dab0787cc6916888b498ca13ef94f6b1272055c1644fbf",
      "firms_manifest_sha256": "synthetic-manifest"
    },
    "cells_sha256": "e8302ec3e5cf4a4a18469a7ed6c7deaa7b7fcdfaa4bf121673dd20d3287cc141"
  },
  "issue_date_range": {
    "start": "2024-09-02",
    "end": "2024-09-16"
  },
  "available_layers": [
    "observed",
    "estimate"
  ],
  "models": {
    "strict": [
      "strict/lightgbm"
    ]
  },
  "variants": [
    "strict"
  ],
  "evaluation_splits": {
    "strict": [
      "val_select"
    ]
  },
  "source_attributions": [
    {
      "name": "NASA FIRMS",
      "product": "VIIRS S-NPP standard processing",
      "url": "https://firms.modaps.eosdis.nasa.gov/"
    },
    {
      "name": "geoBoundaries",
      "license": "CC BY 4.0",
      "url": "https://www.geoboundaries.org/"
    },
    {
      "name": "CHIRPS",
      "provider": "Climate Hazards Center",
      "url": "https://www.chc.ucsb.edu/data/chirps"
    }
  ],
  "limitations": [
    "Satellite detections are not counts of fires, ignitions, or burned area.",
    "Zeros are not proof of absence: clouds, smoke, overpass timing, and small fires reduce detection.",
    "Retrospective estimates are not operational forecasts.",
    "Outage weeks and incomplete observation intervals have missing labels, never zero-filled labels.",
    "Strict estimates assume standard-processing detections approximate near-real-time availability; this equivalence has not been validated.",
    "CHIRPS v3 and MapBiomas historical vintages were unavailable during much of the study period."
  ],
  "boundary_simplification_tolerance_deg": 0.005
}
```

`GET /api/v1/weeks?offset=1&limit=1`

```json
{
  "weeks": [
    {
      "issue_date": "2024-09-09",
      "label_complete_share": 0.0,
      "outage": true,
      "target_low_coverage": true
    }
  ],
  "total": 3,
  "offset": 1,
  "limit": 1
}
```

`GET /api/v1/grid`

```json
{
  "origin": [
    -180.0,
    -90.0
  ],
  "resolution_deg": 0.05,
  "crs": "EPSG:4326",
  "row_range": [
    1341,
    1342
  ],
  "col_range": [
    2247,
    2248
  ],
  "cell_id": [
    "r1341c2247",
    "r1341c2248",
    "r1342c2247"
  ],
  "row": [
    1341,
    1341,
    1342
  ],
  "col": [
    2247,
    2248,
    2247
  ],
  "department": [
    "Santa Cruz",
    "Santa Cruz",
    "Beni"
  ],
  "national_area_km2": [
    20.0,
    25.0,
    24.0
  ],
  "santa_cruz": [
    true,
    true,
    false
  ],
  "geo_holdout": [
    false,
    false,
    true
  ]
}
```

`GET /api/v1/boundaries?level=1`

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "shapeName": "Santa Cruz"
      },
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [
              -68.0,
              -23.0
            ],
            [
              -67.0,
              -23.0
            ],
            [
              -67.0,
              -22.0
            ],
            [
              -68.0,
              -23.0
            ]
          ]
        ]
      }
    }
  ]
}
```

`GET /api/v1/layer?issue_date=2024-09-09&kind=observed`

```json
{
  "issue_date": "2024-09-09",
  "kind": "observed",
  "model": null,
  "variant": "strict",
  "cell_index": [
    0,
    1,
    2
  ],
  "value": [
    null,
    null,
    null
  ],
  "n_primary": [
    2,
    0,
    0
  ]
}
```

`GET /api/v1/layer?issue_date=2024-09-02&kind=estimate&model=strict/lightgbm`

```json
{
  "issue_date": "2024-09-02",
  "kind": "estimate",
  "model": "strict/lightgbm",
  "variant": "strict",
  "cell_index": [
    0,
    1
  ],
  "value": [
    0.8,
    0.1
  ],
  "n_primary": [
    3,
    0
  ]
}
```

`GET /api/v1/cells/r1341c2247/series?model=strict/lightgbm`

```json
{
  "cell_id": "r1341c2247",
  "model": "strict/lightgbm",
  "variant": "strict",
  "issue_date": [
    "2024-09-02",
    "2024-09-09",
    "2024-09-16"
  ],
  "y": [
    1,
    null,
    0
  ],
  "n_primary": [
    3,
    2,
    0
  ],
  "p": [
    0.8,
    0.4,
    null
  ]
}
```

`GET /api/v1/metrics?variant=strict&split=val_select`

```json
{
  "score": 0.4
}
```

`GET /api/v1/export?issue_date=2024-09-09&kind=observed&format=csv`

```csv
# project=Bolivia Fire; protocol_sha256=661b841c5dade7b8f0dab0787cc6916888b498ca13ef94f6b1272055c1644fbf
# issue_date=2024-09-09; kind=observed; variant=strict; model=
# dataset_snapshot_ids={"protocol_sha256":"661b841c5dade7b8f0dab0787cc6916888b498ca13ef94f6b1272055c1644fbf","panel":{"2024":{"protocol_sha256":"661b841c5dade7b8f0dab0787cc6916888b498ca13ef94f6b1272055c1644fbf","firms_manifest_sha256":"synthetic-manifest"}},"sources_sha256":"df72c4b8f44443252eda6464b299727f95d1983bd80b13e45408be2ae8113e1a","cells":{"protocol_sha256":"661b841c5dade7b8f0dab0787cc6916888b498ca13ef94f6b1272055c1644fbf","firms_manifest_sha256":"synthetic-manifest"},"cells_sha256":"e8302ec3e5cf4a4a18469a7ed6c7deaa7b7fcdfaa4bf121673dd20d3287cc141"}
# Sources: NASA FIRMS; geoBoundaries CC BY 4.0; CHIRPS.
# Detections are not fires; zeros are not proof of absence; retrospective estimates are not operational forecasts; missing labels are empty.
cell_id,issue_date,kind,variant,model,value,n_primary
r1341c2247,2024-09-09,observed,strict,,,2
r1341c2248,2024-09-09,observed,strict,,,0
r1342c2247,2024-09-09,observed,strict,,,0
```
