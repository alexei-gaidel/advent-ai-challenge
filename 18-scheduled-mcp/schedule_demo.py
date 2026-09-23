"""Сценарий: инструмент с расписанием, ускоренный до одной минуты.

    python3 schedule_demo.py

Восемь часов ждать неинтересно, поэтому задания заводятся с интервалами в секундах.
Логика планировщика при этом настоящая: та же, что уедет в GitHub Actions.
"""

import pathlib
import sys
import tempfile
import time

import os

# Отдельные хранилища, чтобы демонстрация не мешалась с рабочими данными.
# Через окружение, а не присваиванием: MCP-сервер поднимается отдельным процессом
# и должен видеть те же файлы, иначе агент ответит по другим данным.
TEMP = pathlib.Path(tempfile.gettempdir())
os.environ["ADVENT_SCHEDULE_DB"] = str(TEMP / "schedule_demo.db")
os.environ["ADVENT_SAMPLES"] = str(TEMP / "schedule_demo.jsonl")

import store          # noqa: E402  (импорт после подмены путей)

import digest          # noqa: E402
import market_server   # noqa: E402
import scheduler       # noqa: E402
import tools as mcp_tools  # noqa: E402

COLLECT_EVERY = 4      # вместо часа
DIGEST_EVERY = 12      # вместо восьми часов
DURATION = 26          # столько секунд крутим планировщик


def head(title):
    print(f"\n{'=' * 76}\n{title}\n{'=' * 76}")


def main():
    store.DB_PATH.unlink(missing_ok=True)
    store.SAMPLES_PATH.unlink(missing_ok=True)
    store.init()

    head("РАСПИСАНИЕ")
    scheduler.setup(interval=COLLECT_EVERY, digest_every=DIGEST_EVERY)
    store.add_job("remind-demo", "remind", 0, {"text": "проверить сводку за день"},
                  first_run=store.now() + store.timedelta(seconds=9))
    for job in store.list_jobs():
        period = f"каждые {job['every']} c" if job["every"] else "одноразовое"
        print(f"  {job['name']:<13} {job['kind']:<8} {period:<16} "
              f"следующий запуск {job['next_run'][11:19]}")
    print(f"\n  в реальной жизни: наблюдение раз в час, сводка раз в 8 часов;\n"
          f"  здесь {COLLECT_EVERY} и {DIGEST_EVERY} секунд, чтобы всё уместилось в минуту")

    head(f"ПЛАНИРОВЩИК РАБОТАЕТ {DURATION} СЕКУНД")
    started = time.monotonic()
    while time.monotonic() - started < DURATION:
        done = scheduler.tick(verbose=False)
        for item in done:
            mark = "✓" if item["status"] == "ok" else "✕"
            print(f"  {int(time.monotonic() - started):>3} c  {mark} {item['job']:<13} "
                  f"{item['detail'][:64]}", flush=True)
        time.sleep(0.5)

    head("ЖУРНАЛ ЗАПУСКОВ")
    stats = store.runs_stats()
    print(f"  всего {stats['total']}, с ошибкой {stats['errors']}\n")
    for run in reversed(store.list_runs(20)):
        print(f"  {run['at'][11:19]}  {run['job']:<13} {run['status']:<5} "
              f"{run['detail'][:60]}")

    print("\n  одноразовое напоминание после срабатывания:",
          "активно" if any(job["name"] == "remind-demo" for job in store.list_jobs())
          else "выключено")

    head("АГРЕГИРОВАННЫЙ РЕЗУЛЬТАТ")
    print(digest.as_text(digest.build("24h")))

    head("ИНСТРУМЕНТЫ MCP")
    info = mcp_tools.BRIDGE.info()
    if info["error"]:
        print(f"  сервер недоступен: {info['error']}")
    else:
        print(f"  сервер: {info['server'].get('name')} {info['server'].get('version')} · "
              f"протокол {info['protocol']}")
        for tool in info["tools"]:
            schema = tool["schema"]
            required = set(schema.get("required") or [])
            args = ", ".join(f"{n}{'*' if n in required else ''}"
                             for n in (schema.get("properties") or {})) or "—"
            print(f"    {tool['name']}({args})")

    head("АГЕНТ СПРАШИВАЕТ СВОДКУ")
    import agent as agents     # импорт здесь: агенту нужна своя база
    agents.storage.DB_PATH = pathlib.Path(tempfile.gettempdir()) / "schedule_demo_agent.db"
    agents.storage.DB_PATH.unlink(missing_ok=True)
    agents.storage.init()

    agent = agents.create({
        "model": "deepseek-chat", "temperature": 0.0, "max_tokens": 400,
        "router": False, "task_advisor": False, "audit": False,
        "use_invariants": False, "keep_last": 0, "name": "Рынок",
        "system_prompt": ("Ты следишь за рынком. Отвечай кратко, цифрами из инструментов, "
                          "ничего не выдумывай.")})
    question = "Что с рынком за сутки? Назови, что выросло, а что упало."
    reply = agent.ask(question)
    print(f"  вы → {question}")
    for call in reply["tool_calls"]:
        print(f"  🔧 {call['name']}({call['arguments']})")
    print(f"  агент ← {reply['text'].strip()[:420]}")

    mcp_tools.BRIDGE.close()
    store.DB_PATH.unlink(missing_ok=True)
    store.SAMPLES_PATH.unlink(missing_ok=True)
    agents.storage.DB_PATH.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
