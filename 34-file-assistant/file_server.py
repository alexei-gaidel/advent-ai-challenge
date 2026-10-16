"""MCP-сервер файлов проекта: через него ассистент читает, ищет и правит файлы.

Каркас — `project_server.py` дня 31 (stdio, JSON-RPC 2.0, защита путей). Корень —
песочница `workspace/` (слепок репозитория, см. workspace.py), а не сам репозиторий.

Запись идёт в ЧЕРНОВИК: `write_file` и `edit_file` кладут новую версию в
`workspace/.drafts/`, `read_file` и `search` уже видят её, а файлы слепка не меняются.
Перенести черновик на диск (`apply_drafts`) или выбросить (`discard_drafts`) может
только человек: ассистент эти два инструмента не получает.

    python3 file_server.py                 # ждёт JSON-RPC на stdin
    python3 file_server.py --selftest      # прогон инструментов без клиента
"""

import difflib
import fnmatch
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import workspace

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "advent-file-server", "version": "1.0.0"}

ROOT = pathlib.Path(os.environ.get("ADVENT_WORKSPACE") or workspace.WORKSPACE).resolve()
DRAFTS = ROOT / workspace.DRAFTS

MAX_FILE_LINES = 400
MAX_MATCHES = 60
MAX_WRITE = 60000
# Ключи, история git и сам черновик через инструменты не видны.
FORBIDDEN = (".env", ".git", workspace.DRAFTS)
TEXT_SUFFIXES = (".py", ".md", ".txt", ".json", ".toml", ".yml", ".yaml", ".gitignore")

# Инструменты человека: в tools/list они есть, но ассистенту не передаются.
HUMAN_ONLY = ("apply_drafts", "discard_drafts")

