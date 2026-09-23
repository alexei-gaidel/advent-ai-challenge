"""MCP-сервер над периодическим сбором данных.

Даёт агенту доступ к тому, что накопил планировщик: свежую сводку, ряд наблюдений,
расписание и журнал запусков. Плюс два действия — снять наблюдение прямо сейчас
и завести отложенное напоминание.

    python3 market_server.py              # ждёт JSON-RPC на stdin
    python3 market_server.py --selftest   # прогон инструментов без клиента
"""

import json
import sys

import digest
import market
import scheduler
import store

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "advent-market-server", "version": "1.0.0"}

TOOLS = [
    {
        "name": "market_digest",
        "description": ("Агрегированная сводка по курсам криптовалют за период: цена, "
                        "изменение за сутки и с начала дня, коридор цен, число наблюдений."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "Период агрегации",
                           "enum": list(digest.PERIODS), "default": "24h"},
            },
            "required": [],
        },
    },
    {
        "name": "market_history",
        "description": "Ряд наблюдений по одному инструменту: время и цена.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string",
                           "description": "Инструмент: BTC, ETH, LTC, XMR или пара вида BTCUSDT"},
                "limit": {"type": "integer", "description": "Сколько последних наблюдений",
                          "minimum": 1, "maximum": 200, "default": 20},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "collect_now",
        "description": "Снять наблюдение с биржи немедленно, не дожидаясь расписания.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_jobs",
        "description": "Задания планировщика: вид, периодичность, время следующего запуска.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_runs",
        "description": ("Журнал запусков планировщика: когда сработало задание, "
                        "с каким результатом. По нему видно, соблюдалось ли расписание."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Сколько последних записей",
                          "minimum": 1, "maximum": 100, "default": 10},
            },
            "required": [],
        },
    },
    {
        "name": "schedule_reminder",
        "description": ("Отложенное напоминание: сработает один раз через указанное "
                        "число минут и выключится."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Текст напоминания"},
                "after_minutes": {"type": "number", "minimum": 0.1, "maximum": 10080,
                                  "description": "Через сколько минут напомнить"},
            },
            "required": ["text", "after_minutes"],
        },
    },
]


def run_tool(name, arguments):
    if name == "market_digest":
        period = arguments.get("period", "24h")
        if period not in digest.PERIODS:
            raise ValueError(f"период должен быть одним из: {', '.join(digest.PERIODS)}")
        return digest.as_text(digest.build(period))

    if name == "market_history":
        symbol = str(arguments.get("symbol", "")).strip().upper()
        if not symbol:
            raise ValueError("нужен аргумент symbol")
        if not symbol.endswith("USDT"):
            symbol_pair = f"{symbol}USDT"
        else:
            symbol_pair = symbol

        limit = int(arguments.get("limit", 20))
        rows = store.read(symbol=symbol_pair)[-max(1, min(200, limit)):]
        if not rows:
            return f"наблюдений по {symbol} пока нет"
        lines = [f"{row['at'][:16].replace('T', ' ')}  "
                 f"{market.format_price(row['price'])}  {row['change_24h']:+.2f}%"
                 for row in rows]
        return f"{symbol_pair}, последних наблюдений {len(rows)}:\n" + "\n".join(lines)

    if name == "collect_now":
        samples = market.fetch()
        store.append(samples)
        store.log_run("collect_now", "collect", "ok", f"наблюдений: {len(samples)}")
        return ("Снято наблюдение: " +
                ", ".join(f"{s['title']} {market.format_price(s['price'])}"
                          for s in samples))

    if name == "list_jobs":
        jobs = store.list_jobs()
        if not jobs:
            return "заданий нет — запусти scheduler.py --setup"
        return "\n".join(
            f"{job['name']:<12} {job['kind']:<8} "
            + (f"каждые {job['every']} c" if job["every"] else "одноразовое")
            + f" · следующий запуск {job['next_run'][:19].replace('T', ' ')}"
            for job in jobs)

    if name == "list_runs":
        limit = max(1, min(100, int(arguments.get("limit", 10))))
        runs = store.list_runs(limit)
        if not runs:
            return "журнал пуст: планировщик ещё не отработал"
        stats = store.runs_stats()
        lines = [f"{run['at'][:19].replace('T', ' ')}  {run['job']:<12} "
                 f"{run['status']:<5} {run['detail'][:70]}" for run in runs]
        return (f"всего запусков {stats['total']}, из них с ошибкой {stats['errors']}\n"
                + "\n".join(lines))

    if name == "schedule_reminder":
        text = str(arguments.get("text", "")).strip()
        if not text:
            raise ValueError("нужен аргумент text")
        try:
            minutes = float(arguments.get("after_minutes"))
        except (TypeError, ValueError):
            raise ValueError("after_minutes должен быть числом")
        if not 0.1 <= minutes <= 10080:
            raise ValueError("after_minutes: допустимо от 0.1 до 10080 минут")

        name_id = f"remind-{int(store.now().timestamp())}"
        store.add_job(name_id, "remind", 0, {"text": text},
                      first_run=store.now() + store.timedelta(minutes=minutes))
        return (f"Напоминание «{text}» заведено, сработает один раз через "
                f"{minutes:g} мин (задание {name_id})")

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
    if not store.list_jobs():
        scheduler.setup()
    for name, arguments in (
        ("list_jobs", {}),
        ("collect_now", {}),
        ("market_digest", {"period": "24h"}),
        ("market_history", {"symbol": "BTC", "limit": 3}),
        ("list_runs", {"limit": 3}),
        ("schedule_reminder", {"text": "проверить сводку", "after_minutes": 0.2}),
        ("schedule_reminder", {"text": "", "after_minutes": 5}),
        ("market_digest", {"period": "век"}),
    ):
        print(f"→ {name} {json.dumps(arguments, ensure_ascii=False)}")
        try:
            print("  " + run_tool(name, arguments)[:300].replace("\n", "\n  "))
        except ValueError as error:
            print(f"  отказ: {error}")
        print()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        serve()
