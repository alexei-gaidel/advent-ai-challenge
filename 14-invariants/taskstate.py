"""Состояние задачи как конечный автомат.

Состояние — это три вещи: текущий ЭТАП, текущий ШАГ внутри этапа и ОЖИДАЕМОЕ
ДЕЙСТВИЕ (от кого его ждут и какое). Пауза — флаг поверх этапа: задача замирает
там, где стояла, и продолжается ровно с того же места.

Переходы разрешены только соседние (вперёд или назад на доработку) и проверяются
в `can()`. Агент может предложить переход, но выполняет его пользователь.
"""

import itertools
import json
import re

import providers

# Набор по умолчанию. Этапы настраиваются под задачу: можно задать свои
# (например research → draft → review → publish), автомат от этого не меняется.
DEFAULT_STAGES = [
    {"key": "planning", "title": "Планирование",
     "goal": "разобрать задачу и составить список шагов"},
    {"key": "execution", "title": "Выполнение",
     "goal": "проходить шаги плана по порядку"},
    {"key": "validation", "title": "Проверка",
     "goal": "сверить результат с требованиями и найти пропущенное"},
    {"key": "done", "title": "Готово",
     "goal": "задача закрыта, работа не ведётся"},
]

ACTORS = {"user": "от пользователя", "agent": "от агента"}

_task_counter = itertools.count(1)


def new_task(agent_id, title, stages=None):
    """Пустая задача: первый этап, шагов ещё нет, ход за агентом."""
    stages = stages or [dict(stage) for stage in DEFAULT_STAGES]
    return {
        "id": f"{agent_id}-t{next(_task_counter)}",
        "agent_id": agent_id,
        "title": title,
        "stages": stages,
        "stage": stages[0]["key"],
        "expected": {"actor": "agent", "action": "составить план шагов"},
        "paused": False,
        "pause_note": "",
        "steps": [],
        "events": [],
    }


def stage_index(task, key=None):
    key = key or task["stage"]
    return next((i for i, s in enumerate(task["stages"]) if s["key"] == key), 0)


def stage_of(task, key=None):
    return task["stages"][stage_index(task, key)]


def steps_of(task, stage=None):
    stage = stage or task["stage"]
    return [step for step in task["steps"] if step["stage"] == stage]


def current_step(task):
    """Первый незакрытый шаг текущего этапа — он и есть «текущий шаг»."""
    return next((step for step in steps_of(task) if step["status"] == "pending"), None)


def can(task, target, force=False):
    """Правила автомата. Возвращает (можно ли, причина отказа)."""
    if task["paused"]:
        return False, "задача на паузе — сначала продолжить"
    if target == task["stage"]:
        return False, "это текущий этап"
    if target not in {stage["key"] for stage in task["stages"]}:
        return False, f"нет такого этапа: {target}"

    here, there = stage_index(task), stage_index(task, target)
    if abs(there - here) != 1:
        return False, "переход только на соседний этап: вперёд или назад на доработку"

    if there > here and not force:
        unfinished = [s for s in steps_of(task) if s["status"] == "pending"]
        if unfinished:
            return False, (f"на этапе осталось незакрытых шагов: {len(unfinished)} "
                           f"(первый — «{unfinished[0]['text']}»)")
    return True, ""


def transition(task, target, force=False):
    """Переход между этапами с записью в журнал."""
    allowed, reason = can(task, target, force)
    if not allowed:
        raise RuntimeError(f"Переход {task['stage']} → {target} запрещён: {reason}")

    was = task["stage"]
    task["stage"] = target
    task["events"].append({"kind": "transition",
                           "payload": {"from": was, "to": target, "forced": force}})

    step = current_step(task)
    task["expected"] = {
        "actor": "agent" if step else "user",
        "action": (f"выполнить шаг «{step['text']}»" if step
                   else f"{stage_of(task)['goal']}"),
    }
    return task


def set_expected(task, actor, action):
    task["expected"] = {"actor": actor, "action": action}
    task["events"].append({"kind": "expect", "payload": task["expected"]})
    return task


def pause(task, note=""):
    """Пауза — флаг поверх этапа: этап и шаг сохраняются как есть."""
    task["paused"] = True
    task["pause_note"] = note
    task["events"].append({"kind": "pause",
                           "payload": {"stage": task["stage"], "note": note,
                                       "step": (current_step(task) or {}).get("text", "")}})
    return task


def resume(task):
    task["paused"] = False
    task["events"].append({"kind": "resume", "payload": {"stage": task["stage"]}})
    return task


def set_steps(task, steps):
    """Заменяет план шагов целиком (например, после планирования)."""
    task["steps"] = [{"stage": step.get("stage", task["stage"]),
                      "text": step["text"], "status": step.get("status", "pending")}
                     for step in steps]
    task["events"].append({"kind": "plan", "payload": {"steps": len(task["steps"])}})
    return task


def mark_step(task, text, status="done"):
    """Отмечает шаг и переводит ожидание на следующий."""
    for step in task["steps"]:
        if step["text"] == text:
            step["status"] = status
            break
    task["events"].append({"kind": "step", "payload": {"text": text, "status": status}})

    step = current_step(task)
    if step:
        task["expected"] = {"actor": "agent", "action": f"выполнить шаг «{step['text']}»"}
    else:
        nxt = stage_index(task) + 1
        task["expected"] = {
            "actor": "user",
            "action": (f"подтвердить переход на этап «{task['stages'][nxt]['title']}»"
                       if nxt < len(task["stages"]) else "закрыть задачу"),
        }
    return task


