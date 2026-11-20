"""Deterministic Wilder RSI reference. Compare with terminal formula before production."""
from __future__ import annotations

import math
from typing import Sequence


def rsi_wilder(closes: Sequence[float], period: int) -> list[float | None]:
    """Return one RSI per close; first `period` entries are None.

    Seed is the simple average of the first `period` changes. Smoothing factor is
    1/period afterwards. Flat prices return RSI=50 after warmup by project policy.
    """
    if not isinstance(period, int) or period < 1:
        raise ValueError("period must be a positive integer")
    prices = [float(v) for v in closes]
    if any(not math.isfinite(v) or v <= 0 for v in prices):
        raise ValueError("closes must contain only finite positive prices")
    result: list[float | None] = [None] * len(prices)
    if len(prices) <= period:
        return result

    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(d, 0.0) for d in changes]
    losses = [max(-d, 0.0) for d in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def ratio(g: float, l: float) -> float:
        if l == 0:
            return 100.0 if g > 0 else 50.0
        if g == 0:
            return 0.0
        return 100.0 - 100.0 / (1.0 + g / l)

    result[period] = ratio(avg_gain, avg_loss)
    for index in range(period + 1, len(prices)):
        avg_gain = (avg_gain * (period - 1) + gains[index - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index - 1]) / period
        result[index] = ratio(avg_gain, avg_loss)
    return result


def calculate_rsi_bundle(closes: Sequence[float]) -> dict[str, list[float | None]]:
    return {f"rsi{n}": rsi_wilder(closes, n) for n in (6, 12, 24)}
