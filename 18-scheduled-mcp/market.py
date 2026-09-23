"""Источник данных: публичный API CoinGecko.

Почему не биржа: у Binance пара XMRUSDT осталась после делистинга и молча отдаёт
застывшую цену двухнедельной давности (118 вместо 563). Агрегатор берёт цену
с торгуемых площадок и помечает время последнего обновления, поэтому такую тишину
можно поймать — см. проверку свежести ниже.

    python3 market.py --once     # снять наблюдение и напечатать
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_URL = "https://api.coingecko.com/api/v3/simple/price"

# Что отслеживаем: идентификатор в CoinGecko → короткое имя для человека.
COINS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "litecoin": "LTC",
    "monero": "XMR",
    "ethereum-classic": "ETC",
}

# Если данные старше этого срока, помечаем наблюдение устаревшим.
STALE_AFTER_SECONDS = 3 * 3600


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch(coins=None, timeout=20):
    """Снимает наблюдение по каждой монете: цена и изменение за сутки."""
    coins = coins or COINS
    query = urllib.parse.urlencode({
        "ids": ",".join(coins),
        "vs_currencies": "usd",
        "include_24hr_change": "true",
        # Время последнего обновления — по нему видно, живые ли данные.
        "include_last_updated_at": "true",
    })
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        headers={"User-Agent": "advent-ai-challenge/1.0", "Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise RuntimeError("CoinGecko: превышен лимит запросов, нужно реже") from error
        raise RuntimeError(f"CoinGecko ответил {error.code}: "
                           f"{error.read().decode('utf-8', 'replace')[:160]}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"сеть недоступна: {error.reason}") from error
    except TimeoutError as error:      # не наследник URLError, ловим отдельно
        raise RuntimeError("таймаут: источник не ответил вовремя") from error

    moment = now_iso()
    stamp = datetime.now(timezone.utc).timestamp()
    samples = []

    for coin_id, title in coins.items():
        row = data.get(coin_id)
        if not row or row.get("usd") is None:
            continue

        updated = row.get("last_updated_at")
        age = int(stamp - updated) if updated else None
        samples.append({
            "at": moment,
            "coin": coin_id,
            "title": title,
            "price": float(row["usd"]),
            "change_24h": float(row.get("usd_24h_change") or 0.0),
            # Возраст данных на стороне источника: страховка от «мёртвой» пары.
            "age_seconds": age,
            "stale": bool(age is not None and age > STALE_AFTER_SECONDS),
        })

    if not samples:
        raise RuntimeError("источник не вернул ни одной цены")
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
            mark = "  ⚠ данные устарели" if sample["stale"] else ""
            print(f"{sample['title']:<5} {format_price(sample['price']):>10} USD · "
                  f"за сутки {sample['change_24h']:+.2f}%{mark}")
