"""Сценарий: состояние задачи как конечный автомат, пауза и продолжение.

    python3 task_demo.py

Проверяем ровно то, что просит задание: этап, шаг и ожидаемое действие живут
отдельно от диалога; задачу можно поставить на паузу на любом этапе; после
перезапуска работа продолжается без повторных объяснений.
"""

import pathlib
import sys
import tempfile

import storage

storage.DB_PATH = pathlib.Path(tempfile.gettempdir()) / "task_demo.db"

import agent as agents          # noqa: E402  (импорт после подмены пути к базе)
import taskstate as ts          # noqa: E402

SETTINGS = {
    "model": "deepseek-chat",
    "temperature": 0.0,
    "max_tokens": 200,
    "keep_last": 6,
    "router": False,            # память сегодня не в фокусе, чтобы не мешала
    "system_prompt": "Ты ведёшь проект. Отвечай кратко, 1–3 предложения.",
}

TASK = "ТЗ на сервис аренды велосипедов"
CONTEXT = ("Мобильное приложение для Казани, бюджет 2,5 млн, запуск 1 декабря, "
           "стек Flutter и FastAPI.")


def head(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def show_state(agent, prefix="  "):
    for line in ts.block(agent.task).splitlines():
        print(prefix + line)


def apply(agent, advice):
    """Пользователь смотрит предложение агента и подтверждает его."""
    if not advice:
        print("  агент: состояние менять не предлагает")
        return
    parts = []
    if advice["step_done"] and advice["step"]:
        parts.append(f"закрыть шаг «{advice['step']}»")
    if advice["next_stage"]:
        parts.append(f"перейти на «{advice['next_stage']}»")
    if advice["expected_action"]:
        parts.append(f"ждать: {advice['expected_action']}")
    print(f"  агент предлагает: {' · '.join(parts) or 'ничего'}")
    if advice["reason"]:
        print(f"    причина: {advice['reason']}")
    try:
        agent.apply_advice()
        print("  пользователь нажал ✓")
    except RuntimeError as error:
        print(f"  автомат отказал: {error}")


def main():
    storage.DB_PATH.unlink(missing_ok=True)
    storage.init()

    head("1. ПЛАНИРОВАНИЕ: агент составляет шаги")
    agent = agents.create({**SETTINGS, "name": "Проект"})
    agent.start_task(TASK)
    show_state(agent)
    agent.plan_task(CONTEXT)
    print()
    for step in agent.task["steps"]:
        print(f"    [{step['stage']}] {step['text']}")

    head("2. ВЫПОЛНЕНИЕ: автомат не пускает вперёд, пока этап не закрыт")
    try:
        agent.move("execution")
    except RuntimeError as error:
        print(f"  {error}")

    for step in ts.steps_of(agent.task):
        agent.mark_step(step["text"])
    print(f"  закрыли шаги планирования → переход разрешён: {ts.can(agent.task, 'execution')[0]}")
    agent.move("execution")
    print()
    show_state(agent)
    for question in ("Опиши экраны первой версии, коротко списком.",
                     "Теперь роли пользователей."):
        print(f"\n  вы → {question}")
        result = agent.ask(question)
        print(f"  агент ← {result['text'].strip().splitlines()[0][:90]}")
        apply(agent, result["task_proposal"])

    head("3. ПАУЗА посреди этапа")
    agent.pause_task("созвон, вернусь после обеда")
    show_state(agent)
    print(f"\n  переходы на паузе запрещены: {ts.can(agent.task, 'validation')[1]}")

    head("4. ПЕРЕЗАПУСК приложения")
    agent_id = agent.id
    agents.REGISTRY.clear()
    info = agents.restore()
    agent = agents.get(agent_id)
    print(f"  поднято из базы: агентов {info['agents']}, сообщений {info['messages']}")
    print(f"  состояние задачи восстановлено:")
    show_state(agent, "    ")

    head("5. ПРОДОЛЖЕНИЕ без повторных объяснений")
    agent.resume_task()
    # Реплика короткая и без контекста: всё, что агент знает о задаче,
    # он берёт из состояния, а не из пересказа.
    result = agent.ask("Продолжаем.")
    print("  вы → Продолжаем.")
    print(f"  агент ← {result['text'].strip()[:260]}")
    print(f"\n  токенов в запросе: {result['prompt_tokens']} "
          f"(блок состояния — {len(ts.block(agent.task))} символов)")

    control = agents.create({**SETTINGS, "name": "Без состояния",
                             "use_task_state": False, "task_advisor": False})
    control.start_task(TASK)
    blind = control.ask("Продолжаем.")
    print(f"\n  тот же вопрос агенту без блока состояния:")
    print(f"  агент ← {blind['text'].strip()[:200]}")

    head("6. ЗАПРЕТЫ автомата")
    print(f"  перескок через этап: {ts.can(agent.task, 'done')[1]}")
    unfinished = ts.can(agent.task, "validation")
    print(f"  вперёд с незакрытыми шагами: {unfinished[1] or 'разрешено'}")
    if not unfinished[0]:
        agent.move("validation", force=True)
        print(f"  принудительный переход выполнен → этап {agent.task['stage']}")
    print(f"  назад на доработку: {ts.can(agent.task, 'execution')}")

    head("7. НАСТРАИВАЕМЫЕ ЭТАПЫ под другую задачу")
    writer = agents.create({**SETTINGS, "name": "Статья"})
    writer.start_task("Статья про кикшеринг", stages=[
        {"key": "research", "title": "Сбор материала", "goal": "собрать факты и источники"},
        {"key": "draft", "title": "Черновик", "goal": "написать первый вариант"},
        {"key": "review", "title": "Редактура", "goal": "вычитать и сократить"},
        {"key": "publish", "title": "Публикация", "goal": "опубликовать и закрыть задачу"},
    ])
    writer.plan_task("Статья для блога, 4000 знаков, аудитория — продакты.")
    show_state(writer)
    print(f"\n  тот же автомат, другие этапы: {[s['key'] for s in writer.task['stages']]}")

    head("ЖУРНАЛ ПЕРЕХОДОВ (из базы)")
    saved = storage.load_task(agent_id)
    for event in saved["events"]:
        print(f"  {event['at'][11:19]}  {event['kind']:<11} {event['payload']}")

    storage.DB_PATH.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
