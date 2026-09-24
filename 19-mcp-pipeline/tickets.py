"""Источник данных: страница события на Яндекс Афише.

Что берём и чего не берём. Запрашивается только публичная страница события, не чаще
раза в час, с честным User-Agent. Закрытые в robots.txt разделы (/api и поиск) не
используются, капча не обходится, вход в аккаунт и покупка не автоматизируются.
Если вместо страницы приходит капча — это повод остановиться, а не искать обход:
функция возвращает ошибку, а пайплайн сообщает, что мониторинг ослеп.

Предложения лежат во встроенном состоянии страницы: цены в копейках и статус продаж.

    python3 tickets.py --once     # снять снимок и напечатать
"""

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Матч Россия — Намибия, Екатеринбург Арена, 3 октября 2026.
EVENT_URL = "https://afisha.yandex.ru/yekaterinburg/sport/football-rossiia-namibiia"
EVENT_TITLE = "Россия — Намибия, Екатеринбург Арена, 3 октября"

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")

# Признаки того, что нам отдали проверку, а не страницу.
CAPTCHA_MARKERS = ("smartcaptcha", "showcaptcha", "Подтвердите, что запросы отправляли вы")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_page(url=None, timeout=25):
    """Скачивает страницу события. Возвращает HTML."""
    url = url or EVENT_URL
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9",
    })

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            html = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Афиша ответила {error.code}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"сеть недоступна: {error.reason}") from error
    except TimeoutError as error:      # не наследник URLError, ловим отдельно
        raise RuntimeError("таймаут: страница не ответила вовремя") from error

    lowered = html.lower()
    if any(marker.lower() in lowered for marker in CAPTCHA_MARKERS):
        raise RuntimeError("вместо страницы пришла капча — мониторинг остановлен")
    return html


def parse_offers(html):
    """Достаёт предложения из встроенного состояния страницы.

    Ищем объекты Ticket с ценовым диапазоном и статусом продаж. Цены в копейках,
    поэтому делим на сто.
    """
    offers = {}
    pattern = re.compile(
        r'"id":"(?P<id>[^"]+)","price":\{"__typename":"TicketPriceRange",'
        r'"currency":"(?P<currency>[a-z]+)","min":(?P<min>\d+),"max":(?P<max>\d+)\}'
        r'.{0,240}?"saleStatus":"(?P<status>[a-z_]+)"',
        re.S)

    for match in pattern.finditer(html):
        data = match.groupdict()
        # Один и тот же билет встречается в разметке несколько раз — храним по цене
        # и статусу, чтобы не считать дубли за отдельные категории.
        key = (int(data["min"]), int(data["max"]), data["status"])
        offers[key] = {
            "min_price": int(data["min"]) / 100,
            "max_price": int(data["max"]) / 100,
            "currency": data["currency"],
            "sale_status": data["status"],
        }

    return sorted(offers.values(), key=lambda offer: offer["min_price"])


def fetch(url=None):
    """Снимок предложения: список категорий и сводные числа."""
    html = load_page(url)
    offers = parse_offers(html)

    snapshot = {
        "at": now_iso(),
        "url": url or EVENT_URL,
        "title": EVENT_TITLE,
        "offers": offers,
        "offers_count": len(offers),
        "min_price": min((offer["min_price"] for offer in offers), default=None),
        "max_price": max((offer["max_price"] for offer in offers), default=None),
        "on_sale": any(offer["sale_status"] == "available" for offer in offers),
    }
    return snapshot


def format_price(value):
    if value is None:
        return "—"
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def describe(snapshot):
    """Короткое человеческое описание снимка."""
    if not snapshot["offers"]:
        return f"{snapshot['title']}: предложений не найдено"

    parts = [f"категорий {snapshot['offers_count']}",
             f"от {format_price(snapshot['min_price'])}",
             f"до {format_price(snapshot['max_price'])}",
             "продажа открыта" if snapshot["on_sale"] else "продажа закрыта"]
    return f"{snapshot['title']}: " + ", ".join(parts)


if __name__ == "__main__":
    if "--once" in sys.argv or len(sys.argv) == 1:
        snap = fetch()
        print(describe(snap))
        for offer in snap["offers"]:
            print(f"  {format_price(offer['min_price']):>10} – "
                  f"{format_price(offer['max_price']):<10} {offer['sale_status']}")