def block(task):
    """Состояние задачи текстом — уходит в каждый запрос.

    Это и есть механизм «продолжения без повторных объяснений»: агенту не нужно
    пересказывать задачу, достаточно прочитать, где он остановился.
    """
    if not task:
        return ""

    index = stage_index(task)
    stage = stage_of(task)
    step = current_step(task)
    steps = steps_of(task)
    done = sum(1 for s in steps if s["status"] == "done")

    lines = [
        f"Состояние задачи «{task['title']}»",
        f"Этап: {stage['title']} ({index + 1} из {len(task['stages'])}) — {stage['goal']}",
    ]
    if steps:
        position = done + 1 if step else done
        lines.append(f"Шаг: {position} из {len(steps)}"
                     + (f" — «{step['text']}»" if step else " — все шаги этапа закрыты"))
    lines.append(f"Ожидается: {ACTORS[task['expected']['actor']]} — "
                 f"{task['expected']['action']}")
    if task["paused"]:
        lines.append(f"Задача на паузе{': ' + task['pause_note'] if task['pause_note'] else ''}. "
                     "После продолжения работаем с того же шага, заново ничего не объясняем.")
    if steps:
        lines.append("Шаги этапа:")
        for number, item in enumerate(steps, 1):
            mark = {"done": "x", "skipped": "-", "pending": " "}[item["status"]]
            here = " ←" if step and item["text"] == step["text"] else ""
            lines.append(f"  [{mark}] {number}. {item['text']}{here}")

    return "\n".join(lines)


# --- вызовы LLM ----------------------------------------------------------------

PLAN_PROMPT = (
    "Ты планируешь работу по задаче. Этапы задачи:\n<<STAGES>>\n\n"
    "Задача: <<TITLE>>\nЧто известно: <<CONTEXT>>\n\n"
    "Составь план: 4–7 шагов, каждый привязан к одному из этапов (кроме последнего "
    "этапа — он означает, что работа закончена). Шаг — короткое действие в повелительном "
    "наклонении, без пояснений.\n"
    'Верни ТОЛЬКО JSON-массив без markdown: '
    '[{"stage": "execution", "text": "описать экраны первой версии"}]'
)

PROPOSE_PROMPT = (
    "Ты следишь за состоянием задачи как конечным автоматом.\n\n"
    "СОСТОЯНИЕ:\n<<STATE>>\n\n"
    "Последняя реплика пользователя: <<USER>>\nТвой ответ: <<ANSWER>>\n\n"
    "Реши: закрыт ли текущий шаг этим обменом, нужно ли переходить на соседний этап, "
    "и чьего действия ждём дальше. Ничего не выдумывай: если работа по шагу не сделана, "
    "step_done = false.\n"
    'Верни ТОЛЬКО JSON без markdown: {"step_done": true, "next_stage": "validation", '
    '"expected_actor": "user", "expected_action": "прислать список ролей", '
    '"reason": "коротко почему"}\n'
    "next_stage — ключ соседнего этапа или null, если переходить рано."
)


def _json_from(text):
    raw = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def plan(model, task, context=""):
    """Этап планирования: агент составляет список шагов. Правится руками."""
    stages = "\n".join(f"- {s['key']} ({s['title']}): {s['goal']}" for s in task["stages"])
    result = providers.call(
        model,
        [{"role": "user", "content": PLAN_PROMPT
          .replace("<<STAGES>>", stages)
          .replace("<<TITLE>>", task["title"])
          .replace("<<CONTEXT>>", context or "(ничего сверх названия)")}],
        temperature=0.0,
        max_tokens=600,
    )
    tokens = result["prompt_tokens"] + result["completion_tokens"]

    parsed = _json_from(result["text"])
    if not isinstance(parsed, list):
        return [], tokens

    keys = {stage["key"] for stage in task["stages"]}
    steps = [{"stage": item["stage"] if item.get("stage") in keys else task["stage"],
              "text": str(item.get("text", "")).strip(), "status": "pending"}
             for item in parsed if isinstance(item, dict) and str(item.get("text", "")).strip()]
    return steps, tokens


def propose(model, task, user_text, answer):
    """Агент предлагает, что делать с состоянием. Решение — за пользователем."""
    result = providers.call(
        model,
        [{"role": "user", "content": PROPOSE_PROMPT
          .replace("<<STATE>>", block(task))
          .replace("<<USER>>", user_text)
          .replace("<<ANSWER>>", answer[:1200])}],
        temperature=0.0,
        max_tokens=300,
    )
    tokens = result["prompt_tokens"] + result["completion_tokens"]

    parsed = _json_from(result["text"])
    if not isinstance(parsed, dict):
        return None, tokens

    next_stage = parsed.get("next_stage")
    if next_stage not in {stage["key"] for stage in task["stages"]}:
        next_stage = None

    return {
        "step_done": bool(parsed.get("step_done")),
        "step": (current_step(task) or {}).get("text", ""),
        "next_stage": next_stage,
        "expected_actor": parsed.get("expected_actor") if parsed.get("expected_actor") in ACTORS else "user",
        "expected_action": str(parsed.get("expected_action", "")).strip(),
        "reason": str(parsed.get("reason", "")).strip(),
    }, tokens
