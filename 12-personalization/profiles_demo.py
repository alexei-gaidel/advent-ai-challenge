"""Сценарий: одни и те же вопросы под разными профилями и что ассистент учёл сам.

    python3 profiles_demo.py

Вопросы нейтральные — в них нет ни слова о том, как отвечать. Всё, чем ответы
различаются, агент взял из профиля автоматически. Судья (отдельный вызов LLM)
раскладывает профиль на требования и отмечает, какие выполнены.
"""

import pathlib
import sys
import tempfile

import storage

storage.DB_PATH = pathlib.Path(tempfile.gettempdir()) / "profiles_demo.db"

import agent as agents          # noqa: E402  (импорт после подмены пути к базе)
import profiles                 # noqa: E402

QUESTIONS = [
    "Стоит ли нам добавлять кэширование в сервис аренды велосипедов?",
    "Что такое индекс в базе данных?",
]

SETTINGS = {
    "model": "deepseek-chat",
    "temperature": 0.0,
    "max_tokens": 400,
    "keep_last": 6,
    "use_short": False,         # каждый вопрос — с чистого листа, влияет только профиль
    "router": False,
    "system_prompt": "Ты ассистент команды разработки.",
}


def head(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def show_answer(text, limit=300):
    text = text.strip()
    short = text[:limit] + ("…" if len(text) > limit else "")
    for line in short.splitlines():
        print(f"      {line}")


def metrics_line(text):
    m = profiles.measure(text)
    return (f"{m['chars']} симв. · пунктов {m['bullets']} · эмодзи {m['emoji']} · "
            f"латиницы {m['latin']}% · код {'да' if m['code'] else 'нет'} · "
            f"обращение: {m['address']}")


def main():
    storage.DB_PATH.unlink(missing_ok=True)
    storage.init()
    known = profiles.load_all()

    head("ПРОФИЛИ")
    for profile in known.values():
        print(f"\n  {profile['id']} · {profile['name']}")
        for key, field in profiles.FIELDS.items():
            print(f"      {field['title']:<12} {profile[key]}")
        print(f"      {'Факты':<12} " + "; ".join(f"{k} — {v}" for k, v in profile["facts"].items()))

    # Один агент, профиль переключаем: так видно, что меняется только профиль.
    agent = agents.create({**SETTINGS, "name": "Стенд", "profile": ""})
    judge_tokens = 0
    scoreboard = {}      # (profile_id) → [ok, total]

    for question in QUESTIONS:
        head(f"ВОПРОС: {question}")

        agent.set_profile("")
        result = agent.ask(question)
        print("\n  — без профиля —")
        print(f"      [{metrics_line(result['text'])}]")
        show_answer(result["text"], 220)

        for profile in known.values():
            agent.set_profile(profile["id"])
            result = agent.ask(question)
            print(f"\n  — {profile['name']} — запрос {result['prompt_tokens']} токенов, "
                  f"профиль {agent.snapshot()['layers']['profile']['chars']} симв.")
            print(f"      [{metrics_line(result['text'])}]")
            show_answer(result["text"])

            items, tokens = profiles.check(SETTINGS["model"], profile, question, result["text"])
            judge_tokens += tokens
            ok = sum(1 for i in items if i["ok"])
            board = scoreboard.setdefault(profile["id"], [0, 0])
            board[0] += ok
            board[1] += len(items)
            print(f"      учтено автоматически: {ok} из {len(items)}")
            for item in items:
                mark = "✓" if item["ok"] else "✗"
                print(f"        {mark} {item['requirement']}"
                      f"{' — ' + item['evidence'][:60] if item['evidence'] else ''}")

    head("ИТОГ ПО ПРОФИЛЯМ")
    for profile in known.values():
        ok, total = scoreboard[profile["id"]]
        print(f"  {profile['name']:<16} учтено {ok} из {total} требований "
              f"({round(100 * ok / total) if total else 0}%)")
    print(f"  судья: {judge_tokens} токенов")

    head("ПРОВЕРКА 2. Предпочтение, сказанное в диалоге, попадает в профиль")
    alex = next(iter(known.values()))
    agent.set_profile(alex["id"])
    agent.update({"router": True, "use_short": True})
    remark = "Кстати, я перешёл на Go. И давай без списков — сплошным текстом, максимум три предложения."
    print(f"\n  профиль до:")
    print(f"      Формат       {alex['format']}")
    print(f"\n  вы → {remark}")
    result = agent.ask(remark)
    print(f"  агент ← {result['text'].strip().splitlines()[0][:80]}")
    if not result["proposed"]:
        print("  роутер: предложений нет")
    for proposal in result["proposed"]:
        agent.decide(proposal["id"], "accept")                # пользователь нажал ✓
        print(f"  роутер → {proposal['layer']} · {proposal['kind']}: "
              f"{proposal['key']} = {proposal['value']}")

    alex = profiles.get(alex["id"])
    print("\n  профиль после (новое предпочтение слито со старым, а не дописано):")
    print(f"      Формат       {alex['format']}")
    print(f"      Факты        " + "; ".join(f"{k} — {v}" for k, v in alex["facts"].items()))

    agent.update({"router": False, "use_short": False})
    result = agent.ask(QUESTIONS[1])
    print(f"\n  тот же вопрос «{QUESTIONS[1]}» — без краткосрочной памяти, только профиль:")
    print(f"      [{metrics_line(result['text'])}]")
    show_answer(result["text"])

    head("ПРОВЕРКА 3. Новый агент с тем же профилем")
    fresh = agents.create({**SETTINGS, "name": "Новичок", "profile": alex["id"]})
    result = fresh.ask("Какой у меня стек?")
    print(f"  диалог: 0 сообщ. · профиль: {alex['name']}")
    print(f"  вы → Какой у меня стек?")
    print(f"  агент ← {result['text'].strip()[:120]}")

    storage.DB_PATH.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
