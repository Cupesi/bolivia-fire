# Reproduction

Run the PowerShell sequence below from a clean clone. Keep
[protocol 1.1](../configs/protocol.yaml), [experiment configuration](../configs/experiments.yaml)
and [source pins](../datasets/sources.yaml) unchanged. The strict snapshot has
37,146 cells and 678 issues; [the summary](../artifacts/figures/dataset_summary.json)
records exact input hashes for comparison.

Primary results, all pre-registered sensitivity analyses and the exploratory learner
comparison are published in [results](results.md) and [model comparison](model-comparison.md).
Later test openings are logged separately with their reasons. The commands below reproduce
the pipeline and require the named local artifacts and credentials.

## 1. Install and configure access

Install Git, uv, Python 3.12 or newer and Node 22.12 or newer with npm.

```powershell
git clone https://github.com/Cupesi/bolivia-fire.git
cd bolivia-fire
uv sync --all-extras
uv run --no-sync bolivia-fire --help
```

Copy [.env.example](../.env.example) to `.env` and set `FIRMS_MAP_KEY` to a NASA
FIRMS map key locally. The loader checks the environment first, then the local
file. Never print the key or put it in commands, manifests or source control.
Builds after acquisition use local files and do not need the key.

## 2. Acquire observations and pinned boundaries

```powershell
uv run --no-sync bolivia-fire acquire firms --start 2012-01-20 --end 2026-01-04
uv run --no-sync bolivia-fire acquire boundaries
```

The FIRMS range includes antecedent history and the full final target week.
Acquisition uses bounded five-day requests, validates responses, and records
identities and SHA256 hashes. Repeating the command resumes and verifies local
snapshots. Boundary acquisition restores missing files from pinned URLs and
requires matching hashes. Do not use `--force` to reproduce an existing snapshot.
See [acquisition](acquisition.md) for quota, disk and resume behavior.

The outage HTML is a local dated snapshot, while its parsed table and source pin
are in Git. Restore it before building detections. This command reuses a matching
local snapshot or fetches the recorded public URL, verifies its hash before
writing, and checks the parsed table:

```powershell
@'
import hashlib
from pathlib import Path

import httpx
import yaml

from bolivia_fire.config import Paths
from bolivia_fire.firms.storage import durable_write
from bolivia_fire.outages import load_outages

root = Path.cwd()
pin = yaml.safe_load((root / "datasets/sources.yaml").read_text(encoding="utf-8"))["snpp_outages"]
snapshot = root / pin["path"]
if not snapshot.exists():
    response = httpx.get(pin["url"], timeout=60)
    response.raise_for_status()
    content = response.content
    if hashlib.sha256(content).hexdigest() != pin["sha256"]:
        raise RuntimeError("Provider page changed; restore the original hash-pinned outage HTML")
    durable_write(snapshot, content)
load_outages(Paths(root))
'@ | uv run --no-sync python -
```

The provider URL is mutable. If its current bytes differ, the original dated
HTML with the recorded hash is required; changing the pin would create a new
dataset. Fresh FIRMS responses can also differ from the reference acquisition.
Exact byte reproduction requires the original raw snapshots and manifests;
a new download alone cannot guarantee their availability or identity.

## 3. Build the strict dataset

```powershell
uv run --no-sync bolivia-fire build cells
uv run --no-sync bolivia-fire build detections
uv run --no-sync bolivia-fire build panel
```

Cells use the pinned national and department boundaries. Detection construction
deduplicates canonical raw observations and publishes detections and observation
days as one versioned bundle. Panel construction uses every protocol Monday and
writes yearly files. Do not add issue bounds for a full rebuild: a bounded build
replaces each selected year's file with only the requested issues.
[Dataset construction](dataset.md) defines labels, filters and provenance checks.

## 4. Extract CHIRPS and build retrospective features

```powershell
uv run --no-sync bolivia-fire covariates chirps-annual --start-year 2012 --end-year 2026
uv run --no-sync bolivia-fire covariates features
```

