"""Сравнение двух стратегий chunking на живом индексе.

Три замера:
1. нарезка — сколько чанков, какого размера, сколько разрезали таблицу или код;
2. поиск — 22 вопроса, написанных руками по фактам README: нашёлся ли эталон
   (нужный файл + подстрока-факт) в топ-1 / топ-3, MRR; повторяется 3 раза;
3. DeepSeek-судья — отвечает ли фрагмент на вопрос. Фрагменты обеих стратегий
   обезличены и перемешаны, судья не знает, какая стратегия что нашла.

«Эталон найден» — не «ответ верный»: подстрока может найтись в чанке случайно или
оказаться разрезанной окном. Поэтому метку дублирует судья.

    python3 compare_chunking.py            # нужен готовый index.db
"""

import json
import pathlib
import random
import re
import sys
import time

import chunking
import corpus
import embeddings
import index_store
import providers

K = 3
REPEATS = 3
JUDGE = "deepseek-chat"
RESULTS = pathlib.Path(__file__).with_name("data") / "compare_result.json"

# (вопрос, допустимые файлы, подстрока-факт). Вопросы — перефразы, а не цитаты.
QUESTIONS = [
    ("Окупилось ли сворачивание старых сообщений в сводку на коротком разговоре?",
     ["09-context-compression", "CLAUDE.md"], "312"),
    ("Насколько компактная запись данных экономит токены по сравнению с JSON?",
     ["06-agent", "CLAUDE.md"], "48%"),
    ("У какой стратегии памяти обслуживание обошлось дороже самого диалога?",
     ["10-context-strategies"], "7 851"),
    ("Правда ли, что модель побольше стоит дороже модели поменьше?",
     ["05-model-versions", "CLAUDE.md"], "в пять раз дешевле"),
    ("Почему провайдер Groq отказывал в доступе нашему скрипту?",
     ["CLAUDE.md", "05-model-versions", "19-mcp-pipeline"], "User-Agent"),
    ("Почему цена одной из криптовалют в сводке застыла и была неправильной?",
     ["18-scheduled-mcp"], "делистил"),
    ("Какой заголовок нужно передать, чтобы удалённый MCP-сервер перестал возвращать 406?",
     ["16-mcp-client"], "text/event-stream"),
    ("Сколько токенов съел классификатор, раскладывающий реплики по слоям памяти?",
     ["11-memory-model"], "4295"),
    ("Сколько нарушений правил поймал аудитор с блоком ограничений и без него?",
     ["14-invariants"], "15 против 0"),
    ("Во сколько обходится ответ агента, который ходит в историю git, по сравнению с ответом без неё?",
     ["17-mcp-tool"], "9 206"),
    ("По какой стоимости билеты формально были в продаже всё время?",
     ["19-mcp-pipeline"], "15 000"),
    ("Какую ошибку вернул провайдер, когда модель придумала несуществующий инструмент?",
     ["20-mcp-orchestration"], "Tool call validation failed"),
    ("Какой режим журнала базы включён, чтобы чтение не ждало записи?",
     ["07-persistent-context"], "WAL"),
    ("Насколько похожими остались повторные ответы при самой высокой температуре?",
     ["04-temperature"], "39%"),
    ("Сколько места в базе занимает формализованное состояние задачи?",
     ["13-task-state"], "431 символ"),
    ("Что должно происходить с подтверждениями, если задачу отправили на доработку?",
     ["15-lifecycle"], "снимать утверждения"),
    ("Почему подстановка текста в шаблон промпта падала с KeyError?",
     ["CLAUDE.md"], "фигурные скобки"),
    ("На каком порту поднимать сервер нового дня челленджа?",
     ["CLAUDE.md"], "8000 + номер дня"),
    ("Во сколько раз расходились оценки одной модели при разных способах задать вопрос?",
     ["03-prompting-strategies"], "в семь раз"),
    ("Сколько токенов занял ответ без ограничений формата против ответа с ними?",
     ["02-response-format"], "1464"),
    ("На сколько удлиняется каждый запрос из-за профиля пользователя?",
     ["12-personalization"], "448–516"),
    ("Почему самописный stdio-сервер ломается, если в нём есть отладочная печать?",
     ["16-mcp-client"], "stderr"),
]

