"""MCP-сервер «заметки»: план поездки с чек-листом. Хранение — локальный JSON.

    create(title, text, sources)   → note_id
    checklist_add(note_id, items)  → пункты добавлены
    read(note_id)                  → заметка целиком
    search(query)                  → заметки по словам

Имя `search` есть и у сервера погоды — это сделано специально. Клиент видит
оба инструмента как weather__search и notes__search и должен выбрать нужный.

    python3 notes_server.py              # ждёт JSON-RPC на stdin
    python3 notes_server.py --selftest   # прогон инструментов на временном файле
"""

import datetime
import json
import os
import pathlib
import re
import tempfile

import mcp_server
from mcp_server import require

SERVER_INFO = {"name": "advent-notes-server", "version": "1.0.0"}

# Путь через окружение: демонстрация и каст пишут в свой файл, не трогая рабочий.
NOTES_PATH = pathlib.Path(os.environ.get("ADVENT_NOTES")
                          or pathlib.Path(__file__).with_name("data") / "notes.json")

# Ссылки на данные других серверов: прогноз, бюджет, курс, место.
SOURCE_PATTERN = re.compile(r"^(fc|bud|rate|place)-[\w-]+$")

TOOLS = [
    {
        "name": "create",
        "description": ("Создаёт заметку. sources — идентификаторы данных, на которых она "
                        "основана (forecast_id, budget_id и т. п.). Возвращает note_id."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "text": {"type": "string", "description": "содержание заметки"},
                "sources": {"type": "array", "items": {"type": "string"},
                            "description": "forecast_id, budget_id и другие id"},
            },
            "required": ["title", "text"],
        },
    },
    {
        "name": "checklist_add",
        "description": "Добавляет пункты чек-листа в заметку по note_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "note_id": {"type": "string"},
                "items": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["note_id", "items"],
        },
    },
    {
        "name": "read",
        "description": "Возвращает заметку целиком: текст, источники, чек-лист.",
        "inputSchema": {
            "type": "object",
            "properties": {"note_id": {"type": "string"}},
            "required": ["note_id"],
        },
    },
    {
        "name": "search",
        "description": "Ищет среди сохранённых заметок по словам из заголовка и текста.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]


def load():
    if not NOTES_PATH.exists():
        return {"next": 1, "notes": {}}
    return json.loads(NOTES_PATH.read_text(encoding="utf-8"))


def save(data):
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _note(data, note_id):
    note = data["notes"].get(note_id)
    if not note:
        raise ValueError(f"заметки {note_id} нет — note_id выдаёт create или search")
    return note


def create(arguments):
    title = require(arguments, "title")
    text = require(arguments, "text")
    sources = arguments.get("sources") or []
    if not isinstance(sources, list):
        raise ValueError("sources должен быть списком идентификаторов")
    bad = [s for s in sources if not SOURCE_PATTERN.match(str(s))]
    if bad:
        raise ValueError(f"это не идентификаторы данных: {bad}")

    data = load()
    note_id = f"note-{data['next']}"
    data["next"] += 1
    data["notes"][note_id] = {
        "title": title, "text": text, "sources": [str(s) for s in sources],
        "checklist": [], "created": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    save(data)
    return f"note_id: {note_id}\nзаметка «{title}» создана, источников: {len(sources)}"


def checklist_add(arguments):
    note_id = require(arguments, "note_id")
    items = arguments.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("items должен быть непустым списком строк")

    data = load()
    note = _note(data, note_id)
    note["checklist"].extend(str(item) for item in items)
    save(data)
    return f"в {note_id} добавлено пунктов: {len(items)}, всего {len(note['checklist'])}"


def read(arguments):
    note_id = require(arguments, "note_id")
    note = _note(load(), note_id)
    checklist = "\n".join(f"  [ ] {item}" for item in note["checklist"]) or "  (пусто)"
    return (f"{note_id}: {note['title']}\n{note['text']}\n"
            f"источники: {', '.join(note['sources']) or '—'}\nчек-лист:\n{checklist}")


def search(arguments):
    words = [w for w in re.findall(r"\w+", require(arguments, "query").lower()) if len(w) > 2]
    found = []
    for note_id, note in load()["notes"].items():
        haystack = f"{note['title']} {note['text']} {' '.join(note['checklist'])}".lower()
        # Совпадение по основе слова: «матч» находит «матча», «поездка» — «поездки».
        hits = sum(1 for w in words if w[:max(4, len(w) - 2)] in haystack)
        if hits:
            found.append((hits, note_id, note["title"]))
    if not found:
        return "заметок не найдено"
    found.sort(key=lambda row: (-row[0], row[1]))
    return "\n".join(f"note_id: {note_id} — {title}" for _, note_id, title in found[:5])


HANDLERS = {"create": create, "checklist_add": checklist_add, "read": read, "search": search}


def run_tool(name, arguments):
    handler = HANDLERS.get(name)
    if not handler:
        raise ValueError(f"нет такого инструмента: {name}")
    return handler(arguments)


def selftest():
    global NOTES_PATH
    NOTES_PATH = pathlib.Path(tempfile.gettempdir()) / "notes_selftest.json"
    NOTES_PATH.unlink(missing_ok=True)

    print("→ create")
    made = run_tool("create", {"title": "Поездка на матч", "text": "Екатеринбург, 3 октября",
                               "sources": ["fc-1486209-20261003", "bud-1-EUR"]})
    print("  " + made.replace("\n", "\n  "))
    note_id = made.split("note_id: ")[1].split("\n")[0]
    print("→ checklist_add\n  " + run_tool("checklist_add", {"note_id": note_id,
                                                             "items": ["паспорт", "зонт"]}))
    print("→ search «матча»\n  " + run_tool("search", {"query": "матча"}))
    print("→ read\n  " + run_tool("read", {"note_id": note_id}).replace("\n", "\n  "))

    print("\n=== защита от выдуманных входов ===")
    mcp_server.refuse("чужой note_id", run_tool, "checklist_add",
                      {"note_id": "note-999", "items": ["x"]})
    mcp_server.refuse("источник не id", run_tool, "create",
                      {"title": "x", "text": "y", "sources": ["погода хорошая"]})
    NOTES_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    mcp_server.main(SERVER_INFO, TOOLS, run_tool, selftest)
