from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import ArrayLike

from bolivia_fire.evaluation.metrics import METRIC_NAMES, PreparedMetrics


def block_bootstrap(
    y: ArrayLike,
    p: ArrayLike,
    blocks: ArrayLike,
    *,
    baseline: ArrayLike | None = None,
    area: ArrayLike | None = None,
    issue: ArrayLike | None = None,
    replicates: int = 500,
    seed: int = 7,
) -> dict[str, Any]:
    if replicates < 1:
        raise ValueError("Bootstrap replicate count must be positive")
    prepared = PreparedMetrics(y, p, area=area, issue=issue)
    reference = None if baseline is None else PreparedMetrics(y, baseline, area=area, issue=issue)
    labels = np.asarray(blocks)
    if labels.shape != prepared.y.shape:
        raise ValueError("Bootstrap blocks must align with labels")
    unique, inverse = np.unique(labels, return_inverse=True)
    values: dict[str, list[float]] = {name: [] for name in METRIC_NAMES}
    differences: dict[str, list[float]] = {name: [] for name in METRIC_NAMES}
    rng = np.random.default_rng(seed)
    for _ in range(replicates if len(unique) else 0):
        sampled = rng.integers(0, len(unique), len(unique))
        weights = np.bincount(sampled, minlength=len(unique))[inverse]
        current = prepared.compute(weights, reliability=False)
        compared = reference.compute(weights, reliability=False) if reference else None
        for name in METRIC_NAMES:
            if current[name] is not None:
                values[name].append(current[name])
                if compared and compared[name] is not None:
                    differences[name].append(current[name] - compared[name])

    def intervals(samples: dict[str, list[float]]) -> dict[str, Any]:
        return {
            name: {
                "lower": float(np.percentile(draws, 2.5)) if draws else None,
                "upper": float(np.percentile(draws, 97.5)) if draws else None,
                "valid_replicates": len(draws),
                "reason": None if draws else "undefined_in_all_replicates",
            }
            for name, draws in samples.items()
        }

    return {
        "replicates": replicates,
        "seed": seed,
        "blocks": len(unique),
        "confidence_level": 0.95,
        "metrics": intervals(values),
        "paired_difference": intervals(differences) if reference else None,
    }
