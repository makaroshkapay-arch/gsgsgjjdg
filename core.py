"""P2P Myjot — чистая бизнес-логика (без сторонних зависимостей).

Здесь лежат:
  * формулы P2P / NFT / гарант / обмен;
  * конвертация валют по курсам;
  * проверка подписи Telegram WebApp initData.

Модуль намеренно не импортирует FastAPI/SQLAlchemy, чтобы его можно было
проверять отдельно (см. README, раздел «Проверка»).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from typing import Any, Optional
from urllib.parse import parse_qsl

CURRENCIES = ("RUB", "USD", "TON", "USDT", "BTC", "ETH")
NFT_CURRENCIES = ("TON", "USD", "RUB", "USDT")
SERVICES = ("Send", "xRocket", "Другой обменник", "Своя комиссия")
PAIRS = (
    "USD→TON", "TON→USD",
    "RUB→TON", "TON→RUB",
    "USDT→TON", "TON→USDT",
    "BTC→USDT", "ETH→USDT",
)

# Идентификаторы монет в CoinGecko
COINS = {
    "TON": "the-open-network",
    "USDT": "tether",
    "BTC": "bitcoin",
    "ETH": "ethereum",
}

# Запасные значения, если CoinGecko ни разу не отвечал (только чтобы
# приложение не падало; в интерфейсе они помечаются как устаревшие).
FALLBACK_RATES: dict[str, Any] = {
    "coins": {
        "TON": {"usd": 3.0, "rub": 240.0, "change_24h": 0.0},
        "USDT": {"usd": 1.0, "rub": 80.0, "change_24h": 0.0},
        "BTC": {"usd": 100000.0, "rub": 8000000.0, "change_24h": 0.0},
        "ETH": {"usd": 3500.0, "rub": 280000.0, "change_24h": 0.0},
    },
    "usd_rub": 80.0,
    "updated_at": None,
}


class CalcError(ValueError):
    """Ошибка входных данных для расчёта."""


class AuthError(Exception):
    """Ошибка проверки подписи Telegram."""


# --------------------------------------------------------------------------
# Утилиты
# --------------------------------------------------------------------------
def rnd(value: float, digits: int = 8) -> float:
    """Округление без «-0.0»."""
    result = round(float(value), digits)
    return 0.0 if result == 0 else result


def _check_finite(**values: float) -> None:
    for name, v in values.items():
        if v is None or not math.isfinite(v):
            raise CalcError(f"Некорректное значение: {name}")


def usd_price(currency: str, rates: dict) -> float:
    """Цена 1 единицы валюты в USD."""
    if currency == "USD":
        return 1.0
    if currency == "RUB":
        usd_rub = float(rates.get("usd_rub") or 0)
        if usd_rub <= 0:
            raise CalcError("Нет курса RUB")
        return 1.0 / usd_rub
    coin = rates.get("coins", {}).get(currency)
    if not coin or not coin.get("usd"):
        raise CalcError(f"Нет курса для {currency}")
    return float(coin["usd"])


def convert(amount: float, src: str, dst: str, rates: dict) -> float:
    """Конвертирует сумму через USD. Разные валюты нельзя просто вычитать."""
    if src == dst:
        return amount
    p_src = usd_price(src, rates)
    p_dst = usd_price(dst, rates)
    if p_dst <= 0:
        raise CalcError(f"Нет курса для {dst}")
    return amount * p_src / p_dst


def _fee_value(base: float, value: float, mode: str) -> float:
    """Комиссия: % от base или фиксированная сумма."""
    if mode == "percent":
        if value > 100:
            raise CalcError("Комиссия в процентах не может быть больше 100%")
        return base * value / 100.0
    if mode == "fixed":
        return value
    raise CalcError("Неизвестный режим комиссии")


# --------------------------------------------------------------------------
# P2P
# --------------------------------------------------------------------------
def calc_p2p(
    buy_amount: float,
    buy_currency: str,
    sell_amount: float,
    sell_currency: str,
    buy_fee: float,
    sell_fee: float,
    extra_fee: float,
    fee_mode: str,
    rates: dict,
) -> dict:
    """Расчёт P2P-сделки. Все итоги — в валюте покупки.

    * комиссия покупки считается от суммы покупки (валюта покупки);
    * комиссия продажи и доп. комиссия — от суммы продажи (валюта продажи);
    * в режиме «fixed» это фиксированные суммы в тех же валютах.
    """
    _check_finite(buy_amount=buy_amount, sell_amount=sell_amount,
                  buy_fee=buy_fee, sell_fee=sell_fee, extra_fee=extra_fee)
    if buy_amount <= 0:
        raise CalcError("Сумма покупки должна быть больше нуля")

    buy_fee_v = _fee_value(buy_amount, buy_fee, fee_mode)
    sell_fee_v = _fee_value(sell_amount, sell_fee, fee_mode)
    extra_fee_v = _fee_value(sell_amount, extra_fee, fee_mode)

    sell_in_buy = convert(sell_amount, sell_currency, buy_currency, rates)
    sale_fees_in_buy = convert(sell_fee_v + extra_fee_v, sell_currency, buy_currency, rates)

    fees_total = buy_fee_v + sale_fees_in_buy
    profit_before_fees = sell_in_buy - buy_amount
    total_after_fees = sell_in_buy - fees_total       # что остаётся после всех комиссий
    profit = total_after_fees - buy_amount             # чистая прибыль
    invested = buy_amount + buy_fee_v
    roi = profit / invested * 100.0 if invested > 0 else 0.0
    turnover = buy_amount + sell_in_buy

    return {
        "currency": buy_currency,
        "sell_in_buy_currency": rnd(sell_in_buy),
        "profit_before_fees": rnd(profit_before_fees),
        "buy_fee": rnd(buy_fee_v),
        "sell_fee": rnd(sell_fee_v),
        "sell_fee_currency": sell_currency,
        "extra_fee": rnd(extra_fee_v),
        "fees_total": rnd(fees_total),
        "profit": rnd(profit),
        "roi": rnd(roi, 4),
        "turnover": rnd(turnover),
        "total_after_fees": rnd(total_after_fees),
        "profit_rub": rnd(convert(profit, buy_currency, "RUB", rates), 2),
        "turnover_rub": rnd(convert(turnover, buy_currency, "RUB", rates), 2),
    }


# --------------------------------------------------------------------------
# NFT
# --------------------------------------------------------------------------
def calc_nft(buy_price: float, buy_currency: str, sell_price: float,
             sell_currency: str, rates: dict) -> dict:
    """Прибыль NFT в валюте покупки (продажа конвертируется по текущему курсу)."""
    _check_finite(buy_price=buy_price, sell_price=sell_price)
    if buy_price < 0 or sell_price < 0:
        raise CalcError("Цена не может быть отрицательной")
    sell_in_buy = convert(sell_price, sell_currency, buy_currency, rates)
    profit = sell_in_buy - buy_price
    roi = profit / buy_price * 100.0 if buy_price > 0 else 0.0
    turnover = buy_price + sell_in_buy
    return {
        "currency": buy_currency,
        "profit": rnd(profit),
        "roi": rnd(roi, 4),
        "profit_rub": rnd(convert(profit, buy_currency, "RUB", rates), 2),
        "turnover_rub": rnd(convert(turnover, buy_currency, "RUB", rates), 2),
    }


# --------------------------------------------------------------------------
# Гарант
# --------------------------------------------------------------------------
def calc_guarantee(amount: float, currency: str, profit_percent: float,
                   fee_percent: float, mode: str, rates: dict) -> dict:
    """Гарант-сделка.

    base   = сумма + прибыль
    fee    = комиссия гаранта % от base
    included: total = base - fee   («комиссия включена в сумму»)
    on_top:   total = base + fee   («комиссия добавляется сверху»)

    Пример из ТЗ: 100 000, 5 %, 1 % -> прибыль 5 000, комиссия 1 050,
    к получению 103 950.
    """
    _check_finite(amount=amount, profit_percent=profit_percent, fee_percent=fee_percent)
    if amount <= 0:
        raise CalcError("Сумма должна быть больше нуля")
    if profit_percent < 0 or fee_percent < 0 or fee_percent > 100:
        raise CalcError("Проверьте проценты")
    if mode not in ("included", "on_top"):
        raise CalcError("Неизвестный режим комиссии")

    profit = amount * profit_percent / 100.0
    base = amount + profit
    fee = base * fee_percent / 100.0
    if mode == "included":
        total = base - fee
        net_profit = total - amount
    else:
        total = base + fee
        net_profit = profit
    return {
        "currency": currency,
        "profit": rnd(profit),
        "base": rnd(base),
        "guarantee_fee": rnd(fee),
        "total": rnd(total),
        "net_profit": rnd(net_profit),
        "profit_rub": rnd(convert(net_profit, currency, "RUB", rates), 2),
        "turnover_rub": rnd(convert(amount, currency, "RUB", rates), 2),
    }


# --------------------------------------------------------------------------
# Обмен
# --------------------------------------------------------------------------
def split_pair(pair: str) -> tuple[str, str]:
    if pair not in PAIRS:
        raise CalcError("Неподдерживаемая пара")
    src, dst = pair.split("→")
    return src, dst


def calc_exchange(pair: str, amount: float, rate: Optional[float],
                  fee_percent: float, fee_mode: str, rates: dict) -> dict:
    """Обмен `amount` валюты `src` на валюту `dst`.

    rate — сколько `dst` за 1 `src` (если None — берём актуальный курс).
    gross = amount * rate
    deducted: комиссия вычитается из полученного (итого = gross - fee)
    included: комиссия уже внутри курса (итого = gross, fee = часть gross)
    added:    комиссия платится сверху (итого = gross, платите amount * (1 + %))
    """
    _check_finite(amount=amount, fee_percent=fee_percent)
    if amount <= 0:
        raise CalcError("Сумма должна быть больше нуля")
    if fee_percent < 0 or fee_percent > 100:
        raise CalcError("Комиссия должна быть от 0 до 100%")
    if fee_mode not in ("included", "deducted", "added"):
        raise CalcError("Неизвестный режим комиссии")
    src, dst = split_pair(pair)

    auto_rate = rate is None
    if rate is None:
        rate = convert(1.0, src, dst, rates)
    _check_finite(rate=rate)
    if rate <= 0:
        raise CalcError("Курс должен быть больше нуля")

    f = fee_percent / 100.0
    gross = amount * rate
    pay_total = amount
    if fee_mode == "deducted":
        fee_amount = gross * f
        result = gross - fee_amount
    elif fee_mode == "included":
        fee_amount = gross - gross / (1.0 + f)
        result = gross
    else:  # added
        fee_amount = gross * f
        result = gross
        pay_total = amount * (1.0 + f)

    return {
        "from": src,
        "to": dst,
        "rate": rnd(rate),
        "auto_rate": auto_rate,
        "gross": rnd(gross),
        "fee_percent": rnd(fee_percent, 4),
        "fee_amount": rnd(fee_amount),
        "result": rnd(result),
        "pay_total": rnd(pay_total),
        "turnover_rub": rnd(convert(amount, src, "RUB", rates), 2),
    }


# --------------------------------------------------------------------------
# Telegram WebApp initData
# --------------------------------------------------------------------------
def verify_init_data(init_data: str, bot_token: str, max_age: int = 86400,
                     now: Optional[float] = None) -> dict:
    """Проверяет подпись Telegram WebApp initData и возвращает объект user.

    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data or not bot_token:
        raise AuthError("Нет данных авторизации Telegram")
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError as exc:
        raise AuthError("Некорректные данные Telegram") from exc

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise AuthError("В данных Telegram нет подписи")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise AuthError("Подпись Telegram не совпала")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise AuthError("Некорректная дата авторизации") from exc
    current = time.time() if now is None else now
    if auth_date <= 0 or current - auth_date > max_age:
        raise AuthError("Сессия устарела, откройте приложение заново")

    try:
        user = json.loads(pairs.get("user", ""))
        int(user["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise AuthError("В данных Telegram нет пользователя") from exc
    return user
