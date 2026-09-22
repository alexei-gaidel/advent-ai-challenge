"""MCP-сервер вокруг git: даёт агенту читать историю репозитория.

Транспорт stdio, протокол JSON-RPC 2.0 — тот же каркас, что в задании 16, но вместо
инструментов-заготовок здесь обёртка над настоящим API: командами git.

    python3 git_server.py                  # ждёт JSON-RPC на stdin
    python3 git_server.py --selftest       # прогон инструментов без клиента

Только чтение: ни одна команда не меняет репозиторий. Аргументы передаются списком,
без оболочки, поэтому подставить в них лишнюю команду нельзя.
"""

import json
import pathlib
import re
import subprocess
import sys

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "advent-git-server", "version": "1.0.0"}

# Репозиторий челленджа: папка на уровень выше этого файла.
REPO = pathlib.Path(__file__).resolve().parent.parent

MAX_LIMIT = 50
# Ссылки на коммиты: хеш, HEAD~3, имя ветки или тега. Ничего экзотического не пускаем.
REF_PATTERN = re.compile(r"^[A-Za-z0-9_.~^/-]{1,80}$")

# --- регистрация инструментов ---------------------------------------------------
# Имя, описание для модели и JSON Schema аргументов. По этой схеме модель понимает,
# что можно передать, что обязательно и какие есть варианты.

TOOLS = [
    {
        "name": "git_log",
        "description": ("История коммитов репозитория Advent AI Challenge. "
                        "Полезно, чтобы узнать, что и когда делалось в проекте."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Сколько коммитов вернуть",
                          "minimum": 1, "maximum": MAX_LIMIT, "default": 10},
                "grep": {"type": "string",
                         "description": "Искать подстроку в тексте сообщений коммитов"},
                "path": {"type": "string",
                         "description": "Ограничить историю папкой или файлом, "
                                        "например 14-invariants"},
            },
            "required": [],
        },
    },
    {
        "name": "git_show",
        "description": "Подробности одного коммита: автор, дата, сообщение и список изменённых файлов.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string",
                        "description": "Хеш коммита, HEAD, HEAD~2, имя ветки или тега"},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "git_search",
        "description": ("Поиск строки в содержимом файлов репозитория. "
                        "Отвечает, где в коде встречается нужное понятие."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Что искать в файлах"},
                "path": {"type": "string",
                         "description": "Где искать: папка или маска, например 16-mcp-client"},
                "limit": {"type": "integer", "description": "Сколько совпадений вернуть",
                          "minimum": 1, "maximum": MAX_LIMIT, "default": 15},
            },
            "required": ["query"],
        },
    },
    {
        "name": "git_summary",
        "description": "Сводка по репозиторию: число коммитов, папки заданий, дата последнего коммита.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


def run_git(args, limit_bytes=8000):
    """Запуск git списком аргументов, без оболочки. Только чтение."""
    try:
        result = subprocess.run(["git", "-C", str(REPO), *args],
                                capture_output=True, text=True, timeout=20)
    except FileNotFoundError as error:
        raise ValueError("git не найден в системе") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError("git не ответил вовремя") from error

    if result.returncode != 0:
        raise ValueError((result.stderr or "git вернул ошибку").strip()[:200])
    return result.stdout[:limit_bytes].rstrip()


def clamp_limit(arguments, default):
    value = arguments.get("limit", default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError("limit должен быть числом")
    return max(1, min(MAX_LIMIT, value))


def safe_path(arguments):
    """Путь ограничиваем репозиторием: никаких «..» и абсолютных путей."""
    path = (arguments.get("path") or "").strip()
    if not path:
        return None
    if path.startswith("/") or ".." in path:
        raise ValueError("path должен быть внутри репозитория")
    return path


def run_tool(name, arguments):
    if name == "git_log":
        args = ["log", f"-{clamp_limit(arguments, 10)}",
                "--date=short", "--pretty=format:%h · %ad · %s"]
        if arguments.get("grep"):
            args += ["--grep", str(arguments["grep"]), "-i"]
        path = safe_path(arguments)
        if path:
            args += ["--", path]
        return run_git(args) or "коммитов не найдено"

    if name == "git_show":
        ref = str(arguments.get("ref", "")).strip()
        if not ref:
            raise ValueError("нужен аргумент ref")
        if not REF_PATTERN.match(ref):
            raise ValueError("ref выглядит подозрительно: допустимы хеш, HEAD~N, ветка, тег")
        return run_git(["show", "--stat", "--date=short",
                        "--pretty=format:%h · %an · %ad%n%n%s%n%n%b", ref])

    if name == "git_search":
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("нужен аргумент query")
        args = ["grep", "-n", "-i", "--max-count", str(clamp_limit(arguments, 15)),
                "-e", query]
        path = safe_path(arguments)
        if path:
            args += ["--", path]
        try:
            return run_git(args) or "совпадений нет"
        except ValueError:
            # git grep возвращает код 1, когда ничего не нашёл, — это не ошибка.
            return "совпадений нет"

    if name == "git_summary":
        commits = run_git(["rev-list", "--count", "HEAD"])
        last = run_git(["log", "-1", "--date=short", "--pretty=format:%h · %ad · %s"])
        folders = sorted(item.name for item in REPO.iterdir()
                         if item.is_dir() and item.name[:2].isdigit())
        return (f"Коммитов: {commits}\nПоследний: {last}\n"
                f"Папок заданий: {len(folders)}\n" + "\n".join(f"  {name}" for name in folders))

    raise ValueError(f"неизвестный инструмент: {name}")


# --- протокол -------------------------------------------------------------------

def handle(message):
    method = message.get("method", "")
    request_id = message.get("id")
    if request_id is None:
        return None            # уведомления ответа не требуют

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
        except ValueError as error:
            return _ok(request_id, {"content": [{"type": "text", "text": f"Ошибка: {error}"}],
                                    "isError": True})
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
    print(f"репозиторий: {REPO}\n")
    for name, arguments in (
        ("git_summary", {}),
        ("git_log", {"limit": 3}),
        ("git_log", {"limit": 3, "grep": "инвариант"}),
        ("git_search", {"query": "inputSchema", "path": "16-mcp-client", "limit": 3}),
        ("git_show", {"ref": "HEAD"}),
        ("git_show", {"ref": "HEAD; rm -rf /"}),
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
