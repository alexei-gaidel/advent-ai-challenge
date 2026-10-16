"""Эталон для сценария «проверь инварианты»: правила CLAUDE.md, проверенные кодом.

Ассистент ищет нарушения сам, через MCP. Этот модуль модели не показывается — он
нужен, чтобы было с чем сравнить её отчёт, и выводится рядом с ним в вебе.

Правила (раздел «Жёсткие правила» CLAUDE.md):
    port       PORT в web.py дня NN равен 8000 + NN
    gitignore  если код дня пишет agents.db / custom_roles.json — в .gitignore есть
               строки NN-…/agents.db* и NN-…/custom_roles.json
    stdlib     импорты — только стандартная библиотека и модули своей папки
    boundary   web.py не знает URL провайдеров

    python3 invariants.py             # список нарушений в песочнице
"""

import ast
import json
import pathlib
import re
import sys

import workspace

PROVIDER_URLS = ("api.deepseek.com", "api.groq.com", "chat/completions")
DAY = re.compile(r"^(\d\d)-")


def day_folders(root):
    return sorted(p for p in root.iterdir() if p.is_dir() and DAY.match(p.name))


def imports_of(path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name.split(".")[0], node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.append((node.module.split(".")[0], node.lineno))
    return found


def check(root=None):
    root = pathlib.Path(root or workspace.WORKSPACE)
    ignore = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    ignore = {line.strip() for line in ignore}
    violations = []

    def add(rule, path, line, detail):
        violations.append({"rule": rule, "path": path, "line": line, "detail": detail})

    for folder in day_folders(root):
        number = int(DAY.match(folder.name).group(1))
        local = {p.stem for p in folder.rglob("*.py")}
        code = ""
        for py in sorted(folder.rglob("*.py")):
            relative = py.relative_to(root).as_posix()
            text = py.read_text(encoding="utf-8")
            code += text
            for module, line in imports_of(py):
                if module not in sys.stdlib_module_names and module not in local:
                    add("stdlib", relative, line, f"import {module} — не stdlib и не модуль папки")

        web = folder / "web.py"
        if web.is_file():
            relative = web.relative_to(root).as_posix()
            for number_line, line in enumerate(web.read_text(encoding="utf-8").splitlines(), 1):
                match = re.match(r"\s*PORT\s*=\s*(\d+)", line)
                if match and int(match.group(1)) != 8000 + number:
                    add("port", relative, number_line,
                        f"PORT = {match.group(1)}, по правилу {8000 + number}")
                for url in PROVIDER_URLS:
                    if url in line:
                        add("boundary", relative, number_line, f"web.py знает {url}")
                        break

        for needle, entry in (("agents.db", f"{folder.name}/agents.db*"),
                              ("custom_roles.json", f"{folder.name}/custom_roles.json")):
            if needle in code and entry not in ignore:
                add("gitignore", ".gitignore", None, f"нет строки {entry}")
    return violations


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False, indent=2))