TOOLS = [
    {
        "name": "list_files",
        "description": ("Файлы проекта. Без path — оглавление: папки верхнего уровня с числом "
                        "файлов. С path — все файлы этой папки (рекурсивно) с числом строк. "
                        "pattern — маска имени, например *.py или README.md."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Папка от корня, например 17-mcp-tool"},
                "pattern": {"type": "string", "description": "Маска имени файла"},
            },
            "required": [],
        },
    },
    {
        "name": "read_file",
        "description": ("Содержимое файла с номерами строк. Если файл уже правился в "
                        "черновике — отдаётся версия из черновика."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь от корня, например CLAUDE.md"},
                "start": {"type": "integer", "minimum": 1, "default": 1},
                "lines": {"type": "integer", "minimum": 1, "maximum": MAX_FILE_LINES,
                          "default": 200},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search",
        "description": ("Поиск текста по всем файлам проекта (как grep). Возвращает сводку "
                        "«файл — число совпадений» по ВСЕМ найденным файлам и строки "
                        "«файл:номер: текст» для первых совпадений. Для вопросов «где "
                        "используется X»."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Что искать"},
                "regex": {"type": "boolean", "default": False,
                          "description": "true — pattern это регулярное выражение Python"},
                "path": {"type": "string", "description": "Искать только в этой папке"},
                "glob": {"type": "string", "description": "Маска имени файла, например *.py"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "git_log",
        "description": ("История коммитов проекта до момента слепка: хеш, дата, сообщение. "
                        "С path — только коммиты, менявшие эту папку или файл."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Папка или файл"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 250, "default": 30},
            },
            "required": [],
        },
    },
    {
        "name": "write_file",
        "description": ("Создать файл или целиком заменить его содержимое. Пишет в черновик: "
                        "человек увидит diff и сам решит, применять ли. Для точечной правки "
                        "существующего файла лучше edit_file."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь от корня"},
                "content": {"type": "string", "description": "Полный текст файла"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": ("Заменить фрагмент файла: old — дословный кусок текущего текста "
                        "(должен встретиться в файле ровно один раз), new — на что заменить. "
                        "Пишет в черновик."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь от корня"},
                "old": {"type": "string", "description": "Дословный фрагмент, без номеров строк"},
                "new": {"type": "string", "description": "Новый текст"},
            },
            "required": ["path", "old", "new"],
        },
    },
    {
        "name": "draft_diff",
        "description": "Unified diff всего черновика относительно файлов проекта.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "apply_drafts",
        "description": "Только для человека: перенести черновик в файлы проекта.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "discard_drafts",
        "description": "Только для человека: выбросить черновик.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


# --- пути -----------------------------------------------------------------------

def safe_path(arguments, key="path", required=False):
    """Путь только внутри песочницы: без «..», абсолютных путей, .env, .git и черновика."""
    raw = str(arguments.get(key) or "").strip()
    path = raw.strip("/")
    if path in ("", "."):
        if required:
            raise ValueError(f"нужен аргумент {key}")
        return None
    parts = path.split("/")
    if raw.startswith("/") or ".." in parts:
        raise ValueError("путь должен быть внутри проекта")
    if any(part in FORBIDDEN for part in parts):
        raise ValueError("этот файл ассистенту недоступен")
    target = (ROOT / path).resolve()
    if ROOT not in target.parents:
        raise ValueError("путь должен быть внутри проекта")
    return path


def clamp(arguments, key, default, top):
    try:
        value = int(arguments.get(key, default))
    except (TypeError, ValueError):
        raise ValueError(f"{key} должен быть числом")
    return max(1, min(top, value))


def project_files(path=None):
    """Все файлы проекта (без черновика) + новые файлы, созданные только в черновике."""
    base = ROOT / path if path else ROOT
    names = set()
    for scope, root in ((base, ROOT), (DRAFTS / path if path else DRAFTS, DRAFTS)):
        if not scope.exists():
            continue
        for item in scope.rglob("*"):
            if not item.is_file():
                continue
            relative = item.relative_to(root).as_posix()
            if any(part in FORBIDDEN for part in relative.split("/")):
                continue
            names.add(relative)
    return sorted(names)


def drafted(path):
    return (DRAFTS / path).is_file()


def read_text(path):
    """Текущая версия файла: черновик, если есть, иначе файл проекта."""
    for candidate in (DRAFTS / path, ROOT / path):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="replace")
    raise ValueError(f"файла {path} нет")


def write_draft(path, text):
    if not path.endswith(TEXT_SUFFIXES):
        raise ValueError("писать можно только текстовые файлы: " + ", ".join(TEXT_SUFFIXES))
    if len(text) > MAX_WRITE:
        raise ValueError(f"файл длиннее {MAX_WRITE} символов")
    target = DRAFTS / path
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(target)


def draft_paths():
    if not DRAFTS.exists():
        return []
    return sorted(p.relative_to(DRAFTS).as_posix() for p in DRAFTS.rglob("*") if p.is_file())


def diff_text():
    chunks = []
    for path in draft_paths():
        original = ROOT / path
        old = original.read_text(encoding="utf-8", errors="replace") if original.is_file() else ""
        new = (DRAFTS / path).read_text(encoding="utf-8")
        chunks.append("".join(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile="/dev/null" if not original.is_file() else f"a/{path}",
            tofile=f"b/{path}")))
    return "\n".join(chunk for chunk in chunks if chunk)


# --- инструменты ----------------------------------------------------------------

def run_tool(name, arguments):
    if name == "list_files":
        path = safe_path(arguments)
        pattern = str(arguments.get("pattern") or "").strip()
        if path and not (ROOT / path).is_dir() and not (DRAFTS / path).is_dir():
            raise ValueError(f"папки {path} нет")
        files = project_files(path)
        if pattern:
            files = [f for f in files if fnmatch.fnmatch(f.split("/")[-1], pattern)]
        if not path and not pattern:
            counts = {}
            for name_ in files:
                top = name_.split("/")[0] + ("/" if "/" in name_ else "")
                counts[top] = counts.get(top, 0) + 1
            return "Верхний уровень проекта (число файлов):\n" + "\n".join(
                f"{top:30} {n}" for top, n in sorted(counts.items()))
        lines = []
        for name_ in files:
            mark = "  (черновик)" if drafted(name_) else ""
            count = read_text(name_).count("\n")
            lines.append(f"{name_}  {count} стр.{mark}")
        return "\n".join(lines) + f"\nвсего файлов: {len(files)}" if lines else "файлов не найдено"

    if name == "read_file":
        path = safe_path(arguments, required=True)
        lines = read_text(path).splitlines()
        start = clamp(arguments, "start", 1, 100000)
        count = clamp(arguments, "lines", 200, MAX_FILE_LINES)
        chunk = lines[start - 1:start - 1 + count]
        body = "\n".join(f"{start + i:>4}  {line}" for i, line in enumerate(chunk))
        mark = " (версия из черновика)" if drafted(path) else ""
        tail = ""
        if start - 1 + count < len(lines):
            tail = f"\n… дальше ещё {len(lines) - (start - 1 + count)} строк (start={start + count})"
        return (f"{path}{mark}, строки {start}–{start + len(chunk) - 1} из {len(lines)}\n"
                f"{body}{tail}")

    if name == "search":
        pattern = str(arguments.get("pattern") or "")
        if not pattern:
            raise ValueError("нужен аргумент pattern")
        try:
            regex = re.compile(pattern if arguments.get("regex") else re.escape(pattern))
        except re.error as error:
            raise ValueError(f"плохое регулярное выражение: {error}")
        glob = str(arguments.get("glob") or "").strip()
        counts, shown, total = {}, [], 0
        for path in project_files(safe_path(arguments)):
            if glob and not fnmatch.fnmatch(path.split("/")[-1], glob):
                continue
            if not path.endswith(TEXT_SUFFIXES):
                continue
            for number, line in enumerate(read_text(path).splitlines(), 1):
                if regex.search(line):
                    counts[path] = counts.get(path, 0) + 1
                    total += 1
                    if len(shown) < MAX_MATCHES:
                        shown.append(f"{path}:{number}: {line.strip()[:160]}")
        if not counts:
            return "совпадений нет"
        # Сводка по файлам — всегда полная: обрезанный список врёт о том, где ещё есть
        # совпадения (урок git_files дня 31). Режутся только строки.
        summary = "\n".join(f"  {path} — {n}" for path, n in sorted(counts.items()))
        head = f"Совпадений {total} в {len(counts)} файлах:\n{summary}\n\nСтроки:\n"
        tail = (f"\n… показаны первые {MAX_MATCHES} из {total}; сузь path или glob"
                if total > MAX_MATCHES else "")
        return head + "\n".join(shown) + tail

    if name == "git_log":
        args = ["git", "-C", str(workspace.REPO), "log", workspace.SNAPSHOT,
                f"-{clamp(arguments, 'limit', 30, 250)}", "--date=short",
                "--pretty=format:%h · %ad · %s"]
        path = safe_path(arguments)
        if path:
            args += ["--", path]
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=20)
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            raise ValueError(f"git недоступен: {error}")
        if result.returncode != 0:
            raise ValueError((result.stderr or "git вернул ошибку").strip()[:200])
        return result.stdout.strip() or "коммитов не найдено"

    if name == "write_file":
        path = safe_path(arguments, required=True)
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("нужен аргумент content — текст файла")
        existed = drafted(path) or (ROOT / path).is_file()
        write_draft(path, content)
        verb = "заменён" if existed else "создан"
        return f"{path} {verb} в черновике: {len(content.splitlines())} строк"

    if name == "edit_file":
        path = safe_path(arguments, required=True)
        old, new = arguments.get("old"), arguments.get("new")
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError("нужны аргументы old (непустой) и new")
        text = read_text(path)
        found = text.count(old)
        if found == 0:
            raise ValueError("фрагмент old в файле не найден — перечитай файл, old должен "
                             "совпадать дословно, без номеров строк")
        if found > 1:
            raise ValueError(f"фрагмент old встречается {found} раз — добавь соседние строки, "
                             "чтобы он стал уникальным")
        write_draft(path, text.replace(old, new))
        return (f"{path}: правка внесена в черновик "
                f"(−{len(old.splitlines())} +{len(new.splitlines())} строк)")

    if name == "draft_diff":
        return diff_text() or "черновик пуст"

    if name == "apply_drafts":
        paths = draft_paths()
        for path in paths:
            target = ROOT / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(DRAFTS / path, target)
        shutil.rmtree(DRAFTS, ignore_errors=True)
        return f"применено файлов: {len(paths)}" + "".join(f"\n  {p}" for p in paths)

    if name == "discard_drafts":
        paths = draft_paths()
        shutil.rmtree(DRAFTS, ignore_errors=True)
        return f"выброшено файлов из черновика: {len(paths)}"

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
        except (ValueError, OSError) as error:
            return _ok(request_id, {"content": [{"type": "text", "text": f"Ошибка: {error}"}],
                                    "isError": True})
        return _ok(request_id, {"content": [{"type": "text", "text": text}], "isError": False})

    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": f"метод не поддерживается: {method}"}}


def _ok(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve():
    workspace.build()
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
    workspace.build()
    print(f"песочница: {ROOT}\n")
    for name, arguments in (
        ("list_files", {}),
        ("list_files", {"path": "17-mcp-tool", "pattern": "*.py"}),
        ("read_file", {"path": "CLAUDE.md", "lines": 3}),
        ("search", {"pattern": "MCPClient", "glob": "*.py"}),
        ("git_log", {"limit": 3}),
        ("read_file", {"path": ".env"}),
        ("read_file", {"path": "../.env"}),
        ("write_file", {"path": "notes/selftest.md", "content": "# проверка\n"}),
        ("edit_file", {"path": "CLAUDE.md", "old": "Python", "new": "Питон"}),
        ("edit_file", {"path": "notes/selftest.md", "old": "проверка", "new": "проверка ок"}),
        ("draft_diff", {}),
        ("discard_drafts", {}),
        ("draft_diff", {}),
    ):
        print(f"→ {name} {json.dumps(arguments, ensure_ascii=False)}")
        try:
            print("  " + run_tool(name, arguments)[:400].replace("\n", "\n  "))
        except ValueError as error:
            print(f"  отказ: {error}")
        print()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        serve()
