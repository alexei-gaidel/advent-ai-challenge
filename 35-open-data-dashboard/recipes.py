"""Рецепты сведения: модель говорит, ГДЕ в каждом наборе лежат показатели, код их считает.

Одни и те же «расходы бюджета» у трёх городов устроены по-разному: у Сургута строка на
статью и план/факт колонками в рублях, у Мегиона форма 0503317 с итогом в строке
«Расходы бюджета — всего», у Радужного тысячи рублей, а план и факт — строками. Свести
их кодом без понимания смысла нельзя, а доверить модели сами цифры — значит получить
выдуманные цифры. Поэтому модель пишет РЕЦЕПТ на закрытом языке, а код исполняет его по
полному файлу и проверяет результат.

Рецепт набора:
    {"id": "...", "use": true, "reason": "почему не сводится (если use=false)",
     "latest_by_column": 1 | null,    — оставить только строки с последней датой в колонке
     "values": {"<показатель>": {"column": 4,
                                 "match": {"column": 0, "contains": "Расходы бюджета - всего"} | null,
                                 "sum": false,            — true: сумма колонки по строкам
                                 "multiplier": 1 | 1000 | 1000000}},
     "date": {"column": 1, "match": {...} | null} | "version"}

Код: применяет рецепт → значения в рублях → проверки (числа разобрались, строка найдена,
исполнено ≤ 1,5 × план, нет отрицательного плана) → таблица «МО × показатель».

    python3 recipes.py finansy "расходы"      # тема категории по подстроке названия
"""

import hashlib
import json
import re
import sqlite3
import sys
import time

import catalog
import portal
import providers
import topics as topic_index

MODEL = "deepseek-chat"

PROMPT = """Тема дашборда: «<<TOPIC>>» (<<DESCRIPTION>>).
Нужно свести наборы разных издателей к ОБЩИМ показателям, чтобы сравнить муниципалитеты.
Ниже профили наборов: колонки с номерами [i] и первые строки файла. Файл может быть
длиннее — рецепт исполнит код по всему файлу.

<<PROFILES>>

1. Выбери 1–4 общих показателя, которые есть в большинстве наборов (ключ латиницей,
   название по-русски, единица после приведения — «руб.» для денег).
2. Для КАЖДОГО набора напиши рецепт: где лежит каждый показатель.
   - column — номер колонки со значением;
   - match — если значение в конкретной строке: {"column": номер колонки, где искать,
     "contains": "дословный кусок текста этой ячейки"}; null — если строка не нужна;
   - sum — true, если показатель — сумма колонки по всем (отфильтрованным) строкам.
     Не суммируй, если в файле есть итоговые строки — тогда бери итог через match;
   - multiplier — во сколько раз умножить, чтобы получить рубли (тыс. руб. → 1000);
   - latest_by_column — номер колонки с датой, если в файле несколько отчётных дат
     (останутся строки последней даты); иначе null;
   - date — откуда отчётная дата: {"column": i, "match": ... или null} или "version"
     (дата версии файла, если в данных даты нет);
   - если набор не подходит к показателям темы — use: false и reason.
   Показатель, которого в наборе нет, просто не включай в values.

Ответ JSON:
{"indicators": [{"key": "plan", "title": "...", "unit": "руб."}],
 "recipes": [{"id": "...", "use": true, "reason": "", "latest_by_column": null,
              "values": {"plan": {"column": 3, "match": null, "sum": true, "multiplier": 1}},
              "date": "version"}]}"""


# --- исполнение рецепта ---------------------------------------------------------------

def roles_of(indicators):
    """{роль: ключ} — ПЕРВЫЙ показатель каждой роли. «Налоговые доходы (исполнено)» тоже
    «fact», но главный факт темы — тот, что модель назвала первым."""
    found = {}
    for indicator in indicators:
        found.setdefault(role(indicator), indicator.get("key"))
    found.pop(None, None)
    return found


def role(indicator):
    """Смысл показателя по названию: «plan» (план, утверждено, назначения), «fact»
    (исполнено, факт, поступило) или None. Ключи модель придумывает сама — по ним нельзя."""
    title = (indicator.get("title") or "").lower() if isinstance(indicator, dict) else ""
    if re.search(r"исполн|факт|поступ", title):
        return "fact"
    if re.search(r"план|утвержд|назначен", title):
        return "plan"
    return None


