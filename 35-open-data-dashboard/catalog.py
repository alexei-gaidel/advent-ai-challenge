"""Каталог портала: категории → наборы с издателями, профиль набора, разбор чисел и дат.

Профиль — то, что видит модель, когда пишет рецепт сведения: паспорт, поля структуры
(русские названия и формат) и первые строки последней версии. Весь файл в промпт не
идёт: у формы 0503317 сотни строк, а для рецепта хватает шапки и начала.

    python3 catalog.py finansy                         # наборы категории по издателям
    python3 catalog.py --profile <identifier>          # профиль одного набора
"""

import json
import re
import sys

import portal

HEAD_ROWS = 12

# Издатель → короткое имя МО для графиков: «Администрация города Сургута» → «Сургут»,
# «Администрация Сургутского района» → «Сургутский р-н». Ведомства округа остаются как есть.
MO_PATTERN = re.compile(r"Администрация\s+(?:(города|г\.)\s+)?(.+?)(\s+района)?$")


def short_publisher(name):
    match = MO_PATTERN.match((name or "").strip())
    if not match:
        return name
    place = match.group(2).strip()
    if match.group(3):                                   # Сургутского → Сургутский
        return re.sub(r"ого$", "ий", place) + " р-н"
    # Родительный падеж городов: Сургута, Мегиона, Когалыма → без «а».
    # Нягань, Покачи, Пыть-Ях, Радужный, Урай, Югорск не склоняются на портале.
    return place[:-1] if place.endswith("а") else place


def parse_number(value):
    """«16 441 118,99» · «246769,7» · «-191074,06» · «1 234.5» → float; «x», «-», «» → None."""
    if value is None:
        return None
    text = str(value).replace(" ", "").replace(" ", "").replace(" ", "").strip()
    if text in ("", "-", "—", "x", "х", "X", "Х"):
        return None
    if text.count(",") == 1 and text.count(".") == 0:
        text = text.replace(",", ".")
    elif text.count(",") >= 1 and text.count(".") == 1:
        text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})|(\d{4})-(\d{2})-(\d{2})")


def parse_date(value):
    """Первая дата в строке → «2026-10-01». Понимает «01.10.2026 00:00» и «… на 01.10.2026 года»."""
    match = DATE.search(str(value or ""))
    if not match:
        return None
    if match.group(1):
        return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
    return f"{match.group(4)}-{match.group(5)}-{match.group(6)}"


def version_date(data_url):
    stamp = portal.version_stamp(data_url)
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if stamp != "0" else None


# --- каталог ---------------------------------------------------------------------------

def category(slug):
    """Наборы категории: [{id, title, publisher, mo}] + сведения о категории."""
    cats = {c["slug"]: c for c in portal.categories()}
    if slug not in cats:
        raise ValueError(f"нет категории {slug}")
    items = portal.category_datasets(slug)
    for item in items:
        item["mo"] = short_publisher(item["publisher"])
    return {**cats[slug], "datasets": items}


def profile(identifier, head=HEAD_ROWS):
    """Паспорт + структура + первые строки последней версии."""
    reg = portal.registry()
    if identifier not in reg:
        raise ValueError(f"набора {identifier} нет в реестре портала")
    entry = reg[identifier]
    if entry["format"] != "csv":
        raise ValueError(f"формат {entry['format']} не поддерживается — только csv")
    info = portal.passport(entry["meta"])
    if not info["versions"]:
        raise ValueError("у набора нет ни одной версии данных")
    latest = info["versions"][0]
    rows = portal.data_rows(latest)
    fields = portal.structure(latest)
    header = rows[0] if rows else []
    titles = {f["field"]: f for f in fields}
    columns = []
    for index, name in enumerate(header):
        meta = titles.get(name.strip().lstrip("﻿"), {})
        columns.append({"index": index, "field": name, "title": meta.get("title") or name,
                        "format": meta.get("format", "")})
    return {
        "id": identifier, "title": info.get("title") or entry["title"],
        "description": info.get("description", ""), "creator": info.get("creator", ""),
        "subject": info.get("subject", ""), "modified": info.get("modified", "")[:10],
        "version_url": latest, "version_date": version_date(latest),
        "versions": len(info["versions"]), "columns": columns,
        "rows": rows[1:], "head": rows[1:1 + head], "row_count": len(rows) - 1,
    }


def profile_for_prompt(prof):
    """Компактный текст профиля для модели: колонки с номерами и первые строки."""
    cols = "\n".join(f"  [{c['index']}] {c['title']}" + (f" ({c['format']})" if c["format"] else "")
                     for c in prof["columns"])
    head = "\n".join("  " + " | ".join(cell[:60] for cell in row) for row in prof["head"])
    return (f"id: {prof['id']}\nназвание: {prof['title']}\nиздатель: {prof['creator']}\n"
            f"описание: {prof['description'][:300]}\nключевые слова: {prof['subject'][:200]}\n"
            f"версия данных от {prof['version_date']}, строк {prof['row_count']}\n"
            f"колонки:\n{cols}\nпервые строки:\n{head}")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--profile":
        print(profile_for_prompt(profile(sys.argv[2])))
    else:
        data = category(sys.argv[1] if len(sys.argv) > 1 else "finansy")
        print(f"{data['title']}: {len(data['datasets'])} наборов")
        by_mo = {}
        for item in data["datasets"]:
            by_mo.setdefault(item["mo"], []).append(item["title"])
        for mo, titles in by_mo.items():
            print(f"\n{mo}")
            for title in titles:
                print(f"  · {title[:110]}")
    print(json.dumps(portal.STATS), file=sys.stderr)