JUDGE_PROMPT = """Ты проверяешь поиск по документации. Дан вопрос и несколько найденных фрагментов.
Для каждого фрагмента ответь, содержит ли он ответ на вопрос:
  yes — ответ есть в фрагменте целиком;
  partial — есть часть ответа или тема та же, но нужного факта нет;
  no — фрагмент не про это.
Верни только JSON вида {"A": "yes", "B": "no", ...} без пояснений.

Вопрос: <<Q>>

<<FRAGMENTS>>"""


def hit(row, sources, fact):
    return any(row["source"].startswith(s) for s in sources) and fact in row["text"]


def retrieval(indexes, vectors):
    """Один проход поиска: для каждой стратегии — топ-K и ранг эталона."""
    out = {}
    for name, index in indexes.items():
        rows = []
        for (question, sources, fact), vector in zip(QUESTIONS, vectors):
            top = index.search(vector, 10)
            rank = next((i + 1 for i, r in enumerate(top) if hit(r, sources, fact)), None)
            rows.append({"question": question, "rank": rank,
                         "top": [{"chunk_id": r["chunk_id"], "score": r["score"],
                                  "section": r["section"], "n_chars": r["n_chars"],
                                  "text": r["text"]} for r in top[:K]]})
        out[name] = rows
    return out


def summarize(rows):
    n = len(rows)
    return {
        "top1": sum(r["rank"] == 1 for r in rows),
        "top3": sum(r["rank"] is not None and r["rank"] <= K for r in rows),
        "top10": sum(r["rank"] is not None for r in rows),
        "mrr": round(sum(1 / r["rank"] for r in rows if r["rank"]) / n, 3),
        "context_chars": round(sum(sum(t["n_chars"] for t in r["top"]) for r in rows) / n),
    }


def judge(question, fragments, seed):
    """fragments: [(strategy, chunk_id, text)] → {(strategy, chunk_id): verdict}, usage."""
    shuffled = fragments[:]
    random.Random(seed).shuffle(shuffled)
    letters = "ABCDEFGHIJ"
    block = "\n\n".join(f"[{letters[i]}]\n{text}" for i, (_, _, text) in enumerate(shuffled))
    prompt = JUDGE_PROMPT.replace("<<Q>>", question).replace("<<FRAGMENTS>>", block)
    reply = providers.call(JUDGE, [{"role": "user", "content": prompt}],
                           temperature=0, max_tokens=200)
    match = re.search(r"\{.*\}", reply["text"], flags=re.S)
    verdicts = json.loads(match.group(0)) if match else {}
    result = {}
    for i, (strategy, chunk_id, _) in enumerate(shuffled):
        result[(strategy, chunk_id)] = str(verdicts.get(letters[i], "?")).lower()
    return result, reply


