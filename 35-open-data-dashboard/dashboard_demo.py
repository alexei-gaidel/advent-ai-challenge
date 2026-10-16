"""Замер на живом портале: темы, рецепты сведения и чат. temperature=0, 3 прохода.

    1. Темы: 4 категории — валидно ли разбиение с первой попытки, сколько повторов,
       насколько темы совпадают между проходами (доля пар наборов, которые в двух
       проходах одинаково «вместе» или «порознь» — индекс Рэнда).
    2. Рецепты: 3 темы «Бюджет» (расходы, доходы, долг), 11 наборов — значение,
       посчитанное кодом по рецепту модели, против ручного эталона data/truth.json:
       верно / ошибка единиц (×1000) / не та роль (план записан как факт) / неверно /
       нет. Отдельно — дата: верная, помечена как «из версии файла», неверная.
    3. Чат: 8 просьб × 3 прохода на теме «доходы» — правильно ли изменён вид (кодом по
       ожиданию) и сколько чисел в ответах нет на экране.

Кэш портала используется (данные те же), кэш тем и рецептов — нет: каждый проход
спрашивает модель заново.

    python3 dashboard_demo.py               # ~6 мин → data/dashboard_result.json
"""

import itertools
import json
import pathlib
import sys
import time

import assistant as od
import dashboard
import portal
import recipes
import topics as topic_index

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
PASSES = 3
CATEGORIES = ["finansy", "gosimushchestvo", "gosudarstvennoe-upravlenie", "dosug-i-otdykh"]
TOLERANCE = 0.001          # 0,1%

CHAT = [
    ("Где хуже всего исполняется план?",
     lambda v, t, a: v["mode"] == "execution" and v["sort"] == "value_asc"),
    ("Покажи только Сургут и Урай",
     lambda v, t, a: set(v["mo"]) == {"Сургут", "Урай"}),
    ("Отсортируй по алфавиту",
     lambda v, t, a: v["sort"] == "name"),
    ("Покажи план доходов",
     lambda v, t, a: v["mode"] == "absolute" and recipes.roles_of(t["indicators"]).get("plan") == v["indicator"]),
    ("Кто собрал больше всего доходов?",
     lambda v, t, a: v["mode"] == "absolute" and v["sort"] == "value_desc"
     and recipes.roles_of(t["indicators"]).get("fact") == v["indicator"]),
    ("Сравни Москву и Сургут",
     lambda v, t, a: set(v["mo"]) <= {"Сургут"} and "москв" in a.lower()),
    ("Можно ли сравнивать эти цифры между собой?",
     lambda v, t, a: "дат" in a.lower() or "верси" in a.lower()),
    ("Покажи все муниципалитеты по исполнению плана, лучшие сверху",
     lambda v, t, a: not v["mo"] and v["mode"] == "execution" and v["sort"] == "value_desc"),
]


def rand_index(a, b):
    """Доля пар наборов, которые в двух разбиениях одинаково вместе или порознь."""
    ids = sorted(set(a) & set(b))
    pairs = list(itertools.combinations(ids, 2))
    same = sum((a[x] == a[y]) == (b[x] == b[y]) for x, y in pairs)
    return round(same / len(pairs), 3) if pairs else None


def partition(result):
    return {d["id"]: n for n, t in enumerate(result["topics"]) for d in t["datasets"]}


# --- 1. темы --------------------------------------------------------------------------------

def measure_topics(log):
    rows = []
    for slug in CATEGORIES:
        runs = []
        for attempt in range(1, PASSES + 1):
            result = topic_index.build(slug, temperature=0, use_cache=False, save=False)
            stats = result["stats"]
            runs.append(result)
            log(f"  {slug} #{attempt}: {result['count']} наборов → {len(result['topics'])} тем · "
                f"попыток {stats['attempts']} · валидно {stats['valid']} · "
                f"{stats['prompt_tokens']}+{stats['completion_tokens']} ток. · ${stats['cost']} · {stats['seconds']} с")
            if not stats["valid"]:
                log(f"      ошибки: {stats['errors'][-1][:3]}")
        parts = [partition(r) for r in runs]
        rand = [rand_index(a, b) for a, b in itertools.combinations(parts, 2)]
        rows.append({
            "slug": slug, "category": runs[0]["category"], "count": runs[0]["count"],
            "topics": [len(r["topics"]) for r in runs],
            "first_try": sum(r["stats"]["attempts"] == 1 and r["stats"]["valid"] for r in runs),
            "valid": sum(r["stats"]["valid"] for r in runs),
            "attempts": sum(r["stats"]["attempts"] for r in runs),
            "rand": rand,
            "tokens": sum(r["stats"]["prompt_tokens"] + r["stats"]["completion_tokens"] for r in runs),
            "cost": round(sum(r["stats"]["cost"] for r in runs), 4),
            "seconds": round(sum(r["stats"]["seconds"] for r in runs), 1),
            "titles": [[t["title"] for t in r["topics"]] for r in runs],
        })
        log(f"    стабильность (Рэнд по парам проходов): {rand}")
    return rows


