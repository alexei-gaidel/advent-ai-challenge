"""Агрегация: из ряда наблюдений собирается сводка.

Сводка намеренно короткая: цена и изменение за сутки. Всё остальное — коридоры,
счётчики наблюдений — в сообщение не попадает; для разбора есть market_history.
"""

from datetime import timedelta

import market
import store

PERIODS = {"8h": 8, "24h": 24, "7d": 24 * 7}


def build(period="24h"):
    """Последнее известное состояние по каждой монете за период."""
    hours = PERIODS.get(period, 24)
    since = store.now() - timedelta(hours=hours)
    rows = store.read(since=since)

    latest = {}
    for sample in rows:
        # Наблюдения идут по возрастанию времени, поэтому побеждает последнее.
        latest[sample.get("coin") or sample.get("symbol")] = sample

    order = list(market.COINS.values())
    items = sorted(latest.values(),
                   key=lambda item: order.index(item["title"])
                   if item["title"] in order else 99)

    stats = store.samples_stats()
    return {
        "period": period,
        "hours": hours,
        "built_at": store.iso(store.now()),
        "items": items,
        "total_samples": stats["count"],
        "last_sample_at": stats["last"],
    }


def as_text(summary, title="Курсы"):
    """Человеческая форма сводки: уходит в Telegram и в ответ инструмента."""
    if not summary["items"]:
        return (f"{title}\nЗа последние {summary['hours']} ч наблюдений нет.")

    lines = [f"{title} · {summary['built_at'][:16].replace('T', ' ')} UTC", ""]
    for item in summary["items"]:
        mark = "  ⚠ данные устарели" if item.get("stale") else ""
        lines.append(f"{item['title']:<4} {market.format_price(item['price']):>10} USD   "
                     f"{item['change_24h']:+.2f}% за сутки{mark}")
    return "\n".join(lines)
