"""Мост между MCP и агентом.

Берёт у MCP-сервера список инструментов, переводит его в формат function calling
(его понимают DeepSeek и Groq), а обратные вызовы модели превращает в `tools/call`.

Агент не знает ни про JSON-RPC, ни про транспорт: он получает список описаний
и функцию «выполни инструмент с такими аргументами».
"""

import json
import sys
import threading

from mcp_client import MCPClient, MCPError

# Сервер запускается тем же интерпретатором, что и приложение.
MARKET_SERVER = [sys.executable, "market_server.py"]


class ToolBridge:
    """Одно соединение с MCP-сервером на всё приложение."""

    def __init__(self, command=None, name="market"):
        self.command = command or MARKET_SERVER
        self.name = name
        self.client = None
        self.tools = []
        self.error = ""
        self.calls = []
        # Панели работают параллельно, а stdio-соединение одно: вызовы сериализуем.
        self._lock = threading.Lock()

    def connect(self):
        """Ленивое подключение: первый же запрос поднимает сервер."""
        if self.client or self.error:
            return self.client

        try:
            client = MCPClient.stdio(self.name, self.command)
            client.connect()
            self.tools = client.list_tools()
            self.client = client
        except MCPError as error:
            self.error = str(error)
        return self.client

    def specs(self):
        """Описания инструментов в формате function calling."""
        self.connect()
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    # Схема MCP и есть описание параметров функции — переводить нечего.
                    "parameters": tool.get("inputSchema")
                                  or {"type": "object", "properties": {}},
                },
            }
            for tool in self.tools
        ]

    def call(self, name, arguments):
        """Выполняет инструмент и возвращает текст результата."""
        self.connect()
        if not self.client:
            return f"инструменты недоступны: {self.error}"

        with self._lock:
            try:
                result = self.client.call_tool(name, arguments)
            except MCPError as error:
                return f"ошибка вызова {name}: {error}"

        parts = [item.get("text", "") for item in result.get("content", [])
                 if item.get("type") == "text"]
        text = "\n".join(part for part in parts if part).strip()
        if result.get("isError"):
            text = f"инструмент вернул ошибку: {text}"

        self.calls.append({"name": name, "arguments": arguments, "result": text})
        return text or "(пустой результат)"

    def info(self):
        self.connect()
        return {
            "server": (self.client.server_info if self.client else {}),
            "protocol": (self.client.protocol_version if self.client else None),
            "tools": [{"name": tool["name"], "description": tool.get("description", ""),
                       "schema": tool.get("inputSchema", {})} for tool in self.tools],
            "error": self.error,
        }

    def close(self):
        if self.client:
            self.client.close()
            self.client = None


BRIDGE = ToolBridge()


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
