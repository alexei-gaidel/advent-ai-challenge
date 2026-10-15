"""MCP-сервер проекта: ассистент разработчика видит репозиторий через него.

Каркас — git-сервер дня 17 (stdio, JSON-RPC 2.0, только чтение, аргументы списком без
оболочки). Набор инструментов другой: там была история коммитов, здесь — то, что нужно
разработчику прямо сейчас: ветка, состояние рабочей копии, файлы, diff и сам файл.

    python3 project_server.py                  # ждёт JSON-RPC на stdin
    python3 project_server.py --selftest       # прогон инструментов без клиента
"""

import json
import pathlib
import subprocess
import sys

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "advent-project-server", "version": "1.0.0"}

# Репозиторий челленджа: папка на уровень выше этого файла.
REPO = pathlib.Path(__file__).resolve().parent.parent

MAX_LIMIT = 200
MAX_FILE_LINES = 400
# Файлы, которые не отдаём ни при каких путях: ключи и локальные базы.
FORBIDDEN = (".env", ".git")

TOOLS = [
    {
        "name": "git_branch",
        "description": "Текущая git-ветка репозитория, её последний коммит и отставание от origin.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "git_status",
        "description": ("Состояние рабочей копии: изменённые, добавленные и неотслеживаемые "
                        "файлы. Отвечает на «что сейчас не закоммичено»."),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "git_files",
        "description": ("Файлы проекта под git (без игнорируемых баз и ключей). Без path — "
                        "папки верхнего уровня с числом файлов; с path — файлы этой папки, "
                        "например 17-mcp-tool."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Папка внутри репозитория"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT,
                          "default": 80, "description": "Сколько файлов вернуть"},
            },
            "required": [],
        },
    },
    {
        "name": "git_diff",
        "description": ("Незакоммиченные изменения в отслеживаемых файлах. По умолчанию — "
                        "сводка по файлам; с path — сам diff файла или папки."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Файл или папка"},
                "staged": {"type": "boolean", "default": False,
                           "description": "true — только то, что уже в индексе"},
            },
            "required": [],
        },
    },
    {
        "name": "git_log",
        "description": "Последние коммиты: хеш, дата, сообщение. Можно ограничить папкой.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "path": {"type": "string", "description": "Папка или файл"},
            },
            "required": [],
        },
    },
    {
        "name": "read_file",
        "description": ("Содержимое файла проекта с номерами строк. Для вопросов о коде, "
                        "на которые документация не отвечает."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь от корня, например 17-mcp-tool/tools.py"},
                "start": {"type": "integer", "minimum": 1, "default": 1},
                "lines": {"type": "integer", "minimum": 1, "maximum": MAX_FILE_LINES,
                          "default": 120},
            },
            "required": ["path"],
        },
    },
]


def run_git(args, limit_bytes=12000):
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
    text = result.stdout.rstrip()
    if len(text) > limit_bytes:
        text = text[:limit_bytes] + f"\n… (обрезано, всего {len(result.stdout)} символов)"
    return text


def clamp(arguments, key, default, top):
    try:
        value = int(arguments.get(key, default))
    except (TypeError, ValueError):
        raise ValueError(f"{key} должен быть числом")
    return max(1, min(top, value))


def safe_path(arguments, required=False):
    """Путь ограничиваем репозиторием: никаких «..», абсолютных путей и .env."""
    raw = str(arguments.get("path") or "").strip()
    path = raw.strip("/")
    if not path:
        if required:
            raise ValueError("нужен аргумент path")
        return None
    parts = path.split("/")
    if raw.startswith("/") or ".." in parts:
        raise ValueError("path должен быть внутри репозитория")
    if parts[-1] in FORBIDDEN or parts[0] in FORBIDDEN:
        raise ValueError("этот файл ассистенту недоступен")
    return path


def run_tool(name, arguments):
    if name == "git_branch":
        branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        last = run_git(["log", "-1", "--date=short", "--pretty=format:%h · %ad · %s"])
        try:
            ahead, behind = run_git(["rev-list", "--left-right", "--count",
                                     f"HEAD...origin/{branch}"]).split()
            remote = f"origin/{branch}: впереди на {ahead}, позади на {behind}"
        except ValueError:
            remote = "ветки на origin нет"
        return f"Ветка: {branch}\nПоследний коммит: {last}\n{remote}"

    if name == "git_status":
        text = run_git(["status", "--short", "--branch", "--untracked-files=all"])
        lines = text.splitlines()
        if len(lines) <= 1:
            return text + "\nрабочая копия чистая"
        return text + "\n(M — изменён, A — добавлен, ?? — не отслеживается, D — удалён)"

    if name == "git_files":
        args = ["ls-files"]
        path = safe_path(arguments)
        if not path:
            # Без пути — оглавление: 400 файлов в лимит не влезают, и по первым 80
            # модель не видит, какие папки вообще есть (так было в первом прогоне).
            counts = {}
            for name in run_git(args, limit_bytes=10 ** 6).splitlines():
                top = name.split("/")[0] + ("/" if "/" in name else "")
                counts[top] = counts.get(top, 0) + 1
            return "Верхний уровень репозитория (файлов под git):\n" + "\n".join(
                f"{top:34} {n}" for top, n in sorted(counts.items()))
        args += ["--", path]
        files = run_git(args).splitlines()
        limit = clamp(arguments, "limit", 80, MAX_LIMIT)
        head = "\n".join(files[:limit])
        if len(files) > limit:
            head += f"\n… и ещё {len(files) - limit} (всего {len(files)})"
        return head or "файлов не найдено"

    if name == "git_diff":
        path = safe_path(arguments)
        base = ["diff", "--cached"] if arguments.get("staged") else ["diff"]
        if path:
            return run_git(base + ["--", path]) or "изменений нет"
        return run_git(base + ["--stat"]) or "изменений в отслеживаемых файлах нет"

    if name == "git_log":
        args = ["log", f"-{clamp(arguments, 'limit', 10, 50)}", "--date=short",
                "--pretty=format:%h · %ad · %s"]
        path = safe_path(arguments)
        if path:
            args += ["--", path]
        return run_git(args) or "коммитов не найдено"

    if name == "read_file":
        path = safe_path(arguments, required=True)
        target = (REPO / path).resolve()
        if REPO not in target.parents or not target.is_file():
            raise ValueError(f"файла {path} нет")
        start = clamp(arguments, "start", 1, 100000)
        count = clamp(arguments, "lines", 120, MAX_FILE_LINES)
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        chunk = lines[start - 1:start - 1 + count]
        body = "\n".join(f"{start + i:>4}  {line}" for i, line in enumerate(chunk))
        return f"{path}, строки {start}–{start + len(chunk) - 1} из {len(lines)}\n{body}"

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
        ("git_branch", {}),
        ("git_status", {}),
        ("git_files", {"path": "17-mcp-tool", "limit": 5}),
        ("git_diff", {}),
        ("git_log", {"limit": 3}),
        ("read_file", {"path": "CLAUDE.md", "lines": 3}),
        ("read_file", {"path": ".env"}),
        ("read_file", {"path": "../etc/passwd"}),
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
