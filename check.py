"""Самопроверка проекта P2P Myjot: python check.py

1. наличие обязательных файлов;
2. python -m py_compile для .py файлов;
3. отсутствие localhost / 127.0.0.1 в production-конфигурации;
4. все сторонние импорты есть в requirements.txt;
5. формулы (расчёт из ТЗ) и проверка подписи Telegram initData;
6. если установлены зависимости — запуск FastAPI, GET /, GET /health, создание БД,
   изоляция данных пользователей, основные endpoints.
"""
import ast
import hashlib
import hmac
import json
import os
import py_compile
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
failed = 0


def ok(msg):
    print(f"  ✔ {msg}")


def bad(msg):
    global failed
    failed += 1
    print(f"  ✘ {msg}")


def check(cond, msg):
    ok(msg) if cond else bad(msg)


# 1 -------------------------------------------------------------------------
print("1. Файлы")
REQUIRED = ["requirements.txt", "Dockerfile", "bot.py", "api.py", "database.py", "core.py",
            ".env.example", "README.md", "web/index.html", "web/app.js", "web/style.css"]
for name in REQUIRED:
    check((ROOT / name).is_file(), name)

# 2 -------------------------------------------------------------------------
print("2. Компиляция Python")
for name in ["bot.py", "api.py", "database.py", "core.py"]:
    try:
        py_compile.compile(str(ROOT / name), doraise=True,
                           cfile=str(Path(tempfile.gettempdir()) / (name + 'c')))
        ok(f"py_compile {name}")
    except py_compile.PyCompileError as exc:
        bad(f"{name}: {exc}")

# 3 -------------------------------------------------------------------------
print("3. localhost / 127.0.0.1 в production-конфигурации")
for name in ["bot.py", "api.py", "database.py", "core.py", "Dockerfile", ".env.example",
             "web/index.html", "web/app.js", "web/style.css", "requirements.txt"]:
    text = (ROOT / name).read_text(encoding="utf-8") if (ROOT / name).exists() else ""
    check("localhost" not in text and "127.0.0.1" not in text, f"нет localhost в {name}")
check('host="0.0.0.0"' in (ROOT / "bot.py").read_text(encoding="utf-8"), "uvicorn слушает 0.0.0.0")

# 4 -------------------------------------------------------------------------
print("4. Импорты и requirements.txt")
PKG = {"aiogram": "aiogram", "fastapi": "fastapi", "uvicorn": "uvicorn", "sqlalchemy": "sqlalchemy",
       "pydantic": "pydantic", "dotenv": "python-dotenv", "httpx": "httpx"}
reqs = {line.split("[")[0].split(">")[0].split("=")[0].split("<")[0].strip().lower()
        for line in (ROOT / "requirements.txt").read_text().splitlines() if line.strip()}
local = {p.stem for p in ROOT.glob("*.py")}
for name in ["bot.py", "api.py", "database.py", "core.py"]:
    tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods = [node.module.split(".")[0]]
        for mod in mods:
            if mod in sys.stdlib_module_names or mod in local:
                continue
            pkg = PKG.get(mod, mod)
            check(pkg in reqs, f"{name}: import {mod} -> {pkg} есть в requirements.txt")
check("aiosqlite" in reqs, "aiosqlite в requirements.txt (драйвер SQLite)")
check("greenlet" in reqs, "greenlet в requirements.txt (нужен sqlalchemy.ext.asyncio)")

# 5 -------------------------------------------------------------------------
print("5. Формулы и подпись Telegram")
import core  # noqa: E402

RATES = {"coins": {"TON": {"usd": 2.0, "rub": 160.0, "change_24h": 0},
                   "USDT": {"usd": 1.0, "rub": 80.0, "change_24h": 0},
                   "BTC": {"usd": 100000.0, "rub": 8e6, "change_24h": 0},
                   "ETH": {"usd": 4000.0, "rub": 320000.0, "change_24h": 0}}, "usd_rub": 80.0}

g = core.calc_guarantee(100000, "RUB", 5, 1, "included", RATES)
check((g["profit"], g["guarantee_fee"], g["total"]) == (5000.0, 1050.0, 103950.0), "гарант: 5000 / 1050 / 103950 (пример из ТЗ)")
g2 = core.calc_guarantee(100000, "RUB", 5, 1, "on_top", RATES)
check(g2["total"] == 106050.0 and g2["net_profit"] == 5000.0, "гарант: комиссия сверху -> 106050")

