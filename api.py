"""P2P Myjot — FastAPI: REST API и раздача Mini App."""
import asyncio
import copy
import datetime as dt
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal, Optional

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import core
from core import CalcError, AuthError
from database import (
    BASE_DIR, DATA_DIR, Calculation, Exchange, ExchangeFee, Guarantee, NftDeal,
    User, get_session, init_db, utcnow,
)

load_dotenv()
log = logging.getLogger("p2p_myjot.api")

SERVICE_NAME = "P2P Myjot"
PORT = int(os.getenv("PORT", "3000"))
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Только для локальной отладки без Telegram. В production должно быть 0.
DEV_MODE = os.getenv("DEV_MODE", "0") == "1"
AUTH_MAX_AGE = int(os.getenv("AUTH_MAX_AGE", "86400"))
WEB_DIR = BASE_DIR / "web"
RATES_FILE = DATA_DIR / "rates_cache.json"
RATES_TTL = 60          # сек: как долго курс считается свежим
RATES_RETRY = 20        # сек: пауза между попытками после ошибки
STALE_WARNING = "⚠️ Не удалось обновить курс"
ASSET_VERSION = str(int(time.time()))

Currency = Literal["RUB", "USD", "TON", "USDT", "BTC", "ETH"]
NftCurrency = Literal["TON", "USD", "RUB", "USDT"]
FeeMode = Literal["percent", "fixed"]
Amount = Annotated[float, Field(ge=0, le=1e12, allow_inf_nan=False)]
PositiveAmount = Annotated[float, Field(gt=0, le=1e12, allow_inf_nan=False)]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


# ==========================================================================
# Курсы
# ==========================================================================
_rates_state: dict[str, Any] = {"data": None, "fetched": 0.0, "last_try": 0.0, "stale": False}
_rates_lock = asyncio.Lock()


def _parse_coingecko(raw: dict) -> dict:
    coins = {}
    for symbol, cg_id in core.COINS.items():
        item = raw[cg_id]
        coins[symbol] = {
            "usd": float(item["usd"]),
            "rub": float(item["rub"]),
            "change_24h": float(item.get("usd_24h_change") or 0.0),
        }
    usd_rub = coins["USDT"]["rub"] / coins["USDT"]["usd"]
    return {
        "coins": coins,
        "usd_rub": usd_rub,
        "updated_at": utcnow().isoformat() + "Z",
    }


def _load_saved_rates() -> Optional[dict]:
    try:
        with open(RATES_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("coins") and data.get("usd_rub"):
            return data
    except (OSError, ValueError):
        pass
    return None


def _save_rates(data: dict) -> None:
    try:
        with open(RATES_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError as exc:
        log.warning("Не удалось сохранить курсы: %s", exc)


async def get_rates() -> tuple[dict, bool, Optional[str]]:
    """Возвращает (курсы, устарели_ли, предупреждение). Никогда не падает."""
    async with _rates_lock:
        now = time.time()
        st = _rates_state
        if st["data"] and not st["stale"] and now - st["fetched"] < RATES_TTL:
            return st["data"], False, None
        if st["data"] and st["stale"] and now - st["last_try"] < RATES_RETRY:
            return st["data"], True, STALE_WARNING

        st["last_try"] = now
        try:
            async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "P2PMyjot/1.0"}) as client:
                resp = await client.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={
                        "ids": ",".join(core.COINS.values()),
                        "vs_currencies": "usd,rub",
                        "include_24hr_change": "true",
                    },
                )
                resp.raise_for_status()
                data = _parse_coingecko(resp.json())
            st.update(data=data, fetched=now, stale=False)
            _save_rates(data)
            return data, False, None
        except Exception as exc:  # сеть, лимиты, неожиданный ответ
            log.warning("Курсы не обновлены: %s", exc)
            st["stale"] = True
            if st["data"] is None:
                st["data"] = _load_saved_rates() or copy.deepcopy(core.FALLBACK_RATES)
            return st["data"], True, STALE_WARNING


