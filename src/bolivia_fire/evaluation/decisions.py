from __future__ import annotations

from typing import Any


def hypothesis_decision(interval: dict[str, Any], *, contains_zero: bool = False) -> dict[str, Any]:
    lower, upper = interval["lower"], interval["upper"]
    if lower is None or upper is None:
        return {"supported": None, "reason": "undefined_paired_interval", "interval": interval}
    return {
        "supported": bool(lower <= 0 <= upper) if contains_zero else bool(lower > 0),
        "reason": None,
        "interval": interval,
    }