e = core.calc_exchange("USD→TON", 100, 1.0, 1.5, "deducted", RATES)
check((e["gross"], e["fee_amount"], e["result"]) == (100.0, 1.5, 98.5), "обмен: 100 / 1.5 / 98.5 (пример из ТЗ)")
e2 = core.calc_exchange("USD→TON", 100, None, 0, "deducted", RATES)
check(e2["result"] == 50.0, "обмен: авто-курс USD->TON при 1 TON = $2 даёт 50 TON")
e3 = core.calc_exchange("RUB→TON", 1600, None, 0, "deducted", RATES)
check(e3["result"] == 10.0, "обмен: RUB->TON через курс USD/RUB")

n = core.calc_nft(10, "TON", 15, "TON", RATES)
check((n["profit"], n["roi"]) == (5.0, 50.0), "NFT: +5 TON, ROI 50% (пример из ТЗ)")

p = core.calc_p2p(100, "USDT", 9000, "RUB", 0, 0, 0, "percent", RATES)  # 9000 RUB = 112.5 USDT
check(abs(p["profit"] - 12.5) < 1e-9 and abs(p["roi"] - 12.5) < 1e-9, "P2P: разные валюты пересчитываются по курсу")
p = core.calc_p2p(100, "USD", 100, "USD", 1, 2, 1, "percent", RATES)
check(abs(p["fees_total"] - 4.0) < 1e-9 and abs(p["profit"] - (-4.0)) < 1e-9, "P2P: комиссии в процентах")
p = core.calc_p2p(100, "USD", 110, "USD", 1, 2, 3, "fixed", RATES)
check(abs(p["profit"] - 4.0) < 1e-9, "P2P: фиксированные комиссии")
try:
    core.calc_p2p(0, "USD", 1, "USD", 0, 0, 0, "percent", RATES)
    bad("P2P: нулевая сумма должна давать ошибку")
except core.CalcError:
    ok("P2P: нулевая сумма -> ошибка")

TOKEN = "123456:TEST-TOKEN"


def make_init_data(user_id, token=TOKEN, age=0):
    user = json.dumps({"id": user_id, "first_name": "Тест", "username": "tester"}, separators=(",", ":"), ensure_ascii=False)
    fields = {"auth_date": str(int(time.time()) - age), "query_id": "AAH", "user": user}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


check(core.verify_init_data(make_init_data(42), TOKEN)["id"] == 42, "initData: верная подпись принята")
for label, data, tok in (("чужой токен", make_init_data(42, "1:OTHER"), TOKEN),
                         ("подделанный id", make_init_data(42).replace("%22id%22%3A42", "%22id%22%3A43"), TOKEN),
                         ("просроченная", make_init_data(42, age=10 * 86400), TOKEN)):
    try:
        core.verify_init_data(data, tok)
        bad(f"initData: {label} должна отклоняться")
    except core.AuthError:
        ok(f"initData: {label} отклонена")

# 6 -------------------------------------------------------------------------
print("6. FastAPI, база данных, endpoints")
try:
    import fastapi  # noqa: F401
    import sqlalchemy  # noqa: F401
    import aiosqlite  # noqa: F401
    import httpx  # noqa: F401
except ImportError as exc:
    print(f"  – пропущено: не установлены зависимости ({exc}). Выполните pip install -r requirements.txt")
