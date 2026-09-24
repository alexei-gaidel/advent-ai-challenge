"""Детерминированный раннер пайплайна.

Вызывает три инструмента по очереди **через MCP-клиент**, а не напрямую функции:
так проверяется настоящая композиция через протокол, а не вызовы внутри процесса.
Печатает, какой идентификатор уехал в следующий шаг, и пишет прогон в журнал.

    python3 pipeline.py --once            # один проход цепочки
    python3 pipeline.py --once --notify   # с отправкой в Telegram при изменениях
    python3 pipeline.py --quiet           # без печати шагов (для расписания)
"""

import re
import sys
import time

import store
from mcp_client import MCPClient, MCPError

SERVER = [sys.executable, "pipeline_server.py"]


def extract(text, field):
    """Достаёт значение вида «field: value» из ответа инструмента."""
    match = re.search(rf"^{field}:\s*(\S+)", text, re.M)
    return match.group(1) if match else None


def tool_text(result):
    parts = [item.get("text", "") for item in result.get("content", [])
             if item.get("type") == "text"]
    text = "\n".join(part for part in parts if part).strip()
    if result.get("isError"):
        raise RuntimeError(text or "инструмент вернул ошибку")
    return text


def run(notify_on_change=False, verbose=True):
    """Три шага цепочки. Возвращает описание прогона."""
    store.init()
    started = time.monotonic()
    steps = []

    client = MCPClient.stdio("pipeline", SERVER)
    try:
        client.connect()

        # Шаг 1: получить данные.
        first = tool_text(client.call_tool("tickets_fetch", {}))
        snapshot_id = extract(first, "snapshot_id")
        if not snapshot_id:
            raise RuntimeError("шаг 1 не вернул snapshot_id")
        steps.append({"tool": "tickets_fetch", "out": snapshot_id})
        if verbose:
            print(f"  1. tickets_fetch      → snapshot_id={snapshot_id}")
            print(f"     {first.splitlines()[1]}")

        # Шаг 2: обработать. Вход — ровно то, что вернул шаг 1.
        second = tool_text(client.call_tool("changes_summarize",
                                           {"snapshot_id": snapshot_id}))
        report_id = extract(second, "report_id")
        changed = extract(second, "changed") == "true"
        if not report_id:
            raise RuntimeError("шаг 2 не вернул report_id")
        steps.append({"tool": "changes_summarize", "in": snapshot_id, "out": report_id})
        if verbose:
            print(f"  2. changes_summarize  ← snapshot_id={snapshot_id}")
            print(f"                        → report_id={report_id}, changed={changed}")
            for line in second.splitlines():
                if line.startswith("•"):
                    print(f"     {line}")

        # Шаг 3: сохранить. Вход — ровно то, что вернул шаг 2.
        third = tool_text(client.call_tool("report_save",
                                           {"report_id": report_id,
                                            "notify": bool(notify_on_change)}))
        steps.append({"tool": "report_save", "in": report_id})
        if verbose:
            print(f"  3. report_save        ← report_id={report_id}")
            for line in third.splitlines():
                print(f"     {line}")

    finally:
        client.close()

    seconds = round(time.monotonic() - started, 2)
    detail = (f"снимок {snapshot_id}, отчёт {report_id}, "
              f"изменения: {'да' if changed else 'нет'}")
    store.log_run("pipeline", "pipeline", "ok", detail)
    return {"steps": steps, "snapshot_id": snapshot_id, "report_id": report_id,
            "changed": changed, "seconds": seconds, "detail": detail}


def main():
    args = sys.argv[1:]
    verbose = "--quiet" not in args
    notify_on_change = "--notify" in args

    if verbose:
        print("пайплайн: получить → обработать → сохранить")
    try:
        result = run(notify_on_change=notify_on_change, verbose=verbose)
    except (MCPError, RuntimeError) as error:
        store.init()
        store.log_run("pipeline", "pipeline", "error", str(error))
        print(f"пайплайн упал: {error}")
        return 1

    if verbose:
        print(f"\nготово за {result['seconds']} c · {result['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
