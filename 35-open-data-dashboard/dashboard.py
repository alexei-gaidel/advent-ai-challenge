"""Дашборд темы: всё считает код по сведённой таблице recipes.build().

Модель здесь не участвует. От чата приходит только «вид» — какой показатель, какие МО,
сортировка, абсолютные числа или процент исполнения, — а цифры, итоги и предупреждения о
свежести строит этот модуль. Поэтому в дашборде не может оказаться выдуманного числа.

Свежесть: отчётные даты МО сравниваются между собой. Строка, которая отстаёт от самой
свежей больше чем на 120 дней, или дата которой взята из версии файла (в самих данных
её нет), помечается — иначе план 2019 года тихо сравнивается с исполнением 2026-го.
"""

import datetime

import recipes

STALE_DAYS = 120
SORTS = ("value_desc", "value_asc", "name")
MODES = ("absolute", "execution")


def default_view(table):
    keys = [i["key"] for i in table["indicators"]]
    roles = recipes.roles_of(table["indicators"])
    return {"indicator": roles.get("fact") or (keys[0] if keys else None), "mo": [],
            "sort": "value_desc", "mode": "absolute"}


def normalize_view(view, table):
    """Вид от чата → только допустимые значения. Неизвестное отбрасывается с пометкой."""
    base = default_view(table)
    notes = []
    view = dict(view or {})
    keys = {i["key"] for i in table["indicators"]}
    if view.get("indicator") not in keys:
        if view.get("indicator"):
            notes.append(f"показателя «{view['indicator']}» в теме нет")
        view["indicator"] = base["indicator"]
    all_mo = {r["mo"] for r in table["rows"]}
    wanted = [m for m in (view.get("mo") or []) if m in all_mo]
    unknown = [m for m in (view.get("mo") or []) if m not in all_mo]
    if unknown:
        notes.append("нет в теме: " + ", ".join(unknown))
    view["mo"] = wanted
    if view.get("sort") not in SORTS:
        view["sort"] = base["sort"]
    if view.get("mode") not in MODES:
        view["mode"] = base["mode"]
    return view, notes


def _days(a, b):
    return abs((datetime.date.fromisoformat(a) - datetime.date.fromisoformat(b)).days)


def build(table, view=None):
    view, notes = normalize_view(view, table)
    indicators = {i["key"]: i for i in table["indicators"]}
    roles = recipes.roles_of(table["indicators"])
    plan_key, fact_key = roles.get("plan"), roles.get("fact")
    rows = [r for r in table["rows"] if not view["mo"] or r["mo"] in view["mo"]]

    # Самая свежая дата — только среди дат из самих данных: дата версии файла говорит, когда
    # файл выложили, а не на какое число отчёт.
    newest = max((r["date"] for r in rows if r["date"] and r.get("date_source") != "version"),
                 default=None)
    freshness = []
    for r in rows:
        from_version = r.get("date_source") == "version"
        lag = _days(newest, r["date"]) if newest and r["date"] and not from_version else None
        freshness.append({"mo": r["mo"], "date": r["date"], "modified": r["modified"],
                          "from_version": from_version,
                          "stale": bool(lag and lag > STALE_DAYS), "lag_days": lag})

    # Ряд для графика: абсолютные значения выбранного показателя или процент исполнения.
    series = []
    for r in rows:
        if view["mode"] == "execution":
            plan, fact = r["values"].get(plan_key), r["values"].get(fact_key)
            value = round(100 * fact / plan, 1) if plan and fact is not None else None
        else:
            value = r["values"].get(view["indicator"])
        series.append({"mo": r["mo"], "value": value})
    if view["sort"] == "name":
        series.sort(key=lambda s: s["mo"])
    else:
        series.sort(key=lambda s: (s["value"] is None, s["value"] or 0),
                    reverse=view["sort"] == "value_desc")
        if view["sort"] == "value_desc":
            series = [s for s in series if s["value"] is not None] + \
                     [s for s in series if s["value"] is None]

    kpis = []
    for key, ind in indicators.items():
        present = [r["values"][key] for r in rows if r["values"].get(key) is not None]
        if present:
            kpis.append({"key": key, "title": ind.get("title", key), "value": round(sum(present), 2),
                         "unit": ind.get("unit", ""), "count": len(present), "of": len(rows)})
    if plan_key and fact_key:
        both = [r for r in rows if r["values"].get(plan_key) and r["values"].get(fact_key) is not None]
        if both:
            plan = sum(r["values"][plan_key] for r in both)
            fact = sum(r["values"][fact_key] for r in both)
            kpis.append({"key": "execution", "title": "Исполнение в среднем по МО с планом и фактом",
                         "value": round(100 * fact / plan, 1), "unit": "%", "count": len(both),
                         "of": len(rows)})

    if view["mode"] == "execution":
        chart_title, unit = "Исполнение, % от плана", "%"
    else:
        chart_title = indicators.get(view["indicator"], {}).get("title", view["indicator"])
        unit = indicators.get(view["indicator"], {}).get("unit", "")
    return {
        "topic": table["topic"], "description": table.get("description", ""),
        "view": view, "notes": notes,
        "kpis": kpis,
        "chart": {"title": chart_title, "unit": unit, "series": series},
        "table": {"columns": [{"key": k, "title": i.get("title", k), "unit": i.get("unit", "")}
                              for k, i in indicators.items()],
                  "rows": [{"mo": r["mo"], "date": r["date"],
                            "date_source": r.get("date_source"), "values": r["values"],
                            "problems": r["problems"], "source": r["source"], "title": r["title"]}
                           for r in rows]},
        "freshness": freshness,
        "skipped": table["skipped"],
        "mo_all": sorted({r["mo"] for r in table["rows"]}),
    }


def numbers_on_screen(dash):
    """Все числа, которые видит пользователь: для проверки текста чата."""
    found = []
    for kpi in dash["kpis"]:
        found.append(kpi["value"])
    for item in dash["chart"]["series"]:
        if item["value"] is not None:
            found.append(item["value"])
    for row in dash["table"]["rows"]:
        found += [v for v in row["values"].values() if v is not None]
    for item in dash["freshness"]:
        if item["lag_days"]:
            found.append(item["lag_days"])
    return found
