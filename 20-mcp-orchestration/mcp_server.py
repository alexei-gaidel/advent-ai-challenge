"""Общая обвязка MCP-сервера: JSON-RPC построчно через stdin/stdout.

В дне 19 рукопожатие и разбор сообщений жили прямо в сервере. Здесь серверов три,
поэтому протокол вынесен сюда, а каждый сервер описывает только свои инструменты:

    TOOLS = [{"name": ..., "description": ..., "inputSchema": {...}}, ...]
    def run_tool(name, arguments) -> str      # ValueError = понятная ошибка инструмента

    if __name__ == "__main__":
        mcp_server.main(SERVER_INFO, TOOLS, run_tool, selftest)
"""

import json
import sys

PROTOCOL_VERSION = "2025-06-18"


def handle(message, server_info, tools, run_tool):
    """Ответ на одно сообщение JSON-RPC. Уведомления ответа не получают."""
    method = message.get("method")
    request_id = message.get("id")

    if request_id is None:
        return None

    if method == "initialize":
        return _ok(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": server_info,
        })

    if method == "tools/list":
        return _ok(request_id, {"tools": tools})

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


def serve(server_info, tools, run_tool):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            print("пропущена строка, это не JSON", file=sys.stderr)
            continue
        try:
            reply = handle(message, server_info, tools, run_tool)
        except Exception as error:  # сервер не должен падать из-за одного вызова
            reply = {"jsonrpc": "2.0", "id": message.get("id"),
                     "error": {"code": -32603, "message": f"внутренняя ошибка: {error}"}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main(server_info, tools, run_tool, selftest):
    if "--selftest" in sys.argv:
        selftest()
    else:
        serve(server_info, tools, run_tool)


def require(arguments, name):
    """Обязательный строковый аргумент."""
    value = arguments.get(name)
    if value in (None, ""):
        raise ValueError(f"не передан обязательный аргумент {name}")
    return str(value).strip()


def refuse(label, run_tool, name, arguments):
    """Для самопроверки: вызов обязан закончиться отказом."""
    try:
        run_tool(name, arguments)
        print(f"  {label}: ПРОШЛО, хотя не должно")
    except ValueError as error:
        print(f"  {label}: отказ — {error}")