# --- 2. рецепты -----------------------------------------------------------------------------

def judge_value(truth, row_values, indicators, kind):
    """Вердикт по одному эталонному значению."""
    roles = recipes.roles_of(indicators)
    if kind in ("plan", "fact"):
        key = roles.get(kind)
    else:                                    # «total» — главный (первый) показатель темы
        key = indicators[0]["key"] if indicators else None
    got = row_values.get(key) if key else None
    close = lambda a, b: abs(a - b) <= max(abs(b) * TOLERANCE, 1)
    if got is not None and close(got, truth):
        return "верно"
    if got is not None and truth and (close(got * 1000, truth) or close(got, truth * 1000)):
        return "ошибка единиц"
    other = [k for k, v in row_values.items() if k != key and v is not None and close(v, truth)]
    if other:
        return "не та роль"
    return "неверно" if got is not None else "нет"


def judge_date(truth_date, row):
    if truth_date == "version":
        return "верно" if row.get("date_source") == "version" else "дата выдумана"
    return "верно" if row.get("date") == truth_date else f"неверно ({row.get('date')})"


def measure_recipes(log, truth):
    out = []
    for needle, expected in truth["topics"].items():
        topic = recipes.find_topic("finansy", needle)
        for attempt in range(1, PASSES + 1):
            table = recipes.build(topic, temperature=0, use_cache=False)
            rows = {r["id"]: r for r in table["rows"]}
            skipped = {s["id"]: s for s in table["skipped"]}
            verdicts = []
            for ident, t in expected.items():
                row = rows.get(ident)
                for kind in ("plan", "fact", "total"):
                    if kind not in t:
                        continue
                    if not row:
                        reason = skipped.get(ident, {}).get("reason", "нет в теме")
                        verdicts.append({"id": ident, "mo": t["mo"], "kind": kind,
                                         "verdict": "не сведено", "note": reason})
                        continue
                    verdicts.append({"id": ident, "mo": t["mo"], "kind": kind,
                                     "verdict": judge_value(t[kind], row["values"],
                                                            table["indicators"], kind),
                                     "got": row["values"]})
                verdicts.append({"id": ident, "mo": t["mo"], "kind": "date",
                                 "verdict": judge_date(t["date"], row) if row else "не сведено"})
            ok = sum(v["verdict"] == "верно" for v in verdicts if v["kind"] != "date")
            total = sum(1 for v in verdicts if v["kind"] != "date")
            dates_ok = sum(v["verdict"] == "верно" for v in verdicts if v["kind"] == "date")
            dates = sum(1 for v in verdicts if v["kind"] == "date")
            log(f"  {topic['title']} #{attempt}: значения {ok}/{total} · даты {dates_ok}/{dates} · "
                f"показатели: {', '.join(i['key'] + '=' + i.get('title', '')[:28] for i in table['indicators'])} · "
                f"{table['prompt_tokens']}+{table['completion_tokens']} ток. · ${table['cost']} · {table['seconds']} с")
            for v in verdicts:
                if v["verdict"] != "верно":
                    log(f"      ✗ {v['mo']} {v['kind']}: {v['verdict']} {v.get('note', '')[:90]}")
            out.append({"topic": topic["title"], "needle": needle, "attempt": attempt,
                        "values_ok": ok, "values": total, "dates_ok": dates_ok, "dates": dates,
                        "verdicts": verdicts, "indicators": table["indicators"],
                        "recipes": {r["id"]: r["recipe"] for r in table["rows"]},
                        "skipped": table["skipped"],
                        "tokens": table["prompt_tokens"] + table["completion_tokens"],
                        "cost": table["cost"], "seconds": table["seconds"]})
    return out


# --- 3. чат ---------------------------------------------------------------------------------

def measure_chat(log):
    bot = od.OpenDataAssistant()
    index = topic_index.build("finansy")
    n = next(i for i, t in enumerate(index["topics"]) if "доходы" in t["title"].lower())
    table = bot.table("finansy", n)
    out = []
    for attempt in range(1, PASSES + 1):
        for message, expect in CHAT:
            result = bot.chat("finansy", n, None, message, temperature=0)
            view = result["dashboard"]["view"]
            ok = bool(expect(view, table, result["answer"]))
            out.append({"attempt": attempt, "message": message, "ok": ok, "view": view,
                        "calls": result["calls"], "answer": result["answer"],
                        "unknown_numbers": result["unknown_numbers"],
                        "tokens": result["prompt_tokens"] + result["completion_tokens"],
                        "cost": result["cost"], "seconds": result["seconds"]})
            mark = "✓" if ok else "✗"
            log(f"  #{attempt} {mark} «{message}» → {json.dumps(view, ensure_ascii=False)}"
                + (f" · чисел нет на экране: {result['unknown_numbers']}" if result["unknown_numbers"] else ""))
            if not ok:
                log(f"      ответ: {result['answer'][:200]}")
    return out