Run one extraction process. Annual downloads are sequential and temporary;
native Bolivia windows and manifests are retained. HTTP 429 can switch to the
verified FTP directory. For the incomplete 2026 year, extraction uses January
1–4 and falls back to daily COG windows when the annual file is absent. Repeating
the command resumes verified checkpoints. Follow [covariate status](covariates.md)
for cooldowns and provider access; do not treat partial years as full coverage.

Features join by cell and issue date. Missing rainfall is never zero-filled;
retrospective fitting rejects incomplete values or invalid provenance. Current
retrospective model inputs are the frozen CHIRPS rainfall columns. MapBiomas
remains omitted because public raster access returned HTTP 403.

## 5. Train, select and calibrate on validation years

```powershell
uv run --no-sync bolivia-fire train --variant strict
uv run --no-sync bolivia-fire select --variant strict
uv run --no-sync bolivia-fire train --variant retrospective
uv run --no-sync bolivia-fire select --variant retrospective
```

The strict path can run independently of rainfall extraction;
the retrospective commands require step 4 to finish. Each train/select command
also produces its corresponding geographic model with cell climatology omitted.
Fitting uses 2013–2020, selection and early stopping use 2021, and isotonic
calibration uses 2022. Held-out geographic labels never enter these stages.
Preserve all closed selection records and matching input hashes before evaluation.
See [evaluation](evaluation.md) for the frozen search, eligibility and artifacts.

## 6. Open the final test once and export predictions

After both intended selections are closed and their source snapshots are stable:

```powershell
uv run --no-sync bolivia-fire evaluate --variant strict --open-final-test
uv run --no-sync bolivia-fire predict --variant strict --split test
uv run --no-sync bolivia-fire predict --variant retrospective --split test
```

The evaluation opens 2023–2025 once and records the opening before loading test
labels, including attempts that subsequently fail. A strict report includes the
paired retrospective comparison when the matching closed selection and complete
inputs are available. Do not run a second final-test evaluation as a routine
check: later openings require an explicit `--reopen-reason` and remain logged.
If retrospective inputs are unavailable, its comparison is null with a reason;
omit its prediction export until those inputs and selections exist.

Prediction exports contain calibrated estimates for temporal and geographic
populations and do not compute test metrics. They retain missing labels when
history eligibility permits. Changing panel, covariate or fitted-model inputs
requires the matching stages to be rebuilt; do not reuse stale selections.

## 7. Prepare and serve the API and UI

Build the frontend before starting the API so its static directory is discovered:

```powershell
cd frontend
npm ci
npm run check
npm run test
npm run build
cd ..
uv run --no-sync python -m backend.app.prepare
uv run --no-sync uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/` for the explorer and
`http://127.0.0.1:8765/api/v1/docs` for the API contract. Preparation creates an
optional compact series index. The API also works without it, using the original
panel. Restart after artifact changes. Missing predictions disable estimates;
observations remain available. See [API](api.md) and [frontend](frontend.md).

Offline verification commands, from the repository root:

```powershell
uv run --no-sync pytest -q
uv run --no-sync pytest -q backend/tests -p no:cacheprovider
```

The suites include synthetic build/model workflows and coverage/provenance
checks. Live FIRMS tests are deselected by default; backend tests also exercise
local artifacts when present. Passing synthetic tests does not establish real
model skill, full acquisition coverage or a full-scale resource bound.

## Recorded-artifact notebooks

After restoring the local dataset, recorded evaluation JSON and static figures, execute
each notebook from the repository root with a clean kernel:

```powershell
uv run --no-sync jupyter nbconvert --to notebook --execute --inplace notebooks/01_exploration.ipynb --ExecutePreprocessor.timeout=240
uv run --no-sync jupyter nbconvert --to notebook --execute --inplace notebooks/02_features.ipynb --ExecutePreprocessor.timeout=240
uv run --no-sync jupyter nbconvert --to notebook --execute --inplace notebooks/03_modeling.ipynb --ExecutePreprocessor.timeout=240
uv run --no-sync jupyter nbconvert --to notebook --execute --inplace notebooks/04_evaluation.ipynb --ExecutePreprocessor.timeout=240
```

