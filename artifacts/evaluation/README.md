# Evaluation artifact index

Paths below are relative to this directory. Recorded artifacts are stored
byte-exact because their SHA-256 hashes are recorded in provenance records.
Do not rewrite, move or rename them. Archived attempts are retained for provenance.

## Primary benchmark

- `selection_*.json`: validation reports, frozen model choices and model hashes; see [evaluation](../../docs/evaluation.md).
- `test_*.json`: final temporal and geographic metrics, sensitivity analyses and bootstrap intervals; see [evaluation](../../docs/evaluation.md).
- `final_test_log.json`: append-only final-test access log shared by the benchmark, model comparison and environmental extension; see [evaluation](../../docs/evaluation.md).

## Post-hoc model comparison

- `comparison_*.json`: learner searches, selections, validation and exploratory test metrics, bootstrap results, resource costs and reproduction audits; see [model comparison](../../docs/model-comparison.md).

## Environmental covariates

- `extension_*.json`: vegetation and land-cover registrations, frozen inputs, fit and checkpoint records, audits, selection and test evidence, resource costs and confirmation guards; see [vegetation extension](../../docs/extension-vegetation.md) and [land-cover amendment](../../docs/extension-landcover-amendment.md).
- `extension_archive_attempt_002/`, including `retired_active/`: archived attempt inputs, registrations, fits, checkpoints, audits, resource and execution records, confirmation evidence and test-log snapshot; see [vegetation extension](../../docs/extension-vegetation.md).
- `amendments/landcover-2026-11-02/`: land-cover amendment inputs, availability and admission evidence, fit records and checkpoints; see [land-cover amendment](../../docs/extension-landcover-amendment.md).
