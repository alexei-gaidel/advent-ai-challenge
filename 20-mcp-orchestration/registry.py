"""Реестр MCP-серверов: регистрация, общий каталог инструментов и маршрутизация.

Агент видит один плоский список функций и не знает, что за ними три процесса.
Реестр при регистрации спрашивает у каждого сервера tools/list и даёт инструменту
полное имя `сервер__инструмент` — так же делают MCP-клиенты вроде Claude Code.
Это снимает коллизии: `search` есть у погоды и у заметок, а модель видит
`weather__search` и `notes__search`. Вызов по полному имени реестр отправляет
в нужный процесс и пишет в журнал, какой сервер его выполнил.
"""

import json
import pathlib
import sys
import threading
import time

from mcp_client import MCPClient, MCPError

SEPARATOR = "__"

HERE = pathlib.Path(__file__).resolve().parent

# Серверы запускаются тем же интерпретатором, что и приложение, из любой папки.
SERVERS = {
    "weather": [sys.executable, str(HERE / "weather_server.py")],
    "money": [sys.executable, str(HERE / "money_server.py")],
    "notes": [sys.executable, str(HERE / "notes_server.py")],
}


class Registry:
    def __init__(self, servers=None):
        self.servers = dict(servers or SERVERS)
        self.clients = {}
        self.errors = {}
        self.catalog = {}       # полное имя → {"server", "tool", "description", "schema"}
        self.log = []
        self._locks = {name: threading.Lock() for name in self.servers}
        self._connect_lock = threading.Lock()

    # --- регистрация ------------------------------------------------------------

    def connect(self):
        """Поднимает все серверы и собирает каталог. Повторный вызов ничего не делает."""
        with self._connect_lock:
            for name, command in self.servers.items():
                if name in self.clients or name in self.errors:
                    continue
                try:
                    client = MCPClient.stdio(name, command)
                    client.connect()
                    tools = client.list_tools()
                except MCPError as error:
                    self.errors[name] = str(error)
                    continue
                self.clients[name] = client
                for tool in tools:
                    self.catalog[f"{name}{SEPARATOR}{tool['name']}"] = {
                        "server": name,
                        "tool": tool["name"],
                        "description": tool.get("description", ""),
                        "schema": tool.get("inputSchema")
                                  or {"type": "object", "properties": {}},
                    }
        return self

    def specs(self):
        """Каталог в формате function calling (его понимают DeepSeek и Groq)."""
        self.connect()
        return [
            {
                "type": "function",
                "function": {
                    "name": full_name,
                    # Сервер в описании помогает модели понять, чей это инструмент.
                    "description": f"[сервер {entry['server']}] {entry['description']}",
                    "parameters": entry["schema"],
                },
            }
            for full_name, entry in self.catalog.items()
        ]

    # --- маршрутизация ----------------------------------------------------------

    def route(self, full_name):
        """Полное имя → (сервер, имя инструмента на сервере)."""
        entry = self.catalog.get(full_name)
        if entry:
            return entry["server"], entry["tool"]
        # Модель иногда зовёт короткое имя. Если оно однозначно — чиним, иначе отказ.
        owners = [e for e in self.catalog.values() if e["tool"] == full_name]
        if len(owners) == 1:
            return owners[0]["server"], owners[0]["tool"]
        if owners:
            servers = ", ".join(e["server"] for e in owners)
            raise LookupError(f"имя {full_name} неоднозначно: есть на серверах {servers}")
        raise LookupError(f"инструмента {full_name} нет ни на одном сервере")

    def call(self, full_name, arguments, round_number=0):
        """Выполняет инструмент на своём сервере. Возвращает (текст, запись журнала)."""
        self.connect()
        entry = {"seq": len(self.log) + 1, "round": round_number, "name": full_name,
                 "server": None, "tool": None, "arguments": arguments,
                 "ok": False, "ms": 0, "result": ""}
        started = time.monotonic()
        try:
            server, tool = self.route(full_name)
            entry["server"], entry["tool"] = server, tool
            client = self.clients.get(server)
            if not client:
                raise LookupError(f"сервер {server} недоступен: {self.errors.get(server)}")
            with self._locks[server]:
                result = client.call_tool(tool, arguments)
            parts = [item.get("text", "") for item in result.get("content", [])
                     if item.get("type") == "text"]
            text = "\n".join(p for p in parts if p).strip() or "(пустой результат)"
            entry["ok"] = not result.get("isError")
        except (LookupError, MCPError) as error:
            text = f"Ошибка: {error}"
        entry["ms"] = round((time.monotonic() - started) * 1000)
        entry["result"] = text
        self.log.append(entry)
        return text, entry

    # --- для страницы и демо ----------------------------------------------------

    def info(self):
        self.connect()
        servers = []
        for name in self.servers:
            client = self.clients.get(name)
            servers.append({
                "name": name,
                "server": client.server_info if client else {},
                "error": self.errors.get(name, ""),
                "tools": [{"name": full, "tool": e["tool"],
                           "description": e["description"],
                           "required": e["schema"].get("required") or []}
                          for full, e in self.catalog.items() if e["server"] == name],
            })
        return {"servers": servers, "tools": len(self.catalog),
                "collisions": self.collisions()}

    def collisions(self):
        """Короткие имена, которые встречаются на нескольких серверах."""
        seen = {}
        for entry in self.catalog.values():
            seen.setdefault(entry["tool"], []).append(entry["server"])
        return {tool: servers for tool, servers in seen.items() if len(servers) > 1}

    def close(self):
        for client in self.clients.values():
            client.close()
        self.clients.clear()
        self.errors.clear()
        self.catalog.clear()


def parse_arguments(raw):
    """Модель присылает аргументы строкой JSON — иногда пустой или битой."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


if __name__ == "__main__":
    registry = Registry().connect()
    for server in registry.info()["servers"]:
        print(f"{server['name']:8} {server['server'].get('name', server['error'])}")
        for tool in server["tools"]:
            print(f"   {tool['name']}({', '.join(tool['required'])})")
    print("коллизии:", registry.collisions())
    for name in ("search", "weather__search", "notes__search", "budget", "nope"):
        try:
            print(f"route({name}) → {registry.route(name)}")
        except LookupError as error:
            print(f"route({name}) → отказ: {error}")
    registry.close()
