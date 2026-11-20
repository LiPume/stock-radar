"""Real daily history adapters. Keys and HTTP exception bodies stay in the backend."""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import redirect_stdout
from datetime import date
import io
import json
import os
import subprocess
import sys
from time import sleep

from scripts import market_probe as probe


def failure_reason(error: Exception) -> str:
    # market_probe emits sanitized categories. Never echo arbitrary upstream bodies.
    message = str(error)
    for prefix in ("AUTH", "API_ERROR code=", "HTTP_ERROR", "NETWORK_UNAVAILABLE",
                   "EMPTY", "SCHEMA_CHANGED", "DEPENDENCY_MISSING", "UPSTREAM_ERROR"):
        if message.startswith(prefix):
            return message[:180]
    return f"UPSTREAM_ERROR: {type(error).__name__}"


def b_history(symbol: str, start: date, end: date) -> list[dict]:
    # The SDK has several blocking requests with no deadline. Bound the entire fetch.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "backend.app.providers", symbol, start.isoformat(), end.isoformat()],
            capture_output=True, timeout=45, check=True, text=True,
        )
        reply = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("NETWORK_UNAVAILABLE: AKShare timed out") from None
    except (ValueError, subprocess.CalledProcessError):
        raise RuntimeError("UPSTREAM_ERROR: AKShare worker failed") from None
    if "error" in reply:
        raise RuntimeError(reply["error"])
    return reply["bars"]


def fetch_history(stocks: Sequence[tuple[str, str]], start: date, end: date
                  ) -> Iterator[tuple[str, list[dict], str | None]]:
    """Yield a result for each requested (symbol, market), including missing A groups."""
    a = [symbol for symbol, market in stocks if market == "A"]
    b = [symbol for symbol, market in stocks if market == "B"]
    for i in range(0, len(a), 10):
        batch = a[i:i + 10]
        try:
            payload, _metadata = probe.fetch_hithink(batch, start, end, os.getenv("HITHINK_FINANCE_API_KEY", "").strip())
            data, errors = probe.convert_hithink_payload(payload, batch)
        except (RuntimeError, ValueError, OSError) as exc:
            data, errors = {}, dict.fromkeys(batch, failure_reason(exc))
        for symbol in batch:
            yield symbol, data.get(symbol, []), errors.get(symbol)
    for i, symbol in enumerate(b):
        if i:
            sleep(1)
        try:
            rows = b_history(symbol, start, end)
        except (RuntimeError, ValueError, OSError) as exc:
            yield symbol, [], failure_reason(exc)
        else:
            yield symbol, rows, None


if __name__ == "__main__":
    try:
        code = probe.parse_market_code(sys.argv[1], "B")
        with redirect_stdout(io.StringIO()):
            rows = probe.fetch_akshare_b(code, date.fromisoformat(sys.argv[2]), date.fromisoformat(sys.argv[3]))
        print(json.dumps({"bars": rows}))
    except Exception as exc:
        print(json.dumps({"error": failure_reason(exc)}))
