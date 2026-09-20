"""Контролируемый жизненный цикл задачи.

Отличие от простого автомата: мало разрешить переход между соседними этапами —
у каждого этапа есть **условия входа**, которые проверяются кодом, и **дисциплина**:
что на этом этапе делать можно, а чего нельзя. Второе уходит в промпт, поэтому
ассистент не может перепрыгнуть этап не только кнопкой, но и по существу — он
отказывается писать реализацию, пока план не утверждён.

Принудительного перехода нет: не выполнил условие — не перешёл.
"""

import itertools
import json
import re

import providers

# --- условия входа -------------------------------------------------------------
# Проверки детерминированные: считает код, а не модель.

GATES = {
    "plan_exists": {
        "title": "план составлен",
        "check": lambda task: bool(task["steps"]),
        "howto": "нажми «План» — агент составит список шагов",
    },
    "plan_approved": {
        "title": "план утверждён",
        "check": lambda task: task["plan_approved"],
        "howto": "проверь список шагов и нажми «Утвердить план»",
    },
    "steps_done": {
        "title": "все шаги этапа закрыты",
        "check": lambda task: all(step["status"] != "pending"
                                  for step in steps_of(task)),
        "howto": "закрой оставшиеся шаги галочками или сними их как ненужные",
    },
    "validation_passed": {
        "title": "валидация пройдена",
        "check": lambda task: task["validation_passed"],
        "howto": "на этапе проверки нажми «Валидация пройдена»",
    },
    "no_open_questions": {
        "title": "нет открытых вопросов",
        "check": lambda task: not task["open_questions"],
        "howto": "закрой открытые вопросы в списке под шагами",
    },
}

# Этап описывает: цель, условия входа и дисциплину — что можно и чего нельзя.
DEFAULT_STAGES = [
    {
        "key": "planning", "title": "Планирование",
        "goal": "разобрать задачу и составить план шагов",
        "requires": [],
        "allow": "уточнять требования, разбирать задачу, составлять и править план, "
                 "оценивать объём",
        "forbid": "писать реализацию и код, проектировать схемы данных, выбирать "
                  "конкретные библиотеки",
    },
    {
        "key": "execution", "title": "Выполнение",
        "goal": "проходить шаги утверждённого плана по порядку",
        "requires": ["plan_exists", "plan_approved"],
        "allow": "выполнять шаги плана, писать реализацию по ним, задавать уточняющие "
                 "вопросы по текущему шагу",
        "forbid": "менять план на ходу, брать работу не из плана, объявлять задачу "
                  "готовой",
    },
    {
        "key": "validation", "title": "Проверка",
        "goal": "сверить сделанное с требованиями и найти пропущенное",
        "requires": ["steps_done"],
        "allow": "проверять результат, искать пропущенное и противоречия, заводить "
                 "открытые вопросы",
        "forbid": "добавлять новые возможности, начинать новые шаги, переписывать "
                  "реализацию",
    },
    {
        "key": "done", "title": "Готово",
        "goal": "задача закрыта",
        "requires": ["validation_passed", "no_open_questions"],
        "allow": "подводить итог сделанного",
        "forbid": "продолжать работу по задаче",
    },
]

ACTORS = {"user": "от пользователя", "agent": "от агента"}

_task_counter = itertools.count(1)


def new_task(agent_id, title, stages=None):
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
        # Флаги условий входа: их ставит человек осознанным действием.
        "plan_approved": False,
        "validation_passed": False,
        "open_questions": [],
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
    return next((step for step in steps_of(task) if step["status"] == "pending"), None)


# --- правила перехода ----------------------------------------------------------

def gate_report(task, target):
    """Условия входа в этап с отметками — для интерфейса, промпта и отказов."""
    stage = stage_of(task, target)
    report = []
    for key in stage.get("requires", []):
        gate = GATES.get(key)
        if not gate:
            continue
        report.append({"key": key, "title": gate["title"],
                       "ok": bool(gate["check"](task)), "howto": gate["howto"]})
    return report


def can(task, target):
    """Можно ли перейти. Принудительного перехода нет — только выполнить условия."""
    if task["paused"]:
        return False, "задача на паузе — сначала продолжить"
    if target == task["stage"]:
        return False, "это текущий этап"
    if target not in {stage["key"] for stage in task["stages"]}:
        return False, f"нет такого этапа: {target}"

    here, there = stage_index(task), stage_index(task, target)
    if abs(there - here) != 1:
        return False, ("перепрыгивать этапы нельзя: только на соседний — "
                       "вперёд или назад на доработку")

    # Назад на доработку пускаем всегда: это признание, что этап не закончен.
    if there < here:
        return True, ""

    unmet = [item for item in gate_report(task, target) if not item["ok"]]
    if unmet:
        listing = "; ".join(f"{item['title']} — {item['howto']}" for item in unmet)
        return False, f"не выполнены условия входа в «{stage_of(task, target)['title']}»: {listing}"
    return True, ""


