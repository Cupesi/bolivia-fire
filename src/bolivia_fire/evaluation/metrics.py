from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

METRIC_NAMES = (
    "average_precision",
    "brier_score",
    "log_loss",
    "roc_auc",
    "expected_calibration_error",
    "recall_at_area_budget_5pct",
    "recall_at_area_budget_10pct",
)


def recall_at_area_budget(
    y: ArrayLike,
    p: ArrayLike,
    area: ArrayLike,
    issue: ArrayLike,
    budget: float,
    weights: ArrayLike | None = None,
) -> float | None:
    prepared = PreparedMetrics(y, p, area=area, issue=issue)
    return prepared.area_recall(budget, prepared.weights(weights))


class PreparedMetrics:
    def __init__(
        self,
        y: ArrayLike,
        p: ArrayLike,
        *,
        area: ArrayLike | None = None,
        issue: ArrayLike | None = None,
    ) -> None:
        self.y, self.p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
        if self.y.ndim != 1 or self.p.shape != self.y.shape:
            raise ValueError("Labels and probabilities must be aligned one-dimensional arrays")
        if not np.isin(self.y, [0, 1]).all():
            raise ValueError("Labels must be finite binary values")
        if not (np.isfinite(self.p) & (self.p >= 0) & (self.p <= 1)).all():
            raise ValueError("Probabilities must be finite and in [0, 1]")
        self.order = np.argsort(-self.p, kind="stable")
        ranked = self.p[self.order]
        self.ends = np.r_[np.flatnonzero(np.diff(ranked)), len(ranked) - 1].astype(int)
        self.ascending = np.argsort(self.p, kind="stable")
        self.loss = -(
            self.y * np.log(np.clip(self.p, 1e-15, 1 - 1e-15))
            + (1 - self.y) * np.log1p(-np.clip(self.p, 1e-15, 1 - 1e-15))
        )
        self.area = self.issue = self.area_order = self.starts = None
        if (area is None) != (issue is None):
            raise ValueError("Area and issue must be provided together")
        if area is not None:
            self.area, self.issue = np.asarray(area, dtype=float), np.asarray(issue)
            if self.area.shape != self.y.shape or self.issue.shape != self.y.shape:
                raise ValueError("Area and issue must align with labels")
            if not (np.isfinite(self.area) & (self.area > 0)).all():
                raise ValueError("National areas must be positive and finite")
            self.area_order = np.lexsort((np.arange(len(self.y)), -self.p, self.issue))
            weeks = self.issue[self.area_order]
            self.starts = np.r_[0, np.flatnonzero(weeks[1:] != weeks[:-1]) + 1]

    def weights(self, weights: ArrayLike | None) -> NDArray:
        values = np.ones(len(self.y)) if weights is None else np.asarray(weights, dtype=float)
        if values.shape != self.y.shape or not (np.isfinite(values) & (values >= 0)).all():
            raise ValueError("Weights must be aligned, finite and nonnegative")
        return values

    def area_recall(self, budget: float, weights: NDArray) -> float | None:
        if not 0 < budget <= 1:
            raise ValueError("Area budget must be in (0, 1]")
        if self.area is None or not len(self.y) or np.dot(self.y, weights) == 0:
            return None
        order = self.area_order
        weighted_area = self.area[order] * weights[order]
        cumulative = np.cumsum(weighted_area)
        before = cumulative - weighted_area
        offsets = before[self.starts]
        sizes = np.diff(np.r_[self.starts, len(order)])
        before -= np.repeat(offsets, sizes)
        totals = np.add.reduceat(weighted_area, self.starts)
        remaining = np.repeat(totals * budget, sizes) - before
        fraction = np.divide(
            remaining, weighted_area, out=np.zeros(len(order)), where=weighted_area > 0
        ).clip(0, 1)
        return float(np.dot(self.y[order] * weights[order], fraction) / np.dot(self.y, weights))

    def compute(
        self, weights: ArrayLike | None = None, *, reliability: bool = True
    ) -> dict[str, Any]:
        w = self.weights(weights)
        total, positives = w.sum(), np.dot(w, self.y)
        reasons: dict[str, str] = {}
        result: dict[str, Any] = {name: None for name in METRIC_NAMES}
        if total == 0:
            reasons = dict.fromkeys(METRIC_NAMES, "empty_subset")
            result.update(undefined_reasons=reasons, reliability=[])
            return result
        result["brier_score"] = float(np.dot(w, (self.p - self.y) ** 2) / total)
        result["log_loss"] = float(np.dot(w, self.loss) / total)
        tp = np.cumsum((self.y * w)[self.order])[self.ends]
        fp = np.cumsum(((1 - self.y) * w)[self.order])[self.ends]
        delta_tp = np.diff(np.r_[0, tp])
        if 0 < positives < total:
            precision = np.divide(tp, tp + fp, out=np.zeros_like(tp), where=tp + fp > 0)
            result["average_precision"] = float(np.dot(delta_tp, precision) / positives)
            delta_fp = np.diff(np.r_[0, fp])
            result["roc_auc"] = float(
                np.dot(delta_tp, total - positives - fp + 0.5 * delta_fp)
                / (positives * (total - positives))
            )
        else:
            reasons.update(average_precision="single_class", roc_auc="single_class")
        rows = []
        order = self.ascending
        cumulative = np.cumsum(w[order])
        previous = cumulative - w[order]
        bins = min(10, max(1, int(total)))
        ece = 0.0
        for index in range(bins):
            left, right = index * total / bins, (index + 1) * total / bins
            first, last = (
                np.searchsorted(cumulative, left, side="right"),
                np.searchsorted(previous, right, side="left"),
            )
            positions = order[first:last]
            portions = np.maximum(
                0,
                np.minimum(cumulative[first:last], right) - np.maximum(previous[first:last], left),
            )
            mass = portions.sum()
            if not mass:
                continue
            predicted = float(np.dot(self.p[positions], portions) / mass)
            observed = float(np.dot(self.y[positions], portions) / mass)
            ece += mass / total * abs(predicted - observed)
            if reliability:
                rows.append(
                    {
                        "bin": index,
                        "count": float(mass),
                        "mean_probability": predicted,
                        "positive_rate": observed,
                    }
                )
        result["expected_calibration_error"] = float(ece)
        for budget, name in ((0.05, METRIC_NAMES[-2]), (0.1, METRIC_NAMES[-1])):
            result[name] = self.area_recall(budget, w)
            if result[name] is None:
                reasons[name] = "area_not_supplied" if self.area is None else "no_positives"
        result.update(undefined_reasons=reasons, reliability=rows)
        return result


def metrics(
    y: ArrayLike,
    p: ArrayLike,
    *,
    area: ArrayLike | None = None,
    issue: ArrayLike | None = None,
    weights: ArrayLike | None = None,
) -> dict[str, Any]:
    return PreparedMetrics(y, p, area=area, issue=issue).compute(weights)
