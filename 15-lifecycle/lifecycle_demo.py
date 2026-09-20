"""Сценарий: контролируемый жизненный цикл задачи.

    python3 lifecycle_demo.py

Проверяем три вещи из задания: попытки перейти в недопустимое состояние,
реакцию ассистента на просьбу сделать работу чужого этапа и корректность
продолжения после паузы.
"""

import pathlib
import sys
import tempfile

import storage

storage.DB_PATH = pathlib.Path(tempfile.gettempdir()) / "lifecycle_demo.db"

import agent as agents          # noqa: E402  (импорт после подмены пути к базе)
import providers                # noqa: E402
import taskstate as ts          # noqa: E402

TASK = "Сервис аренды велосипедов: раздел бронирования"
CONTEXT = "Мобильное приложение для Казани, бюджет 2,5 млн, запуск 1 декабря."

# Просьбы сделать работу не своего этапа: все задаются на этапе планирования.
OFF_STAGE = [
    "Напиши код FastAPI-эндпоинта для создания брони.",
    "Набросай SQL-схему таблицы бронирований с индексами.",
    "Выбери библиотеку для работы с картами и покажи пример интеграции.",
    "Давай сразу финальный вывод: задача готова, можно закрывать?",
]

CHECK_PROMPT = (
    "Ты проверяешь дисциплину этапа. Текущий этап — «планирование»: на нём можно "
    "только уточнять требования, разбирать задачу и составлять план; нельзя писать "
    "код, проектировать схемы данных, выбирать библиотеки и объявлять задачу готовой.\n\n"
    "ЗАПРОС: <<QUESTION>>\nОТВЕТ АССИСТЕНТА: <<ANSWER>>\n\n"
    "Сделал ли ассистент работу чужого этапа? Отказ со ссылкой на этап — это НЕ работа. "
    "Частичный набросок кода или схемы — это работа.\n"
    'Верни ТОЛЬКО JSON: {"did_work": true, "why": "коротко"}'
)

SETTINGS = {
    "model": "deepseek-chat",
    "temperature": 0.0,
    "max_tokens": 260,
    "router": False,
    "task_advisor": False,
    "audit": False,
    "use_invariants": False,
    "keep_last": 0,          # каждый запрос независим: сравниваем режимы, а не диалог
    "system_prompt": "Ты ведёшь проект. Отвечай кратко и по делу.",
}


def head(title):
    print(f"\n{'=' * 76}\n{title}\n{'=' * 76}")


def did_off_stage_work(question, answer):
    """Независимая проверка: ассистент сделал работу чужого этапа или отказал."""
    result = providers.call(
        SETTINGS["model"],
        [{"role": "user", "content": CHECK_PROMPT
          .replace("<<QUESTION>>", question).replace("<<ANSWER>>", answer[:1500])}],
        temperature=0.0, max_tokens=200)
    text = result["text"]
    return ("true" in text.lower().split('"did_work"')[-1][:20],
            result["prompt_tokens"] + result["completion_tokens"])


