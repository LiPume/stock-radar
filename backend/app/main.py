"""Authenticated HTTP API for the single-process local holdings monitor."""
from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from scripts.market_probe import current_time, latest_closed_session, parse_market_code
from .auth import Auth, LoginDenied
from .db import CheckRun, DailyBar, DB, Delivery, Holding, Setting, SignalEvent, timestamp


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Payload):
    password: str = Field(min_length=1, max_length=512)


class HoldingInput(Payload):
    symbol: str = Field(min_length=6, max_length=12)
    market: Literal["A", "B"]
    display_name: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=500)


class HoldingPatch(Payload):
    enabled: bool | None = None
    display_name: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=500)


class SettingsInput(Payload):
    overbought: float = Field(gt=0, le=100)
    high_level: float = Field(ge=0, le=100)
    notifications_enabled: bool

    @classmethod
    def public_values(cls, row: Setting) -> dict:
        return {key: getattr(row, key) for key in cls.model_fields}


def record(row) -> dict | None:
    if row is None:
        return None
    return {c.key: getattr(row, c.key) for c in row.__table__.columns}


def holding_record(row: Holding) -> dict:
    data = record(row)
    del data["deleted"]
    if row.enabled and row.data_status == "OK":
        try:
            expected = latest_closed_session(current_time()).isoformat()
        except ValueError:
            data.update(data_status="DATA_UNAVAILABLE", status="DATA_UNAVAILABLE", reason="当前年份的交易日历待更新")
        else:
            if row.bar_date != expected:
                data.update(data_status="STALE", status="STALE", reason=f"最新完整交易日为 {expected}，请重新检测")
    return data


