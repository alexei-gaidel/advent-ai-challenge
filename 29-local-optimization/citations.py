"""Ответ с обязательными источниками и цитатами + режим «не знаю».

Три рубежа против галлюцинаций, по порядку:

1. Гейт релевантности — до генерации. Если после порога косинуса не осталось кандидатов
   или лучшая оценка реранкера ниже GATE_MIN, модель не вызывается: агент говорит
   «не знаю» и предлагает уточнить вопрос, перечисляя ближайшие найденные статьи.
2. Генерация в JSON: answer + sources (chunk_id) + quotes (chunk_id + дословный фрагмент).
   Ссылку на kremlin.ru, главу и части статьи подставляет код по chunk_id — модели
   метаданные не доверяем.
3. Проверка цитат кодом: после нормализации цитата обязана дословно входить в текст
   того чанка, на который ссылается. Фальшивые выкидываются; если подтверждённых не
   осталось — один повтор с перечнем ошибок, затем «не знаю».
"""

import json
import re

import llm
GATE_MIN = 7            # лучшая оценка реранкера ниже — контекст слабый, «не знаю»
CLARIFY_OPTIONS = 3

# v1 — промпт дня 24 без изменений. В дне 28 qwen2.5:7b ответила по нему «Да, вы обязаны давать
# показания против жены», приложив цитату ст. 51 «Никто не обязан…». Модель пишет answer
# раньше quotes и подгоняет вывод до того, как «прочитала» цитату.
# v2 — порядок полей обратный: сначала цитаты, затем вывод, который обязан с ними совпадать;
# в примере нет настоящих id, которые малая модель копирует (день 28).
SYSTEMS = {
    "v1": """Ты — справочник по действующей Конституции Российской Федерации (kremlin.ru, с поправками 2020 года).
Отвечай только по фрагментам из сообщения пользователя, не по памяти.

Верни строго JSON:
{
  "answer": "ответ по-русски, 2–4 предложения",
  "sources": ["chunk_id", ...],
  "quotes": [{"chunk_id": "ст.81#1", "text": "дословный фрагмент"}, ...]
}

Правила:
- каждое утверждение ответа должно опираться на цитату из quotes;
- цитата — ДОСЛОВНАЯ копия 1–2 предложений или части предложения из фрагмента с этим chunk_id:
  ни одного изменённого слова, без пересказа и без склейки кусков из разных мест;
- chunk_id бери ровно из квадратных скобок перед фрагментом;
- sources — chunk_id всех фрагментов, на которые опирается ответ;
- если во фрагментах ответа нет, верни {"answer": "не знаю", "sources": [], "quotes": [],
  "clarify": "что нужно уточнить в вопросе"}.""",
    "v2": """Ты — справочник по действующей Конституции Российской Федерации (kremlin.ru, с поправками 2020 года).
Отвечай только по фрагментам из сообщения пользователя, не по памяти.

Действуй в два шага и верни строго JSON в этом порядке полей:
{
  "quotes": [{"chunk_id": "<id из квадратных скобок>", "text": "<дословный фрагмент>"}],
  "answer": "<вывод по-русски, 1–3 предложения>",
  "sources": ["<id>"]
}

Шаг 1 — quotes: выпиши ДОСЛОВНО 1–2 предложения из фрагментов, которые прямо отвечают на вопрос.
  Ни одного изменённого слова. chunk_id — ровно то, что стоит в квадратных скобках перед фрагментом.
Шаг 2 — answer: сформулируй вывод ТОЛЬКО из выписанных цитат.
  - Вывод не может противоречить цитате. Если цитата говорит «никто не обязан», «не может»,
    «запрещается» — ответ отрицательный.
  - Начинай с «Да» или «Нет», только если вопрос требует этого и цитата прямо это подтверждает.
  - Не добавляй фактов, которых нет в цитатах.
Если во фрагментах ответа нет — верни {"quotes": [], "answer": "не знаю", "sources": [],
  "clarify": "что нужно уточнить в вопросе"}.""",
}
SYSTEM = SYSTEMS["v1"]

USER = """Фрагменты Конституции:

<<CONTEXT>>

Вопрос: <<QUESTION>>"""

