"""Темы внутри категории — их формирует модель, проверяет код.

На портале у категории нет тематических подкатегорий, только издатели. «Бюджет» —
это вперемешку расходы Сургута, доходы Мегиона, госдолг округа и меры поддержки МСП
из разных МО. Модель группирует наборы в 3–8 тем так, чтобы внутри темы наборы разных
издателей можно было сравнивать между собой.

Модель видит номер, название и издателя набора (паспорта на этом шаге не качаются:
в «Госуправлении» 287 наборов — это ~600 запросов к порталу). Возвращает номера, а не
id: так короче и нельзя выдумать идентификатор. Код проверяет, что каждый номер попал
ровно в одну тему; при ошибке — повтор с перечнем ошибок, не больше двух.

    python3 topics.py finansy
"""

import hashlib
import json
import sqlite3
import sys
import time

import catalog
import portal
import providers

MODEL = "deepseek-chat"
MAX_RETRIES = 2

PROMPT = """Ты помогаешь построить дашборды по порталу открытых данных ХМАО — Югры.
Категория портала: «<<CATEGORY>>». В ней <<N>> наборов (номер | название | издатель):

<<ITEMS>>

Разбей ВСЕ наборы на 3–8 тем. Тема — это то, что человек выберет, чтобы получить
сводный дашборд: внутри темы должны быть наборы одного смысла от РАЗНЫХ издателей
(например «Исполнение бюджета МО: расходы»), чтобы их можно было сравнить между собой.
Не делай темы по издателю. Мелкие разнородные наборы собери в тему «Прочее».

Ответь JSON:
{"topics": [{"title": "короткое название темы", "description": "одно предложение: что
можно сравнить", "comparable": true или false — есть ли смысл сводить наборы темы
в одну таблицу по МО, "items": [номера наборов]}]}
Каждый номер от 0 до <<LAST>> — ровно в одной теме."""


def _db():
    db = sqlite3.connect(portal.CACHE)
    db.execute("CREATE TABLE IF NOT EXISTS topics (slug TEXT, fingerprint TEXT, payload TEXT, "
               "created_at REAL, PRIMARY KEY (slug, fingerprint))")
    return db


def validate(topics, count):
    """Ошибки разбиения: пропущенные, повторные и несуществующие номера, пустые темы."""
    errors, seen = [], {}
    if not isinstance(topics, list) or not 3 <= len(topics) <= 8:
        errors.append(f"тем должно быть от 3 до 8, а не {len(topics) if isinstance(topics, list) else '—'}")
        topics = topics if isinstance(topics, list) else []
    for t, topic in enumerate(topics):
        items = topic.get("items") if isinstance(topic, dict) else None
        if not isinstance(items, list) or not items:
            errors.append(f"тема {t + 1} без наборов")
            continue
        for item in items:
            if not isinstance(item, int) or not 0 <= item < count:
                errors.append(f"номера {item} нет")
            elif item in seen:
                errors.append(f"номер {item} в двух темах: {seen[item] + 1} и {t + 1}")
            else:
                seen[item] = t
    missing = [i for i in range(count) if i not in seen]
    if missing:
        errors.append(f"не распределены номера: {missing}")
    return errors


def build(slug, temperature=0.0, use_cache=True, save=True):
    data = catalog.category(slug)
    items = data["datasets"]
    fingerprint = hashlib.sha256("\n".join(sorted(i["id"] for i in items)).encode()).hexdigest()[:16]
    db = _db()
    if use_cache:
        row = db.execute("SELECT payload FROM topics WHERE slug = ? AND fingerprint = ?",
                         (slug, fingerprint)).fetchone()
        if row:
            db.close()
            return {**json.loads(row[0]), "cached": True}

    listing = "\n".join(f"{n} | {i['title'][:150]} | {i['mo']}" for n, i in enumerate(items))
    prompt = (PROMPT.replace("<<CATEGORY>>", data["title"]).replace("<<N>>", str(len(items)))
              .replace("<<ITEMS>>", listing).replace("<<LAST>>", str(len(items) - 1)))
    messages = [{"role": "user", "content": prompt}]
    stats = {"attempts": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0,
             "errors": []}
    started = time.monotonic()
    topics = []
    for attempt in range(MAX_RETRIES + 1):
        stats["attempts"] += 1
        reply = providers.call(MODEL, messages, temperature=temperature, max_tokens=4000,
                               response_format={"type": "json_object"})
        stats["prompt_tokens"] += reply["prompt_tokens"]
        stats["completion_tokens"] += reply["completion_tokens"]
        stats["cost"] = round(stats["cost"] + (reply["cost"] or 0), 6)
        try:
            topics = json.loads(reply["text"]).get("topics", [])
        except (json.JSONDecodeError, AttributeError):
            topics = []
        errors = validate(topics, len(items))
        stats["errors"].append(errors)
        if not errors:
            break
        messages += [{"role": "assistant", "content": reply["text"]},
                     {"role": "user", "content": "Ошибки разбиения: " + "; ".join(errors[:20])
                      + ". Исправь и пришли JSON целиком заново."}]
    stats["seconds"] = round(time.monotonic() - started, 1)
    stats["valid"] = not stats["errors"][-1]

    result = {"slug": slug, "category": data["title"], "count": len(items), "topics": []}
    for topic in topics if isinstance(topics, list) else []:
        members = [items[i] for i in topic.get("items", []) if isinstance(i, int) and 0 <= i < len(items)]
        result["topics"].append({
            "title": str(topic.get("title", "без названия"))[:80],
            "description": str(topic.get("description", ""))[:240],
            "comparable": bool(topic.get("comparable")),
            "datasets": members,
            "publishers": sorted({m["mo"] for m in members}),
        })
    result["stats"] = stats
    if stats["valid"] and save:
        db.execute("INSERT OR REPLACE INTO topics VALUES (?, ?, ?, ?)",
                   (slug, fingerprint, json.dumps(result, ensure_ascii=False), time.time()))
        db.commit()
    db.close()
    return {**result, "cached": False}


if __name__ == "__main__":
    slug = sys.argv[1] if len(sys.argv) > 1 else "finansy"
    out = build(slug, use_cache="--fresh" not in sys.argv)
    print(f"{out['category']}: {out['count']} наборов → {len(out['topics'])} тем "
          f"(кэш: {out['cached']}, {out.get('stats', {})})")
    for topic in out["topics"]:
        flag = "сводится" if topic["comparable"] else "не сводится"
        print(f"\n■ {topic['title']} — {len(topic['datasets'])} наборов, {len(topic['publishers'])} издателей, {flag}")
        print(f"  {topic['description']}")
        for d in topic["datasets"][:6]:
            print(f"    · {d['mo']}: {d['title'][:90]}")
