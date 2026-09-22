"""Минимальный MCP-сервер на стандартной библиотеке, транспорт stdio.

Читает JSON-RPC построчно из stdin, отвечает в stdout. Всё, что нужно напечатать
для человека, идёт в stderr: любой посторонний байт в stdout ломает протокол.

Инструменты здесь намеренно простые — это каркас. В задании 17 на нём вырастут
инструменты вокруг git.

    python3 mcp_server.py              # обычный режим: ждёт JSON-RPC на stdin
    python3 mcp_server.py --selftest   # прогон трёх сообщений без клиента
"""

import json
import sys
from datetime import datetime, timezone

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "advent-demo-server", "version": "0.1.0"}

# Регистрация инструмента = имя, описание для модели и схема аргументов.
# Схема — обычный JSON Schema: по ней клиент понимает, что передавать.
TOOLS = [
    {
        "name": "ping",
        "description": "Проверка связи: возвращает pong и версию сервера.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "echo",
        "description": "Возвращает переданный текст без изменений.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Что вернуть обратно"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "now",
        "description": "Текущее время сервера в выбранном формате.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "description": "Формат вывода времени",
                    "enum": ["iso", "date", "time", "unix"],
                    "default": "iso",
                },
            },
            "required": [],
        },
    },
]


def run_tool(name, arguments):
    """Выполнение инструмента. Возвращает текст результата."""
    if name == "ping":
        return f"pong · {SERVER_INFO['name']} {SERVER_INFO['version']}"

    if name == "echo":
        text = arguments.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("нужен непустой аргумент text")
        return text

    if name == "now":
        moment = datetime.now(timezone.utc)
        shape = arguments.get("format", "iso")
        if shape == "date":
            return moment.strftime("%Y-%m-%d")
        if shape == "time":
            return moment.strftime("%H:%M:%S")
        if shape == "unix":
            return str(int(moment.timestamp()))
        return moment.isoformat(timespec="seconds")

    raise ValueError(f"неизвестный инструмент: {name}")


def handle(message):
    """Обработка одного сообщения JSON-RPC. None — отвечать не нужно."""
    method = message.get("method", "")
    request_id = message.get("id")

    # Уведомления приходят без id и ответа не требуют.
    if request_id is None:
        return None

    if method == "initialize":
        return _ok(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        })

    if method == "tools/list":
        return _ok(request_id, {"tools": TOOLS})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        try:
            text = run_tool(name, params.get("arguments") or {})
        except ValueError as error:
            # Ошибка инструмента — не ошибка протокола: возвращаем isError.
            return _ok(request_id, {
                "content": [{"type": "text", "text": f"Ошибка: {error}"}],
                "isError": True,
            })
        return _ok(request_id, {"content": [{"type": "text", "text": text}],
                                "isError": False})

    if method == "ping":
        return _ok(request_id, {})

    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": f"метод не поддерживается: {method}"}}


def _ok(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(stream_in=sys.stdin, stream_out=sys.stdout):
    for line in stream_in:
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
            stream_out.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stream_out.flush()


def selftest():
    """Прогон протокола без клиента — удобно, когда что-то не сходится."""
    for message in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": PROTOCOL_VERSION}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "echo", "arguments": {"text": "привет"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "echo", "arguments": {}}},
    ):
        reply = handle(message)
        print(f"→ {message.get('method')}")
        print(f"← {json.dumps(reply, ensure_ascii=False)[:160] if reply else '(ответа нет)'}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        serve()
