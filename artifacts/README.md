# Experiment artifacts

Keep machine-readable run metadata, selection records, model hashes, prediction
checksums and the append-only final-test opening log with each experiment.
Generated model binaries and predictions belong under `models/<run>/` and
`predictions/<run>/`; evaluation JSON belongs under `evaluation/`.

Each run identifies its protocol, configuration, dataset snapshot and seed. Do
not compare runs with different snapshots without stating that difference.
Retain the selection record before opening final-test metrics. Every final-test
opening, including an attempted opening whose evaluation later fails, remains in
`evaluation/final_test_log.json`.

Synthetic resource measurements must be marked synthetic and kept separate from
scientific results. Temporary matrices under `.cache/` are disposable stage
working files, removed on normal exit. Do not track raw datasets, credentials,
temporary matrices or environment files here.
