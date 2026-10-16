"""Песочница: слепок репозитория, над которым работает файловый ассистент.

Ассистент не трогает настоящий репозиторий. Он работает с `workspace/` — копией
исходников челленджа на зафиксированном коммите, поэтому каждый прогон начинается
с одного и того же состояния и эталоны не «плывут» от коммита к коммиту.

Поверх слепка накладывается «дрейф» — правка кода дня 31, после которой его
документация устарела: инструмент MCP `git_files` переименован в `list_files`
и добавлен `git_blame`. Это сценарий «обнови документацию по коду». Ещё четыре правки — подсаженные
нарушения правил CLAUDE.md для сценария «проверь инварианты».

    python3 workspace.py            # собрать, если ещё нет
    python3 workspace.py --reset    # снести и собрать заново (черновики тоже)
"""

import io
import pathlib
import shutil
import subprocess
import sys
import tarfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
WORKSPACE = HERE / "workspace"
DRAFTS = ".drafts"

# Коммит дня 33 — последний перед этим днём.
SNAPSHOT = "665b017"

# В слепок идут исходники и документация; данные прогонов, касты и базы — нет.
SUFFIXES = (".py", ".md")
SKIP_PARTS = {"data", "demo", "__pycache__"}

# Дрейф кода дня 31: (файл, было, стало). Каждая замена обязана сработать ровно раз
# по счёту count, иначе слепок не тот, что ожидает демо.
DRIFT = [
    ("31-dev-assistant/project_server.py",
     '        "name": "git_files",\n'
     '        "description": ("Файлы проекта под git',
     '        "name": "list_files",\n'
     '        "description": ("Файлы проекта под git'),
    ("31-dev-assistant/project_server.py",
     '    if name == "git_files":',
     '    if name == "list_files":'),
    ("31-dev-assistant/project_server.py",
     '        ("git_files", {"path": "17-mcp-tool", "limit": 5}),',
     '        ("list_files", {"path": "17-mcp-tool", "limit": 5}),\n'
     '        ("git_blame", {"path": "CLAUDE.md", "start": 1, "lines": 3}),'),
    ("31-dev-assistant/project_server.py",
     '    {\n        "name": "read_file",',
     '    {\n'
     '        "name": "git_blame",\n'
     '        "description": ("Кто и в каком коммите последним менял строки файла: '
     'хеш, автор, дата. "\n'
     '                        "Для вопросов «когда и зачем появилась эта строка»."),\n'
     '        "inputSchema": {\n'
     '            "type": "object",\n'
     '            "properties": {\n'
     '                "path": {"type": "string", "description": "Файл от корня"},\n'
     '                "start": {"type": "integer", "minimum": 1, "default": 1},\n'
     '                "lines": {"type": "integer", "minimum": 1, "maximum": 80, '
     '"default": 20},\n'
     '            },\n'
     '            "required": ["path"],\n'
     '        },\n'
     '    },\n'
     '    {\n        "name": "read_file",'),
    ("31-dev-assistant/project_server.py",
     '    if name == "read_file":',
     '    if name == "git_blame":\n'
     '        path = safe_path(arguments, required=True)\n'
     '        start = clamp(arguments, "start", 1, 100000)\n'
     '        count = clamp(arguments, "lines", 20, 80)\n'
     '        return run_git(["blame", "--date=short", "-L", '
     'f"{start},+{count}", "--", path])\n\n'
     '    if name == "read_file":'),
    ("31-dev-assistant/assistant.py",
     '"/files": ("git_files",',
     '"/files": ("list_files",'),
    ("31-dev-assistant/assistant.py",
     '            "/log": ("git_log", {"path": rest} if rest else {}),',
     '            "/log": ("git_log", {"path": rest} if rest else {}),\n'
     '            "/blame": ("git_blame", {"path": rest}),'),
    ("31-dev-assistant/help_demo.py",
     'git("git_files", path="26-local-llm")',
     'git("list_files", path="26-local-llm")'),
]


# Подсаженные нарушения правил CLAUDE.md — к трём настоящим, которые нашёл
# invariants.py в слепке (порт дня 01 и мёртвый `import corpus` в днях 32 и 33).
PLANTED = [
    ("20-mcp-orchestration/web.py", "PORT = 8020", "PORT = 8002"),
    ("25-rag-chat/web.py", "PORT = 8025\n",
     'PORT = 8025\nPING_URL = "https://api.deepseek.com/chat/completions"  # проверка ключа при старте\n'),
    ("23-rerank-filter/embeddings.py", "import os\n", "import os\nimport requests\n"),
    (".gitignore", "13-task-state/agents.db*\n", ""),
]


def snapshot_files():
    """Байты всех файлов слепка: {путь: содержимое}."""
    raw = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", SNAPSHOT],
                         capture_output=True, check=True).stdout
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split("/")
            if SKIP_PARTS & set(parts[:-1]):
                continue
            if member.name == ".gitignore" or member.name.endswith(SUFFIXES):
                files[member.name] = tar.extractfile(member).read()
    return files


def build(reset=False):
    if WORKSPACE.exists():
        if not reset:
            return {"built": False, "path": str(WORKSPACE)}
        shutil.rmtree(WORKSPACE)
    files = snapshot_files()
    for name, data in files.items():
        target = WORKSPACE / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for name, old, new in DRIFT + PLANTED:
        target = WORKSPACE / name
        text = target.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise RuntimeError(f"дрейф не наложился: {name}: {old[:50]!r}")
        target.write_text(text.replace(old, new), encoding="utf-8")
    return {"built": True, "path": str(WORKSPACE), "files": len(files),
            "snapshot": SNAPSHOT, "drift": len(DRIFT), "planted": len(PLANTED)}


if __name__ == "__main__":
    print(build(reset="--reset" in sys.argv))