# ==========================================================================
# Авторизация через Telegram initData
# ==========================================================================
async def current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    tg_user: Optional[dict] = None
    if init_data:
        try:
            tg_user = core.verify_init_data(init_data, BOT_TOKEN, AUTH_MAX_AGE)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc))
    elif DEV_MODE and request.headers.get("X-Dev-Telegram-Id", "").lstrip("-").isdigit():
        tg_user = {"id": int(request.headers["X-Dev-Telegram-Id"]), "first_name": "Dev"}
    else:
        raise HTTPException(status_code=401, detail="Откройте приложение через бота в Telegram")

    tid = int(tg_user["id"])
    user = (await session.execute(select(User).where(User.telegram_id == tid))).scalar_one_or_none()
    username = (tg_user.get("username") or None)
    first_name = (tg_user.get("first_name") or None)
    if user is None:
        user = User(telegram_id=tid, username=username, first_name=first_name)
        session.add(user)
        await session.commit()
    elif user.username != username or user.first_name != first_name:
        user.username, user.first_name = username, first_name
        await session.commit()
    return user


def own(telegram_id: int, user: User) -> None:
    """Пользователь видит только свои данные."""
    if telegram_id != user.telegram_id:
        raise HTTPException(status_code=403, detail="Нет доступа к чужим данным")


# ==========================================================================
# Схемы запросов
# ==========================================================================
class CalcIn(BaseModel):
    buy_amount: PositiveAmount
    buy_currency: Currency
    sell_amount: Amount
    sell_currency: Currency
    buy_fee: Amount = 0
    sell_fee: Amount = 0
    extra_fee: Amount = 0
    fee_mode: FeeMode = "percent"
    save: bool = True


class NftIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    link: Optional[str] = Field(default=None, max_length=2000)
    buy_price: Amount
    buy_currency: NftCurrency
    sell_price: Amount
    sell_currency: NftCurrency

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Название не может быть пустым")
        return v

    @field_validator("link")
    @classmethod
    def _link(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("Ссылка должна начинаться с http:// или https://")
        return v


class GuaranteeIn(BaseModel):
    amount: PositiveAmount
    currency: Currency
    profit_percent: Annotated[float, Field(ge=0, le=1000, allow_inf_nan=False)]
    fee_percent: Percent
    mode: Literal["included", "on_top"] = "included"
    save: bool = True


class ExchangeIn(BaseModel):
    service: Literal["Send", "xRocket", "Другой обменник", "Своя комиссия"]
    pair: Literal["USD→TON", "TON→USD", "RUB→TON", "TON→RUB",
                  "USDT→TON", "TON→USDT", "BTC→USDT", "ETH→USDT"]
    amount: PositiveAmount
    rate: Optional[PositiveAmount] = None          # None — взять актуальный курс
    fee_percent: Optional[Percent] = None          # None — автоматическая комиссия
    fee_mode: Literal["included", "deducted", "added"] = "deducted"
    save: bool = True


# ==========================================================================
# Сериализация
# ==========================================================================
def iso(d: dt.datetime) -> str:
    return d.isoformat() + "Z"


def calc_dict(r: Calculation) -> dict:
    return {"type": "p2p", "id": r.id, "amount": r.amount, "currency": r.currency,
            "sell_amount": r.sell_amount, "sell_currency": r.sell_currency,
            "profit": r.profit, "roi": r.roi, "commission": r.commission,
            "profit_rub": r.profit_rub, "turnover_rub": r.turnover_rub,
            "created_at": iso(r.created_at)}


def nft_dict(r: NftDeal) -> dict:
    return {"type": "nft", "id": r.id, "name": r.name, "link": r.link,
            "buy_price": r.buy_price, "buy_currency": r.buy_currency,
            "sell_price": r.sell_price, "sell_currency": r.sell_currency,
            "profit": r.profit, "currency": r.buy_currency, "roi": r.roi,
            "profit_rub": r.profit_rub, "turnover_rub": r.turnover_rub,
            "created_at": iso(r.created_at)}


def guarantee_dict(r: Guarantee) -> dict:
    roi = r.net_profit / r.amount * 100.0 if r.amount else 0.0
    return {"type": "guarantee", "id": r.id, "amount": r.amount, "currency": r.currency,
            "profit_percent": r.profit_percent, "fee_percent": r.fee_percent,
            "fee_mode": r.fee_mode, "guarantee_fee": r.guarantee_fee,
            "gross_profit": r.profit, "total": r.total,
            "profit": r.net_profit, "roi": round(roi, 4),
            "profit_rub": r.profit_rub, "turnover_rub": r.turnover_rub,
            "created_at": iso(r.created_at)}


def exchange_dict(r: Exchange) -> dict:
    src, dst = r.pair.split("→")
    return {"type": "exchange", "id": r.id, "service": r.service, "pair": r.pair,
            "from": src, "to": dst, "amount": r.amount, "rate": r.rate,
            "fee_percent": r.fee_percent, "fee_amount": r.fee_amount,
            "fee_mode": r.fee_mode, "result": r.result,
            "profit": 0.0, "currency": dst, "roi": None,
            "profit_rub": 0.0, "turnover_rub": r.turnover_rub,
            "created_at": iso(r.created_at)}


MODELS = {
    "p2p": (Calculation, calc_dict),
    "nft": (NftDeal, nft_dict),
    "guarantee": (Guarantee, guarantee_dict),
    "exchange": (Exchange, exchange_dict),
}


def since_from_period(period: str, tz: int) -> Optional[dt.datetime]:
    """Начало периода в UTC. tz — смещение пользователя от UTC в минутах."""
    if period == "all":
        return None
    local_now = utcnow() + dt.timedelta(minutes=tz)
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        start = midnight
    elif period == "7d":
        start = midnight - dt.timedelta(days=6)
    elif period == "30d":
        start = midnight - dt.timedelta(days=29)
    else:
        raise HTTPException(status_code=400, detail="Неизвестный период")
    return start - dt.timedelta(minutes=tz)


async def collect_items(session: AsyncSession, tid: int, types: Optional[list] = None,
                        since: Optional[dt.datetime] = None) -> list:
    items: list = []
    for name, (model, to_dict) in MODELS.items():
        if types and name not in types:
            continue
        query = select(model).where(model.telegram_id == tid)
        if since is not None:
            query = query.where(model.created_at >= since)
        rows = (await session.execute(query)).scalars().all()
        items.extend(to_dict(r) for r in rows)
    items.sort(key=lambda i: i["created_at"], reverse=True)
    return items


# ==========================================================================
# Приложение
# ==========================================================================
@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    if not BOT_TOKEN:
        log.warning("BOT_TOKEN не задан: подпись Telegram проверить нельзя, API закрыто для пользователей")
    if DEV_MODE:
        log.warning("DEV_MODE=1 — авторизация по заголовку X-Dev-Telegram-Id. Не используйте в production!")
    yield


app = FastAPI(title=SERVICE_NAME, version="1.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(CalcError)
async def calc_error_handler(_: Request, exc: CalcError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    msg = "Проверьте введённые данные"
    errors = exc.errors()
    if errors:
        first = errors[0]
        field = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        text = str(first.get("msg", "")).replace("Value error, ", "")
        msg = f"{msg} ({field}: {text})" if field else msg
    return JSONResponse(status_code=422, content={"detail": msg})


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception):
    log.exception("Необработанная ошибка: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})


# --------------------------------------------------------------------------
# Общие
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    page = WEB_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="web/index.html не найден")
    html = page.read_text(encoding="utf-8").replace("__V__", ASSET_VERSION)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME, "port": PORT}


@app.get("/api/rates")
async def api_rates():
    data, stale, warning = await get_rates()
    return {"coins": data["coins"], "usd_rub": data["usd_rub"],
            "updated_at": data.get("updated_at"), "stale": stale, "warning": warning}


@app.get("/api/fees")
async def api_fees(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ExchangeFee))).scalars().all()
    return [{"service": r.service, "pair": r.pair, "fee_percent": r.fee_percent,
             "updated_at": iso(r.updated_at)} for r in rows]


