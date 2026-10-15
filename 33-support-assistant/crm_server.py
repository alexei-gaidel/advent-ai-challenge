"""MCP-сервер CRM поддержки: пользователи, их ключи, журнал шлюза и тикеты.

Каркас — stdio JSON-RPC 2.0 из дней 16–17. Данные — JSON-файл:
    data/crm.json        исходные данные (в git);
    data/crm_live.json   рабочая копия: в неё пишутся заметки и статусы (в .gitignore),
                         создаётся из crm.json при первой записи.

Инструменты чтения доступны модели. add_note — запись в тикет — сервер тоже отдаёт, но
ассистент не передаёт его модели: заметку сохраняет оператор кнопкой (см. assistant.py).

    python3 crm_server.py                  # ждёт JSON-RPC на stdin
    python3 crm_server.py --selftest
    python3 crm_server.py --reset          # удалить рабочую копию, вернуться к crm.json
"""

import datetime
import json
import os
import pathlib
import sys
import threading

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "advent-support-crm", "version": "1.0.0"}

HERE = pathlib.Path(__file__).resolve().parent
SEED = HERE / "data" / "crm.json"
LIVE = pathlib.Path(os.environ.get("ADVENT_CRM") or HERE / "data" / "crm_live.json")
STATUSES = ("новый", "в работе", "ждём клиента", "закрыт")
LOCK = threading.Lock()

TOOLS = [
    {
        "name": "get_ticket",
        "description": "Тикет поддержки: тема, текст, вложение, статус, заметки, id пользователя.",
        "inputSchema": {"type": "object", "properties": {
            "ticket_id": {"type": "string", "description": "Например T-101"}},
            "required": ["ticket_id"]},
    },
    {
        "name": "get_user",
        "description": ("Карточка пользователя: тариф, сеть, ключи (маска, лимит, статус, "
                        "когда и почему менялся)."),
        "inputSchema": {"type": "object", "properties": {
            "user_id": {"type": "string", "description": "Например u-01"}},
            "required": ["user_id"]},
    },
    {
        "name": "get_events",
        "description": ("Журнал шлюза по пользователю: время, ключ, адрес, HTTP-статус, тип "
                        "ошибки и параметры запроса. Главный источник фактов о том, что произошло."),
        "inputSchema": {"type": "object", "properties": {
            "user_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}},
            "required": ["user_id"]},
    },
    {
        "name": "list_tickets",
        "description": "Тикеты: все или одного пользователя, можно по статусу. Для истории обращений.",
        "inputSchema": {"type": "object", "properties": {
            "user_id": {"type": "string"},
            "status": {"type": "string", "enum": list(STATUSES)}},
            "required": []},
    },
    {
        "name": "add_note",
        "description": "Добавить заметку в тикет и (необязательно) сменить статус. Только для оператора.",
        "inputSchema": {"type": "object", "properties": {
            "ticket_id": {"type": "string"},
            "text": {"type": "string"},
            "author": {"type": "string", "default": "оператор"},
            "status": {"type": "string", "enum": list(STATUSES)}},
            "required": ["ticket_id", "text"]},
    },
]


def load():
    path = LIVE if LIVE.exists() else SEED
    return json.loads(path.read_text(encoding="utf-8"))


def save(data):
    LIVE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LIVE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(LIVE)              # атомарно: полузаписанного файла не бывает


def _ticket(data, ticket_id):
    ticket_id = str(ticket_id or "").strip().upper()
    for t in data["tickets"]:
        if t["id"] == ticket_id:
            return t
    raise ValueError(f"тикета {ticket_id or '(пусто)'} нет")


def _user(data, user_id):
    user_id = str(user_id or "").strip().lower()
    for u in data["users"]:
        if u["id"] == user_id:
            return u
    raise ValueError(f"пользователя {user_id or '(пусто)'} нет")


def run_tool(name, arguments):
    with LOCK:
        data = load()
        if name == "get_ticket":
            return _ticket(data, arguments.get("ticket_id"))
        if name == "get_user":
            return _user(data, arguments.get("user_id"))
        if name == "get_events":
            user = _user(data, arguments.get("user_id"))
            try:
                limit = max(1, min(50, int(arguments.get("limit", 20))))
            except (TypeError, ValueError):
                raise ValueError("limit должен быть числом")
            events = data["events"].get(user["id"], [])
            return {"user_id": user["id"], "total": len(events), "events": events[-limit:],
                    "note": "" if events else "в журнале шлюза нет ни одного запроса от этого пользователя"}
        if name == "list_tickets":
            items = data["tickets"]
            if arguments.get("user_id"):
                items = [t for t in items if t["user_id"] == str(arguments["user_id"]).lower()]
            if arguments.get("status"):
                items = [t for t in items if t["status"] == arguments["status"]]
            return [{k: t[k] for k in ("id", "user_id", "status", "created", "subject")}
                    for t in items]
        if name == "add_note":
            ticket = _ticket(data, arguments.get("ticket_id"))
            text = str(arguments.get("text") or "").strip()
            if not text:
                raise ValueError("пустая заметка")
            status = arguments.get("status")
            if status and status not in STATUSES:
                raise ValueError(f"статус — один из: {', '.join(STATUSES)}")
            note = {"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "author": str(arguments.get("author") or "оператор")[:40], "text": text[:4000]}
            ticket["notes"].append(note)
            if status:
                ticket["status"] = status
            save(data)
            return {"ticket_id": ticket["id"], "status": ticket["status"], "notes": len(ticket["notes"])}
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
            result = run_tool(params.get("name"), params.get("arguments") or {})
        except ValueError as error:
            return _ok(request_id, {"content": [{"type": "text", "text": f"Ошибка: {error}"}],
                                    "isError": True})
        text = json.dumps(result, ensure_ascii=False, indent=1)
        return _ok(request_id, {"content": [{"type": "text", "text": text}], "isError": False})
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": f"метод не поддерживается: {method}"}}


def _ok(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve():
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
    for name, arguments in (("list_tickets", {"status": "новый"}),
                            ("get_ticket", {"ticket_id": "t-101"}),
                            ("get_user", {"user_id": "u-01"}),
                            ("get_events", {"user_id": "u-06"}),
                            ("get_ticket", {"ticket_id": "T-999"})):
        print(f"→ {name} {json.dumps(arguments, ensure_ascii=False)}")
        try:
            print("  " + json.dumps(run_tool(name, arguments), ensure_ascii=False)[:260])
        except ValueError as error:
            print(f"  отказ: {error}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--reset" in sys.argv:
        LIVE.unlink(missing_ok=True)
        print("рабочая копия удалена, данные — из crm.json")
    else:
        serve()
