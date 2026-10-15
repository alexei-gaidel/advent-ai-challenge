"""Генератор справочной части docs/: схемы данных и API-описания берутся из кода.

Руками такие документы устаревают на следующий день, поэтому они собираются скриптом:

    docs/schemas.md   — все CREATE TABLE из *.py проекта, одинаковые схемы склеены
    docs/mcp-api.md   — инструменты каждого MCP-сервера, полученные настоящим tools/list
                        по stdio (тот же клиент, что у ассистента), и порты веб-серверов

    python3 gen_docs.py
"""

import collections
import json
import os
import pathlib
import re
import sys
import tempfile

from mcp_client import MCPClient, MCPError

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
DOCS = HERE / "docs"

CREATE = re.compile(r"CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)\s*\((.*?)\n\s*\);?",
                    re.S | re.I)
PORT = re.compile(r"^PORT\s*=\s*(\d{4})", re.M)
# Серверы, которые пишут свои базы при старте, получают временные пути через env.
SANDBOX_ENV = ("ADVENT_SCHEDULE_DB", "ADVENT_PIPELINE_DB", "ADVENT_NOTES")


def day_files(pattern):
    return sorted(p for p in ROOT.glob(f"[0-9][0-9]-*/{pattern}") if "__pycache__" not in str(p))


def schemas():
    """{(таблица, тело): [файлы]} — одна и та же схема в 8 днях печатается один раз."""
    groups = collections.OrderedDict()
    for path in day_files("*.py"):
        text = path.read_text(encoding="utf-8")
        for name, body in CREATE.findall(text):
            body = "\n".join(line.rstrip() for line in body.strip("\n").splitlines())
            groups.setdefault((name, body), []).append(str(path.relative_to(ROOT)))
    return groups


def write_schemas():
    groups = schemas()
    by_table = collections.defaultdict(list)
    for (name, body), files in groups.items():
        by_table[name].append((body, files))

    out = ["# Схемы данных проекта (SQLite)", "",
           "Сгенерировано `31-dev-assistant/gen_docs.py` из исходников: каждая таблица — "
           "это `CREATE TABLE` из кода. Базы лежат рядом с кодом дня и в git не попадают "
           "(`agents.db`, `index.db`, `chat.db` в `.gitignore`).", "",
           f"Всего таблиц: {len(by_table)}, вариантов схем: {len(groups)}.", ""]
    for name in sorted(by_table):
        variants = by_table[name]
        out += [f"## Таблица `{name}`", ""]
        for number, (body, files) in enumerate(variants, 1):
            days = sorted({f.split("/")[0] for f in files})
            label = f"Вариант {number}" if len(variants) > 1 else "Схема"
            out += [f"{label} — используется в: {', '.join(f'`{f}`' for f in files)} "
                    f"(дни: {', '.join(d[:2] for d in days)}).", "",
                    "```sql", f"CREATE TABLE {name} (", body, ");", "```", ""]
    (DOCS / "schemas.md").write_text("\n".join(out), encoding="utf-8")
    return len(by_table), len(groups)


def list_server_tools(path, sandbox):
    env = os.environ.copy()
    for key in SANDBOX_ENV:
        env[key] = str(sandbox / key.lower())
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    client = MCPClient.stdio(path.parent.name, [sys.executable, str(path)])
    # Транспорт дня 16 окружение не принимает — подставляем его на время запуска.
    saved = os.environ.copy()
    os.environ.update(env)
    try:
        client.connect()
        return client.server_info, client.list_tools()
    finally:
        client.close()
        os.environ.clear()
        os.environ.update(saved)


def describe_params(schema):
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    if not props:
        return "—"
    parts = []
    for name, spec in props.items():
        kind = spec.get("type", "?")
        mark = "" if name in required else "?"
        parts.append(f"`{name}{mark}: {kind}`")
    return ", ".join(parts)


def write_api():
    servers = [p for p in day_files("*server*.py") if p.parent.name != "30-private-llm-service"]
    out = ["# API проекта: MCP-серверы и веб-порты", "",
           "Сгенерировано `31-dev-assistant/gen_docs.py`. Список инструментов каждого "
           "сервера получен вживую: клиент запускает сервер по stdio, делает `initialize` "
           "и `tools/list`. `?` после имени параметра — необязательный.", ""]
    count = 0
    with tempfile.TemporaryDirectory() as sandbox:
        for path in servers:
            rel = path.relative_to(ROOT)
            try:
                info, tools = list_server_tools(path, pathlib.Path(sandbox))
            except MCPError as error:
                out += [f"## `{rel}`", "", f"Не запустился: {error}", ""]
                continue
            count += len(tools)
            out += [f"## `{rel}` — {info.get('name', '?')} {info.get('version', '')}", "",
                    f"Запуск: `python3 {rel}` (stdio, JSON-RPC 2.0). Инструментов: {len(tools)}.",
                    "", "| Инструмент | Параметры | Описание |", "|---|---|---|"]
            for tool in tools:
                desc = " ".join(tool.get("description", "").split()).replace("|", "/")
                out.append(f"| `{tool['name']}` | {describe_params(tool.get('inputSchema'))} "
                           f"| {desc} |")
            out.append("")

    out += ["## Порты веб-интерфейсов", "",
            "Правило проекта: порт = 8000 + номер дня. Значения прочитаны из констант `PORT`.",
            "", "| Папка | Файл | Порт |", "|---|---|---|"]
    for path in day_files("*.py"):
        if path.name not in ("web.py", "server.py"):
            continue
        match = PORT.search(path.read_text(encoding="utf-8"))
        if match:
            out.append(f"| `{path.parent.name}` | `{path.name}` | {match.group(1)} |")
    out.append("")
    (DOCS / "mcp-api.md").write_text("\n".join(out), encoding="utf-8")
    return len(servers), count


if __name__ == "__main__":
    DOCS.mkdir(exist_ok=True)
    tables, variants = write_schemas()
    print(f"docs/schemas.md: {tables} таблиц, {variants} вариантов схем")
    servers, tools = write_api()
    print(f"docs/mcp-api.md: {servers} серверов, {tools} инструментов")
