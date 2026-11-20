#!/usr/bin/env python3
"""Offline demonstration of RSI calculation and risk flags; NO market API."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from stock_radar.indicators.rsi import calculate_rsi_bundle
from stock_radar.rules.rsi import evaluate_rsi_v1

if __name__ == "__main__":
    prices = [100 + i * .18 + ((i % 11) - 5) * 1.4 for i in range(140)]
    bundle = calculate_rsi_bundle(prices)
    print("DEMO ONLY: synthetic close prices; NOT stock-market data")
    print("Last RSI:", {k: round(v[-1], 3) if v[-1] is not None else None for k, v in bundle.items()})
    rule = evaluate_rsi_v1(prev_rsi6=82, prev_rsi12=78, curr_rsi6=74, curr_rsi12=77)
    print("DEMO rule evaluation:", rule)