RETRY = """Проверка нашла ошибки в цитатах:
<<ERRORS>>
Цитаты должны быть дословными копиями текста фрагмента с указанным chunk_id. Исправь и верни JSON
в том же формате. Если дословно подтвердить ответ нельзя — верни "answer": "не знаю"."""


def normalize(text):
    """Сравниваем без учёта регистра, ё/е, вида кавычек и тире, переносов и пробелов."""
    text = text.lower().replace("ё", "е").replace("\xa0", " ")
    text = re.sub(r"[«»“”„\"']", "", text)
    text = re.sub(r"[‐‑‒–—]", "-", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .;:,")


def resolve_id(chunk_id, chunks):
    """qwen2.5 копирует формат примера «ст.81#1» и пишет «ст.51#1» или «ст.75#5» (номер части),
    хотя статья — один чанк «ст.51». Несуществующий id сводим к статье без суффикса;
    дословность цитаты всё равно проверяется внутри этого чанка."""
    chunk_id = str(chunk_id or "").strip().strip("[]")
    if chunk_id in chunks:
        return chunk_id
    # Q3_K_M ещё и теряет префикс: «81#1» вместо «ст.81#1».
    for candidate in (chunk_id, "ст." + chunk_id.removeprefix("ст.")):
        if candidate in chunks:
            return candidate
        bare = candidate.split("#")[0]
        if bare in chunks:
            return bare
    return chunk_id


def verify_quote(quote, chunks):
    """→ (статус, где нашлась). verified | wrong_chunk | not_found | empty.

    Многоточие внутри цитаты разрешено: каждая часть должна найтись по порядку.
    """
    text = normalize(quote.get("text") or "")
    if len(text) < 12:
        return "empty", None
    parts = [p.strip(" .;:,") for p in re.split(r"\.\.\.|…", text) if p.strip(" .;:,")]

    def inside(chunk_text):
        position, haystack = 0, normalize(chunk_text)
        for part in parts:
            position = haystack.find(part, position)
            if position < 0:
                return False
            position += len(part)
        return True

    chunk_id = resolve_id(quote.get("chunk_id"), chunks)
    own = chunks.get(chunk_id)
    if own and inside(own["text"]):
        return "verified", chunk_id
    for chunk_id, chunk in chunks.items():
        if inside(chunk["text"]):
            return "wrong_chunk", chunk_id
    return "not_found", None


def source_info(chunk, url):
    """Метаданные источника — из индекса, а не из ответа модели."""
    article = chunk["article"]
    anchor = "" if article == "ЗП" else f"#article-{article.replace('.', '-')}"
    return {"chunk_id": chunk["chunk_id"], "source": url + anchor,
            "section": " › ".join(x for x in (chunk["chapter"], chunk["text"].split("\n")[0]) if x),
            "parts": chunk["parts"]}


def gate(hits, trace, gate_min=GATE_MIN):
    """Слабый контекст? → (причина, варианты уточнения) или (None, None)."""
    if hits and trace.get("rerank_failed"):
        return None, None           # реранкер не ответил — не наказываем вопрос, отвечаем по косинусу
    best = max((h.get("rerank") or 0 for h in hits), default=0)
    if hits and best >= gate_min:
        return None, None
    candidates = sorted(trace["candidates"],
                        key=lambda c: (c.get("rerank") or 0, c["score"]), reverse=True)
    options, seen = [], set()
    for c in candidates:
        if c["score"] < trace["params"]["threshold"] or c["article"] in seen:
            continue
        seen.add(c["article"])
        first = c["text"].split("\n")[1] if "\n" in c["text"] else c["text"]
        options.append({"chunk_id": c["chunk_id"], "article": c["article"],
                        "hint": re.sub(r"^\S+\.\s", "", first)[:110]})
        if len(options) == CLARIFY_OPTIONS:
            break
    if not hits and not options:
        reason = "ни один фрагмент не прошёл порог релевантности — вопрос, похоже, не про Конституцию"
    elif not hits:
        reason = "кандидаты нашлись, но реранкер не счёл ни один относящимся к вопросу"
    else:
        reason = f"лучший фрагмент оценён реранкером на {best}/10 — меньше {gate_min}"
    return reason, options


def unknown(reason, options, stage):
    if options:
        listed = "; ".join(f"ст. {o['article']} — {o['hint']}" for o in options)
        clarify = f"Не знаю: {reason}. Уточните, пожалуйста, вопрос. Возможно, вас интересует: {listed}."
    else:
        clarify = f"Не знаю: {reason}. Уточните, пожалуйста, что именно вы хотите узнать о Конституции."
    return {"unknown": True, "stage": stage, "reason": reason, "answer": clarify,
            "clarify": options, "sources": [], "quotes": [], "rejected": []}


def context_block(hits):
    return "\n\n---\n\n".join(f"[{h['chunk_id']}] {h['chapter'] or 'Заключительные положения'}\n{h['text']}"
                              for h in hits)


def _parse(text):
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        try:
            data = json.loads(match.group(0)) if match else {}
        except json.JSONDecodeError:
            data = {}
    return data if isinstance(data, dict) else {}


def _check(data, chunks):
    """Разбор ответа модели: подтверждённые цитаты, отбракованные, ошибки для повтора."""
    verified, rejected, errors = [], [], []
    for quote in data.get("quotes") or []:
        if not isinstance(quote, dict):
            continue
        status, where = verify_quote(quote, chunks)
        item = {"chunk_id": quote.get("chunk_id"), "text": quote.get("text", ""), "status": status}
        if status == "verified":
            verified.append(item)
        else:
            rejected.append({**item, "found_in": where})
            if status == "wrong_chunk":
                errors.append(f"- цитата «{item['text'][:80]}» есть, но во фрагменте {where}, "
                              f"а не {item['chunk_id']}")
            else:
                errors.append(f"- цитаты «{item['text'][:80]}» нет дословно во фрагменте {item['chunk_id']}")
    if not verified and not errors:
        errors.append("- в ответе нет ни одной цитаты")
    return verified, rejected, errors


def answer(question, hits, trace, url, preset, gate_min=GATE_MIN):
    """Главная функция дня: гейт → JSON-ответ → проверка цитат → повтор → результат."""
    reason, options = gate(hits, trace, gate_min)
    if reason:
        return unknown(reason, options, "gate"), []

    chunks = {h["chunk_id"]: h for h in hits}
    messages = [{"role": "system", "content": SYSTEMS[preset["prompt"]]},
                {"role": "user", "content": USER.replace("<<CONTEXT>>", context_block(hits))
                                                 .replace("<<QUESTION>>", question)}]
    usages, rejected_all = [], []
    for attempt in range(2):
        reply = llm.call(preset, messages, "answer", json_mode=True)
        usages.append(reply)
        data = _parse(reply["text"])
        reply["json_ok"] = bool(data)
        text = str(data.get("answer") or "").strip()
        if normalize(text).startswith("не знаю"):
            result = unknown("модель сама не нашла ответа во фрагментах", options_from(hits), "model")
            if data.get("clarify"):
                result["answer"] += f" Модель просит уточнить: {data['clarify']}"
            result["rejected"] = rejected_all
            return result, usages
        verified, rejected, errors = _check(data, chunks)
        rejected_all += [{**r, "attempt": attempt + 1} for r in rejected]
        if verified:
            used = [resolve_id(s, chunks) for s in (data.get("sources") or [])]
            used = list(dict.fromkeys(s for s in used if s in chunks))
            for q in verified:
                q["chunk_id"] = resolve_id(q["chunk_id"], chunks)
                if q["chunk_id"] not in used:
                    used.append(q["chunk_id"])
            return {"unknown": False, "stage": "answer", "answer": text, "attempts": attempt + 1,
                    "sources": [source_info(chunks[s], url) for s in used],
                    "foreign_sources": [s for s in (data.get("sources") or [])
                                        if resolve_id(s, chunks) not in chunks],
                    "quotes": verified, "rejected": rejected_all, "clarify": []}, usages
        messages += [{"role": "assistant", "content": reply["text"]},
                     {"role": "user", "content": RETRY.replace("<<ERRORS>>", "\n".join(errors))}]
    result = unknown("модель не смогла подтвердить ответ дословными цитатами", options_from(hits),
                     "verify")
    result["rejected"] = rejected_all
    return result, usages


def options_from(hits):
    return [{"chunk_id": h["chunk_id"], "article": h["article"],
             "hint": re.sub(r"^\S+\.\s", "", (h["text"].split("\n") + [""])[1])[:110]}
            for h in hits[:CLARIFY_OPTIONS]]
