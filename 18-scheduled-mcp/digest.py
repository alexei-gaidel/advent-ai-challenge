"""Агрегация: из ряда наблюдений собирается сводка.

Одна функция считает, другая превращает в текст — текст одинаковый и для Telegram,
и для MCP-инструмента, и для веб-панели.
"""

from datetime import datetime, timedelta, timezone

import market
import store

PERIODS = {"8h": 8, "24h": 24, "7d": 24 * 7}


def build(period="24h"):
    """Сводка по каждому инструменту за период."""
    hours = PERIODS.get(period, 24)
    since = store.now() - timedelta(hours=hours)
    rows = store.read(since=since)

    # Начало текущих суток по UTC: с ним считаем изменение «с начала дня».
    day_start = store.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today = store.read(since=day_start)

    by_symbol = {}
    for sample in rows:
        by_symbol.setdefault(sample["symbol"], []).append(sample)

    items = []
    for symbol, series in by_symbol.items():
        series.sort(key=lambda item: item["at"])
        last = series[-1]
        prices = [item["price"] for item in series]

        day_series = [item for item in today if item["symbol"] == symbol]
        # Изменение с начала суток считаем по своим наблюдениям, а не по бирже:
        # это честная величина «сколько прошло с тех пор, как мы начали смотреть».
        since_day = None
        if len(day_series) > 1:
            first = day_series[0]["price"]
            if first:
                since_day = (last["price"] - first) / first * 100

        items.append({
            "symbol": symbol,
            "title": last.get("title", symbol),
            "price": last["price"],
            "change_24h": last["change_24h"],
            "change_since_day": since_day,
            "min": min(prices),
            "max": max(prices),
            "samples": len(series),
            "first_at": series[0]["at"],
            "last_at": last["at"],
        })

    items.sort(key=lambda item: item["title"])
    stats = store.samples_stats()
    return {
        "period": period,
        "hours": hours,
        "built_at": store.iso(store.now()),
        "items": items,
        "total_samples": stats["count"],
        "last_sample_at": stats["last"],
    }


def as_text(summary, title="Сводка по рынку"):
    """Человеческая форма сводки: уходит в Telegram и в ответ инструмента."""
    if not summary["items"]:
        return (f"{title}\nЗа последние {summary['hours']} ч наблюдений нет. "
                f"Всего в базе: {summary['total_samples']}.")

    lines = [f"{title} · за {summary['hours']} ч",
             f"собрано {summary['built_at'][:16].replace('T', ' ')} UTC"]
    for item in summary["items"]:
        day = (f" · с начала дня {item['change_since_day']:+.2f}%"
               if item["change_since_day"] is not None else "")
        lines.append(
            f"\n{item['title']}  {market.format_price(item['price'])} USDT\n"
            f"  за сутки {item['change_24h']:+.2f}%{day}\n"
            f"  коридор {market.format_price(item['min'])}–"
            f"{market.format_price(item['max'])} · наблюдений {item['samples']}")

    lines.append(f"\nвсего наблюдений в базе: {summary['total_samples']}")
    return "\n".join(lines)
