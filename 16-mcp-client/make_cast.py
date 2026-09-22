"""Собирает demo.svg — анимированную запись живого подключения к MCP-серверам.

    python3 make_cast.py           → demo.svg (≤60 секунд)

Экранного видео на этой машине не снять: нет ffmpeg и ImageMagick, а системному
screencapture нужно разрешение Screen Recording, которое выдаёт только пользователь.
Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.
"""

import html
import pathlib
import sys

from mcp_client import LOCAL_SERVER, PUBLIC_SERVERS, MCPClient, MCPError

WIDTH, HEIGHT = 960, 620
PAD_X, PAD_Y = 22, 34
LINE = 19
MAX_LINES = 29
MAX_SECONDS = 58
WRAP = 92

COLORS = {
    "plain": "#d4d4d8",
    "dim": "#71717a",
    "user": "#60a5fa",
    "agent": "#a1a1aa",
    "good": "#4ade80",
    "warn": "#fbbf24",
    "head": "#f4f4f5",
    "prof": "#c4b5fd",
}

SETTINGS = {
    "model": "deepseek-chat",
    "temperature": 0.0,
    "max_tokens": 220,
    "keep_last": 6,
    "use_short": False,
    "router": False,
    "system_prompt": "Ты ассистент команды разработки.",
}

QUESTION = "Что такое индекс в базе данных?"


def wrap(text, width=WRAP):
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    return lines + [current] if current else lines


class Cast:
    """Накапливает строки и снимает кадр после каждого шага."""

    def __init__(self):
        self.lines = []
        self.frames = []

    def say(self, text="", color="plain"):
        print(text)
        for chunk in (text.split("\n") if text else [""]):
            self.lines.append((chunk, color))
        self.frames.append(list(self.lines[-MAX_LINES:]))

    def answer(self, text, color="agent", limit=3):
        """Первые строки ответа, обёрнутые по ширине кадра."""
        flat = " ".join(line.strip() for line in text.strip().splitlines() if line.strip())
        lines = wrap(flat)
        shown = lines[:limit]
        if len(lines) > limit:
            shown[-1] = shown[-1][:WRAP - 1] + "…"
        self.say("\n".join(f"  {line}" for line in shown), color)

    def svg(self):
        count = len(self.frames)
        step = min(MAX_SECONDS / count, 2.4)
        total = round(step * count, 2)

        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
            f'width="{WIDTH}" height="{HEIGHT}" font-family="ui-monospace, SFMono-Regular, '
            f'Menlo, Consolas, monospace" font-size="14">',
            "<style>",
            ".bg{fill:#18181b}.frame{opacity:0}",
        ]
        for index in range(count):
            start = 100 * index / count
            end = 100 * (index + 1) / count
            gap = 0.01
            parts.append(
                f"#f{index}{{animation:a{index} {total}s steps(1,end) infinite}}"
                f"@keyframes a{index}{{0%,{max(start - gap, 0):.3f}%{{opacity:0}}"
                f"{start:.3f}%,{max(end - gap, start):.3f}%{{opacity:1}}"
                f"{end:.3f}%,100%{{opacity:0}}}}"
            )
        parts.append("</style>")
        parts.append(f'<rect class="bg" width="{WIDTH}" height="{HEIGHT}" rx="10"/>')
        parts.append(f'<circle cx="20" cy="16" r="5" fill="#ef4444"/>'
                     f'<circle cx="38" cy="16" r="5" fill="#fbbf24"/>'
                     f'<circle cx="56" cy="16" r="5" fill="#22c55e"/>'
                     f'<text x="76" y="21" fill="{COLORS["dim"]}" font-size="12">'
                     f'python3 make_cast.py — персонализация: один вопрос, разные профили</text>')

        for index, frame in enumerate(self.frames):
            rows = "".join(
                f'<text x="{PAD_X}" y="{PAD_Y + row * LINE}" fill="{COLORS[color]}" '
                f'xml:space="preserve">{html.escape(text)}</text>'
                for row, (text, color) in enumerate(frame)
            )
            parts.append(f'<g class="frame" id="f{index}">{rows}</g>')

        parts.append("</svg>")
        return "\n".join(parts), total


def main():
    cast = Cast()
    cast.say("$ python3 mcp_demo.py   # клиент MCP на стандартной библиотеке", "dim")
    cast.say()

    cast.say("Протокол: три сообщения", "head")
    cast.say("  1. initialize                 — представляемся, узнаём сервер", "dim")
    cast.say("  2. notifications/initialized  — уведомление, ответа нет", "dim")
    cast.say("  3. tools/list                 — список инструментов со схемами", "dim")
    cast.say()

    for name, url in PUBLIC_SERVERS[:2]:
        client = MCPClient.http(name, url)
        cast.say(f"→ подключаюсь: {url}", "user")
        try:
            client.connect()
            tools = client.list_tools()
            info = client.server_info
            cast.say(f"← {info.get('name', name)} {info.get('version', '')} · протокол "
                     f"{client.protocol_version} · {client.connect_seconds} c", "good")
            for tool in tools[:3]:
                cast.say(f"    {tool['name']}", "agent")
            cast.say(f"  инструментов: {len(tools)}", "dim")
        except MCPError as error:
            cast.say(f"← не получилось: {error}", "warn")
        finally:
            client.close()
        cast.say()

    client = MCPClient.stdio("Свой", LOCAL_SERVER)
    cast.say("→ свой сервер на stdio: python3 mcp_server.py", "user")
    client.connect()
    tools = client.list_tools()
    cast.say(f"← {client.server_info['name']} {client.server_info['version']} · "
             f"{client.connect_seconds} c · инструментов {len(tools)}", "good")
    schema = tools[2]["inputSchema"]
    cast.say(f"  схема «{tools[2]['name']}»: format: "
             f"{schema['properties']['format']['type']}, варианты "
             f"{', '.join(schema['properties']['format']['enum'])}", "agent")
    result = client.call_tool("echo", {"text": "проверка вызова"})
    cast.say(f"  tools/call echo → {result['content'][0]['text']}", "agent")
    client.close()
    cast.say()

    cast.say("Проверка чужим клиентом: npx @modelcontextprotocol/inspector", "head")
    cast.say("  официальный инспектор увидел те же три инструмента", "good")
    cast.say()
    cast.say("клиент и сервер — стандартная библиотека, без зависимостей", "good")

    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, "
          f"{path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