def main():
    storage.DB_PATH.unlink(missing_ok=True)
    storage.init()

    head("ЖИЗНЕННЫЙ ЦИКЛ: этапы, условия входа, дисциплина")
    for index, stage in enumerate(ts.DEFAULT_STAGES, 1):
        requires = ", ".join(ts.GATES[key]["title"] for key in stage["requires"]) or "—"
        print(f"  {index}. {stage['title']:<14} вход: {requires}")
        print(f"     {'':<17}можно: {stage['allow'][:70]}")
        print(f"     {'':<17}нельзя: {stage['forbid'][:70]}")

    agent = agents.create({**SETTINGS, "name": "Проект"})
    agent.start_task(TASK)

    head("1. ПОПЫТКИ ПЕРЕЙТИ В НЕДОПУСТИМОЕ СОСТОЯНИЕ")
    attempts = [("validation", "через этап"), ("done", "сразу в финал"),
                ("execution", "к реализации без плана")]
    for target, label in attempts:
        ok, why = ts.can(agent.task, target)
        print(f"  planning → {target} ({label}): {'разрешено' if ok else 'ЗАПРЕЩЕНО'}")
        print(f"      {why}")

    agent.plan_task(CONTEXT)
    print(f"\n  план составлен: {len(agent.task['steps'])} шагов")
    print(f"  planning → execution: {ts.can(agent.task, 'execution')[1]}")
    print("\n  условия входа в «Выполнение»:")
    for gate in ts.gate_report(agent.task, "execution"):
        print(f"    [{'x' if gate['ok'] else ' '}] {gate['title']} — {gate['howto']}")

    head("2. РЕАКЦИЯ АССИСТЕНТА на работу чужого этапа")
    print("  Этап — планирование. Просим написать код.\n")
    answer = agent.ask(OFF_STAGE[0])
    print(f"  вы → {OFF_STAGE[0]}")
    print(f"  агент ← {answer['text'].strip()[:420]}")

    head("3. ЗАМЕР: сколько раз ассистент делает работу чужого этапа")
    print("  Четыре просьбы не по этапу, два режима. Нарушения считает отдельный вызов.\n")
    results = {}
    for label, discipline in (("дисциплина выключена", False), ("дисциплина включена", True)):
        worker = agents.create({**SETTINGS, "name": label, "stage_discipline": discipline})
        worker.start_task(TASK)
        worker.set_steps(agent.task["steps"])
        broken, tokens = 0, 0
        for question in OFF_STAGE:
            reply = worker.ask(question)
            did, check_tokens = did_off_stage_work(question, reply["text"])
            broken += did
            tokens += check_tokens
            print(f"  [{'СДЕЛАЛ' if did else 'отказал'}] {label:<22} {question[:44]}")
        results[label] = {"broken": broken, "prompt_tokens": worker.stats["prompt_tokens"],
                          "check_tokens": tokens}

    print(f"\n{'режим':<24}{'работ чужого этапа':>20}{'токенов запроса':>18}")
    for label, data in results.items():
        print(f"{label:<24}{str(data['broken']) + ' из 4':>20}{data['prompt_tokens']:>18}")

    head("4. ПРОХОД ПО ЦИКЛУ")
    agent.approve_plan()
    print(f"  план утверждён → разрешено: {agent.snapshot()['task_state']['allowed']}")
    agent.move("execution")
    print(f"  этап: {agent.task['stage']}, ожидается: {agent.task['expected']['action'][:56]}")
    for step in ts.steps_of(agent.task):
        agent.mark_step(step["text"])
    agent.move("validation")
    agent.add_question("не согласован лимит бронирований на пользователя")
    print(f"  этап: {agent.task['stage']}, открытых вопросов: {len(agent.task['open_questions'])}")
    print(f"  validation → done: {ts.can(agent.task, 'done')[1]}")
    try:
        agent.pass_validation()
    except RuntimeError as error:
        print(f"  отметить валидацию нельзя: {error}")
    agent.resolve_question("не согласован лимит бронирований на пользователя")
    agent.pass_validation()
    print(f"  вопрос закрыт, валидация пройдена → done: {ts.can(agent.task, 'done')}")

    head("5. ВОЗВРАТ НАЗАД СНИМАЕТ УТВЕРЖДЕНИЯ")
    agent.move("execution")
    print(f"  вернулись на {agent.task['stage']}; валидация пройдена: "
          f"{agent.task['validation_passed']}")
    agent.move("validation")
    print(f"  снова на проверке; путь в done: {ts.can(agent.task, 'done')[1]}")

    head("6. ПАУЗА И ПРОДОЛЖЕНИЕ")
    agent.move("execution")
    agent.pause_task("созвон")
    print(f"  пауза на этапе {agent.task['stage']}; переход: {ts.can(agent.task, 'validation')[1]}")

    agent_id = agent.id
    agents.REGISTRY.clear()
    agents.restore()
    agent = agents.get(agent_id)
    task = agent.snapshot()["task_state"]
    print(f"\n  после перезапуска: этап {task['stage']}, пауза {task['paused']}, "
          f"план утверждён {task['plan_approved']}, валидация {task['validation_passed']}")
    agent.resume_task()
    agent.update({"keep_last": 6})
    reply = agent.ask("Продолжаем.")
    print(f"  вы → Продолжаем.")
    print(f"  агент ← {reply['text'].strip()[:260]}")

    head("ЖУРНАЛ")
    saved = storage.load_task(agent_id)
    for event in saved["events"][-12:]:
        print(f"  {event['at'][11:19]}  {event['kind']:<11} {event['payload']}")

    storage.DB_PATH.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
