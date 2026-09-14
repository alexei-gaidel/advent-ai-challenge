"""Сценарий: что попадает в каждый слой памяти и как слои влияют на ответы.

    python3 memory_demo.py

Демонстрация отвечает за пользователя: принимает все предложения роутера. В интерфейсе
это те же кнопки ✓ / ⇄ / ✕ — ничто не попадает в память без решения человека.
"""

import pathlib
import sys
import tempfile

import storage

storage.DB_PATH = pathlib.Path(tempfile.gettempdir()) / "memory_demo.db"

import agent as agents          # noqa: E402  (импорт после подмены пути к базе)
import memory                   # noqa: E402

# В репликах намеренно перемешаны три вида информации: профиль, данные задачи и решения.
SCRIPT = [
    "Привет! Меня зовут Алекс, я тимлид, работаю из Казани.",
    "Обычно предпочитаю Python и не люблю тяжёлые фреймворки.",
    "Задача: собрать ТЗ на сервис аренды велосипедов для Казани.",
    "Бюджет 2,5 млн рублей, срок — до 1 декабря.",
    "Решили: эквайринг только Тинькофф, это согласовано с финансами.",
    "Какие экраны нужны в первой версии?",
    "Ожидаем 500 активных пользователей в день на старте.",
    "Кстати, я всегда предпочитаю тёмную тему в интерфейсах.",
    "Что важно учесть при интеграции карты?",
]

CONTROL = [
    ("Как меня зовут и из какого я города? Одной строкой.",
     lambda t: "лекс" in t and "азан" in t, "long"),
    ("Какой эквайринг мы решили использовать? Одним словом.",
     lambda t: "инькофф" in t, "long"),
    ("Какой у задачи бюджет и срок? Одной строкой.",
     lambda t: ("2,5" in t or "2.5" in t) and "декабр" in t.lower(), "working"),
    ("Сколько активных пользователей в день мы ожидаем? Одним числом.",
     lambda t: "500" in t, "working"),
]

SETTINGS = {
    "model": "deepseek-chat",
    "temperature": 0.0,
    "max_tokens": 150,
    "keep_last": 6,
    "system_prompt": "Ты ассистент команды. Отвечай кратко, 1–2 предложения.",
}


def head(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def ask_control(agent, use_long, use_working):
    """Контрольные вопросы при выключенной краткосрочной памяти.

    Короткое окно выключаем намеренно: иначе ответ можно было бы взять
    из недавних реплик, и влияние слоёв не проверить.
    """
    agent.update({"use_short": False, "use_long": use_long,
                  "use_working": use_working, "router": False})
    rows, tokens = [], 0
    for question, check, layer in CONTROL:
        result = agent.ask(question)
        tokens += result["prompt_tokens"]
        rows.append((layer, question, result["text"].strip().replace("\n", " ")[:64],
                     check(result["text"])))
    return rows, tokens


def main():
    storage.DB_PATH.unlink(missing_ok=True)
    storage.init()

    head("МОДЕЛЬ ПАМЯТИ")
    for key, layer in memory.LAYERS.items():
        print(f"  {layer['title']:<16} {layer['holds']}")
        print(f"  {'':<16} живёт: {layer['life']} · область: {layer['scope']}")

    agent = agents.create({**SETTINGS, "name": "Память", "task": "ТЗ на кикшеринг"})

    head("ДИАЛОГ: роутер предлагает, пользователь принимает")
    for text in SCRIPT:
        result = agent.ask(text)
        print(f"\n  вы → {text}")
        print(f"  агент ← {result['text'].strip().splitlines()[0][:70]}")
        if not result["proposed"]:
            print("  роутер: новых фактов нет")
        for proposal in result["proposed"]:
            agent.decide(proposal["id"], "accept")        # пользователь нажал ✓
            print(f"  роутер → {memory.LAYERS[proposal['layer']]['title'].lower()}: "
                  f"{proposal['key']} = {proposal['value']}"
                  f"{' · ' + proposal['kind'] if proposal.get('kind') else ''}")

    head("ЧТО В КАКОМ СЛОЕ")
    snapshot = agent.snapshot()
    print(f"  Краткосрочная ({snapshot['layers']['short']['count']} сообщ., "
          f"в модель уходит окно {SETTINGS['keep_last']})")
    print(f"\n  Рабочая — {len(snapshot['layers']['working']['items'])} записей, "
          f"{snapshot['layers']['working']['chars']} симв.")
    for key, value in snapshot["layers"]["working"]["items"].items():
        print(f"    {key}: {value}")
    print(f"\n  Долговременная — {len(snapshot['layers']['long']['items'])} записей, "
          f"{snapshot['layers']['long']['chars']} симв. (общая на все агенты)")
    for key, item in snapshot["layers"]["long"]["items"].items():
        print(f"    [{item['kind']}] {key}: {item['value']}")
    print(f"\n  роутер: {agent.stats['router_calls']} вызовов, "
          f"{agent.stats['router_tokens']} токенов")

    head("ПРОВЕРКА 1. Как слои влияют на ответы")
    print("  Краткосрочная память выключена во всех трёх прогонах — отвечать можно\n"
          "  только из слоёв.\n")
    configurations = [
        ("все слои включены", True, True),
        ("без долговременной", False, True),
        ("без рабочей", True, False),
    ]
    results = {}
    for title, use_long, use_working in configurations:
        rows, tokens = ask_control(agent, use_long, use_working)
        results[title] = (rows, tokens)
        correct = sum(1 for *_, ok in rows if ok)
        print(f"  {title:<22} верных {correct}/4 · токенов запроса {tokens}")
        for layer, question, answer, ok in rows:
            print(f"      [{'помнит' if ok else 'забыл '}] ({layer}) {question}")
            print(f"               ← {answer}")
        print()

    head("ПРОВЕРКА 2. Новая задача чистит рабочую память, долговременную — нет")
    agent.new_task("другая задача")
    snapshot = agent.snapshot()
    print(f"  рабочая: {snapshot['layers']['working']['items'] or 'пусто'}")
    print(f"  долговременная: {len(snapshot['layers']['long']['items'])} записей на месте")
    agent.update({"use_short": False, "router": False})
    print(f"  вопрос про бюджет → {agent.ask(CONTROL[2][0])['text'].strip()[:70]}")
    print(f"  вопрос про имя    → {agent.ask(CONTROL[0][0])['text'].strip()[:70]}")

    head("ПРОВЕРКА 3. Новый агент наследует только долговременную память")
    fresh = agents.create({**SETTINGS, "name": "Новичок", "use_short": False, "router": False})
    print(f"  диалог: {len(fresh.history)} сообщ. · рабочая: {fresh.working or 'пусто'} · "
          f"долговременная: {len(fresh.long)} записей")
    print(f"  вопрос про имя    → {fresh.ask(CONTROL[0][0])['text'].strip()[:70]}")
    print(f"  вопрос про бюджет → {fresh.ask(CONTROL[2][0])['text'].strip()[:70]}")

    storage.DB_PATH.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