def transition(task, target):
    allowed, reason = can(task, target)
    if not allowed:
        raise RuntimeError(f"Переход {task['stage']} → {target} запрещён: {reason}")

    was = task["stage"]
    here, there = stage_index(task, was), stage_index(task, target)
    task["stage"] = target

    # Возврат назад снимает утверждения: то, что было проверено, устарело.
    reset = []
    if there < here:
        if there <= stage_index(task, "planning") and task["plan_approved"]:
            task["plan_approved"] = False
            reset.append("утверждение плана")
        if task["validation_passed"]:
            task["validation_passed"] = False
            reset.append("результат валидации")

    task["events"].append({"kind": "transition",
                           "payload": {"from": was, "to": target, "reset": reset}})
    _recalc_expected(task)
    return task


def _recalc_expected(task):
    step = current_step(task)
    if step:
        task["expected"] = {"actor": "agent", "action": f"выполнить шаг «{step['text']}»"}
        return task

    following = stage_index(task) + 1
    if following < len(task["stages"]):
        unmet = [item for item in gate_report(task, task["stages"][following]["key"])
                 if not item["ok"]]
        if unmet:
            task["expected"] = {"actor": "user", "action": unmet[0]["howto"]}
        else:
            task["expected"] = {
                "actor": "user",
                "action": f"подтвердить переход на этап «{task['stages'][following]['title']}»"}
    else:
        task["expected"] = {"actor": "user", "action": "закрыть задачу"}
    return task


# --- действия человека, закрывающие условия ------------------------------------

def approve_plan(task):
    if not task["steps"]:
        raise RuntimeError("Нечего утверждать: плана нет")
    task["plan_approved"] = True
    task["events"].append({"kind": "approve", "payload": {"steps": len(task["steps"])}})
    return _recalc_expected(task)

def pass_validation(task):
    if task["stage"] != "validation":
        raise RuntimeError("Валидацию отмечают на этапе проверки")
    if task["open_questions"]:
        raise RuntimeError(f"Сначала закрой открытые вопросы: {len(task['open_questions'])}")
    task["validation_passed"] = True
    task["events"].append({"kind": "validated", "payload": {}})
    return _recalc_expected(task)

def add_question(task, text):
    text = text.strip()
    if text:
        task["open_questions"].append(text)
        task["events"].append({"kind": "question", "payload": {"text": text}})
    return _recalc_expected(task)

def resolve_question(task, text):
    task["open_questions"] = [q for q in task["open_questions"] if q != text]
    task["events"].append({"kind": "resolved", "payload": {"text": text}})
    return _recalc_expected(task)


def pause(task, note=""):
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
    task["steps"] = [{"stage": step.get("stage", task["stage"]),
                      "text": step["text"], "status": step.get("status", "pending")}
                     for step in steps]
    # Новый план требует нового утверждения.
    task["plan_approved"] = False
    task["events"].append({"kind": "plan", "payload": {"steps": len(task["steps"])}})
    return _recalc_expected(task)


def mark_step(task, text, status="done"):
    for step in task["steps"]:
        if step["text"] == text:
            step["status"] = status
            break
    task["events"].append({"kind": "step", "payload": {"text": text, "status": status}})
    return _recalc_expected(task)


def set_expected(task, actor, action):
    task["expected"] = {"actor": actor, "action": action}
    task["events"].append({"kind": "expect", "payload": task["expected"]})
    return task


# --- блоки для промпта ---------------------------------------------------------

def block(task):
    """Состояние задачи — уходит в каждый запрос."""
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
    lines.append(f"Ожидается: {ACTORS[task['expected']['actor']]} — {task['expected']['action']}")
    lines.append(f"План утверждён: {'да' if task['plan_approved'] else 'нет'}"
                 + (f" · валидация пройдена: {'да' if task['validation_passed'] else 'нет'}"
                    if index >= 2 else ""))
    if task["open_questions"]:
        lines.append("Открытые вопросы: " + "; ".join(task["open_questions"]))
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


def discipline(task):
    """Дисциплина этапа — правила поведения, из-за которых ассистент не перепрыгивает.

    Без этого блока автомат сторожит только кнопки: в диалоге модель спокойно
    напишет реализацию на этапе планирования, если её попросить.
    """
    if not task:
        return ""

    stage = stage_of(task)
    index = stage_index(task)
    lines = [
        f"ДИСЦИПЛИНА ЭТАПА «{stage['title']}» — обязательна к соблюдению.",
        f"На этом этапе можно: {stage['allow']}.",
        f"На этом этапе нельзя: {stage['forbid']}.",
    ]

    following = index + 1
    if following < len(task["stages"]):
        nxt = task["stages"][following]
        unmet = [item for item in gate_report(task, nxt["key"]) if not item["ok"]]
        if unmet:
            lines.append(
                f"Переход на «{nxt['title']}» пока закрыт: "
                + "; ".join(f"{item['title']} ({item['howto']})" for item in unmet) + ".")
        else:
            lines.append(f"Условия перехода на «{nxt['title']}» выполнены — "
                         "нужно подтверждение пользователя.")

    lines += [
        "Если пользователь просит сделать работу другого этапа — откажись. "
        "Назови текущий этап, скажи, что именно мешает перейти дальше, и какое "
        "действие это условие закроет. Не выполняй просьбу частично и не показывай "
        "«просто набросок».",
        "Само по себе требование пользователя этап не меняет: переход делается "
        "кнопкой после выполнения условий.",
    ]
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
