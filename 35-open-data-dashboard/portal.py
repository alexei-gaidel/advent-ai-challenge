"""Клиент портала открытых данных ХМАО — Югры (data.admhmao.ru).

Портал устроен по методическим рекомендациям 3.0:
    /opendata/list.json                 реестр всех наборов
    /datasets/?category=<slug>          страница категории: ВСЕ её наборы, сгруппированные
                                        по издателю (ведомство или муниципалитет)
    /opendata/csv/<n>/meta.csv          паспорт: описание, издатель, ссылки на версии данных
    /opendata/csv/<n>/structure/…csv    структура: поле, описание по-русски, формат
    /opendata/csv/<n>/data/…csv         сами данные (версия = метка времени в имени)

Грабли: ссылки в паспортах ведут на admhmao.ru, а оттуда urllib получает 403. Тот же путь
на data.admhmao.ru отдаётся — хост переписывается.

Вежливость: свой User-Agent, не чаще одного запроса в секунду, кэш в data/cache.db.
Файлы данных с меткой времени в имени неизменны — кэшируются навсегда, паспорта и
страницы — на сутки. Нет сети — работаем из кэша, даже просроченного, и говорим об этом.

    python3 portal.py --selftest
"""

import csv
import html
import io
import json
import pathlib
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = "https://data.admhmao.ru"
HERE = pathlib.Path(__file__).resolve().parent
CACHE = HERE / "data" / "cache.db"
USER_AGENT = "Mozilla/5.0 (compatible; advent-ai-challenge/1.0; open-data dashboard)"
MIN_INTERVAL = 1.0          # секунд между запросами к порталу
TTL = 24 * 3600             # паспорта, реестр, страницы категорий
IMMUTABLE = re.compile(r"/(data|structure)/[^/]*\d{8}T\d{6}[^/]*\.csv$")

_lock = threading.Lock()
_last_request = [0.0]
STATS = {"network": 0, "cache": 0, "stale": 0}


class PortalError(RuntimeError):
    pass


def _db():
    CACHE.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(CACHE)
    db.execute("CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, body BLOB NOT NULL, "
               "fetched_at REAL NOT NULL)")
    return db


def normalize(url):
    """Ссылки паспортов ведут на admhmao.ru (403 для скриптов) — берём data.admhmao.ru."""
    url = url.strip()
    if url.startswith("/"):
        url = BASE + url
    return re.sub(r"^https?://(www\.)?admhmao\.ru/", BASE + "/", url)


def fetch(url, fresh=False):
    """Текст по URL: из кэша, если он свежий, иначе с портала. Кодировка — utf-8 или cp1251."""
    url = normalize(url)
    with _lock:
        db = _db()
        row = db.execute("SELECT body, fetched_at FROM pages WHERE url = ?", (url,)).fetchone()
        if row and not fresh and (IMMUTABLE.search(url) or time.time() - row[1] < TTL):
            STATS["cache"] += 1
            db.close()
            return _decode(row[0])
        wait = MIN_INTERVAL - (time.monotonic() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.monotonic()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                           "Accept": "*/*"})
            with urllib.request.urlopen(request, timeout=40) as response:
                body = response.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if row:
                STATS["stale"] += 1
                db.close()
                return _decode(row[0])
            db.close()
            reason = getattr(error, "code", None) or getattr(error, "reason", error)
            raise PortalError(f"портал не отдал {url}: {reason}") from error
        STATS["network"] += 1
        if b"smartcaptcha" in body[:20000] and url.endswith(".csv"):
            db.close()
            raise PortalError(f"вместо данных портал показал капчу: {url}")
        db.execute("INSERT OR REPLACE INTO pages VALUES (?, ?, ?)", (url, body, time.time()))
        db.commit()
        db.close()
        return _decode(body)


def _decode(body):
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", "replace")


# --- реестр и категории ------------------------------------------------------------

def registry():
    """Все наборы портала: {identifier: {title, meta, format}}."""
    data = json.loads(fetch("/opendata/list.json"))
    return {item["identifier"]: {"title": item["title"].strip(), "meta": item["link"],
                                 "format": item["format"]} for item in data["meta"]}


