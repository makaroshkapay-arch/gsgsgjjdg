"""P2P Myjot — база данных (SQLite + SQLAlchemy async)."""
from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv
from sqlalchemy import BigInteger, DateTime, Float, Integer, String, Text, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core import PAIRS

load_dotenv()
log = logging.getLogger("p2p_myjot.db")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR") or (BASE_DIR / "data"))
DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite+aiosqlite:///{DATA_DIR / 'p2p_helper.db'}"


def _ensure_dirs() -> None:
    """Создаёт папки для БД и кэша курсов, если их ещё нет."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        db_path = make_url(DATABASE_URL).database
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # pragma: no cover
        log.error("Не удалось создать папку данных: %s", exc)
        raise


_ensure_dirs()
engine = create_async_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Calculation(Base):
    """P2P-расчёты."""
    __tablename__ = "calculations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    type: Mapped[str] = mapped_column(String(16), default="p2p")
    amount: Mapped[float] = mapped_column(Float)          # сумма покупки
    currency: Mapped[str] = mapped_column(String(8))      # валюта покупки
    sell_amount: Mapped[float] = mapped_column(Float, default=0)
    sell_currency: Mapped[str] = mapped_column(String(8), default="")
    profit: Mapped[float] = mapped_column(Float)          # чистая прибыль (валюта покупки)
    roi: Mapped[float] = mapped_column(Float, default=0)
    commission: Mapped[float] = mapped_column(Float, default=0)  # все комиссии (валюта покупки)
    profit_rub: Mapped[float] = mapped_column(Float, default=0)
    turnover_rub: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)


class NftDeal(Base):
    __tablename__ = "nft_deals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(200))
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    buy_price: Mapped[float] = mapped_column(Float)
    buy_currency: Mapped[str] = mapped_column(String(8))
    sell_price: Mapped[float] = mapped_column(Float)
    sell_currency: Mapped[str] = mapped_column(String(8))
    profit: Mapped[float] = mapped_column(Float)          # в валюте покупки
    roi: Mapped[float] = mapped_column(Float, default=0)
    profit_rub: Mapped[float] = mapped_column(Float, default=0)
    turnover_rub: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Guarantee(Base):
    __tablename__ = "guarantees"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8))
    profit_percent: Mapped[float] = mapped_column(Float)
    guarantee_fee: Mapped[float] = mapped_column(Float)
    profit: Mapped[float] = mapped_column(Float)          # прибыль до комиссии гаранта
    total: Mapped[float] = mapped_column(Float)
    fee_percent: Mapped[float] = mapped_column(Float, default=0)
    fee_mode: Mapped[str] = mapped_column(String(16), default="included")
    net_profit: Mapped[float] = mapped_column(Float, default=0)
    profit_rub: Mapped[float] = mapped_column(Float, default=0)
    turnover_rub: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Exchange(Base):
    __tablename__ = "exchanges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    service: Mapped[str] = mapped_column(String(64))
    pair: Mapped[str] = mapped_column(String(32))
    amount: Mapped[float] = mapped_column(Float)          # в валюте «из»
    rate: Mapped[float] = mapped_column(Float)
    fee_percent: Mapped[float] = mapped_column(Float)
    fee_amount: Mapped[float] = mapped_column(Float)      # в валюте «в»
    result: Mapped[float] = mapped_column(Float)          # в валюте «в»
    fee_mode: Mapped[str] = mapped_column(String(16), default="deducted")
    turnover_rub: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ExchangeFee(Base):
    __tablename__ = "exchange_fees"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service: Mapped[str] = mapped_column(String(64), index=True)
    pair: Mapped[str] = mapped_column(String(32), index=True)
    fee_percent: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


# Стартовые (примерные!) комиссии. Актуальные значения меняются в таблице
# exchange_fees без правки кода — см. README, раздел «Комиссии обменников».
DEFAULT_FEES = {"Send": 0.75, "xRocket": 1.5, "Другой обменник": 1.0}


async def init_db() -> None:
    """Создаёт таблицы и заполняет exchange_fees, если она пуста."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        exists = (await session.execute(select(ExchangeFee.id).limit(1))).first()
        if not exists:
            for service, percent in DEFAULT_FEES.items():
                for pair in PAIRS:
                    session.add(ExchangeFee(service=service, pair=pair, fee_percent=percent))
            await session.commit()
            log.info("Таблица exchange_fees заполнена стартовыми значениями")


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
