"""Optional private directory of security names verified from issuer disclosures."""
import json
import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=2)
def _load(path: Path, modified: int) -> dict:
    with path.open(encoding="utf-8") as source:
        records = json.load(source)
    return records if isinstance(records, dict) else {}


def verified_name(symbol: str) -> str | None:
    path = Path(os.getenv("SECURITY_MASTER_FILE", "/var/lib/stock-radar/security-master.json"))
    try:
        entry = _load(path, path.stat().st_mtime_ns).get(symbol)
    except (ValueError, OSError):
        return None
    if isinstance(entry, dict) and entry.get("source") and isinstance(entry.get("name"), str):
        return entry["name"] or None
    return None


def currency_for(symbol: str, market: str) -> str:
    if market == "B":
        return "USD" if symbol.endswith(".SH") else "HKD"
    return "CNY"
