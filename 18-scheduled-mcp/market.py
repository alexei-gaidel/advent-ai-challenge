"""Источник данных: публичный API Binance.

Ключей не требует, отвечает быстро и щедр на лимиты. Из ответа берём то, что нужно
для сводки: цену, изменение за сутки, максимум, минимум и объём.

    python3 market.py --once     # снять наблюдение и напечатать
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_URL = "https://api.binance.com/api/v3/ticker/24hr"

# Что отслеживаем. Пара к USDT — привычный ориентир по доллару.
SYMBOLS = ["BTCUSDT", "ETHUSDT", "LTCUSDT", "XMRUSDT"]

# Короткие имена для человека.
TITLES = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "LTCUSDT": "LTC", "XMRUSDT": "XMR"}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch(symbols=None, timeout=20):
    """Снимает наблюдение по каждому инструменту. Возвращает список словарей."""
    symbols = symbols or SYMBOLS
    query = urllib.parse.urlencode({"symbols": json.dumps(symbols, separators=(",", ":"))})
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        headers={"User-Agent": "advent-ai-challenge/1.0", "Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"биржа ответила {error.code}: "
                           f"{error.read().decode('utf-8', 'replace')[:160]}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"сеть недоступна: {error.reason}") from error
    except TimeoutError as error:      # не наследник URLError, ловим отдельно
        raise RuntimeError("таймаут: биржа не ответила вовремя") from error

    moment = now_iso()
    samples = []
    for item in data:
        samples.append({
            "at": moment,
            "symbol": item["symbol"],
            "title": TITLES.get(item["symbol"], item["symbol"]),
            "price": float(item["lastPrice"]),
            "change_24h": float(item["priceChangePercent"]),
            "high": float(item["highPrice"]),
            "low": float(item["lowPrice"]),
            "volume": round(float(item["quoteVolume"])),
        })
    return samples


def format_price(value):
    """Крупные цены округляем до целых, мелкие оставляем с копейками."""
    if value >= 1000:
        return f"{value:,.0f}".replace(",", " ")
    if value >= 1:
        return f"{value:,.2f}".replace(",", " ")
    return f"{value:.6f}"


if __name__ == "__main__":
    if "--once" in sys.argv or len(sys.argv) == 1:
        for sample in fetch():
            print(f"{sample['title']:<5} {format_price(sample['price']):>12} USDT · "
                  f"за сутки {sample['change_24h']:+.2f}% · "
                  f"коридор {format_price(sample['low'])}–{format_price(sample['high'])}")