# --------------------------------------------------------------------------
# P2P
# --------------------------------------------------------------------------
@app.post("/api/calculate")
async def api_calculate(body: CalcIn, user: User = Depends(current_user),
                        session: AsyncSession = Depends(get_session)):
    rates, stale, warning = await get_rates()
    result = core.calc_p2p(body.buy_amount, body.buy_currency, body.sell_amount,
                           body.sell_currency, body.buy_fee, body.sell_fee,
                           body.extra_fee, body.fee_mode, rates)
    saved_id = None
    if body.save:
        row = Calculation(
            telegram_id=user.telegram_id, type="p2p", amount=body.buy_amount,
            currency=body.buy_currency, sell_amount=body.sell_amount,
            sell_currency=body.sell_currency, profit=result["profit"], roi=result["roi"],
            commission=result["fees_total"], profit_rub=result["profit_rub"],
            turnover_rub=result["turnover_rub"])
        session.add(row)
        await session.commit()
        saved_id = row.id
    return {"saved": body.save, "id": saved_id, "result": result, "stale": stale, "warning": warning}


# --------------------------------------------------------------------------
# История
# --------------------------------------------------------------------------
@app.get("/api/history/{telegram_id}")
async def api_history(
    telegram_id: int,
    type: Literal["all", "p2p", "nft", "guarantee", "exchange"] = "all",
    period: Literal["today", "7d", "30d", "all"] = "all",
    tz: int = Query(0, ge=-840, le=840),
    limit: int = Query(200, ge=1, le=500),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    own(telegram_id, user)
    types = None if type == "all" else [type]
    items = await collect_items(session, telegram_id, types, since_from_period(period, tz))
    return {"total": len(items), "items": items[:limit]}


@app.delete("/api/history/{telegram_id}")
async def api_history_clear(
    telegram_id: int,
    type: Literal["all", "p2p", "nft", "guarantee", "exchange"] = "all",
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    own(telegram_id, user)
    deleted = 0
    for name, (model, _) in MODELS.items():
        if type != "all" and name != type:
            continue
        res = await session.execute(delete(model).where(model.telegram_id == telegram_id))
        deleted += res.rowcount or 0
    await session.commit()
    return {"deleted": deleted}


@app.delete("/api/history/{telegram_id}/{item_type}/{item_id}")
async def api_history_delete_one(
    telegram_id: int, item_type: Literal["p2p", "nft", "guarantee", "exchange"], item_id: int,
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    own(telegram_id, user)
    model = MODELS[item_type][0]
    res = await session.execute(delete(model).where(model.id == item_id, model.telegram_id == telegram_id))
    await session.commit()
    if not res.rowcount:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return {"deleted": 1}


# --------------------------------------------------------------------------
# NFT
# --------------------------------------------------------------------------
@app.post("/api/nft")
async def api_nft_create(body: NftIn, user: User = Depends(current_user),
                         session: AsyncSession = Depends(get_session)):
    rates, stale, warning = await get_rates()
    res = core.calc_nft(body.buy_price, body.buy_currency, body.sell_price, body.sell_currency, rates)
    row = NftDeal(
        telegram_id=user.telegram_id, name=body.name, link=body.link,
        buy_price=body.buy_price, buy_currency=body.buy_currency,
        sell_price=body.sell_price, sell_currency=body.sell_currency,
        profit=res["profit"], roi=res["roi"],
        profit_rub=res["profit_rub"], turnover_rub=res["turnover_rub"])
    session.add(row)
    await session.commit()
    return {"item": nft_dict(row), "stale": stale, "warning": warning}


@app.get("/api/nft/{telegram_id}")
async def api_nft_list(telegram_id: int, limit: int = Query(200, ge=1, le=500),
                       user: User = Depends(current_user),
                       session: AsyncSession = Depends(get_session)):
    own(telegram_id, user)
    rows = (await session.execute(
        select(NftDeal).where(NftDeal.telegram_id == telegram_id)
        .order_by(NftDeal.created_at.desc(), NftDeal.id.desc()).limit(limit))).scalars().all()
    return {"items": [nft_dict(r) for r in rows]}


@app.delete("/api/nft/{item_id}")
async def api_nft_delete(item_id: int, user: User = Depends(current_user),
                         session: AsyncSession = Depends(get_session)):
    res = await session.execute(
        delete(NftDeal).where(NftDeal.id == item_id, NftDeal.telegram_id == user.telegram_id))
    await session.commit()
    if not res.rowcount:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return {"deleted": 1}


# --------------------------------------------------------------------------
# Гарант
# --------------------------------------------------------------------------
@app.post("/api/guarantee")
async def api_guarantee_create(body: GuaranteeIn, user: User = Depends(current_user),
                               session: AsyncSession = Depends(get_session)):
    rates, stale, warning = await get_rates()
    res = core.calc_guarantee(body.amount, body.currency, body.profit_percent,
                              body.fee_percent, body.mode, rates)
    saved_id = None
    if body.save:
        row = Guarantee(
            telegram_id=user.telegram_id, amount=body.amount, currency=body.currency,
            profit_percent=body.profit_percent, guarantee_fee=res["guarantee_fee"],
            profit=res["profit"], total=res["total"], fee_percent=body.fee_percent,
            fee_mode=body.mode, net_profit=res["net_profit"],
            profit_rub=res["profit_rub"], turnover_rub=res["turnover_rub"])
        session.add(row)
        await session.commit()
        saved_id = row.id
    return {"saved": body.save, "id": saved_id, "result": res, "stale": stale, "warning": warning}


@app.get("/api/guarantee/{telegram_id}")
async def api_guarantee_list(telegram_id: int, limit: int = Query(200, ge=1, le=500),
                             user: User = Depends(current_user),
                             session: AsyncSession = Depends(get_session)):
    own(telegram_id, user)
    rows = (await session.execute(
        select(Guarantee).where(Guarantee.telegram_id == telegram_id)
        .order_by(Guarantee.created_at.desc(), Guarantee.id.desc()).limit(limit))).scalars().all()
    return {"items": [guarantee_dict(r) for r in rows]}


# --------------------------------------------------------------------------
# Обмен
# --------------------------------------------------------------------------
@app.post("/api/exchange")
async def api_exchange_create(body: ExchangeIn, user: User = Depends(current_user),
                              session: AsyncSession = Depends(get_session)):
    rates, stale, warning = await get_rates()

    fee_percent = body.fee_percent
    fee_source = "custom"
    if fee_percent is None:
        fee_source = "auto"
        fee_percent = 0.0
        if body.service != "Своя комиссия":
            found = (await session.execute(
                select(ExchangeFee.fee_percent).where(
                    ExchangeFee.service == body.service, ExchangeFee.pair == body.pair)
            )).scalar_one_or_none()
            if found is None:
                fee_source = "none"
            else:
                fee_percent = float(found)
        else:
            fee_source = "none"

    res = core.calc_exchange(body.pair, body.amount, body.rate, fee_percent, body.fee_mode, rates)
    res["fee_source"] = fee_source
    saved_id = None
    if body.save:
        row = Exchange(
            telegram_id=user.telegram_id, service=body.service, pair=body.pair,
            amount=body.amount, rate=res["rate"], fee_percent=res["fee_percent"],
            fee_amount=res["fee_amount"], result=res["result"], fee_mode=body.fee_mode,
            turnover_rub=res["turnover_rub"])
        session.add(row)
        await session.commit()
        saved_id = row.id
    return {"saved": body.save, "id": saved_id, "result": res, "stale": stale, "warning": warning}


@app.get("/api/exchange/{telegram_id}")
async def api_exchange_list(telegram_id: int, limit: int = Query(200, ge=1, le=500),
                            user: User = Depends(current_user),
                            session: AsyncSession = Depends(get_session)):
    own(telegram_id, user)
    rows = (await session.execute(
        select(Exchange).where(Exchange.telegram_id == telegram_id)
        .order_by(Exchange.created_at.desc(), Exchange.id.desc()).limit(limit))).scalars().all()
    return {"items": [exchange_dict(r) for r in rows]}


# --------------------------------------------------------------------------
# Статистика (все суммы — в рублях по курсу на момент операции)
# --------------------------------------------------------------------------
@app.get("/api/stats/{telegram_id}")
async def api_stats(telegram_id: int, tz: int = Query(0, ge=-840, le=840),
                    user: User = Depends(current_user),
                    session: AsyncSession = Depends(get_session)):
    own(telegram_id, user)
    items = await collect_items(session, telegram_id)

    def bucket(kind: str) -> dict:
        subset = [i for i in items if i["type"] == kind]
        return {"count": len(subset),
                "profit_rub": round(sum(i["profit_rub"] for i in subset), 2),
                "turnover_rub": round(sum(i["turnover_rub"] for i in subset), 2)}

    breakdown = {k: bucket(k) for k in MODELS}
    rois = [i["roi"] for i in items if i.get("roi") is not None]
    best = None
    profitable = [i for i in items if i["type"] != "exchange"]
    if profitable:
        top = max(profitable, key=lambda i: i["profit_rub"])
        best = {"type": top["type"], "profit_rub": top["profit_rub"],
                "created_at": top["created_at"]}

    # Ряды для графиков в часовом поясе пользователя
    offset = dt.timedelta(minutes=tz)
    today = (utcnow() + offset).date()
    days = [today - dt.timedelta(days=n) for n in range(29, -1, -1)]
    this_monday = today - dt.timedelta(days=today.weekday())
    weeks = [this_monday - dt.timedelta(weeks=n) for n in range(11, -1, -1)]
    day_acc = {d: [0.0, 0.0, 0] for d in days}
    week_acc = {w: [0.0, 0.0, 0] for w in weeks}
    for it in items:
        local = (dt.datetime.fromisoformat(it["created_at"][:-1]) + offset).date()
        monday = local - dt.timedelta(days=local.weekday())
        for acc, key in ((day_acc, local), (week_acc, monday)):
            if key in acc:
                acc[key][0] += it["profit_rub"]
                acc[key][1] += it["turnover_rub"]
                acc[key][2] += 1

    def series(acc: dict) -> list:
        return [{"date": k.isoformat(), "profit": round(v[0], 2),
                 "turnover": round(v[1], 2), "count": v[2]} for k, v in acc.items()]

    return {
        "total_deals": breakdown["p2p"]["count"] + breakdown["guarantee"]["count"],
        "total_operations": len(items),
        "total_profit_rub": round(sum(i["profit_rub"] for i in items), 2),
        "total_turnover_rub": round(sum(i["turnover_rub"] for i in items), 2),
        "avg_roi": round(sum(rois) / len(rois), 2) if rois else 0.0,
        "best": best,
        "nft_count": breakdown["nft"]["count"],
        "nft_profit_rub": breakdown["nft"]["profit_rub"],
        "exchange_count": breakdown["exchange"]["count"],
        "breakdown": breakdown,
        "daily": series(day_acc),
        "weekly": series(week_acc),
    }


# Статические файлы Mini App (/static/app.js, /static/style.css)
if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
