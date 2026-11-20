"""Evaluate the latest completed session of an already validated close series."""
from collections.abc import Mapping, Sequence
from typing import Protocol

from stock_radar.indicators.rsi import calculate_rsi_bundle
from stock_radar.rules.rsi import evaluate_rsi_v1


class HistoryRule(Protocol):
    """Extension contract for future double-top and bearish-divergence rules.

    Implementations inspect only the supplied history up to the decision date.
    No pattern rule is registered in V1.
    """

    def __call__(self, closes: Sequence[float],
                 indicators: Mapping[str, Sequence[float | None]]) -> str | None: ...


def evaluate_history(closes: Sequence[float], *, overbought: float = 80.0,
                     high_level: float = 70.0) -> dict:
    """Require 100 bars for a signal; compute crossings only on the last pair."""
    bundle = calculate_rsi_bundle(closes)
    enough = len(closes) >= 100
    today = {name: values[-1] if enough else None for name, values in bundle.items()}
    prev6 = bundle["rsi6"][-2] if enough else None
    prev12 = bundle["rsi12"][-2] if enough else None
    event = evaluate_rsi_v1(prev6, prev12, today["rsi6"], today["rsi12"],
                            overbought=overbought, high_level=high_level)
    if (overbought, high_level) != (80, 70):
        event["strategy_version"] = f"rsi-v1.0:{float(overbought)}:{float(high_level)}"
    return {**event, **today, "prev_rsi6": prev6, "prev_rsi12": prev12}
