"""Отбор чанков из дня 23 без изменений по сути: топ-20 → порог косинуса → LLM-реранкер → топ-5.

Query rewrite и промежуточные режимы дня 23 выброшены: в дне 22–23 они уже измерены,
а здесь важно другое — что делать, когда отобранного мало. Для этого retrieve()
возвращает и отсечённых кандидатов: по ним citations.py строит просьбу уточнить.
"""

import json
import re

import providers

MODEL = "deepseek-chat"

# Порог — калибровка дня 23: вопросы вне базы набирают до 0.427, слабейшая нужная статья 0.469.
DEFAULTS = {"threshold": 0.45, "k_before": 20, "k_after": 5, "rerank_min": 5}

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


def retrieve(index, question, threshold=None, k_before=None, k_after=None, rerank_min=None):
    """Возвращает (итоговые чанки, trace). trace: кандидаты со статусами и оценками, расход."""
    p = dict(DEFAULTS)
    p.update({k: v for k, v in dict(threshold=threshold, k_before=k_before, k_after=k_after,
                                     rerank_min=rerank_min).items() if v is not None})
    trace = {"params": p, "stages": {}, "rerank_failed": False}

    hits, embed_seconds = index.search(question, p["k_before"])
    trace["embed_seconds"] = embed_seconds
    candidates = [{**h, "status": "kept", "rerank": None} for h in hits]
    for c in candidates:
        if c["score"] < p["threshold"]:
            c["status"] = "below_threshold"
    alive = [c for c in candidates if c["status"] == "kept"]

    if alive:
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
    trace["candidates"] = candidates           # с текстом: нужен для уточнения и проверки
    trace["final"] = [c["chunk_id"] for c in final]
    return final, trace
