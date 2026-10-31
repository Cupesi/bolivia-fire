# Acquisition

The frozen protocol is `configs/protocol.yaml`. FIRMS acquisition reads its
product and bounding box. Dates passed to the CLI are inclusive; planning uses
consecutive chunks of at most five days, including chunks crossing calendar years.
No geographic or confidence filtering is applied to raw observations.

```powershell
uv sync --all-extras
uv run bolivia-fire firms status
uv run bolivia-fire acquire firms --start 2024-09-01 --end 2024-09-10 --dry-run
uv run bolivia-fire acquire firms --start 2024-09-01 --end 2024-09-10
uv run bolivia-fire firms status
uv run bolivia-fire firms availability
uv run bolivia-fire acquire boundaries
```

Set `UV_CACHE_DIR` to a writable directory if the default uv cache is unavailable.
The key loader checks environment variable `FIRMS_MAP_KEY` first, then parses the
repository `.env` as assignments without executing shell commands or expanding
variables. Keep credentials outside source control. CLI log handlers redact the
key, FIRMS credential URL paths, query strings, and exception text. Invalid
response bodies are sanitized before storage in `datasets/.quarantine/`.

## FIRMS validation and storage

The [Area API](https://firms.modaps.eosdis.nasa.gov/api/area/) accepts a starting
date and one to five days. Requests use TLS verification, explicit timeouts, and
no redirects. The client bounds response bodies to 128 MiB. HTTP 429, HTTP 5xx,
timeouts, and disconnected connections have bounded retries with exponential
full jitter and `Retry-After` support. Quota responses impose at least a
ten-minute cooldown. Status queries and their retries share a cross-process
minute gate and reserve quota before each attempt.

Every response receives an outcome: `ok_rows`, `ok_empty`, `error_text_200`,
`invalid_key`, `quota_exceeded`, `truncated`, `schema_drift`, or `http_error`.
Only the first two can become raw snapshots. Validation requires the exact SP
header, consistent column counts through the last row, interval and bounding-box
membership, valid UTC acquisition times, finite numeric measurements, and
documented codes. A complete final row does not require a trailing newline.
Missing columns or an invalid final row are `truncated`; invalid interior rows
are `schema_drift`. When `Content-Length` is present, it must match the received
transfer byte count before any HTTP content decoding. Invalid keys, invalid
calls, schema changes, and row-validation failures terminate acquisition.
Header-only responses, with or without a newline, represent successful empty
partitions. Times lacking leading zeroes are validated after zero-padding; source
bytes remain unchanged. Column types, units, and codes are in
`datasets/schemas/firms_viirs_sp.yaml`.

Partitions are written durably to `datasets/.tmp/*.csv.part`, validated, and
promoted with `os.replace` to
`datasets/raw/firms/VIIRS_SNPP_SP/request_{identity_hash}/{year}/VIIRS_SNPP_SP_{start}_{n}d.csv`.
`datasets/manifests/firms_VIIRS_SNPP_SP.jsonl` records sanitized URLs, hashes,
byte and row counts, header, versions, row-validation counters, and retrieval
metadata. Each distinct stored snapshot has one record. `--force` requests fresh
data; changed bytes receive a `__sha256_{digest_prefix}` suffix (16 hexadecimal
characters, full hash verified), preserving the original.
Identical bytes reuse the existing snapshot and manifest record.

Resume verifies stored bytes against manifest hashes before skipping requests.
Raw files and interrupted `.part` files are recovered only with exact product,
bbox, start and day_range identity plus matching content hash recorded in
`datasets/manifests/firms_request_identities.jsonl`. This durable manifest is
written before staging and records staging and intended final paths. Files
lacking identity are preserved, rejected and reported; filenames never establish
identity. Request namespaces hash product and bbox. Recovered files have null HTTP status and retrieval time when those
facts were not durably recorded; recovery time is recorded separately. A torn
last manifest record is archived before repairing the JSONL stream. Other
manifest corruption stops the run. Modified snapshots are preserved and require
a re-fetch. Files from unrelated partition plans are left in place.

`Acquisition.revalidate_quarantine(source, partition)` validates an explicitly
selected quarantine file offline, writes its original bytes through the normal
temporary-file and promotion path, and records `recovery_source` as
`quarantine_revalidation` with the original `quarantine_file` name. HTTP status
and retrieval time remain null. Only after the manifest is durable and the
snapshot hash matches is that quarantine source removed. Invalid bodies and
files outside the quarantine directory are preserved and rejected.

The acquisition run holds an OS file lock and writes owner PID, active state,
and a heartbeat every ten seconds. A previous active owner with a heartbeat at
least 120 seconds old is classified as stale after obtaining the OS lock. An
active OS lock is never stolen. The OS releases locks after a process exits.
Progress lines include partition, action, outcome, rows, bytes, and elapsed time;
they go to stdout and `datasets/.tmp/acquire_firms.log`.

Free disk is checked before each partition; the default minimum is 5 GiB.
`--minimum-free-gb` changes this threshold. `--max-partitions N` limits the number
of planned partitions examined, including existing partitions skipped on resume.

## Shared quota

All cooperating processes use `datasets/.tmp/firms_quota.lock` and an atomically
updated `firms_quota.json` ledger. Completed request costs age out after 600
seconds; unresolved reservations remain conservative outstanding costs.
Every area attempt reserves its day range, including retries. The
default cost is two transactions per requested day, including retries;
`--transactions-per-day N` configures this positive integer multiplier. A five-day
attempt therefore reserves ten transactions. The default budget is 4000 of the
5000 server transactions; `--budget N` accepts 1 through 5000.
The ledger accounts for status queries and availability requests and imposes
backpressure when its local or reconciled usage reaches the budget.

Reconciliation with `mapkey_status` occurs at most once per minute. The observed
server count is a conservative floor plus unresolved requests and responses not
covered by a later snapshot. A response is recorded by reservation token; a
snapshot releases that reservation only if its request began strictly after
the recorded response. Transport failures remain unresolved. Explicit `firms status`
waits for a fresh query if the minute gate is closed. Other commands reuse recent
status. The [documented limit](https://firms.modaps.eosdis.nasa.gov/api/area/) is
5000 transactions per ten minutes; FIRMS does not publish a larger-request cost
formula. A single sample does not establish a universal cost. Other key users
outside this coordinator can consume quota between observations.

The isolated one-day request on 2026-10-31 increased `current_transactions` from
20 to 22, with exactly one data request and fresh status queries before and
after. The previously measured five-day request cost ten transactions. Both
samples cost two transactions per day, so five-day chunks are retained.

For 2012-01-20 through 2026-01-04 inclusive, the plan has 5099 days and 1020
requests (1019 five-day chunks and one four-day chunk). At the measured cost,
the estimate is 10,198 area transactions, plus status queries and any retries.
This spans three 4000-transaction quota windows, or about 25.5 minutes at a
uniform rate of 4000 transactions per ten minutes. An end-to-end duration
estimate is about one hour if each partition takes the observed 3.495 seconds
for the September 6-10 sample; that rate stays below the configured quota.
These are estimates from limited samples: response sizes, network latency,
validation, retries, and other key users can change the duration. The full
acquisition is managed separately; use a frozen manifest copy for derived builds
while acquisition is active.

Availability is validated as CSV and sanitized before writing
`datasets/manifests/firms_availability_{UTC-date}.csv`.

## Boundary pins

The downloader reads public metadata for
[BOL ADM0](https://www.geoboundaries.org/api/current/gbOpen/BOL/ADM0/) and
[BOL ADM1](https://www.geoboundaries.org/api/current/gbOpen/BOL/ADM1/), preferring
`gjDownloadURL` over simplified geometry. Only HTTPS GitHub, raw.githubusercontent,
and media.githubusercontent download hosts are allowed; each redirect is checked
before following it. Metadata requests do not follow redirects.

GeoJSON files must be nonempty polygon feature collections; ADM1 must include a
feature whose `shapeName` is Santa Cruz. Snapshots are stored under
`datasets/raw/boundaries/`. The manifest and `datasets/sources.yaml` retain the
original URL, boundary ID, source revision, represented year, update/build dates,
license, SHA-256, byte count, and Santa Cruz check. Existing pins are reused and
hash-verified without consulting the mutable `current` endpoint. Missing pinned
files are downloaded from the pinned URL and must match the pinned hash. Pin or
snapshot hash mismatches abort without replacing existing data.

## Verification

```powershell
uv run --no-sync ruff check src tests backend
uv run --no-sync pytest -q tests backend/tests -m "not live and not local_data"
```

Tests use synthetic data and `httpx.MockTransport`. Real HTTP transports are
blocked unless the test has the `live` marker, which pytest excludes by default.

Live acceptance on 2026-10-31 produced these snapshots:

| Interval | Rows | Bytes | Acquisition |
| --- | ---: | ---: | --- |
| 2024-09-01..2024-09-05 | 125,825 | 10,028,276 | Offline quarantine re-validation |
| 2024-09-06..2024-09-10 | 135,681 | 10,808,689 | One live data request |
| 2024-09-01 | 25,165 | 2,006,741 | Isolated one-day cost measurement |

The recovered five-day source lacks a final newline and has zero row-validation
failures. Its unchanged SHA-256 is
`fcbcdab9dc2602431bd974f88f3bc38d416e46fd4cde48b19758f26e414c4e3e`.
Recovery made zero HTTP requests and removed only
`firms_truncated_203d4dd3a884419594ef2b723f97f8e5.txt`; the other quarantine
files remain. The first ten-day CLI run logged
`downloaded=1 skipped=1 recovered=0 data_requests=1`. Repeating the same command
logged `downloaded=0 skipped=2 recovered=0 data_requests=0`, with both manifest
hashes verified before skipping. Evidence is in `datasets/.tmp/acquire_firms.log`
and `datasets/manifests/firms_VIIRS_SNPP_SP.jsonl`.

Repository-wide verification on 2026-10-31 passed both commands above: Ruff
reported no issues, and pytest passed 388 tests with four deselected. The run
reported Starlette/httpx and DuckDB/pandas deprecations and a denied pytest-cache
write. Live and local-data tests were excluded; this check made no production
requests and did not reopen the final test.

Boundary acquisition stored ADM0 with one feature and 4,034,693 bytes, and ADM1
with nine features and 9,273,745 bytes. ADM1 contains Santa Cruz. Both pins use
revision `9469f09`; identifiers, licenses, and hashes are in `datasets/sources.yaml`.
The availability export has twelve product rows and 442 bytes.

## Observation coverage

Successful retrieval establishes acquisition, not sensor observation coverage.
Protocol 1.1 pins the [NASA MODAPS S-NPP outage table](https://modaps.modaps.eosdis.nasa.gov/services/production/outages_suomi_npp.html)
in `datasets/raw/outages/snpp_modaps_2026-10-31.html` and records its SHA256 in
`datasets/sources.yaml`. The parsed table is
`datasets/manifests/snpp_outages.csv`, with UTC start/end and its own pinned hash.
A day is observed only when acquired and the union of documented outages covers
strictly less than six hours. Detection and observation days publish as one
versioned bundle; `docs/dataset.md` describes pointer and provenance checks.
