"""Сценарий: два способа собрать одну и ту же цепочку из трёх MCP-инструментов.

    python3 pipeline_demo.py

Первый способ — детерминированный раннер: порядок задан в коде.
Второй — агент: те же три инструмента отданы модели, и она собирает цепочку сама.
Сравниваем, правильно ли переданы идентификаторы между шагами, и чего это стоит.
"""

import os
import pathlib
import re
import sys
import tempfile
import time

# Своя база и свои снимки: демонстрация не должна мешать рабочему мониторингу.
# Через окружение, а не присваиванием: MCP-сервер поднимается отдельным процессом.
TEMP = pathlib.Path(tempfile.gettempdir())
os.environ["ADVENT_PIPELINE_DB"] = str(TEMP / "pipeline_demo.db")
os.environ["ADVENT_SNAPSHOTS"] = str(TEMP / "pipeline_demo.jsonl")
os.environ["ADVENT_REPORTS"] = str(TEMP / "pipeline_demo_reports")

import pipeline            # noqa: E402
import pipeline_server     # noqa: E402
import store               # noqa: E402
import tools as mcp_tools  # noqa: E402

TASK = ("Проверь, не изменилось ли предложение билетов на матч. "
        "Если изменилось — сохрани отчёт и отправь уведомление. "
        "Действуй инструментами по порядку, передавая идентификаторы между шагами.")

SETTINGS = {
    "model": "deepseek-chat",
    "temperature": 0.0,
    "max_tokens": 500,
    "router": False,
    "task_advisor": False,
    "audit": False,
    "use_invariants": False,
    "keep_last": 0,
    "max_tool_rounds": 5,
    "system_prompt": ("Ты оператор мониторинга. Пользуйся инструментами строго по "
                      "порядку: шаг 1 даёт snapshot_id, шаг 2 принимает его и даёт "
                      "report_id, шаг 3 принимает report_id. Не выдумывай "
                      "идентификаторы — бери их из ответов инструментов."),
}


def head(title):
    print(f"\n{'=' * 76}\n{title}\n{'=' * 76}")


def main():
    for path in (TEMP / "pipeline_demo.db", TEMP / "pipeline_demo.jsonl"):
        path.unlink(missing_ok=True)
    store.init()

    head("ИНСТРУМЕНТЫ ЦЕПОЧКИ")
    info = mcp_tools.BRIDGE.info()
    if info["error"]:
        print(f"  сервер недоступен: {info['error']}")
        return 1
    print(f"  сервер: {info['server'].get('name')} {info['server'].get('version')} · "
          f"протокол {info['protocol']}\n")
    for tool in info["tools"]:
        schema = tool["schema"]
        required = set(schema.get("required") or [])
        args = ", ".join(f"{name}{'*' if name in required else ''}"
                         for name in (schema.get("properties") or {})) or "—"
        print(f"    {tool['name']}({args})")
        print(f"      {' '.join(tool['description'].split())[:92]}")

    head("СПОСОБ 1. Детерминированный раннер")
    print("  Порядок задан в коде, вызовы идут через MCP-протокол.\n")
    started = time.monotonic()
    runner = pipeline.run(notify_on_change=False, verbose=True)
    runner_seconds = round(time.monotonic() - started, 2)
    print(f"\n  снимок {runner['snapshot_id']} → отчёт {runner['report_id']}, "
          f"изменения: {'да' if runner['changed'] else 'нет'}")

    head("СПОСОБ 2. Цепочку собирает агент")
    print(f"  задача: {TASK}\n")
    import agent as agents
    agents.storage.DB_PATH = TEMP / "pipeline_demo_agent.db"
    agents.storage.DB_PATH.unlink(missing_ok=True)
    agents.storage.init()

    agent = agents.create({**SETTINGS, "name": "Оператор"})
    started = time.monotonic()
    reply = agent.ask(TASK)
    agent_seconds = round(time.monotonic() - started, 2)

    calls = reply["tool_calls"]
    for call in calls:
        print(f"  🔧 круг {call['round']}: {call['name']}({call['arguments']})")
        print(f"      → {call['result'].splitlines()[0][:80]}")
    print(f"\n  агент ← {reply['text'].strip()[:280]}")

    head("КОРРЕКТНОСТЬ ПЕРЕДАЧИ ДАННЫХ")
    # Проверяем не «сработало ли», а именно то, что во второй шаг уехал
    # идентификатор из первого, а в третий — из второго.
    order = [call["name"] for call in calls]
    expected = ["tickets_fetch", "changes_summarize", "report_save"]
    print(f"  порядок вызовов: {' → '.join(order) or '(вызовов нет)'}")
    print(f"  ожидался:        {' → '.join(expected)}")
    print(f"  порядок верный: {order[:3] == expected}")

    produced = {}
    passed_ok = True
    for call in calls:
        if call["name"] == "tickets_fetch":
            match = re.search(r"snapshot_id:\s*(\S+)", call["result"])
            produced["snapshot_id"] = match.group(1) if match else None
        elif call["name"] == "changes_summarize":
            given = call["arguments"].get("snapshot_id")
            ok = given == produced.get("snapshot_id")
            passed_ok &= ok
            print(f"  шаг 2 получил snapshot_id={given} · совпадает с выданным шагом 1: {ok}")
            match = re.search(r"report_id:\s*(\S+)", call["result"])
            produced["report_id"] = match.group(1) if match else None
        elif call["name"] == "report_save":
            given = call["arguments"].get("report_id")
            ok = given == produced.get("report_id")
            passed_ok &= ok
            print(f"  шаг 3 получил report_id={given} · совпадает с выданным шагом 2: {ok}")

    print(f"  идентификаторы протащены без подмен: {passed_ok}")
    print(f"  выдуманных идентификаторов: "
          f"{sum(1 for call in calls if 'не найден' in call['result'])}")

    head("ЗАЩИТА ОТ НЕВЕРНЫХ СВЯЗЕЙ")
    print("  Шаги связаны только идентификаторами, поэтому неверную связь видно сразу.\n")
    for name, arguments in (
        ("changes_summarize", {"snapshot_id": "snap-выдуманный"}),
        ("changes_summarize", {"snapshot_id": runner["report_id"]}),
        ("report_save", {"report_id": runner["snapshot_id"]}),
        ("report_save", {}),
    ):
        try:
            pipeline_server.run_tool(name, arguments)
            print(f"  {name}: ПРОШЛО, хотя не должно")
        except ValueError as error:
            print(f"  {name}: отказ — {error}")

    head("СРАВНЕНИЕ")
    print(f"{'способ':<24}{'вызовов':>9}{'порядок':>10}{'секунд':>9}{'токенов':>10}")
    print(f"{'раннер в коде':<24}{3:>9}{'задан':>10}{runner_seconds:>9}{0:>10}")
    print(f"{'агент':<24}{len(calls):>9}"
          f"{('верный' if order[:3] == expected else 'сбился'):>10}"
          f"{agent_seconds:>9}"
          f"{agent.stats['prompt_tokens'] + agent.stats['completion_tokens']:>10}")

    mcp_tools.BRIDGE.close()
    for path in (TEMP / "pipeline_demo.db", TEMP / "pipeline_demo.jsonl",
                 TEMP / "pipeline_demo_agent.db"):
        path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
