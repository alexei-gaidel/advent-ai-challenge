"""Отбор чанков дня 23–28: топ-20 → порог косинуса → LLM-реранкер → топ-5.

День 29: реранкеру можно отдать не всех кандидатов, а топ-`rerank_k` по косинусу, и обрезать
текст фрагмента до `frag_chars` — только в промпте реранка, генератор получает чанк целиком.
Так промпт реранка помещается в num_ctx 4096 вместо 8192.
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


def _stems(text):
    """Грубая основа слова для русского: первые 5 букв, слова короче 4 букв не считаем."""
    return {w[:5] for w in re.findall(r"[а-яёa-z]+", text.lower().replace("ё", "е")) if len(w) >= 4}


def _clip(text, limit, question=""):
    """Сжать фрагмент до limit символов: абзацы с наибольшим пересечением с вопросом,
    в исходном порядке; первая строка («Статья N») остаётся. 0 — не резать.

    Первая версия резала просто первые 600 символов и теряла ответ в глубине статьи:
    у ст. 75 про МРОТ говорит ч. 5 — реранкер её не видел.
    """
    if not limit or len(text) <= limit:
        return text
    lines = text.split("\n")
    head, paragraphs = lines[0], [x for x in lines[1:] if x.strip()]
    query = _stems(question)
    ranked = sorted(range(len(paragraphs)),
                    key=lambda i: (-len(_stems(paragraphs[i]) & query), i))
    chosen, used = set(), len(head)
    for i in ranked:
        if used + len(paragraphs[i]) + 1 > limit and chosen:
            continue
        chosen.add(i)
        used += len(paragraphs[i]) + 1
    body = "\n".join(paragraphs[i] if len(paragraphs[i]) <= limit else paragraphs[i][:limit] + " …"
                     for i in sorted(chosen))
    return f"{head}\n{body}"


def rerank(question, candidates, preset, prompt="v2", frag_chars=0):
    clip = lambda text: _clip(text, frag_chars, question)
    """Один запрос на всех кандидатов: {chunk_id: оценка 0–10}. Не разобралось — None."""
    if prompt == "v1":
        blocks = "\n\n".join(f"[{c['chunk_id']}]\n{clip(c['text'])}" for c in candidates)
    else:
        blocks = "\n\n".join(f"Фрагмент {i} [{c['chunk_id']}]\n{clip(c['text'])}"
                              for i, c in enumerate(candidates, 1))
    text = (RERANK_PROMPTS[prompt].replace("<<Q>>", question).replace("<<FRAGMENTS>>", blocks)
            .replace("<<N>>", str(len(candidates))))
    reply = llm.call(preset, [{"role": "user", "content": text}], "rerank",
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


def retrieve(index, question, preset, threshold=None, k_before=None, k_after=None, rerank_min=None,
             prompt=None):
    """Возвращает (итоговые чанки, trace). trace: кандидаты со статусами и оценками, расход."""
    p = dict(DEFAULTS)
    p.update({k: v for k, v in dict(threshold=threshold, k_before=k_before, k_after=k_after,
                                     rerank_min=rerank_min, prompt=prompt).items() if v is not None})
    trace = {"params": p, "stages": {}, "rerank_failed": False, "rerank_model": preset["model"]}

    embed_options = {"num_gpu": 0} if preset.get("embed_cpu") else None
    hits, embed_seconds = index.search(question, p["k_before"], embed_options)
    trace["embed_seconds"] = embed_seconds
    candidates = [{**h, "status": "kept", "rerank": None} for h in hits]
    for c in candidates:
        if c["score"] < p["threshold"]:
            c["status"] = "below_threshold"
    alive = [c for c in candidates if c["status"] == "kept"]

    # Реранкеру — только топ-rerank_k по косинусу; остальные считаются отсечёнными по k.
    k = preset.get("rerank_k") or len(alive)
    for c in alive[k:]:
        c["status"] = "cut_rerank_k"
    alive = alive[:k]

    if alive:
        scores, usage = rerank(question, alive, preset, p["prompt"], preset.get("frag_chars", 0))
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
