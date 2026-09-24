"""MCP-сервер с тремя инструментами, из которых собирается пайплайн.

    tickets_fetch       получает данные  → snapshot_id
    changes_summarize   обрабатывает     → report_id
    report_save         сохраняет        → путь к файлу (и уведомление)

Шаги связаны только идентификаторами: второй без snapshot_id работать не может,
третий принимает исключительно report_id. Поэтому «цепочка сработала» —
проверяемый факт, а не впечатление: подставили чужой идентификатор — получили отказ.

    python3 pipeline_server.py              # ждёт JSON-RPC на stdin
    python3 pipeline_server.py --selftest   # прогон инструментов без клиента
"""

import json
import sys

import notify
import store
import tickets

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "advent-pipeline-server", "version": "1.0.0"}

TOOLS = [
    {
        "name": "tickets_fetch",
        "description": ("ШАГ 1 из 3. Снимает страницу события на Яндекс Афише и сохраняет "
                        "снимок предложения: категории билетов, цены, статус продаж. "
                        "Возвращает snapshot_id, который нужен шагу 2."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string",
                        "description": "Адрес страницы события; по умолчанию матч Россия — Намибия"},
            },
            "required": [],
        },
    },
    {
        "name": "changes_summarize",
        "description": ("ШАГ 2 из 3. Сравнивает снимок с предыдущим и описывает изменения: "
                        "подешевел ли минимальный билет, добавились ли категории, "
                        "изменился ли статус продаж. Возвращает report_id для шага 3."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "snapshot_id": {"type": "string",
                                "description": "Идентификатор снимка, полученный на шаге 1"},
            },
            "required": ["snapshot_id"],
        },
    },
    {
        "name": "report_save",
        "description": ("ШАГ 3 из 3. Сохраняет отчёт в файл и, если включено уведомление "
                        "и есть изменения, отправляет его в Telegram. "
                        "Принимает report_id, полученный на шаге 2."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "report_id": {"type": "string",
                              "description": "Идентификатор отчёта, полученный на шаге 2"},
                "notify": {"type": "boolean", "default": False,
                           "description": "Отправить в Telegram, если есть изменения"},
            },
            "required": ["report_id"],
        },
    },
    {
        "name": "pipeline_runs",
        "description": "Журнал прогонов пайплайна: когда выполнялся и что нашёл.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10,
                          "description": "Сколько последних записей вернуть"},
            },
            "required": [],
        },
    },
]


# --- сравнение снимков ----------------------------------------------------------

def compare(current, previous):
    """Список изменений между снимками. Пустой список — предложение не менялось."""
    if previous is None:
        return [f"первый снимок: {tickets.describe(current)}"]

    changes = []
    was, now_ = previous.get("min_price"), current.get("min_price")
    if was != now_:
        if was is not None and now_ is not None and now_ < was:
            changes.append(f"минимальная цена упала: {tickets.format_price(was)} → "
                           f"{tickets.format_price(now_)} — похоже на новую партию")
        else:
            changes.append(f"минимальная цена изменилась: {tickets.format_price(was)} → "
                           f"{tickets.format_price(now_)}")

    if previous.get("max_price") != current.get("max_price"):
        changes.append(f"максимальная цена: {tickets.format_price(previous.get('max_price'))} → "
                       f"{tickets.format_price(current.get('max_price'))}")

    if previous.get("offers_count") != current.get("offers_count"):
        changes.append(f"категорий билетов: {previous.get('offers_count')} → "
                       f"{current.get('offers_count')}")

    if previous.get("on_sale") != current.get("on_sale"):
        changes.append("продажа открылась" if current.get("on_sale") else "продажа закрылась")

    return changes


def summary_text(current, changes):
    """Человеческий текст отчёта — он же уходит в Telegram."""
    lines = [tickets.EVENT_TITLE,
             f"проверено {current['at'][:16].replace('T', ' ')} UTC", ""]
    if changes:
        lines.append("Изменения:")
        lines += [f"• {change}" for change in changes]
        lines.append("")
    else:
        lines.append("Изменений нет.")
        lines.append("")

    lines.append("Сейчас в продаже:")
    if current["offers"]:
        for offer in current["offers"]:
            price = (tickets.format_price(offer["min_price"])
                     if offer["min_price"] == offer["max_price"]
                     else f"{tickets.format_price(offer['min_price'])} – "
                          f"{tickets.format_price(offer['max_price'])}")
            lines.append(f"• {price} · {offer['sale_status']}")
    else:
        lines.append("• предложений не найдено")

    lines.append("")
    lines.append(current["url"])
    return "\n".join(lines)


# --- инструменты ----------------------------------------------------------------

