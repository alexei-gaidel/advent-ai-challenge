"""Сценарий: агент отвечает про репозиторий через MCP-инструменты.

    python3 tool_demo.py

Один и тот же набор вопросов задаётся дважды — с доступом к инструментам и без.
Правильность проверяется не на глаз: эталон берётся прямо из git.
"""

import pathlib
import subprocess
import sys
import tempfile

import storage

storage.DB_PATH = pathlib.Path(tempfile.gettempdir()) / "tool_demo.db"

import agent as agents          # noqa: E402  (импорт после подмены пути к базе)
import tools as mcp_tools       # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True).stdout.strip()


# Эталоны берём из git, а не из головы: иначе сравнивать было бы не с чем.
TRUTH = {
    "commits": git("rev-list", "--count", "HEAD"),
    "last_hash": git("log", "-1", "--pretty=format:%h"),
    "invariants_date": git("log", "-1", "--date=short", "--pretty=format:%ad",
                           "--grep", "Задание 14"),
    "folders": str(len([item for item in REPO.iterdir()
                        if item.is_dir() and item.name[:2].isdigit()])),
}

QUESTIONS = [
    ("Сколько всего коммитов в репозитории? Ответь числом.",
     lambda t: TRUTH["commits"] in t),
    ("Какой хеш последнего коммита? Ответь коротко.",
     lambda t: TRUTH["last_hash"] in t),
    ("Когда было сделано задание 14 про инварианты? Ответь датой.",
     lambda t: TRUTH["invariants_date"] in t),
    ("Сколько в репозитории папок с заданиями? Ответь числом.",
     lambda t: TRUTH["folders"] in t),
    ("В каком задании впервые появилось слово inputSchema? Ответь номером папки.",
     lambda t: "16" in t),
]

SETTINGS = {
    "model": "deepseek-chat",
    "temperature": 0.0,
    "max_tokens": 400,
    "router": False,
    "task_advisor": False,
    "audit": False,
    "use_invariants": False,
    "keep_last": 0,          # каждый вопрос независим
    "system_prompt": ("Ты помогаешь разобраться в репозитории Advent AI Challenge. "
                      "Отвечай кратко и только фактами."),
}


def head(title):
    print(f"\n{'=' * 76}\n{title}\n{'=' * 76}")


def main():
    storage.DB_PATH.unlink(missing_ok=True)
    storage.init()

    head("MCP-СЕРВЕР ВОКРУГ GIT")
    info = mcp_tools.BRIDGE.info()
    if info["error"]:
        print(f"  сервер недоступен: {info['error']}")
        return 1
    print(f"  сервер: {info['server'].get('name')} {info['server'].get('version')} · "
          f"протокол {info['protocol']} · транспорт stdio")
    for tool in info["tools"]:
        schema = tool["schema"]
        required = set(schema.get("required") or [])
        args = ", ".join(
            f"{name}{'*' if name in required else ''}: {field.get('type', '?')}"
            for name, field in (schema.get("properties") or {}).items()) or "—"
        print(f"\n    {tool['name']}({args})")
        print(f"      {' '.join(tool['description'].split())[:88]}")
    print("\n  * — обязательный аргумент")

    head("ЭТАЛОНЫ ИЗ GIT")
    for key, value in TRUTH.items():
        print(f"  {key:<18} {value}")

    results = {}
    for label, use_tools in (("без инструментов", False), ("с инструментами", True)):
        head(f"РЕЖИМ: {label}")
        agent = agents.create({**SETTINGS, "name": label, "use_tools": use_tools})
        correct, calls = 0, []
        for question, check in QUESTIONS:
            reply = agent.ask(question)
            ok = check(reply["text"])
            correct += ok
            answer = reply["text"].strip().replace("\n", " ")[:72]
            print(f"  [{'верно' if ok else 'мимо '}] {question[:52]}")
            print(f"           ← {answer}")
            for call in reply["tool_calls"]:
                calls.append(call)
                print(f"             ↳ {call['name']}({call['arguments']})")
        results[label] = {"correct": correct, "calls": len(calls),
                          "tokens": agent.stats["prompt_tokens"] + agent.stats["completion_tokens"],
                          "cost": agent.stats["cost"]}

    head("СРАВНЕНИЕ")
    print(f"{'режим':<20}{'верных ответов':>16}{'вызовов инструментов':>23}{'токенов':>10}")
    for label, data in results.items():
        print(f"{label:<20}{str(data['correct']) + ' из 5':>16}"
              f"{data['calls']:>23}{data['tokens']:>10}")

    head("КАК ВЫГЛЯДИТ ОБМЕН")
    agent = agents.create({**SETTINGS, "name": "Показ", "use_tools": True})
    reply = agent.ask("Что было сделано в задании 14 и какие файлы оно добавило?")
    print("  вы → Что было сделано в задании 14 и какие файлы оно добавило?")
    for call in reply["tool_calls"]:
        print(f"  ↳ круг {call['round']}: {call['name']}({call['arguments']})")
        print(f"      результат: {call['result'][:150].replace(chr(10), ' ')}…")
    print(f"  агент ← {reply['text'].strip()[:400]}")
    print(f"\n  кругов вызовов: {agent.stats['tool_rounds']}, "
          f"вызовов: {agent.stats['tool_calls']}")

    mcp_tools.BRIDGE.close()
    storage.DB_PATH.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
