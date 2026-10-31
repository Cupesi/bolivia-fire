from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray


@dataclass
class Climatology:
    index: int

    def predict(self, x: NDArray) -> NDArray:
        return np.clip(x[:, self.index], 1e-6, 1 - 1e-6)


@dataclass
class Persistence:
    index: int = 0
    probabilities: NDArray = field(default_factory=lambda: np.zeros(4))

    @staticmethod
    def bins(counts: NDArray) -> NDArray:
        return np.searchsorted([1, 2, 5], counts, side="right")

    def fit(self, counts: NDArray, y: NDArray) -> Persistence:
        bins = self.bins(counts)
        totals = np.bincount(bins, minlength=4)
        positives = np.bincount(bins, weights=y, minlength=4)
        self.probabilities = np.divide(
            positives, totals, out=np.full(4, float(np.mean(y))), where=totals > 0
        )
        return self

    def predict(self, x: NDArray) -> NDArray:
        counts = x if x.ndim == 1 else x[:, self.index]
        return self.probabilities[self.bins(counts)]
