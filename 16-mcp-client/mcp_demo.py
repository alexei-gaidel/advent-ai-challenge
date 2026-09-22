"""Подключается к MCP-серверам и печатает их инструменты.

    python3 mcp_demo.py                 # публичные серверы + свой локальный
    python3 mcp_demo.py <url>           # произвольный сервер по HTTP

Каждый сервер обрабатывается независимо: если чужой недоступен, прогон продолжается.
"""

import json
import sys

from mcp_client import LOCAL_SERVER, PUBLIC_SERVERS, MCPClient, MCPError


def head(title):
    print(f"\n{'=' * 76}\n{title}\n{'=' * 76}")


def show(client):
    """Подключается, печатает данные сервера и список инструментов."""
    client.connect()
    info = client.server_info
    print(f"  сервер:   {info.get('name', '—')} {info.get('version', '')}".rstrip())
    print(f"  протокол: {client.protocol_version} · транспорт: {client.transport.kind}"
          f" · соединение за {client.connect_seconds} c")

    tools = client.list_tools()
    print(f"  инструментов: {len(tools)}\n")
    for tool in tools:
        description = " ".join((tool.get("description") or "").split())
        print(f"    {tool['name']}")
        print(f"      {description[:96]}")
    return tools


def show_schema(tool):
    """Схема аргументов одного инструмента — то, по чему модель поймёт, что передавать."""
    schema = tool.get("inputSchema") or {}
    required = set(schema.get("required") or [])
    print(f"\n  схема аргументов «{tool['name']}»:")
    properties = schema.get("properties") or {}
    if not properties:
        print("    (аргументов нет)")
    for name, field in properties.items():
        kind = _type_of(field)
        mark = "обязательный" if name in required else "необязательный"
        extra = f" · варианты: {', '.join(map(str, field['enum']))}" if field.get("enum") else ""
        print(f"    {name}: {kind} · {mark}{extra}")
        if field.get("description"):
            print(f"      {field['description']}")


def _type_of(field):
    """Тип аргумента. Часть серверов описывает его не «type», а union через anyOf."""
    if field.get("type"):
        return field["type"]
    for key in ("anyOf", "oneOf"):
        if field.get(key):
            kinds = [item.get("type", "?") for item in field[key]]
            return " | ".join(dict.fromkeys(kinds))
    return "не указан"


def main():
    targets = []
    if len(sys.argv) > 1:
        targets = [("Указанный сервер", MCPClient.http("Указанный", sys.argv[1]))]
    else:
        targets = [(name, MCPClient.http(name, url)) for name, url in PUBLIC_SERVERS]
        targets.append(("Свой сервер (stdio)", MCPClient.stdio("Свой", LOCAL_SERVER)))

    summary = []
    first_tool = None

    for title, client in targets:
        head(title)
        try:
            tools = show(client)
            summary.append({
                "name": title,
                "transport": client.transport.kind,
                "protocol": client.protocol_version,
                "tools": len(tools),
                "seconds": client.connect_seconds,
            })
            if tools and first_tool is None:
                first_tool = tools[0]
        except MCPError as error:
            print(f"  не получилось: {error}")
            summary.append({"name": title, "transport": client.transport.kind,
                            "protocol": "—", "tools": "—", "seconds": "—"})
        finally:
            client.close()

    if first_tool:
        head("СХЕМА АРГУМЕНТОВ")
        show_schema(first_tool)

    head("ИТОГ")
    print(f"{'сервер':<24}{'транспорт':>12}{'протокол':>14}"
          f"{'инструментов':>15}{'секунд':>9}")
    for row in summary:
        print(f"{row['name']:<24}{row['transport']:>12}{str(row['protocol']):>14}"
              f"{str(row['tools']):>15}{str(row['seconds']):>9}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
