"""Сравнение трёх стратегий управления контекстом на одном сценарии.

Сценарий — сбор ТЗ: сначала пять реплик с требованиями, потом восемь обсуждающих,
в конце пять контрольных вопросов ровно про требования из начала. К этому моменту
начало разговора давно вышло за пределы окна.

    python3 compare_strategies.py
"""

import pathlib
import sys
import tempfile
import time

import storage

# Отдельная база, чтобы сравнение не мешалось с рабочими диалогами.
storage.DB_PATH = pathlib.Path(tempfile.gettempdir()) / "compare_strategies.db"

import agent as agents          # noqa: E402  (импорт после подмены пути к базе)

REQUIREMENTS = [
    "Собираем ТЗ. Цель: мобильное приложение для аренды велосипедов в Казани.",
    "Бюджет 2,5 млн рублей, срок три месяца, запуск 1 декабря.",
    "Стек: Flutter на фронте, Python и FastAPI на бэке, база PostgreSQL.",
    "Ограничение: эквайринг только Тинькофф, других провайдеров подключать нельзя.",
    "Предпочтения по дизайну: тёмная тема по умолчанию, минимализм, без анимаций.",
]
DISCUSSION = [
    "Какие экраны нужны в первой версии?",
    "Как организовать роли пользователей?",
    "Что делать с push-уведомлениями?",
    "Как работать с картой и поиском велосипедов?",
    "Нужен ли офлайн-режим?",
    "Как тестировать приложение перед запуском?",
    "Какую аналитику собирать?",
    "Как организовать поддержку пользователей?",
]
CONTROL = [
    ("Напомни цель проекта одной строкой.",
     lambda t: "велосипед" in t.lower() and "азан" in t),
    ("Какой у нас бюджет и срок? Одной строкой.",
     lambda t: ("2,5" in t or "2.5" in t) and ("декабр" in t.lower() or "три мес" in t.lower())),
    ("Какой у нас стек? Одной строкой.",
     lambda t: "lutter" in t and ("astAPI" in t or "ostgre" in t)),
    ("Какое у нас ограничение по приёму платежей? Одной строкой.",
     lambda t: "инькофф" in t),
    ("Какие предпочтения по дизайну? Одной строкой.",
     lambda t: "ёмн" in t.lower()),
]

SETTINGS = {
    "model": "deepseek-chat",
    "temperature": 0.0,
    "max_tokens": 160,
    "keep_last": 6,
    "system_prompt": "Ты аналитик, собираешь ТЗ. Отвечай кратко, 1–3 предложения.",
}


def run(strategy):
    agent = agents.create({**SETTINGS, "strategy": strategy,
                           "name": agents.STRATEGIES[strategy]["title"]})
    started = time.monotonic()

    for text in REQUIREMENTS + DISCUSSION:
        agent.ask(text)

    answers, correct = [], 0
    for question, check in CONTROL:
        result = agent.ask(question)
        ok = check(result["text"])
        correct += ok
        answers.append((question, result["text"].strip().replace("\n", " ")[:80], ok))

    return {"agent": agent, "answers": answers, "correct": correct,
            "stats": agent.stats, "seconds": round(time.monotonic() - started, 1)}


def branching_demo():
    """Две ветки от одного чекпоинта: решения не протекают друг в друга.

    Берём однозначный факт — кодовое имя проекта. С архитектурой модель начинает
    спорить и предлагать своё, и это мешает увидеть суть проверки.
    """
    agent = agents.create({**SETTINGS, "strategy": "branching", "name": "Ветвление"})
    for text in REQUIREMENTS[:3]:
        agent.ask(text)

    agent.checkpoint("требования собраны")
    question = "Как называется наш проект? Ответь только названием."
    result = {}

    for title, codename in (("Тюльпан", "Кодовое имя проекта — «Тюльпан». Запомни."),
                            ("Кедр", "Кодовое имя проекта — «Кедр». Запомни.")):
        agent.switch(storage.MAIN_BRANCH)
        agent.fork(title=title)
        agent.ask(codename)
        answer = agent.ask(question)["text"].strip().replace("\n", " ")
        chain = " ".join(m["content"] for m in agent.chain())
        other = "Кедр" if title == "Тюльпан" else "Тюльпан"
        result[title] = {
            "branch": agent.settings["branch"],
            "answer": answer[:70],
            "верно": title in answer,
            # Структурная проверка: имени из соседней ветки в контексте быть не должно.
            "чужого нет": other not in chain,
        }

    return agent, result


