"""RSI alert events, not trade or investment recommendations."""
from __future__ import annotations


def evaluate_rsi_v1(
    prev_rsi6: float | None,
    prev_rsi12: float | None,
    curr_rsi6: float | None,
    curr_rsi12: float | None,
    *,
    overbought: float = 80.0,
    high_level: float = 70.0,
) -> dict[str, object]:
    """Compute deterministic R1/R2 events and current display state.

    R1: prev RSI6 <= 80 and current RSI6 > 80.
    R2: prev RSI6 >= prev RSI12 and current RSI6 < current RSI12
        and prev RSI6 >= 70.
    """
    if not (0 < overbought <= 100 and 0 <= high_level <= 100):
        raise ValueError("invalid RSI thresholds")
    for value in (prev_rsi6, prev_rsi12, curr_rsi6, curr_rsi12):
        if value is not None and not 0 <= float(value) <= 100:
            raise ValueError("RSI values must be between 0 and 100")
    events: list[str] = []
    if prev_rsi6 is not None and curr_rsi6 is not None:
        if prev_rsi6 <= overbought and curr_rsi6 > overbought:
            events.append("RSI_OVERBOUGHT_CROSS")
    if None not in (prev_rsi6, prev_rsi12, curr_rsi6, curr_rsi12):
        if (prev_rsi6 >= prev_rsi12  # type: ignore[operator]
                and curr_rsi6 < curr_rsi12  # type: ignore[operator]
                and prev_rsi6 >= high_level):  # type: ignore[operator]
            events.append("RSI_HIGH_DEATH_CROSS")

    if curr_rsi6 is None:
        status = "INSUFFICIENT_DATA"
    elif "RSI_HIGH_DEATH_CROSS" in events:
        status = "ALERT"
    elif curr_rsi6 > overbought or "RSI_OVERBOUGHT_CROSS" in events:
        status = "WATCH"
    else:
        status = "NO_RULE_TRIGGERED"
    return {"events": events, "status": status, "strategy_version": "rsi-v1.0"}
