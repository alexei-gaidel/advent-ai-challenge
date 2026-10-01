"""Второй этап поиска: query rewrite → широкий поиск → порог → LLM-реранкер → топ-K.

Каждая стадия пишет в trace, что она получила и что отбросила: по нему web.py рисует
таблицу кандидатов, а rerank_demo.py считает, на какой стадии теряется нужная статья.

Режимы — абляция по стадиям, чтобы был виден вклад каждой:
    base            топ-K_AFTER по косинусу (как в дне 22)
    filter          топ-K_BEFORE → порог косинуса → топ-K_AFTER
    rerank          топ-K_BEFORE → порог → реранкер deepseek-chat → топ-K_AFTER
    rewrite+rerank  то же, но искать по переписанному запросу
"""

import json
import re

import providers

MODEL = "deepseek-chat"
MODES = ("base", "filter", "rerank", "rewrite+rerank")
MODE_LABELS = {"base": "базовый", "filter": "порог", "rerank": "порог + реранкер",
               "rewrite+rerank": "rewrite + порог + реранкер"}

# Порог подобран по распределению в rerank_demo.py (калибровка): вопросы вне базы набирают
# максимум 0.427, самая слабая правильная статья — 0.469. 0.45 — середина зазора.
DEFAULTS = {"threshold": 0.45, "k_before": 20, "k_after": 5, "rerank_min": 5}

REWRITE_PROMPT = """Перепиши вопрос пользователя в поисковый запрос по тексту Конституции Российской Федерации.
Используй юридические формулировки, которыми такая норма записана в Конституции, и добавь
ключевые термины. На вопрос НЕ отвечай: никаких чисел, сроков и выводов, которых нет в вопросе.
Верни только запрос, одной строкой, до 25 слов.

Вопрос: <<Q>>"""

RERANK_PROMPT = """Ты оцениваешь фрагменты Конституции РФ для ответа на вопрос.
Для каждого фрагмента поставь оценку 0–10: насколько он нужен, чтобы ответить на вопрос.
  10 — содержит прямой ответ; 5 — относится к теме и помогает; 0 — не про это.
Верни только JSON вида {"ст.81#1": 9, "ст.5": 0, ...} — по всем id, без пояснений.

Вопрос: <<Q>>

<<FRAGMENTS>>"""


def _usage(reply):
    return {"prompt_tokens": reply["prompt_tokens"],
            "completion_tokens": reply["completion_tokens"],
            "cost": reply["cost"] or 0, "seconds": reply["seconds"]}


def rewrite(question):
    """Бытовой вопрос → запрос языком Конституции. Ответ модели не должен попадать в запрос."""
    reply = providers.call(MODEL, [{"role": "user",
                                    "content": REWRITE_PROMPT.replace("<<Q>>", question)}],
                           temperature=0, max_tokens=120)
    query = " ".join(reply["text"].split()).strip("«»\"'")
    return query or question, _usage(reply)


def rerank(question, candidates):
    """Один запрос на всех кандидатов: {chunk_id: оценка 0–10}. Не разобралось — None."""
    blocks = "\n\n".join(f"[{c['chunk_id']}]\n{c['text']}" for c in candidates)
    prompt = RERANK_PROMPT.replace("<<Q>>", question).replace("<<FRAGMENTS>>", blocks)
    reply = providers.call(MODEL, [{"role": "user", "content": prompt}],
                           temperature=0, max_tokens=60 + 12 * len(candidates))
    match = re.search(r"\{.*\}", reply["text"], flags=re.S)
    try:
        raw = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        raw = {}
    scores = {}
    for c in candidates:
        value = raw.get(c["chunk_id"])
        scores[c["chunk_id"]] = value if isinstance(value, (int, float)) else None
    return scores, _usage(reply)


def retrieve(index, question, mode="rerank", threshold=None, k_before=None, k_after=None,
             rerank_min=None):
    """Возвращает (итоговые чанки, trace). trace: query, candidates со статусами, расход."""
    if mode not in MODES:
        raise ValueError(f"неизвестный режим {mode!r}, есть {MODES}")
    p = dict(DEFAULTS)
    p.update({k: v for k, v in dict(threshold=threshold, k_before=k_before, k_after=k_after,
                                     rerank_min=rerank_min).items() if v is not None})
    trace = {"mode": mode, "params": p, "query": question, "stages": {}}

    query = question
    if mode == "rewrite+rerank":
        query, usage = rewrite(question)
        trace["query"] = query
        trace["stages"]["rewrite"] = usage

    wide = p["k_after"] if mode == "base" else p["k_before"]
    hits, embed_seconds = index.search(query, wide)
    trace["embed_seconds"] = embed_seconds
    candidates = [{**h, "status": "kept", "rerank": None} for h in hits]

    if mode != "base":
        for c in candidates:
            if c["score"] < p["threshold"]:
                c["status"] = "below_threshold"
    alive = [c for c in candidates if c["status"] == "kept"]

    if mode in ("rerank", "rewrite+rerank") and alive:
        # Реранкер судит по исходному вопросу: rewrite помогает найти, а не решать.
        scores, usage = rerank(question, alive)
        trace["stages"]["rerank"] = usage
        failed = all(s is None for s in scores.values())
        trace["rerank_failed"] = failed
        for c in alive:
            c["rerank"] = scores[c["chunk_id"]]
            if not failed and (c["rerank"] is None or c["rerank"] < p["rerank_min"]):
                c["status"] = "rerank_low"
        if not failed:
            alive = sorted((c for c in alive if c["status"] == "kept"),
                           key=lambda c: (c["rerank"], c["score"]), reverse=True)

    final = alive[:p["k_after"]]
    for c in alive[p["k_after"]:]:
        c["status"] = "cut_k"
    trace["candidates"] = [{key: c[key] for key in ("chunk_id", "article", "score", "rerank",
                                                     "status", "amended", "parts")}
                           for c in candidates]
    trace["final"] = [c["chunk_id"] for c in final]
    return final, trace