def main():
    storage.DB_PATH.unlink(missing_ok=True)
    storage.init()

    if "--branching" in sys.argv:
        agent, branches = branching_demo()
        print("ВЕТВЛЕНИЕ: две ветки от одного чекпоинта\n" + "=" * 72)
        for title, data in branches.items():
            print(f"  ветка «{title}» ({data['branch']}): {data['answer']}")
            print(f"      назвал своё имя: {data['верно']} · "
                  f"имени соседней ветки в контексте нет: {data['чужого нет']}")
        print(f"\n  веток: {len(agent.branches)}, общий префикс: "
              f"{agent.branches[agent.settings['branch']]['fork_at']} сообщений")
        storage.DB_PATH.unlink(missing_ok=True)
        return 0

    total = len(REQUIREMENTS) + len(DISCUSSION) + len(CONTROL)
    print(f"Сценарий «собираем ТЗ»: {total} реплик "
          f"({len(REQUIREMENTS)} с требованиями, {len(DISCUSSION)} обсуждения, "
          f"{len(CONTROL)} контрольных).\nОкно keep_last={SETTINGS['keep_last']}, "
          f"модель {SETTINGS['model']}, temperature 0.\n")

    results = {}
    for strategy in ("window", "facts", "branching"):
        title = agents.STRATEGIES[strategy]["title"]
        print("=" * 72)
        print(f"{title} — {agents.STRATEGIES[strategy]['note']}")
        print("=" * 72)
        results[strategy] = run(strategy)
        for question, answer, ok in results[strategy]["answers"]:
            print(f"  [{'помнит' if ok else 'забыл '}] {question}\n            ← {answer}")
        if strategy == "facts":
            print("\n  карточка фактов:")
            for key, value in results[strategy]["agent"].facts.items():
                print(f"    {key}: {value[:70]}")
        print()

    print("=" * 72)
    print("СРАВНЕНИЕ")
    print("=" * 72)
    print(f"{'стратегия':<18}{'токенов запроса':>16}{'накладные':>11}"
          f"{'всего':>9}{'помнит':>9}{'сек':>7}")
    for strategy, data in results.items():
        stats = data["stats"]
        overhead = stats["facts_tokens"]
        total_tokens = stats["prompt_tokens"] + stats["completion_tokens"] + overhead
        print(f"{agents.STRATEGIES[strategy]['title']:<18}{stats['prompt_tokens']:>16}"
              f"{overhead:>11}{total_tokens:>9}{str(data['correct']) + '/5':>9}"
              f"{data['seconds']:>7}")

    print("\n" + "=" * 72)
    print("ВЕТВЛЕНИЕ: две ветки от одного чекпоинта")
    print("=" * 72)
    agent, branches = branching_demo()
    for title, data in branches.items():
        print(f"  ветка «{title}» ({data['branch']}): {data['answer']}")
        print(f"      назвал своё имя: {data['верно']} · "
              f"имени соседней ветки в контексте нет: {data['чужого нет']}")
    print(f"\n  веток у агента: {len(agent.branches)} "
          f"(включая главную), чекпоинтов: {len(agent.checkpoints)}")
    print("  общий префикс: первые "
          f"{agent.branches[agent.settings['branch']]['fork_at']} сообщений "
          "унаследованы обеими ветками")

    storage.DB_PATH.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