def recheck_chat():
    """Перепроверить числа в сохранённых ответах чата текущей страховкой (без модели):
    дашборд пересчитывается кодом по тому же виду и тому же рецепту из кэша."""
    path = DATA / "dashboard_result.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    bot = od.OpenDataAssistant()
    index = topic_index.build("finansy")
    n = next(i for i, t in enumerate(index["topics"]) if "доходы" in t["title"].lower())
    table = bot.table("finansy", n)
    for row in data["chat"]:
        dash = dashboard.build(table, row["view"])
        row.setdefault("unknown_numbers_v1", row["unknown_numbers"])
        row["unknown_numbers"] = od.check_numbers(row["answer"], dash)
        if row["unknown_numbers"]:
            print(f"#{row['attempt']} «{row['message']}»: {row['unknown_numbers']}\n   {row['answer'][:300]}")
    chat = data["summary"]["chat"]
    chat.setdefault("answers_with_unknown_v1", chat["answers_with_unknown"])
    chat.setdefault("unknown_numbers_v1", chat["unknown_numbers"])
    chat["answers_with_unknown"] = sum(bool(r["unknown_numbers"]) for r in data["chat"])
    chat["unknown_numbers"] = sum(len(r["unknown_numbers"]) for r in data["chat"])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"страховка v1: {chat['answers_with_unknown_v1']} ответов ({chat['unknown_numbers_v1']} чисел) → "
          f"v2: {chat['answers_with_unknown']} ({chat['unknown_numbers']})")


def main():
    if "--recheck-chat" in sys.argv:
        return recheck_chat()
    DATA.mkdir(exist_ok=True)
    log_file = (DATA / "dashboard_demo.log").open("w", encoding="utf-8")

    def log(text):
        print(text, flush=True)
        log_file.write(text + "\n")
        log_file.flush()

    truth = json.loads((DATA / "truth.json").read_text(encoding="utf-8"))
    started = time.monotonic()
    log(f"модель {topic_index.MODEL}, temperature=0, проходов {PASSES}, портал data.admhmao.ru")
    log("\n1. Темы")
    topic_rows = measure_topics(log)
    # Для рецептов и чата темы «Бюджета» фиксируются одним разбиением из кэша:
    # иначе от прохода к проходу менялся бы состав темы, и сравнивать было бы нечего.
    topic_index.build("finansy")
    log("\n2. Рецепты сведения против ручного эталона")
    recipe_rows = measure_recipes(log, truth)
    log("\n3. Чат")
    chat_rows = measure_chat(log)

    summary = {
        "topics": {r["slug"]: {k: r[k] for k in ("category", "count", "topics", "first_try", "valid",
                                                 "attempts", "rand", "tokens", "cost", "seconds")}
                   for r in topic_rows},
        "recipes": {},
        "chat": {"ok": sum(r["ok"] for r in chat_rows), "of": len(chat_rows),
                 "answers_with_unknown": sum(bool(r["unknown_numbers"]) for r in chat_rows),
                 "unknown_numbers": sum(len(r["unknown_numbers"]) for r in chat_rows),
                 "tokens": sum(r["tokens"] for r in chat_rows),
                 "cost": round(sum(r["cost"] for r in chat_rows), 4)},
    }
    for needle in truth["topics"]:
        rows = [r for r in recipe_rows if r["needle"] == needle]
        verdicts = {}
        for r in rows:
            for v in r["verdicts"]:
                if v["kind"] != "date":
                    verdicts[v["verdict"]] = verdicts.get(v["verdict"], 0) + 1
        summary["recipes"][needle] = {
            "topic": rows[0]["topic"], "values_ok": sum(r["values_ok"] for r in rows),
            "values": sum(r["values"] for r in rows), "dates_ok": sum(r["dates_ok"] for r in rows),
            "dates": sum(r["dates"] for r in rows), "verdicts": verdicts,
            "tokens": sum(r["tokens"] for r in rows), "cost": round(sum(r["cost"] for r in rows), 4)}

    log("\nИтог")
    for slug, s in summary["topics"].items():
        log(f"  темы {s['category'][:28]:28} {s['count']:>4} наборов · тем {s['topics']} · с первой "
            f"попытки {s['first_try']}/{PASSES} · Рэнд {s['rand']} · ${s['cost']}")
    for needle, s in summary["recipes"].items():
        log(f"  рецепты {s['topic'][:32]:32} значения {s['values_ok']}/{s['values']} · даты "
            f"{s['dates_ok']}/{s['dates']} · {s['verdicts']} · ${s['cost']}")
    c = summary["chat"]
    log(f"  чат: вид верный {c['ok']}/{c['of']} · ответов с числами не с экрана "
        f"{c['answers_with_unknown']} ({c['unknown_numbers']} чисел) · ${c['cost']}")
    log(f"  портал: {portal.STATS} · всего {time.monotonic() - started:.0f} с")

    (DATA / "dashboard_result.json").write_text(json.dumps(
        {"model": topic_index.MODEL, "passes": PASSES, "summary": summary, "topics": topic_rows,
         "recipes": recipe_rows, "chat": chat_rows, "portal": portal.STATS},
        ensure_ascii=False, indent=2), encoding="utf-8")
    log("→ data/dashboard_result.json")


if __name__ == "__main__":
    main()
