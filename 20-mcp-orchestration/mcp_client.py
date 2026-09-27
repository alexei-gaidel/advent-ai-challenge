"""Минимальный клиент MCP на стандартной библиотеке.

MCP — это JSON-RPC 2.0 поверх транспорта. Для «подключиться и получить инструменты»
нужно ровно три сообщения:

    1. initialize                 — клиент представляется, сервер отвечает своими данными
    2. notifications/initialized  — уведомление без ответа: рукопожатие завершено
    3. tools/list                 — список инструментов со схемами аргументов

Поддерживаются два транспорта: Streamable HTTP (публичные серверы) и stdio
(локальный процесс). Зависимостей нет.
"""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "advent-mcp-client", "version": "0.1.0"}


class MCPError(RuntimeError):
    """Ошибка транспорта или протокола — с текстом, понятным человеку."""


# --- транспорты ----------------------------------------------------------------

class HttpTransport:
    """Streamable HTTP: POST с JSON-RPC, ответ — JSON или поток SSE."""

    kind = "http"

    def __init__(self, url, timeout=30):
        self.url = url
        self.timeout = timeout
        # Сервер выдаёт идентификатор сессии в ответе на initialize и ждёт его обратно.
        self.session_id = None
        self.protocol_version = None

    def send(self, message, expect_reply=True):
        headers = {
            "Content-Type": "application/json",
            # Без text/event-stream серверы отвечают 406: они вправе прислать поток.
            "Accept": "application/json, text/event-stream",
            "User-Agent": "advent-ai-challenge/1.0",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version

        request = urllib.request.Request(
            self.url, data=json.dumps(message).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self.session_id = session
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:200]
            raise MCPError(f"HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise MCPError(f"сеть недоступна: {error.reason}") from error
        except TimeoutError as error:      # не наследник URLError, ловим отдельно
            raise MCPError("таймаут: сервер не ответил вовремя") from error

        if not expect_reply:
            return None
        return _first_payload(body)

    def close(self):
        pass


class StdioTransport:
    """Локальный сервер: JSON-RPC построчно через stdin/stdout дочернего процесса."""

    kind = "stdio"

    def __init__(self, command, timeout=30):
        self.command = command
        self.timeout = timeout
        self.process = None
        self.protocol_version = None

    def _start(self):
        if self.process is None:
            self.process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1)

    def send(self, message, expect_reply=True):
        self._start()
        try:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, ValueError) as error:
            raise MCPError("процесс сервера закрыл входной поток") from error

        if not expect_reply:
            return None

        line = self.process.stdout.readline()
        if not line:
            stderr = (self.process.stderr.read() or "").strip()[:200]
            raise MCPError(f"сервер не ответил{': ' + stderr if stderr else ''}")
        try:
            return json.loads(line)
        except json.JSONDecodeError as error:
            raise MCPError(f"сервер прислал не JSON: {line[:120]}") from error

    def close(self):
        if self.process:
            self.process.terminate()
            self.process = None


def _first_payload(body):
    """Достаёт JSON-RPC из ответа: либо это обычный JSON, либо поток SSE."""
    text = body.strip()
    if not text:
        return None
    if text.startswith("{"):
        return json.loads(text)

    # SSE: строки вида "event: message" и "data: {...}"; берём первый data.
    for line in text.splitlines():
        if line.startswith("data:"):
            chunk = line[5:].strip()
            if chunk and chunk != "[DONE]":
                return json.loads(chunk)
    raise MCPError(f"не удалось разобрать ответ: {text[:120]}")


# --- клиент ---------------------------------------------------------------------

class MCPClient:
    """Соединение с одним MCP-сервером."""

    def __init__(self, name, transport):
        self.name = name
        self.transport = transport
        self.server_info = {}
        self.capabilities = {}
        self.protocol_version = None
        self._id = 0
        self.connect_seconds = None

    @classmethod
    def http(cls, name, url, timeout=30):
        return cls(name, HttpTransport(url, timeout))

    @classmethod
    def stdio(cls, name, command, timeout=30):
        return cls(name, StdioTransport(command, timeout))

    def _next_id(self):
        self._id += 1
        return self._id

    def _request(self, method, params=None):
        message = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
        if params is not None:
            message["params"] = params

        reply = self.transport.send(message)
        if reply is None:
            raise MCPError(f"{method}: пустой ответ")
        if "error" in reply:
            error = reply["error"]
            raise MCPError(f"{method}: {error.get('message')} (код {error.get('code')})")
        return reply.get("result", {})

    def _notify(self, method, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.transport.send(message, expect_reply=False)

    def connect(self):
        """Рукопожатие: initialize, затем уведомление о готовности."""
        started = time.monotonic()
        result = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })

        self.server_info = result.get("serverInfo", {})
        self.capabilities = result.get("capabilities", {})
        self.protocol_version = result.get("protocolVersion")
        # Дальше сервер ждёт свою версию протокола в заголовке каждого запроса.
        self.transport.protocol_version = self.protocol_version

        self._notify("notifications/initialized")
        self.connect_seconds = round(time.monotonic() - started, 2)
        return result

    def list_tools(self):
        """Список инструментов. Длинные списки приходят страницами по cursor."""
        tools, cursor = [], None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._request("tools/list", params)
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name, arguments=None):
        """Вызов инструмента — понадобится в задании 17."""
        return self._request("tools/call",
                             {"name": name, "arguments": arguments or {}})

    def close(self):
        self.transport.close()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# Серверы для демонстрации: три публичных по HTTP и свой локальный по stdio.
PUBLIC_SERVERS = [
    ("DeepWiki", "https://mcp.deepwiki.com/mcp"),
    ("Context7", "https://mcp.context7.com/mcp"),
    ("GitMCP", "https://gitmcp.io/docs"),
]

LOCAL_SERVER = [sys.executable, "mcp_server.py"]
