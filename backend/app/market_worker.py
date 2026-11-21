"""Isolated AKShare transport; receive only necessary symbol and ledger coordinates."""
from contextlib import redirect_stdout
from datetime import date, datetime
import io
import json
import re
import sys

from .db import DB
from .market_usage import Meter
from .sdk_transport import metered_sdk
from .providers import failure_reason
from scripts import market_probe as probe


def sdk_worker(job):
    db=DB(job["database"])
    meter=Meter(db,"akshare-b",datetime.fromisoformat(job["now"]),job["priority"],job["operation"])
    with redirect_stdout(io.StringIO()), metered_sdk(meter):
        if job["operation"] == "close":
            rows=probe.fetch_akshare_b(job["symbols"][0], date.fromisoformat(job["start"]),date.fromisoformat(job["end"]))
            return rows
        import akshare as ak
        frame=ak.stock_zh_b_spot()
        selected={}
        for record in frame.to_dict(orient="records"):
            match=re.fullmatch(r"(sh|sz)([0-9]{6})",str(record["代码"]).lower())
            if not match:
                continue
            symbol=f"{match[2]}.{match[1].upper()}"
            if symbol in job["symbols"]:
                selected[symbol]={k:(v if isinstance(v,str) or v is None else
                    float(v) if float('-inf')<float(v)<float('inf') else None)
                    for k,v in record.items() if k in ("名称","最新价","涨跌幅","昨收","今开","最高","最低")}
        return {"selected":selected,"source_records":len(frame)}


if __name__=="__main__":
    try:
        print(json.dumps({"result":sdk_worker(json.load(sys.stdin))},allow_nan=False))
    except Exception as exc:
        print(json.dumps({"error":failure_reason(exc)}))