def main():
    embeddings.check()
    db = index_store.connect()
    documents = corpus.load()
    names = list(chunking.STRATEGIES)
    indexes = {name: index_store.Index(db, name) for name in names}
    builds = {b["strategy"]: b for b in index_store.builds(db)}

    print("=== 1. Нарезка")
    chunk_stats = {}
    for name in names:
        stats = json.loads(builds[name]["stats"])
        chunk_stats[name] = stats
        print(f"  {name:10} чанков {stats['chunks']:>4}  средний {stats['avg']:>5}  "
              f"медиана {stats['median']:>5}  мин {stats['min']:>4}  макс {stats['max']:>5}  "
              f"разрезано таблиц/кода {stats['broken']:>3} ({stats['broken_pct']}%)  "
              f"эмбеддинг {stats['embed_seconds']} с")

    print(f"\n=== 2. Поиск: {len(QUESTIONS)} вопросов × {REPEATS} прохода")
    passes, query_vectors = [], []
    for attempt in range(REPEATS):
        started = time.monotonic()
        vectors = embeddings.embed([q for q, _, _ in QUESTIONS])
        seconds = round(time.monotonic() - started, 2)
        query_vectors.append(vectors)
        passes.append(retrieval(indexes, vectors))
        line = "  ".join(f"{n}: топ1 {summarize(passes[-1][n])['top1']} "
                         f"топ3 {summarize(passes[-1][n])['top3']}" for n in names)
        print(f"  проход {attempt + 1}: {line}  (эмбеддинг вопросов {seconds} с)")

    drift = max(abs(a - b) for v1, v2 in zip(query_vectors[0], query_vectors[-1])
                for a, b in zip(v1, v2))
    same_top = all(
        [t["chunk_id"] for t in p[n][i]["top"]] == [t["chunk_id"] for t in passes[0][n][i]["top"]]
        for p in passes for n in names for i in range(len(QUESTIONS)))
    print(f"  расхождение векторов между проходами: {drift:.2e}, топы совпали: {same_top}")

    retrieval_summary = {n: summarize(passes[0][n]) for n in names}
    print()
    print(f"  {'':10} {'топ-1':>6} {'топ-3':>6} {'топ-10':>7} {'MRR':>6} {'симв. в топ-3':>14}")
    for n in names:
        s = retrieval_summary[n]
        print(f"  {n:10} {s['top1']:>6} {s['top3']:>6} {s['top10']:>7} {s['mrr']:>6} "
              f"{s['context_chars']:>14}")

    print("\n  по вопросам (ранг эталона, — = нет в топ-10):")
    for i, (question, _, fact) in enumerate(QUESTIONS):
        ranks = "  ".join(f"{n[:6]} {passes[0][n][i]['rank'] or '—':>2}" for n in names)
        print(f"    {ranks}   «{fact}»  {question[:60]}")

    print(f"\n=== 3. DeepSeek-судья: топ-{K} обеих стратегий вперемешку, {REPEATS} прохода")
    verdict_runs, tokens, cost = [], 0, 0.0
    for attempt in range(REPEATS):
        counts = {n: {"yes": 0, "partial": 0, "no": 0, "?": 0} for n in names}
        answered = {n: 0 for n in names}
        for i, (question, _, _) in enumerate(QUESTIONS):
            fragments = [(n, t["chunk_id"], t["text"]) for n in names
                         for t in passes[0][n][i]["top"]]
            verdicts, reply = judge(question, fragments, seed=attempt * 100 + i)
            tokens += reply.get("prompt_tokens", 0) + reply.get("completion_tokens", 0)
            cost += reply.get("cost") or 0
            for n in names:
                mine = [v for (s, _), v in verdicts.items() if s == n]
                for v in mine:
                    counts[n][v if v in counts[n] else "?"] += 1
                answered[n] += any(v == "yes" for v in mine)
        verdict_runs.append({"counts": counts, "answered": answered})
        line = "  ".join(f"{n}: yes {counts[n]['yes']} partial {counts[n]['partial']} "
                         f"no {counts[n]['no']}, вопросов с ответом {answered[n]}" for n in names)
        print(f"  проход {attempt + 1}: {line}")
    print(f"  судья потратил {tokens} токенов, ${cost:.4f}")

    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps({
        "corpus": {"documents": len(documents),
                   "chars": sum(len(d["text"]) for d in documents)},
        "model": builds[names[0]]["model"],
        "chunking": chunk_stats,
        "retrieval": retrieval_summary,
        "stable": {"vector_drift": drift, "same_top": same_top},
        "per_question": [{"question": q, "fact": f,
                          **{n: passes[0][n][i]["rank"] for n in names}}
                         for i, (q, _, f) in enumerate(QUESTIONS)],
        "judge": {"model": JUDGE, "runs": verdict_runs, "tokens": tokens,
                  "cost": round(cost, 5)},
        "examples": {n: passes[0][n][:3] for n in names},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {RESULTS.relative_to(pathlib.Path(__file__).parent)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        print(f"ошибка: {error}", file=sys.stderr)
        sys.exit(1)
