"""Сравнение: один и тот же длинный диалог со сжатием истории и без него.

Меряем расход токенов на каждом шаге и проверяем, помнит ли агент факты,
названные в самом начале разговора.

    python3 compare.py        # 9 наполняющих реплик
    python3 compare.py 27     # длинный диалог: видно, где сжатие окупается
"""

import pathlib
import sys
import tempfile

import storage

# Отдельная база, чтобы сравнение не мешалось с рабочими диалогами.
storage.DB_PATH = pathlib.Path(tempfile.gettempdir()) / "compare_agents.db"

import agent as agents            # noqa: E402  (импорт после подмены пути к базе)

# Первые три реплики засевают факты, дальше идёт наполнение, в конце — контрольные
# вопросы ровно про эти факты. К моменту контрольных вопросов начало разговора
# у сжимающего агента уже свёрнуто в сводку.
SEED = [
    "Привет! Меня зовут Алекс, я тимлид. Мы делаем сервис кикшеринга «Самокат-Сити» на Python.",
    "База данных у нас PostgreSQL, очередь задач — Redis. Запомни это.",
    "Дедлайн релиза — 14 ноября, бюджет на инфраструктуру 300 тысяч рублей в месяц.",
]
FILLER = [
    "Как лучше организовать логирование в сервисе?",
    "А что насчёт мониторинга?",
    "Какие метрики собирать для API?",
    "Посоветуй стратегию кеширования.",
    "Как обрабатывать пиковые нагрузки в выходные?",
    "Что думаешь про очереди задач в целом?",
    "Как организовать автотесты?",
    "Что важно в код-ревью?",
    "Как вести техническую документацию?",
]
CONTROL = [
    ("Как меня зовут? Ответь одним словом.", lambda t: "лекс" in t),
    ("Как называется наш сервис и на каком языке он написан? Одной строкой.",
     lambda t: "амокат" in t and "ython" in t.lower()),
    ("Какой у нас дедлайн и месячный бюджет? Одной строкой.",
     lambda t: "14" in t and "300" in t),
    ("Какая у нас база данных и какая очередь? Одной строкой.",
     lambda t: "ostgre" in t.lower() and "edis" in t.lower()),
]

SETTINGS = {
    "model": "deepseek-chat",
    "temperature": 0.0,
    "max_tokens": 160,
    "system_prompt": "Ты ассистент команды. Отвечай кратко, 1–3 предложения.",
    "keep_last": 6,
    "summarize_every": 10,
}


def filler(count):
    """Наполняющие реплики: список повторяется по кругу до нужной длины."""
    return [f"{FILLER[i % len(FILLER)]} (вопрос {i + 1})" for i in range(count)]


def run(compress, turns):
    agent = agents.create({**SETTINGS, "compress": compress,
                           "name": "со сжатием" if compress else "без сжатия"})
    steps = []

    for text in SEED + filler(turns):
        result = agent.ask(text)
        steps.append(result["prompt_tokens"])

    correct = 0
    answers = []
    for question, check in CONTROL:
        result = agent.ask(question)
        steps.append(result["prompt_tokens"])
        ok = check(result["text"])
        correct += ok
        answers.append((question, result["text"].strip().replace("\n", " ")[:90], ok))

    return {
        "agent": agent,
        "steps": steps,
        "correct": correct,
        "answers": answers,
        "stats": agent.stats,
    }


def main():
    turns = int(sys.argv[1]) if len(sys.argv) > 1 else len(FILLER)
    storage.DB_PATH.unlink(missing_ok=True)
    storage.init()

    print(f"Диалог из {len(SEED) + turns + len(CONTROL)} реплик, модель "
          f"{SETTINGS['model']}, temperature 0.\n"
          f"Сжатие: дословно последние {SETTINGS['keep_last']} сообщений, "
          f"сводка каждые {SETTINGS['summarize_every']}.\n")

    results = {}
    for compress in (False, True):
        label = "СО СЖАТИЕМ" if compress else "БЕЗ СЖАТИЯ"
        print(f"{'=' * 70}\n{label}\n{'=' * 70}")
        results[compress] = run(compress, turns)
        for question, answer, ok in results[compress]["answers"]:
            print(f"  [{'верно' if ok else 'мимо '}] {question}\n           ← {answer}")
        print()

    plain, packed = results[False], results[True]

    print("=" * 70)
    print("РАСХОД ТОКЕНОВ ЗАПРОСА ПО ШАГАМ")
    print("=" * 70)
    print(f"{'шаг':>4}  {'без сжатия':>12}  {'со сжатием':>12}")
    step_rows = list(enumerate(zip(plain["steps"], packed["steps"]), 1))
    for index, (a, b) in step_rows:
        drop = index > 1 and b < packed["steps"][index - 2]
        # В длинном диалоге печатаем только начало, конец и шаги, где сработало сжатие.
        if len(step_rows) > 22 and not (drop or index <= 3 or index > len(step_rows) - 3):
            continue
        print(f"{index:>4}  {a:>12}  {b:>12}{'  ← сжатие' if drop else ''}")

    print("\n" + "=" * 70)
    print("ИТОГ")
    print("=" * 70)
    for label, data in (("без сжатия", plain), ("со сжатием", packed)):
        stats = data["stats"]
        total = stats["prompt_tokens"] + stats["completion_tokens"] + stats["summary_tokens"]
        print(f"  {label:<12} токенов запроса: {stats['prompt_tokens']:>6} · "
              f"на сжатие: {stats['summary_tokens']:>5} · всего: {total:>6} · "
              f"${stats['cost']:.6f} · контрольных верно: {data['correct']}/4")

    saved = plain["stats"]["prompt_tokens"] - packed["stats"]["prompt_tokens"]
    net = ((plain["stats"]["prompt_tokens"] + plain["stats"]["completion_tokens"])
           - (packed["stats"]["prompt_tokens"] + packed["stats"]["completion_tokens"]
              + packed["stats"]["summary_tokens"]))
    print(f"\n  экономия на запросах: {saved} токенов "
          f"({round(100 * saved / plain['stats']['prompt_tokens'])}%)")
    print(f"  чистая экономия с учётом расходов на сжатие: {net} токенов")
    print(f"\n  сводка агента ({packed['agent'].covered} сообщ. свёрнуто):")
    for line in (packed["agent"].summary or "(не понадобилась)").splitlines():
        print(f"    {line}")

    storage.DB_PATH.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