else:
    tmp = tempfile.mkdtemp()
    os.environ.update(DATA_DIR=tmp, DATABASE_URL=f"sqlite+aiosqlite:///{tmp}/test.db",
                      BOT_TOKEN=TOKEN, DEV_MODE="1", PORT="3000")
    from fastapi.testclient import TestClient
    from api import app

    with TestClient(app) as c:
        r = c.get("/health")
        check(r.status_code == 200 and r.json() == {"status": "ok", "service": "P2P Myjot", "port": 3000}, "GET /health")
        r = c.get("/")
        check(r.status_code == 200 and "P2P Myjot" in r.text and "__V__" not in r.text, "GET /")
        check(c.get("/static/app.js").status_code == 200 and c.get("/static/style.css").status_code == 200, "статика /static/*")
        check(Path(tmp, "test.db").exists(), "файл базы данных создан")

        r = c.get("/api/rates")
        check(r.status_code == 200 and "TON" in r.json()["coins"], "GET /api/rates (даже без интернета)")
        check(len(c.get("/api/fees").json()) == 24, "exchange_fees заполнена (3 сервиса x 8 пар)")

        check(c.get("/api/history/1").status_code == 401, "без авторизации -> 401")
        H1 = {"X-Dev-Telegram-Id": "1"}
        check(c.get("/api/history/1", headers=H1).status_code == 200, "свой /api/history -> 200")
        check(c.get("/api/history/2", headers=H1).status_code == 403, "чужой /api/history -> 403")
        H_SIGNED = {"X-Telegram-Init-Data": make_init_data(7)}
        check(c.get("/api/history/7", headers=H_SIGNED).status_code == 200, "подписанный initData принят")
        check(c.get("/api/history/8", headers=H_SIGNED).status_code == 403, "подписанный initData не открывает чужой ID")
        bad_sig = {"X-Telegram-Init-Data": make_init_data(7).replace("hash=", "hash=00")}
        check(c.get("/api/history/7", headers=bad_sig).status_code == 401, "битая подпись -> 401")

        r = c.post("/api/guarantee", headers=H1, json={"amount": 100000, "currency": "RUB", "profit_percent": 5, "fee_percent": 1})
        check(r.status_code == 200 and r.json()["result"]["total"] == 103950.0, "POST /api/guarantee")
        r = c.post("/api/calculate", headers=H1, json={"buy_amount": 100, "buy_currency": "USD", "sell_amount": 110, "sell_currency": "USD"})
        check(r.status_code == 200 and r.json()["result"]["profit"] == 10.0, "POST /api/calculate")
        r = c.post("/api/calculate", headers=H1, json={"buy_amount": -1, "buy_currency": "USD", "sell_amount": 1, "sell_currency": "USD"})
        check(r.status_code == 422, "некорректные данные -> 422, не 500")
        r = c.post("/api/nft", headers=H1, json={"name": "Example NFT", "link": "https://t.me/nft/x", "buy_price": 10,
                                                 "buy_currency": "TON", "sell_price": 15, "sell_currency": "TON"})
        nft_id = r.json()["item"]["id"]
        check(r.status_code == 200 and r.json()["item"]["profit"] == 5.0, "POST /api/nft")
        r = c.post("/api/nft", headers=H1, json={"name": "x", "link": "javascript:alert(1)", "buy_price": 1,
                                                 "buy_currency": "TON", "sell_price": 1, "sell_currency": "TON"})
        check(r.status_code == 422, "ссылка не http(s) отклонена")
        r = c.post("/api/exchange", headers=H1, json={"service": "Send", "pair": "USD→TON", "amount": 100, "rate": 1})
        check(r.status_code == 200 and r.json()["result"]["fee_percent"] == 0.75, "POST /api/exchange (автокомиссия из таблицы)")
        check(len(c.get("/api/nft/1", headers=H1).json()["items"]) == 1, "GET /api/nft")
        check(len(c.get("/api/guarantee/1", headers=H1).json()["items"]) == 1, "GET /api/guarantee")
        check(len(c.get("/api/exchange/1", headers=H1).json()["items"]) == 1, "GET /api/exchange")
        check(c.get("/api/history/1?type=p2p&period=today", headers=H1).json()["total"] == 1, "история: фильтры type/period")
        st = c.get("/api/stats/1", headers=H1).json()
        check(st["nft_count"] == 1 and st["exchange_count"] == 1 and len(st["daily"]) == 30, "GET /api/stats")
        H2 = {"X-Dev-Telegram-Id": "2"}
        check(c.delete(f"/api/nft/{nft_id}", headers=H2).status_code == 404, "чужой NFT нельзя удалить")
        check(c.delete(f"/api/nft/{nft_id}", headers=H1).status_code == 200, "DELETE /api/nft/{id}")
        check(c.delete("/api/history/1", headers=H1).json()["deleted"] >= 3, "DELETE /api/history/{id}")

print()
print("ГОТОВО: все проверки пройдены" if not failed else f"ОШИБОК: {failed}")
sys.exit(1 if failed else 0)