def _cell(row, index):
    return row[index] if isinstance(index, int) and 0 <= index < len(row) else None


def _matches(row, match):
    if not match:
        return True
    if isinstance(match, str):
        # Модель иногда пишет match строкой — ищем этот текст в любой ячейке строки.
        needle = " ".join(match.lower().split())
        return any(needle in " ".join(cell.lower().split()) for cell in row)
    if not isinstance(match, dict):
        return False
    cell = _cell(row, match.get("column"))
    needle = " ".join(str(match.get("contains") or "").lower().split())
    return cell is not None and bool(needle) and needle in " ".join(cell.lower().split())


def apply(recipe, prof, indicators):
    """Рецепт → {mo, date, values: {key: рубли}, problems: [...]}. Ничего не выдумывает:
    не нашлось — None и запись в problems."""
    rows = prof["rows"]
    problems = []
    latest = recipe.get("latest_by_column")
    if isinstance(latest, int):
        dated = [(catalog.parse_date(_cell(r, latest)), r) for r in rows]
        dates = [d for d, _ in dated if d]
        if dates:
            last = max(dates)
            rows = [r for d, r in dated if d == last]
        else:
            problems.append(f"в колонке {latest} нет дат — фильтр последней даты не применён")

    values = {}
    for key, rule in (recipe.get("values") or {}).items():
        if key not in indicators or not isinstance(rule, dict):
            continue
        column = rule.get("column")
        selected = [r for r in rows if _matches(r, rule.get("match"))]
        if rule.get("match") and not selected:
            problems.append(f"{key}: строка «{rule['match'].get('contains')}» не найдена")
            values[key] = None
            continue
        numbers = [catalog.parse_number(_cell(r, column)) for r in selected]
        numbers = [n for n in numbers if n is not None]
        if not numbers:
            problems.append(f"{key}: в колонке {column} нет чисел")
            values[key] = None
            continue
        raw = sum(numbers) if rule.get("sum") else numbers[0]
        multiplier = rule.get("multiplier") or 1
        if multiplier not in (1, 1000, 1000000):
            problems.append(f"{key}: странный множитель {multiplier}")
        values[key] = round(raw * multiplier, 2)

    date_rule = recipe.get("date")
    date = None
    if isinstance(date_rule, dict):
        found = [r for r in rows if _matches(r, date_rule.get("match"))]
        dates = [catalog.parse_date(_cell(r, date_rule.get("column"))) for r in found]
        dates = [d for d in dates if d]
        date = max(dates) if dates else None
    date_source = "data"
    if not date:
        date, date_source = prof["version_date"], "version"
        if date_rule != "version":
            problems.append("отчётная дата не найдена — взята дата версии файла")

    # Санитарные проверки: модель могла указать не ту колонку или не тот множитель.
    roles = roles_of(list(indicators.values()))
    plan, fact = values.get(roles.get("plan")), values.get(roles.get("fact"))
    if plan is not None and plan < 0:
        problems.append("отрицательный план")
    if plan and fact is not None and fact > 1.5 * plan:
        problems.append(f"исполнено больше плана в {fact / plan:.1f} раза — проверь колонки")
    return {"date": date, "date_source": date_source, "values": values, "problems": problems}


# --- тема целиком -----------------------------------------------------------------------

def find_topic(slug, needle):
    index = topic_index.build(slug)
    for number, topic in enumerate(index["topics"]):
        if needle.isdigit() and int(needle) == number or needle.lower() in topic["title"].lower():
            return topic
    raise ValueError(f"в категории {slug} нет темы «{needle}»")


def _cache():
    db = sqlite3.connect(portal.CACHE)
    db.execute("CREATE TABLE IF NOT EXISTS recipes (fingerprint TEXT PRIMARY KEY, answer TEXT, "
               "created_at REAL)")
    return db


