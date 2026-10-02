"""Память задачи: цель диалога, что пользователь уточнил, ограничения и термины.

На каждом ходу ДО поиска один вызов deepseek-chat (JSON) делает две вещи:
1. обновляет состояние по новой реплике;
2. формулирует самостоятельный поисковый запрос: «а у сенатора?» сам по себе в базе
   ничего не найдёт, а «требования к кандидату в сенаторы РФ» — найдёт.

Режим `history` (абляция) делает тот же вызов без состояния: запрос строится только по
окну последних сообщений, память не ведётся. Так разница между режимами — ровно память.

Поля из state["locked"] пользователь зафиксировал вручную — модель их не меняет.
"""

import json
import re

import providers

MODEL = "deepseek-chat"
FIELDS = ("goal", "clarified", "constraints", "terms")
MAX_ITEMS = 8


def empty():
    return {"goal": "", "clarified": [], "constraints": [], "terms": {}, "locked": []}


UPDATE_PROMPT = """Ты ведёшь память задачи в диалоге со справочником по Конституции РФ.

Текущая память (JSON):
<<STATE>>

Последние сообщения диалога:
<<WINDOW>>

Новая реплика пользователя: <<MESSAGE>>

Обнови память и верни строго JSON:
{
  "goal": "цель диалога одной фразой — зачем пользователь пришёл",
  "clarified": ["что пользователь уже уточнил о себе, ситуации или нужном ответе"],
  "constraints": ["требования к ответам: длина, стиль, формат, рамки"],
  "terms": {"термин или сокращение пользователя": "что он означает"},
  "side_question": true/false,
  "search_query": "самостоятельный вопрос для поиска по тексту Конституции"
}

Правила:
- цель меняй, только если пользователь явно сменил задачу; вопрос «кстати, …» — это side_question,
  цель при нём остаётся прежней;
- clarified, constraints, terms — дополняй, ничего не теряя; удаляй пункт, только если пользователь
  его отменил; не больше 8 пунктов в списке, похожие объединяй;
- search_query раскрывает отсылки («а у него?», «а если ночью?») по контексту и цели, заменяет
  термины пользователя на полные слова; на вопрос НЕ отвечай — никаких чисел и выводов;
- если реплика просит итог или резюме — search_query по цели диалога."""

QUERY_PROMPT = """Последние сообщения диалога со справочником по Конституции РФ:
<<WINDOW>>

Новая реплика пользователя: <<MESSAGE>>

Верни строго JSON: {"search_query": "самостоятельный вопрос для поиска по тексту Конституции"}.
Раскрой отсылки («а у него?», «а если ночью?») по последним сообщениям. На вопрос НЕ отвечай."""


def _window_text(window):
    if not window:
        return "(диалог только начался)"
    return "\n".join(f"{'Пользователь' if m['role'] == 'user' else 'Ассистент'}: {m['content'][:600]}"
                     for m in window)


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


def _clean(new, old):
    """Ответ модели → валидное состояние; закреплённые поля берутся из старого."""
    state = empty()
    state["locked"] = list(old.get("locked") or [])
    state["goal"] = str(new.get("goal") or old.get("goal") or "").strip()
    for key in ("clarified", "constraints"):
        items = new.get(key) if isinstance(new.get(key), list) else old.get(key, [])
        state[key] = [str(x).strip() for x in items if str(x).strip()][:MAX_ITEMS]
    terms = new.get("terms") if isinstance(new.get("terms"), dict) else old.get("terms", {})
    state["terms"] = {str(k).strip(): str(v).strip() for k, v in list(terms.items())[:MAX_ITEMS]}
    for key in state["locked"]:
        if key in FIELDS:
            state[key] = old.get(key, state[key])
    return state


def update(state, window, message, with_state=True):
    """→ (новое состояние, поисковый запрос, side_question, расход)."""
    if with_state:
        prompt = (UPDATE_PROMPT
                  .replace("<<STATE>>", json.dumps({k: state[k] for k in FIELDS}, ensure_ascii=False))
                  .replace("<<WINDOW>>", _window_text(window))
                  .replace("<<MESSAGE>>", message))
    else:
        prompt = QUERY_PROMPT.replace("<<WINDOW>>", _window_text(window)).replace("<<MESSAGE>>", message)
    reply = providers.call(MODEL, [{"role": "user", "content": prompt}], temperature=0,
                           max_tokens=700, response_format={"type": "json_object"})
    data = _parse(reply["text"])
    query = " ".join(str(data.get("search_query") or message).split())
    new_state = _clean(data, state) if with_state else state
    return new_state, query, bool(data.get("side_question")), reply


def block(state):
    """Текст памяти для промпта ответа. Пустая память — пустая строка."""
    if not state or not any(state.get(k) for k in FIELDS):
        return ""
    lines = ["Память задачи:"]
    if state["goal"]:
        lines.append(f"- цель диалога: {state['goal']}")
    if state["clarified"]:
        lines.append("- пользователь уточнил: " + "; ".join(state["clarified"]))
    if state["constraints"]:
        lines.append("- ограничения к ответам: " + "; ".join(state["constraints"]))
    if state["terms"]:
        lines.append("- термины пользователя: " + "; ".join(f"{k} = {v}" for k, v in state["terms"].items()))
    return "\n".join(lines) + "\n\n"
