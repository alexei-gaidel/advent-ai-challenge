"""Срез данных по акции на дату: котировка, график, отчётность, ключевая ставка.

Сводит три источника в один словарь — дальше с ним работают и формула (scoring.py), и AI
(analyst.py). Параметр as_of позволяет собрать срез «как было тогда»: свечи до даты,
только опубликованные к ней отчёты, ставка ЦБ на тот день. На этом построена проверка
задним числом в rating_demo.py.

    python3 snapshot.py LKOH [2025-04-01]
"""

import datetime as dt
import re
import sys

import moex
import reports

CBR_URL = ("https://www.cbr.ru/hd_base/KeyRate/?UniDbQuery.Posted=True"
           "&UniDbQuery.From=01.01.2020&UniDbQuery.To={to}")


def key_rates():
    """История ключевой ставки ЦБ: [(YYYY-MM-DD, %)] по возрастанию даты."""
    today = dt.date.today()
    page = moex.fetch(CBR_URL.format(to=today.strftime("%d.%m.%Y")), ttl=86400, raw=True)
    rows = re.findall(r"<td>(\d\d)\.(\d\d)\.(\d{4})</td>\s*<td>([\d,]+)</td>", page)
    return sorted((f"{y}-{m}-{d}", float(v.replace(",", "."))) for d, m, y, v in rows)


def key_rate_on(date):
    rates = [r for r in key_rates() if r[0] <= date]
    return rates[-1][1] if rates else None


def take(ticker, as_of=None):
    """Собрать срез. as_of=None — сейчас (текущая котировка и LTM)."""
    ticker = ticker.upper()
    quote = moex.quote(ticker)
    data = reports.load(ticker)
    end = as_of or dt.date.today().isoformat()
    stock, index = moex.history(ticker, days=730, end=end)
    if len(stock) < 60:
        raise RuntimeError(f"у {ticker} меньше 60 торговых дней истории — график не оценить")
    if as_of:
        data = reports.as_of(data, as_of)
        quote = dict(quote, price=stock[-1]["close"], change_pct=0.0, time=stock[-1]["date"])
    period = reports.last_period(data)
    if period is None:
        raise RuntimeError(f"у {ticker} нет ни одного отчёта с чистой прибылью")
    return {
        "ticker": ticker, "name": quote["name"], "full_name": quote["full_name"],
        "as_of": end, "live": as_of is None,
        "quote": quote, "candles": stock, "index": index,
        "report": data, "period": period,
        "report_date": data["dates"].get(period, ""),
        "key_rate": key_rate_on(end),
    }


if __name__ == "__main__":
    snap = take(sys.argv[1] if len(sys.argv) > 1 else "LKOH", sys.argv[2] if len(sys.argv) > 2 else None)
    print({k: v for k, v in snap.items() if k not in ("candles", "index", "report")})
    print("свечей:", len(snap["candles"]), "с", snap["candles"][0]["date"], "по", snap["candles"][-1]["date"])
    print("периоды отчётности:", snap["report"]["periods"])