def build(topic, temperature=0.0, use_cache=True):
    """Профили наборов темы → рецепты от модели → таблица, посчитанная кодом.

    Кэшируется ответ модели (рецепты), а не цифры: при каждом открытии код заново
    применяет рецепты к текущей версии данных."""
    started = time.monotonic()
    profiles, skipped = {}, []
    for item in topic["datasets"]:
        try:
            profiles[item["id"]] = {**catalog.profile(item["id"]), "mo": item["mo"]}
        except (ValueError, catalog.portal.PortalError) as error:
            skipped.append({"id": item["id"], "mo": item["mo"], "title": item["title"],
                            "reason": f"не загрузился: {error}"})
    text = "\n\n---\n".join(catalog.profile_for_prompt(p) for p in profiles.values())
    prompt = (PROMPT.replace("<<TOPIC>>", topic["title"])
              .replace("<<DESCRIPTION>>", topic.get("description", ""))
              .replace("<<PROFILES>>", text))
    # Отпечаток — промпт целиком: поменялись колонки или первые строки — рецепт заново.
    fingerprint = hashlib.sha256(prompt.encode()).hexdigest()[:20]
    db = _cache()
    row = db.execute("SELECT answer FROM recipes WHERE fingerprint = ?", (fingerprint,)).fetchone()
    if row and use_cache:
        answer, reply = json.loads(row[0]), {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
    else:
        reply = providers.call(MODEL, [{"role": "user", "content": prompt}],
                               temperature=temperature, max_tokens=4000,
                               response_format={"type": "json_object"})
        try:
            answer = json.loads(reply["text"])
        except json.JSONDecodeError:
            answer = {}
        if answer.get("recipes"):
            db.execute("INSERT OR REPLACE INTO recipes VALUES (?, ?, ?)",
                       (fingerprint, json.dumps(answer, ensure_ascii=False), time.time()))
            db.commit()
    db.close()
    indicators = {i["key"]: i for i in answer.get("indicators", [])
                  if isinstance(i, dict) and i.get("key")}

    rows = []
    recipes = {r.get("id"): r for r in answer.get("recipes", []) if isinstance(r, dict)}
    for ident, prof in profiles.items():
        recipe = recipes.get(ident)
        base = {"id": ident, "mo": prof["mo"], "title": prof["title"], "modified": prof["modified"],
                "source": f"https://data.admhmao.ru/opendata/{ident}"}
        if not recipe:
            skipped.append({**base, "reason": "модель не дала рецепт"})
            continue
        if not recipe.get("use", True):
            skipped.append({**base, "reason": f"модель: {recipe.get('reason') or 'не подходит к теме'}"})
            continue
        applied = apply(recipe, prof, indicators)
        if not any(v is not None for v in applied["values"].values()):
            skipped.append({**base, "reason": "по рецепту не нашлось ни одного числа: "
                                              + "; ".join(applied["problems"])})
            continue
        rows.append({**base, **applied, "recipe": recipe})
    return {
        "topic": topic["title"], "description": topic.get("description", ""),
        "indicators": list(indicators.values()), "rows": rows, "skipped": skipped,
        "prompt_tokens": reply["prompt_tokens"], "completion_tokens": reply["completion_tokens"],
        "cost": reply["cost"] or 0.0, "seconds": round(time.monotonic() - started, 1),
        "cached": bool(row and use_cache),
    }


if __name__ == "__main__":
    slug = sys.argv[1] if len(sys.argv) > 1 else "finansy"
    needle = sys.argv[2] if len(sys.argv) > 2 else "расходы"
    result = build(find_topic(slug, needle))
    print(f"■ {result['topic']} · показатели: "
          + ", ".join(f"{i['key']}={i.get('title')}" for i in result["indicators"]))
    for row in result["rows"]:
        values = ", ".join(f"{k}={v:,.0f}".replace(",", " ") if v is not None else f"{k}=—"
                           for k, v in row["values"].items())
        print(f"  {row['mo']:22} {row['date']}  {values}")
        for problem in row["problems"]:
            print(f"      ⚠ {problem}")
        print(f"      рецепт: {json.dumps({k: row['recipe'][k] for k in ('values', 'date', 'latest_by_column') if k in row['recipe']}, ensure_ascii=False)}")
    for item in result["skipped"]:
        print(f"  ✗ {item['mo']}: {item['reason']}")
    print(f"  {result['prompt_tokens']}+{result['completion_tokens']} ток. · ${result['cost']} · {result['seconds']} с")