[Exploration](../notebooks/01_exploration.ipynb) describes observation coverage and labels.
[Features](../notebooks/02_features.ipynb) reconstructs one real 2024 cell-week and checks
history against the saved panel, future-detection invariance and LOYO climatology.
[Modeling](../notebooks/03_modeling.ipynb) reads validation records and saved-model gain
importance. [Evaluation](../notebooks/04_evaluation.ipynb) displays recorded final-test
and sensitivity results, with a final post-hoc comparison section that remains executable
when alternative-learner artifacts are absent.

These commands fit no models, create no new predictions and make no final-test openings.
They read the local canonical detection bundle, panel, CHIRPS features, selected strict
model objects, evaluation records and existing PNGs. Only notebook outputs are updated.
See [figures](figures.md) for the notebook inventory, figure sources and visual checks.

## Containerized tests

With Docker running and `python:3.13-slim` and `node:22-bookworm-slim` already
available locally, run from the repository root in a POSIX shell or Git Bash:

```sh
sh scripts/docker-test.sh
```

The script mounts a sanitized source snapshot read-only at `/src`, copies it to
container-local `/work`, lists that workdir and checks that neither path contains
`.env`. Secrets, local datasets, models, predictions, host dependency directories
and generated caches are excluded before mounting. The Python stage installs
Git, `libexpat1`, `libgomp1` and uv, runs `uv sync --all-extras --frozen`, Ruff and
the offline pytest suites, including backend tests; `live` and `local_data`
tests are deselected.
The frontend stage runs `npm ci`, `npm run check`, `npm run test` and `npm run build`.

Stages run sequentially with two CPUs and a 3 GiB memory limit each. Images are
never pulled; package dependencies require network access. Named volumes
`bolivia-fire-uv-cache` and `bolivia-fire-npm-cache` retain download caches.
Containers and the temporary source snapshot are removed after use. The script
reports stage wall times and exit codes and checks a 55 GiB free-space floor on
the repository drive before and between stages. All test and build outputs stay
inside the disposable containers.

## Recorded resource measurements

| Scope | Recorded result | Evidence |
| --- | --- | --- |
| Strict panel population | 25,184,988 cell-weeks across 37,146 cells and 678 issues | [Dataset summary](../artifacts/figures/dataset_summary.json), [figures](figures.md) |
| Partial CHIRPS extraction, 41 days in 2024 | 70,267,042 committed transfer bytes; 9,298,006 compressed bytes; checkpoint timestamps span 157.100 s under the earlier unpaced policy | [Covariates](covariates.md) |
| Offline CHIRPS checkpoint promotion | 0.410 s; 170.52 MiB process peak working set | [Covariates](covariates.md) |
| Full-panel serving-index preparation | 8.044 s; 22,930,369 output bytes | [API measurements](api.md) |
| API with compact index | Startup 2.332 s; RSS 184,893,440 bytes after startup and 235,769,856 after requests | [Measurement JSON](../backend/measurements.json) |
| API compact-index workload | 50 layers and 50 series; p95 97.12 ms and 105.41 ms respectively | [Measurement JSON](../backend/measurements.json) |

These are local measurements on the snapshots and machines described in the
linked records. CHIRPS values are partial-extraction measurements, not full-range
runtime or storage totals. No production model runtime or full-scale peak RAM
measurement is asserted here. Acquisition duration estimates in older notes are
not measured full-run durations.

CHIRPS extraction preserves a 55 GB free-space floor and reserves the remaining
annual download before starting it. Preparation requires 60 GiB free to preserve
a 55 GiB reserve; its DuckDB settings allow 512 MB query memory and up to 4 GB
temporary spill. Detection staging also uses a 512 MiB DuckDB query limit.
These are configured limits, not measured total process peaks. Keep room for
raw snapshots, one annual download, processed outputs and temporary model
matrices. The [API measurement](../backend/measurements.json) recorded
108,723,605,504 free disk bytes after its indexed workload; that is a machine
checkpoint, not a minimum storage requirement for a new clone.
