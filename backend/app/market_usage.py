"""One SQLite ledger for every physical request (including SDK pagination and retries)."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from time import monotonic

from sqlalchemy import Column, Index, Integer, String, Table, Text, text

from .db import Base

from scripts.market_probe import SHANGHAI

PROVIDERS = ("hithink", "akshare-b")


usage_table = Table(
    "market_usage", Base.metadata,
    Column("id", Integer, primary_key=True), Column("provider", String, nullable=False),
    Column("at", String, nullable=False), Column("kind", String, nullable=False),
    Column("operation", String, nullable=False), Column("symbols", Integer, nullable=False),
    Column("records", Integer, nullable=False), Column("status", String, nullable=False),
    Column("elapsed_ms", Integer, nullable=False), Column("pause_until", String),
    Index("ix_market_provider_time", "provider", "at"),
)
cache_table = Table("market_cache", Base.metadata, Column("cache_key", String, primary_key=True),
                    Column("at", String, nullable=False), Column("payload", Text, nullable=False))


class MarketBlocked(RuntimeError):
    pass


def _insert(conn, provider, now, kind, operation, symbols, records, status):
    return conn.execute(text("""
        INSERT INTO market_usage(provider,at,kind,operation,symbols,records,status,elapsed_ms)
        VALUES(:p,:t,:k,:op,:n,:r,:s,0)"""), dict(
            p=provider,t=now.astimezone(SHANGHAI).isoformat(),k=kind,op=operation,n=symbols,r=records,s=status)
    ).lastrowid


class Meter:
    def __init__(self, db, provider, now, priority, operation):
        self.db = db
        self.provider = provider
        self.now = now.astimezone(SHANGHAI)
        self.priority = priority
        self.operation = operation

    def log(self, kind, symbols=0, records=0, status="OK"):
        with self.db.engine.begin() as conn:
            return _insert(conn,self.provider,self.now,kind,self.operation,symbols,records,status)

    def reserve(self):
        start = self.now.date().isoformat()
        # A single writer owns both the check and the reservation, even across SDK workers.
        with self.db.engine.begin() as c:
            c.exec_driver_sql("BEGIN IMMEDIATE")
            pause = c.execute(text("""SELECT status FROM market_usage WHERE provider=:p
                AND pause_until>:now ORDER BY id DESC LIMIT 1"""),
                {"p":self.provider,"now":self.now.isoformat()}).scalar()
            if pause:
                raise MarketBlocked(f"SOURCE_PAUSED: {self.provider} {pause}")
            limit = c.execute(text("SELECT api_daily_limit FROM settings WHERE id=1")).scalar_one()
            used = c.execute(text("""SELECT count(*) FROM market_usage WHERE provider=:p
                AND at>=:day AND at<:tomorrow AND kind='HTTP'"""),dict(
                    p=self.provider,day=start,tomorrow=(self.now.date()+timedelta(days=1)).isoformat())).scalar_one()
            # Reserve one close batch per 10 A shares, three physical Sina requests per B stock.
            market = "A" if self.provider=="hithink" else "B"
            count = c.execute(text("""SELECT count(*) FROM holdings WHERE market=:m AND enabled=1
                AND deleted=0"""),{"m":market}).scalar_one()
            close_cost = (count+9)//10 if market=="A" else 3*count
            slots = 0
            if self.priority != "close" and (self.now.hour,self.now.minute) < (15,45):
                slots = close_cost
                if self.priority != "brief":
                    report = c.execute(text("SELECT brief_time,brief_enabled FROM settings WHERE id=1")).one()
                    if report[1] and self.now.strftime("%H:%M") <= report[0]:
                        slots += 1 if market=="A" else 0  # B shares use closing data in the brief
            if used >= max(0,limit-slots):
                raise MarketBlocked(f"REQUEST_BUDGET: {self.provider} 每日预算已用完或为简报/收盘预留")
            return _insert(c,self.provider,self.now,"HTTP",self.operation,0,0,"PENDING")

    def finish(self, ident, status, elapsed):
        until = None
        if status in ("AUTH", "RATE_LIMIT"):
            # Bound repeated attempts across logical queries AND service restarts.
            if status == "AUTH":
                until = self.now.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
            else:
                with self.db.engine.connect() as c:
                    count = c.execute(text("""SELECT count(*) FROM market_usage WHERE provider=:p
                        AND at>=:d AND status='RATE_LIMIT'"""),{"p":self.provider,"d":self.now.date().isoformat()}).scalar_one()
                until = self.now+timedelta(seconds=min(3600,60*2**min(count,6)))
        with self.db.engine.begin() as c:
            c.execute(text("""UPDATE market_usage SET status=:s,elapsed_ms=:m,pause_until=:u
                  WHERE id=:i"""),dict(s=status,m=round(1000*elapsed),u=until.isoformat() if until else None,i=ident))

    def request(self, call):
        ident = self.reserve()
        start = monotonic()
        status = "ERROR"
        try:
            result = call()
            status = "OK"
            return result
        except Exception as exc:
            status = error_category(exc)
            raise
        finally:
            self.finish(ident,status,monotonic()-start)

    def read_cache(self, key, age, n):
        with self.db.engine.connect() as c:
            row=c.execute(text("SELECT at,payload FROM market_cache WHERE cache_key=:k"),{"k":key}).first()
        if row and timedelta(0)<=self.now-datetime.fromisoformat(row.at)<age:
            self.log("CACHE",n)
            return json.loads(row.payload)
        return None

    def save_cache(self,key,payload):
        with self.db.engine.begin() as c:
            c.execute(text("""INSERT INTO market_cache(cache_key,at,payload) VALUES(:k,:t,:v)
                ON CONFLICT(cache_key) DO UPDATE SET at=excluded.at,payload=excluded.payload"""),
                {"k":key,"t":self.now.isoformat(),"v":json.dumps(payload,ensure_ascii=False,allow_nan=False)})


def error_category(exception):
    metadata=getattr(exception,"metadata",{})
    value=metadata.get("http_status") or getattr(exception,"code",None)
    api=metadata.get("api_code")
    if value in (401,403) or api in (2001,2003) or str(exception).startswith("AUTH:"):
        return "AUTH"
    if value==429 or api==4001 or str(exception).startswith("RATE_LIMIT:"):
        return "RATE_LIMIT"
    return "ERROR"


def usage_summary(db, now):
    now=now.astimezone(SHANGHAI)
    day=now.date().isoformat()
    month=now.date().replace(day=1).isoformat()
    end=(now.date()+timedelta(days=1)).isoformat()
    output={}
    with db.engine.connect() as c:
        for provider in PROVIDERS:
            result={}
            for name,start in (("today",day),("month",month)):
                row=c.execute(text("""SELECT COUNT(CASE WHEN kind='HTTP' THEN 1 END) AS http_requests,
                    COUNT(CASE WHEN kind='QUERY' THEN 1 END) AS queries,
                    COALESCE(SUM(CASE WHEN kind='QUERY' THEN symbols ELSE 0 END),0) AS symbols,
                    COALESCE(SUM(records),0) AS records,
                    COUNT(CASE WHEN kind='HTTP' AND status='OK' THEN 1 END) AS successes,
                    COUNT(CASE WHEN kind='HTTP' AND status NOT IN ('OK','PENDING') THEN 1 END) AS failures,
                    COUNT(CASE WHEN kind='HTTP' AND status='PENDING' THEN 1 END) AS pending,
                    COALESCE(SUM(elapsed_ms),0) AS elapsed_ms,
                    COUNT(CASE WHEN kind='CACHE' THEN 1 END) AS cache_hits,
                    COUNT(CASE WHEN kind='HTTP' AND status='RATE_LIMIT' THEN 1 END) AS rate_limited,
                    MAX(CASE WHEN kind='HTTP' AND status='OK' THEN at END) AS last_success
                    FROM market_usage WHERE provider=:p AND at>=:t AND at<:e"""),
                    {"p":provider,"t":start,"e":end}).mappings().one()
                result[name]=dict(row)
            result["pause_reason"]=c.execute(text("""SELECT status FROM market_usage
                WHERE provider=:p AND pause_until>:t ORDER BY id DESC LIMIT 1"""),
                {"p":provider,"t":now.isoformat()}).scalar()
            limit = c.execute(text("SELECT api_daily_limit FROM settings WHERE id=1")).scalar_one()
            if not result["pause_reason"] and result["today"]["http_requests"] >= limit:
                result["pause_reason"] = "REQUEST_BUDGET"
            output[provider]=result
    return output
