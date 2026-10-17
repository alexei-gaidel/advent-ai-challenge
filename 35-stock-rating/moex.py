"""Цены с Московской биржи: официальный ISS API (iss.moex.com), без ключа.

Отсюда берутся дневные свечи акции и индекса IMOEX, текущая котировка, число акций и
поиск тикера по названию («лукойл» → LKOH). Всё приходит JSON-ом.
Ответы кэшируются в data/cache/: история не меняется, а замер должен воспроизводиться.

    python3 moex.py LKOH          # котировка и последние свечи
    python3 moex.py "сбербанк"     # поиск тикера
"""

import datetime as dt
import hashlib
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

ISS = "https://iss.moex.com/iss"
BOARD = "TQBR"                  # основной режим торгов акциями
INDEX = "IMOEX"
CACHE = pathlib.Path(__file__).resolve().parent / "data" / "cache"
USER_AGENT = "advent-ai-challenge/1.0"


def fetch(url, ttl=3600, raw=False):
    """GET с кэшем на диске. ttl — сколько секунд ответ считается свежим (None — навсегда)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + (".html" if raw else ".json"))
    if path.exists() and (ttl is None or time.time() - path.stat().st_mtime < ttl):
        text = path.read_text(encoding="utf-8")
    else:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                text = response.read().decode("utf-8")
        except (OSError, TimeoutError) as error:
            if path.exists():               # сеть упала — лучше вчерашние данные, чем никаких
                text = path.read_text(encoding="utf-8")
            else:
                raise RuntimeError(f"не удалось скачать {url.split('?')[0]}: {error}") from error
        else:
            path.write_text(text, encoding="utf-8")
    return text if raw else json.loads(text)


def table(block):
    """Блок ISS {columns, data} → список словарей."""
    return [dict(zip(block["columns"], row)) for row in block["data"]]


def find_ticker(query):
    """Тикер или название → [(SECID, название)] среди акций, торгующихся на TQBR."""
    query = query.strip()
    url = f"{ISS}/securities.json?" + urllib.parse.urlencode({
        "q": query, "iss.meta": "off", "group_by": "group", "group_by_filter": "stock_shares",
        "securities.columns": "secid,shortname,name,primary_boardid,is_traded"})
    rows = table(fetch(url, ttl=7 * 86400)["securities"])
    found = [(r["secid"], r["shortname"]) for r in rows
             if r["primary_boardid"] == BOARD and r["is_traded"]]
    exact = [f for f in found if f[0].upper() == query.upper()]
    return exact or found


def quote(ticker):
    """Текущая котировка: последняя цена, изменение за день, капитализация, число акций."""
    url = (f"{ISS}/engines/stock/markets/shares/boards/{BOARD}/securities/{ticker}.json?"
           "iss.meta=off&iss.only=securities,marketdata"
           "&securities.columns=SECID,SHORTNAME,SECNAME,ISSUESIZE,PREVPRICE"
           "&marketdata.columns=LAST,LASTTOPREVPRICE,ISSUECAPITALIZATION,UPDATETIME,SYSTIME")
    data = fetch(url, ttl=300)
    sec = table(data["securities"])
    if not sec:
        raise RuntimeError(f"на Мосбирже нет акции {ticker} в режиме {BOARD}")
    sec, md = sec[0], (table(data["marketdata"]) or [{}])[0]
    price = md.get("LAST") or sec["PREVPRICE"]
    return {
        "ticker": sec["SECID"], "name": sec["SHORTNAME"], "full_name": sec["SECNAME"],
        "price": price, "change_pct": md.get("LASTTOPREVPRICE") or 0.0,
        "shares": sec["ISSUESIZE"], "cap": md.get("ISSUECAPITALIZATION") or price * sec["ISSUESIZE"],
        "time": md.get("SYSTIME") or "",
    }


def candles(ticker, start, end=None, index=False):
    """Дневные свечи [{date, open, high, low, close, value}] с start по end (строки YYYY-MM-DD).

    ISS отдаёт не больше 500 свечей за раз — листаем через start=.
    """
    end = end or dt.date.today().isoformat()
    path = (f"engines/stock/markets/index/boards/SNDX/securities/{ticker}" if index
            else f"engines/stock/markets/shares/boards/{BOARD}/securities/{ticker}")
    out, offset = [], 0
    # Прошлые годы не меняются — кэш навсегда; если в диапазоне есть сегодня — на час.
    ttl = None if end < dt.date.today().isoformat() else 3600
    while True:
        url = (f"{ISS}/{path}/candles.json?iss.meta=off&interval=24"
               f"&from={start}&till={end}&start={offset}")
        rows = table(fetch(url, ttl=ttl)["candles"])
        out += [{"date": r["begin"][:10], "open": r["open"], "high": r["high"], "low": r["low"],
                 "close": r["close"], "value": r["value"]} for r in rows]
        if len(rows) < 500:
            return out
        offset += len(rows)


def history(ticker, days=730, end=None):
    """Свечи акции и IMOEX за days дней до end (по умолчанию — сегодня)."""
    end = end or dt.date.today().isoformat()
    start = (dt.date.fromisoformat(end) - dt.timedelta(days=days)).isoformat()
    return candles(ticker, start, end), candles(INDEX, start, end, index=True)


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "LKOH"
    hits = find_ticker(arg)
    print("найдено:", hits[:5])
    if hits:
        q = quote(hits[0][0])
        print(q)
        stock, index = history(q["ticker"], days=30)
        print(f"свечей: {len(stock)}, IMOEX: {len(index)}")
        for c in stock[-3:]:
            print(c)
