"""Diff PR: получить из git, разобрать на файлы и строки, отфильтровать шум.

Разбор нужен не только для промпта. По нему код проверяет ответ ревьюера: замечание
«файл X, строка N» имеет смысл, только если X есть в diff, а N — в изменённых строках.
"""

import re
import subprocess

# Что ревьюить бессмысленно: генерируемое, данные прогонов, бинарное.
SKIP = re.compile(r"(\.svg|\.png|\.jpg|\.db|\.jsonl?|\.log|\.lock)$|/data/|__pycache__")
MAX_FILE_CHARS = 12000      # больше — обрезаем и честно пишем об этом в промпте
MAX_TOTAL_CHARS = 60000

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                            text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}: {result.stderr.strip()[:200]}")
    return result.stdout


def get_diff(repo, base=None, head=None):
    """base...head — как на GitHub: изменения ветки относительно точки расхождения.

    Без base — рабочая копия против HEAD, включая новые файлы. Это режим временного
    worktree демо: `git add -N` трогает индекс, поэтому в основном репо так не зовём.
    """
    if base:
        return git(repo, "diff", "--no-color", "-U3", f"{base}...{head or 'HEAD'}")
    git(repo, "add", "-N", ".")          # неотслеживаемые файлы попадают в diff как новые
    return git(repo, "diff", "--no-color", "-U3", "HEAD")


def parse(diff_text):
    """[{path, status, added: {строка: текст}, removed, text}] по файлам."""
    files, current = [], None
    new_line = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            path = line.split(" b/", 1)[-1]
            current = {"path": path, "status": "modified", "added": {}, "removed": 0,
                       "lines": [line]}
            files.append(current)
            continue
        if current is None:
            continue
        current["lines"].append(line)
        if line.startswith("new file mode"):
            current["status"] = "added"
        elif line.startswith("deleted file mode"):
            current["status"] = "deleted"
        elif line.startswith("Binary files"):
            current["status"] = "binary"
        match = HUNK.match(line)
        if match:
            new_line = int(match.group(3))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            current["added"][new_line] = line[1:]
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            current["removed"] += 1
        elif line.startswith(" "):
            new_line += 1
    for f in files:
        f["text"] = "\n".join(f.pop("lines"))
        f["skipped"] = bool(SKIP.search(f["path"])) or f["status"] == "binary"
    return files


def for_prompt(files):
    """Текст diff для модели: только ревьюируемые файлы, с бюджетом на размер."""
    parts, total, cut = [], 0, []
    for f in files:
        if f["skipped"]:
            continue
        text = f["text"]
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + f"\n… (diff файла обрезан, всего {len(f['text'])} символов)"
            cut.append(f["path"])
        if total + len(text) > MAX_TOTAL_CHARS:
            cut.append(f["path"])
            continue
        parts.append(text)
        total += len(text)
    return "\n\n".join(parts), cut


def near_change(file, line, slack=3):
    """Строка замечания попадает в изменённое место (± slack строк контекста)."""
    if not line:
        return False
    return any(abs(line - n) <= slack for n in file["added"])


def folders(files):
    """Папки дней, которые трогает PR, — их код пойдёт в RAG."""
    found = []
    for f in files:
        top = f["path"].split("/")[0]
        if re.match(r"\d\d-", top) and top not in found:
            found.append(top)
    return found