def run_tool(name, arguments):
    if name == "tickets_fetch":
        snapshot = tickets.fetch(arguments.get("url") or None)
        snapshot_id = store.save_snapshot(snapshot)
        return (f"snapshot_id: {snapshot_id}\n{tickets.describe(snapshot)}\n"
                f"Передай snapshot_id шагу 2 (changes_summarize).")

    if name == "changes_summarize":
        snapshot_id = str(arguments.get("snapshot_id", "")).strip()
        if not snapshot_id:
            raise ValueError("нужен snapshot_id — его возвращает шаг 1 (tickets_fetch)")
        if snapshot_id.startswith("rep-"):
            raise ValueError("это идентификатор отчёта, а шагу 2 нужен снимок "
                             "(snap-…) от tickets_fetch")

        current = store.get_snapshot(snapshot_id)
        if not current:
            raise ValueError(f"снимок {snapshot_id} не найден: сначала выполни шаг 1")

        previous = store.previous_snapshot(snapshot_id)
        changes = compare(current, previous)
        report_id = store.save_report({
            "snapshot_id": snapshot_id,
            "changed": bool(changes),
            "summary": summary_text(current, changes),
            "changes": changes,
        })
        head = "изменения есть" if changes else "изменений нет"
        return (f"report_id: {report_id}\nchanged: {str(bool(changes)).lower()} — {head}\n"
                + ("\n".join(f"• {change}" for change in changes) if changes else "")
                + f"\nПередай report_id шагу 3 (report_save).")

    if name == "report_save":
        report_id = str(arguments.get("report_id", "")).strip()
        if not report_id:
            raise ValueError("нужен report_id — его возвращает шаг 2 (changes_summarize)")
        if report_id.startswith("snap-"):
            raise ValueError("это идентификатор снимка, а шагу 3 нужен отчёт "
                             "(rep-…) от changes_summarize")

        report = store.get_report(report_id)
        if not report:
            raise ValueError(f"отчёт {report_id} не найден: сначала выполни шаг 2")

        path = store.REPORTS_DIR / f"{report_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report["summary"] + "\n", encoding="utf-8")
        store.mark_report_saved(report_id, path)

        lines = [f"отчёт сохранён: {path.name}"]
        if arguments.get("notify"):
            if report["changed"]:
                lines.append(notify.send(report["summary"]))
            else:
                lines.append("уведомление не отправлено: изменений нет")
        return "\n".join(lines)

    if name == "pipeline_runs":
        limit = max(1, min(50, int(arguments.get("limit", 10))))
        runs = store.list_runs(limit)
        if not runs:
            return "журнал пуст: пайплайн ещё не запускался"
        stats = store.runs_stats()
        return (f"всего прогонов {stats['total']}, с ошибкой {stats['errors']}\n"
                + "\n".join(f"{run['at'][:19].replace('T', ' ')}  {run['job']:<10} "
                            f"{run['status']:<5} {run['detail'][:70]}" for run in runs))

    raise ValueError(f"неизвестный инструмент: {name}")


# --- протокол -------------------------------------------------------------------

def handle(message):
    method = message.get("method", "")
    request_id = message.get("id")
    if request_id is None:
        return None

    if method == "initialize":
        return _ok(request_id, {"protocolVersion": PROTOCOL_VERSION,
                                "capabilities": {"tools": {"listChanged": False}},
                                "serverInfo": SERVER_INFO})
    if method == "tools/list":
        return _ok(request_id, {"tools": TOOLS})
    if method == "ping":
        return _ok(request_id, {})

    if method == "tools/call":
        params = message.get("params") or {}
        try:
            text = run_tool(params.get("name"), params.get("arguments") or {})
        except (ValueError, RuntimeError) as error:
            return _ok(request_id, {"content": [{"type": "text", "text": f"Ошибка: {error}"}],
                                    "isError": True})
        return _ok(request_id, {"content": [{"type": "text", "text": text}], "isError": False})

    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": f"метод не поддерживается: {method}"}}


def _ok(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve():
    store.init()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            print("пропущена строка, это не JSON", file=sys.stderr)
            continue
        reply = handle(message)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def selftest():
    store.init()
    print("→ шаг 1: tickets_fetch")
    first = run_tool("tickets_fetch", {})
    print("  " + first.replace("\n", "\n  "))
    snapshot_id = first.split("snapshot_id: ")[1].split("\n")[0]

    print("\n→ шаг 2: changes_summarize")
    second = run_tool("changes_summarize", {"snapshot_id": snapshot_id})
    print("  " + second.replace("\n", "\n  "))
    report_id = second.split("report_id: ")[1].split("\n")[0]

    print("\n→ шаг 3: report_save")
    print("  " + run_tool("report_save", {"report_id": report_id, "notify": True}))

    print("\n=== проверка связей между шагами ===")
    for name, arguments in (
        ("changes_summarize", {"snapshot_id": "snap-выдуманный"}),
        ("changes_summarize", {"snapshot_id": report_id}),
        ("report_save", {"report_id": snapshot_id}),
        ("report_save", {"report_id": "rep-выдуманный"}),
        ("changes_summarize", {}),
    ):
        try:
            run_tool(name, arguments)
            print(f"  {name}({arguments}) — ПРОШЛО, хотя не должно")
        except ValueError as error:
            print(f"  {name}: отказ — {error}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        serve()
