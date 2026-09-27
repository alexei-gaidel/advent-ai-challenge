"""Проверка оркестрации: те ли инструменты, на тех ли серверах, в том ли порядке.

Эталон сценария — не список вызовов, а граф зависимостей. Прогноз и курс можно
получать в любом порядке относительно друг друга, но прогноз — только после
поиска города, бюджет — только после курса, заметка — после прогноза и бюджета.
Поэтому проверяется частичный порядок, а не совпадение с одной «правильной» лентой.

Порядок проверяется по данным, а не по номерам вызовов: аргумент place_id у
forecast должен встречаться в ответе более раннего успешного search. Если модель
вызвала forecast в том же круге, что и search, идентификатор она выдумала — и
проверка это увидит, даже если по номерам всё «по порядку».
"""

import re

TRIP_DATE = "2026-10-03"
TRIP_ITEMS = {"билет на матч": 15000, "поезд Москва — Екатеринбург туда-обратно": 12400,
              "гостиница, 2 ночи": 9000}
TRIP_TOTAL = sum(TRIP_ITEMS.values())

TASK = (
    "Еду из Москвы в Екатеринбург на матч Россия — Намибия 3 октября. "
    "Узнай погоду в Екатеринбурге на день матча. Посчитай бюджет поездки: билет "
    "15 000 ₽, поезд туда-обратно 12 400 ₽, гостиница 2 ночи по 4 500 ₽ — и переведи "
    "итог в евро по курсу ЦБ, это для друга из Германии. Сохрани план поездки в заметку "
    "со ссылками на прогноз и бюджет, добавь чек-лист вещей с учётом погоды и покажи "
    "мне итоговую заметку."
)

# Шаги эталона. needs: аргумент → инструмент, из ответа которого он должен прийти.
# after: инструменты, которые обязаны быть выполнены раньше (без передачи id).
STEPS = [
    {"tool": "weather__search"},
    {"tool": "weather__forecast", "needs": {"place_id": "weather__search"}},
    {"tool": "money__rates"},
    {"tool": "money__budget", "needs": {"rate_id": "money__rates"}},
    {"tool": "notes__create",
     "needs": {"sources": ["weather__forecast", "money__budget"]}},
    {"tool": "notes__checklist_add", "needs": {"note_id": "notes__create"},
     "after": ["weather__forecast"]},
    {"tool": "notes__read", "needs": {"note_id": "notes__create"},
     "after": ["notes__checklist_add"]},
]

SERVER_OF = {step["tool"]: step["tool"].split("__")[0] for step in STEPS}

# Короткие запросы на маршрутизацию: какой инструмент должен быть вызван первым.
# Два из них упираются в коллизию имён search.
PROBES = [
    ("Какая погода будет в Казани послезавтра?", "weather__search"),
    ("Найди мою заметку про поездку на матч.", "notes__search"),
    ("Почём сейчас юань по курсу ЦБ?", "money__rates"),
    ("Что у меня в чек-листе к матчу?", "notes__search"),
    ("Найди город Пермь — какой у него place_id?", "weather__search"),
]


def _values(value):
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)] if value not in (None, "") else []


def _traced(value, source_tool, before_seq, calls):
    """Встречается ли значение в ответе успешного вызова source_tool до before_seq."""
    return any(c["name"] == source_tool and c["ok"] and c["seq"] < before_seq
               and value in c["result"] and c["round"] < _round_of(before_seq, calls)
               for c in calls)


def _round_of(seq, calls):
    return next(c["round"] for c in calls if c["seq"] == seq)


def check(calls):
    """Разбор ленты вызовов одного прогона сценария."""
    names = [c["name"] for c in calls]
    report = {"steps": [], "violations": [], "facts": {}}

    for step in STEPS:
        tool = step["tool"]
        attempts = [c for c in calls if c["name"] == tool]
        good = [c for c in attempts if c["ok"]]
        row = {"tool": tool, "called": bool(attempts), "ok": bool(good),
               "server_ok": all(c["server"] == SERVER_OF[tool] for c in attempts),
               "ids_ok": None, "order_ok": None}

        if good:
            # Засчитывается первый успешный вызов: по нему и проверяем происхождение id.
            call = good[0]
            ids_ok = True
            for argument, sources in (step.get("needs") or {}).items():
                sources = sources if isinstance(sources, list) else [sources]
                values = _values(call["arguments"].get(argument))
                for source in sources:
                    if not any(_traced(v, source, call["seq"], calls) for v in values):
                        ids_ok = False
                        report["violations"].append(
                            f"{tool}: {argument} не пришёл из ответа {source}")
            order_ok = ids_ok
            for earlier in step.get("after") or []:
                if not any(c["name"] == earlier and c["ok"] and c["seq"] < call["seq"]
                           for c in calls):
                    order_ok = False
                    report["violations"].append(f"{tool} вызван раньше {earlier}")
            row["ids_ok"], row["order_ok"] = ids_ok, order_ok
        else:
            report["violations"].append(f"{tool}: " + ("все вызовы с ошибкой" if attempts
                                                       else "не вызван"))
        report["steps"].append(row)

    report["facts"] = facts(calls)
    for fact, ok in report["facts"].items():
        if ok is False:
            report["violations"].append(f"факт не сошёлся: {fact}")

    expected = {s["tool"] for s in STEPS}
    report["calls"] = len(calls)
    report["errors"] = sum(1 for c in calls if not c["ok"])
    report["extra"] = len(calls) - len(STEPS)
    report["unknown"] = sorted({n for n in names if n not in expected})
    report["servers"] = sorted({c["server"] for c in calls if c["server"]})
    report["rounds"] = max((c["round"] for c in calls), default=0)
    report["steps_ok"] = sum(1 for r in report["steps"] if r["ok"])
    report["order_ok"] = all(r["order_ok"] for r in report["steps"])
    report["passed"] = not report["violations"]
    return report


def facts(calls):
    """Содержательные проверки: тот ли город, та ли дата, та ли валюта, та ли сумма."""
    def first_ok(tool):
        return next((c for c in calls if c["name"] == tool and c["ok"]), None)

    search, forecast = first_ok("weather__search"), first_ok("weather__forecast")
    rates, budget = first_ok("money__rates"), first_ok("money__budget")
    total = None
    if budget:
        found = re.search(r"итого: ([\d  ]+) ₽", budget["result"])
        total = int(re.sub(r"\D", "", found.group(1))) if found else None
    return {
        "город Екатеринбург": ("екатеринбург" in str(search["arguments"]).lower()
                               if search else None),
        f"дата {TRIP_DATE}": (forecast["arguments"].get("date") == TRIP_DATE
                              if forecast else None),
        "валюта EUR": (str(rates["arguments"].get("currency", "")).upper() == "EUR"
                       if rates else None),
        f"итог {TRIP_TOTAL} ₽": (total == TRIP_TOTAL if budget else None),
    }


def check_probe(calls, expected):
    first = calls[0]["name"] if calls else None
    return {"first": first, "ok": first == expected,
            "servers": sorted({c["server"] for c in calls if c["server"]}),
            "calls": len(calls)}
