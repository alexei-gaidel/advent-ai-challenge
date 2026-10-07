"""Отбор чанков дня 23–24: топ-20 → порог косинуса → LLM-реранкер → топ-5.

В дне 28 реранкер — параметр: локальная qwen2.5 через Ollama или облачный deepseek-chat.
Поиск по векторам и так локальный (bge-m3 в Ollama + SQLite).
"""

import json
import re

import llm

# Порог — калибровка дня 23: вопросы вне базы набирают до 0.427, слабейшая нужная статья 0.469.
DEFAULTS = {"threshold": 0.45, "k_before": 20, "k_after": 5, "rerank_min": 5, "prompt": "v2"}

# v1 — промпт дня 23–24. qwen2.5 3b и 7b копируют из него пример целиком: на вопрос про ст.51
# и ст.49 приходит {"ст.81#1": 0, "ст.5": 10, "ст.49": 0}, нужная статья теряется.
# v2 — фрагменты пронумерованы, в примере нет ни одного настоящего id, ключи — номера 1..N.
RERANK_PROMPTS = {
    "v1": """Ты оцениваешь фрагменты Конституции РФ для ответа на вопрос.
Для каждого фрагмента поставь оценку 0–10: насколько он нужен, чтобы ответить на вопрос.
  10 — содержит прямой ответ; 5 — относится к теме и помогает; 0 — не про это.
Верни только JSON вида {"ст.81#1": 9, "ст.5": 0, ...} — по всем id, без пояснений.

Вопрос: <<Q>>

<<FRAGMENTS>>""",
    "v2": """Ты оцениваешь фрагменты Конституции РФ для ответа на вопрос.
Для каждого фрагмента поставь оценку 0–10: насколько он нужен, чтобы ответить на вопрос.
  10 — содержит прямой ответ; 5 — относится к теме и помогает; 0 — не про это.

Вопрос: <<Q>>

<<FRAGMENTS>>

Вопрос ещё раз: <<Q>>
Верни только JSON, где ключ — номер фрагмента, значение — оценка: {"1": оценка, "2": оценка, ...}.
Ровно <<N>> ключей, от "1" до "<<N>>", без пояснений.""",
}


def _usage(reply):
    return {key: reply[key] for key in ("prompt_tokens", "completion_tokens", "cost",
                                        "seconds", "load_seconds", "local")}


def _keys_v1(raw, candidates):
    """Ключи — chunk_id. Малые модели пишут «ст.5#1» вместо «ст.5» или просто «5» — сводим."""
    loose = {}
    for key, value in raw.items():
        bare = str(key).strip().strip("[]").replace(" ", "")
        for variant in (bare, re.sub(r"#1$", "", bare), "ст." + bare.removeprefix("ст.")):
            loose.setdefault(variant, value)
    return {c["chunk_id"]: raw.get(c["chunk_id"], loose.get(c["chunk_id"])) for c in candidates}


def _keys_v2(raw, candidates):
    """Ключи — номера фрагментов 1..N."""
    return {c["chunk_id"]: raw.get(str(i)) for i, c in enumerate(candidates, 1)}


def rerank(question, candidates, model, prompt="v2"):
    """Один запрос на всех кандидатов: {chunk_id: оценка 0–10}. Не разобралось — None."""
    if prompt == "v1":
        blocks = "\n\n".join(f"[{c['chunk_id']}]\n{c['text']}" for c in candidates)
    else:
        blocks = "\n\n".join(f"Фрагмент {i} [{c['chunk_id']}]\n{c['text']}"
                              for i, c in enumerate(candidates, 1))
    text = (RERANK_PROMPTS[prompt].replace("<<Q>>", question).replace("<<FRAGMENTS>>", blocks)
            .replace("<<N>>", str(len(candidates))))
    reply = llm.call(model, [{"role": "user", "content": text}], temperature=0,
                     max_tokens=60 + 12 * len(candidates), json_mode=True)
    match = re.search(r"\{.*\}", reply["text"], flags=re.S)
    try:
        raw = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    found = (_keys_v1 if prompt == "v1" else _keys_v2)(raw, candidates)
    scores = {cid: value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
              for cid, value in found.items()}
    return scores, {**_usage(reply), "raw": reply["text"][:400]}


def retrieve(index, question, model, threshold=None, k_before=None, k_after=None, rerank_min=None,
             prompt=None):
    """Возвращает (итоговые чанки, trace). trace: кандидаты со статусами и оценками, расход."""
    p = dict(DEFAULTS)
    p.update({k: v for k, v in dict(threshold=threshold, k_before=k_before, k_after=k_after,
                                     rerank_min=rerank_min, prompt=prompt).items() if v is not None})
    trace = {"params": p, "stages": {}, "rerank_failed": False, "rerank_model": model}

    hits, embed_seconds = index.search(question, p["k_before"])
    trace["embed_seconds"] = embed_seconds
    candidates = [{**h, "status": "kept", "rerank": None} for h in hits]
    for c in candidates:
        if c["score"] < p["threshold"]:
            c["status"] = "below_threshold"
    alive = [c for c in candidates if c["status"] == "kept"]

    if alive:
        scores, usage = rerank(question, alive, model, p["prompt"])
        trace["stages"]["rerank"] = usage
        failed = all(s is None for s in scores.values())
        trace["rerank_failed"] = failed
        trace["rerank_missing"] = sum(s is None for s in scores.values())
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