def _text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def categories():
    """19 категорий портала: [{slug, title, count}] — из боковой панели."""
    page = fetch("/datasets/")
    found = []
    for slug, body in re.findall(r'<a href="\?category=([\w-]+)" class="aside-list-link">(.*?)</a>',
                                 page, re.S):
        count = re.search(r'aside-list-count">\s*(\d+)', body)
        title = re.search(r'aside-list-title">(.*?)</span>', body, re.S)
        found.append({"slug": slug, "title": _text(title.group(1)) if title else slug,
                      "count": int(count.group(1)) if count else None})
    if not found:
        raise PortalError("не нашёл список категорий на /datasets/ — разметка портала изменилась?")
    return found


def category_datasets(slug):
    """Наборы категории с издателями: [{id, title, publisher}]. Издатель — из группировки
    на странице категории (ведомство округа или администрация МО)."""
    if not re.fullmatch(r"[\w-]+", slug or ""):
        raise PortalError("плохой slug категории")
    page = fetch(f"/datasets/?category={slug}")
    items = []
    for owner, block in re.findall(r'<dt class="owner-title">(.*?)</dt>\s*<dd class="set-container">'
                                   r'(.*?)</dd>', page, re.S):
        for ident, title in re.findall(r'<a href="/opendata/([^"/]+)"[^>]*class="set-link">(.*?)</a>',
                                       block, re.S):
            items.append({"id": ident, "title": _text(title), "publisher": _text(owner)})
    return items


# --- набор: паспорт, структура, данные ---------------------------------------------

def _rows(text):
    """CSV с автоопределением разделителя: на портале встречаются и «,», и «;»."""
    head = text[:4000]
    delimiter = ";" if head.count(";") > head.count(",") else ","
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


def passport(meta_url):
    """Паспорт набора: поля + версии данных от новой к старой."""
    rows = _rows(fetch(meta_url))
    info, versions = {}, []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        key, value = row[0].strip(), row[1].strip()
        if key.startswith("data-"):
            versions.append(normalize(value))
        else:
            info[key] = value
    # Новые сверху. У самых старых версий метки «data-…T…» в имени нет — они в конец.
    versions.sort(key=version_stamp, reverse=True)
    info["versions"] = versions
    return info


def version_stamp(data_url):
    """Метка версии «20260930T153717» из имени файла; у самых старых её нет — «0»."""
    match = re.search(r"data-(\d{8}T\d{6})", data_url)
    return match.group(1) if match else "0"


def structure_url(data_url):
    match = re.search(r"structure-(\d{8}T\d{6})\.csv$", data_url)
    if not match:
        return None
    return re.sub(r"/data/[^/]+$", f"/structure/structure-{match.group(1)}.csv", data_url)


def structure(data_url):
    """Структура версии: [{field, title, format}]. Нет файла — пустой список."""
    url = structure_url(data_url)
    if not url:
        return []
    try:
        rows = _rows(fetch(url))
    except PortalError:
        return []
    fields = []
    for row in rows[1:]:
        if len(row) >= 4:
            fields.append({"field": row[0].strip(), "title": row[2].strip().lstrip("﻿"),
                           "format": row[3].strip()})
    return fields


def data_rows(data_url):
    """Сырые строки CSV последней версии (заголовок — первая строка)."""
    return [row for row in _rows(fetch(data_url)) if any(cell.strip() for cell in row)]


def selftest():
    started = time.monotonic()
    reg = registry()
    print(f"реестр: {len(reg)} наборов")
    cats = categories()
    print(f"категорий: {len(cats)} — " + ", ".join(f"{c['title']} ({c['count']})" for c in cats[:5]) + " …")
    items = category_datasets("finansy")
    publishers = sorted({i["publisher"] for i in items})
    print(f"finansy: {len(items)} наборов, издателей {len(publishers)}; первый: {items[0]}")
    missing = [i["id"] for i in items if i["id"] not in reg]
    print(f"нет в реестре: {len(missing)} {missing[:3]}")
    ident = "8601009620-gosdolghmao2_depfin"
    info = passport(reg[ident]["meta"])
    print(f"паспорт {ident}: {info['title']} · {info.get('creator')} · версий {len(info['versions'])}")
    fields = structure(info["versions"][0])
    print("структура:", [f["title"][:30] for f in fields])
    rows = data_rows(info["versions"][0])
    print(f"данные: {len(rows)} строк, первая: {rows[1][:4]}")
    print(f"{STATS} за {time.monotonic() - started:.1f} с")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        STATS.update(network=0, cache=0, stale=0)
        print("\nповтор (должен идти из кэша):")
        selftest()