def require_token(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token or not request.app.state.auth.valid(token):
        raise HTTPException(401, "请重新登录", headers={"WWW-Authenticate": "Bearer"})
    return token


def envelope(request: Request, data=None, error=None) -> dict:
    return {"data": data, "error": error, "meta": {"request_id": request.state.request_id, "as_of": timestamp()}}


def create_app(database_url: str | None = None, password_hash: str | None = None,
               scanner_factory=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = DB(database_url or os.getenv("DATABASE_URL", "sqlite:///./data/stock_radar.db"))
        db.migrate()
        if scanner_factory is None:
            from .scanner import ScanService
            scanner = ScanService(db)
        else:
            scanner = scanner_factory(db)
        app.state.db = db
        app.state.scanner = scanner
        app.state.auth = Auth(password_hash if password_hash is not None else os.getenv("ADMIN_PASSWORD_HASH", ""))
        try:
            scanner.start()
            yield
        finally:
            scanner.stop()
            db.engine.dispose()

    app = FastAPI(title="持仓雷达", lifespan=lifespan)
    default_origins = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173"
    origins = [o.strip().rstrip("/") for o in os.getenv("CORS_ORIGINS", default_origins).split(",") if o.strip()]
    if "*" in origins:
        raise ValueError("CORS_ORIGINS requires explicit origins")
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"], expose_headers=["X-Request-ID"])

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        request.state.request_id = uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, ex: StarletteHTTPException):
        return JSONResponse(envelope(request, error={"code": ex.status_code, "message": str(ex.detail)}),
                            status_code=ex.status_code, headers=ex.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, ex: RequestValidationError):
        # The submitted body can contain an administrator password.
        field = ".".join(str(part) for part in ex.errors()[0]["loc"][1:])
        return JSONResponse(envelope(request, error={"code": 422, "message": f"请求参数无效：{field}"}), status_code=422)

    @app.get("/api/v1/health")
    def health(request: Request):
        return envelope(request, {"status": "ok"})

    @app.post("/api/v1/auth/login")
    def login(request: Request, payload: Login):
        auth = request.app.state.auth
        if password_hash is None and not os.getenv("ADMIN_PASSWORD_HASH"):
            path = Path(os.getenv("ADMIN_PASSWORD_HASH_FILE", "data/admin.hash"))
            try:
                auth.password_hash = path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError):
                auth.password_hash = ""
        try:
            token = auth.login(payload.password, request.client.host if request.client else "local")
        except LoginDenied as ex:
            raise HTTPException(ex.status, str(ex)) from None
        return envelope(request, {"access_token": token, "token_type": "bearer", "expires_in": auth.ttl})

    api = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])

    @api.post("/auth/logout")
    def logout(request: Request, token: str = Depends(require_token)):
        request.app.state.auth.revoke(token)
        return envelope(request, {"logged_out": True})

    @api.get("/holdings")
    def holdings(request: Request):
        with request.app.state.db.sessions() as session:
            rows = session.scalars(select(Holding).where(Holding.deleted.is_(False)).order_by(Holding.id))
            items = [holding_record(r) for r in rows]
        order = {"ALERT": 0, "WATCH": 1, "DATA_UNAVAILABLE": 2, "STALE": 2, "INSUFFICIENT_DATA": 2}
        items.sort(key=lambda h: (not h["enabled"], order.get(h["status"], 3), h["id"]))
        return envelope(request, items)

    @api.post("/holdings", status_code=201)
    def add_holding(request: Request, payload: HoldingInput):
        try:
            symbol = parse_market_code(payload.symbol, payload.market)
        except ValueError:
            raise HTTPException(422, "请核对六位代码、交易所及 A/B 股分类") from None
        with request.app.state.db.sessions() as session:
            row = session.scalar(select(Holding).where(Holding.symbol == symbol))
            if row and not row.deleted:
                raise HTTPException(409, "该股票已登记")
            if row is None:
                row = Holding(symbol=symbol, market=payload.market)
                session.add(row)
            else:
                row.deleted, row.enabled = False, True
                row.status, row.data_status, row.reason = "PENDING", "PENDING", None
                row.bar_date, row.fetched_at = None, None
                row.rsi6, row.rsi12, row.rsi24 = None, None, None
            row.display_name = payload.display_name.strip() or symbol
            row.notes = payload.notes.strip()
            try:
                session.commit()
            except IntegrityError:
                raise HTTPException(409, "该股票已登记") from None
            return envelope(request, holding_record(row))

    def active(session, holding_id: int) -> Holding:
        row = session.get(Holding, holding_id)
        if row is None or row.deleted:
            raise HTTPException(404, "未找到持仓")
        return row

    @api.patch("/holdings/{holding_id}")
    def edit_holding(request: Request, holding_id: int, payload: HoldingPatch):
        with request.app.state.db.sessions() as session:
            row = active(session, holding_id)
            for key, value in payload.model_dump(exclude_none=True).items():
                if isinstance(value, str):
                    value = value.strip()
                setattr(row, key, row.symbol if key == "display_name" and not value else value)
            session.commit()
            return envelope(request, holding_record(row))

    @api.delete("/holdings/{holding_id}")
    def delete_holding(request: Request, holding_id: int):
        with request.app.state.db.sessions() as session:
            row = active(session, holding_id)
            row.deleted, row.enabled = True, False
            session.commit()
            return envelope(request, {"id": holding_id})

    @api.get("/stocks/{symbol}/bars")
    def bars(request: Request, symbol: str, days: int = Query(default=120, ge=1, le=500)):
        with request.app.state.db.sessions() as session:
            if session.scalar(select(Holding.id).where(Holding.symbol == symbol, Holding.deleted.is_(False))) is None:
                raise HTTPException(404, "未找到持仓")
            rows = session.scalars(select(DailyBar).where(DailyBar.symbol == symbol)
                                   .order_by(DailyBar.bar_date.desc()).limit(days)).all()
            return envelope(request, [record(r) for r in reversed(rows)])

    @api.get("/signals")
    def signals(request: Request, symbol: str | None = None, limit: int = Query(default=50, ge=1, le=500)):
        query = select(SignalEvent)
        if symbol:
            query = query.where(SignalEvent.symbol == symbol)
        with request.app.state.db.sessions() as session:
            rows = session.scalars(query.order_by(SignalEvent.bar_date.desc(), SignalEvent.id.desc()).limit(limit))
            return envelope(request, [record(r) for r in rows])

    @api.post("/checks", status_code=202)
    def start_check(request: Request):
        from .scanner import ScanBusy, ScanCooldown
        try:
            run_id = request.app.state.scanner.trigger(trigger_type="manual")
        except ScanBusy as ex:
            raise HTTPException(409, str(ex)) from None
        except ScanCooldown as ex:
            raise HTTPException(429, str(ex)) from None
        return envelope(request, {"run_id": run_id})

    @api.get("/checks/{run_id}")
    def check(request: Request, run_id: int):
        with request.app.state.db.sessions() as session:
            row = session.get(CheckRun, run_id)
            if row is None:
                raise HTTPException(404, "未找到检测记录")
            return envelope(request, record(row))

    @api.get("/settings")
    def settings(request: Request):
        with request.app.state.db.sessions() as session:
            return envelope(request, SettingsInput.public_values(session.get(Setting, 1)))

    @api.put("/settings")
    def change_settings(request: Request, payload: SettingsInput):
        if payload.notifications_enabled and not os.getenv("SERVERCHAN_SENDKEY", "").strip():
            raise HTTPException(409, "请先在后端配置 Server酱 SendKey")
        with request.app.state.db.sessions() as session:
            row = session.get(Setting, 1)
            for key, value in payload.model_dump().items():
                setattr(row, key, value)
            session.commit()
            return envelope(request, SettingsInput.public_values(row))

    @api.get("/system/status")
    def status(request: Request):
        with request.app.state.db.sessions() as session:
            return envelope(request, {
                "latest_run": record(session.scalar(select(CheckRun).order_by(CheckRun.id.desc()).limit(1))),
                "latest_delivery": record(session.scalar(select(Delivery).order_by(Delivery.id.desc()).limit(1))),
                "hithink_configured": bool(os.getenv("HITHINK_FINANCE_API_KEY", "").strip()),
                "notification_configured": bool(os.getenv("SERVERCHAN_SENDKEY", "").strip()),
                "scheduler_enabled": os.getenv("SCHEDULER_ENABLED", "true").lower() in ("true", "1", "yes", "on"),
            })

    app.include_router(api)
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/stock-radar", StaticFiles(directory=dist, html=True), name="pwa")

        @app.get("/", include_in_schema=False)
        def home():
            return RedirectResponse("/stock-radar/")
    return app


app = create_app()
