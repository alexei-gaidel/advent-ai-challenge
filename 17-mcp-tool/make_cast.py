"""Собирает demo.svg — анимированную запись вызова MCP-инструмента агентом.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией —
он крутится в браузере и прямо в README на GitHub, зависимостей не требует.

    python3 make_cast.py           → demo.svg (≤60 секунд)
"""

import html
import pathlib
import sys
import tempfile

import storage

storage.DB_PATH = pathlib.Path(tempfile.gettempdir()) / "cast.db"

import agent as agents            # noqa: E402
import tools as mcp_tools         # noqa: E402
import profiles                   # noqa: E402

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
    storage.DB_PATH.unlink(missing_ok=True)
    storage.init()
    cast = Cast()

    cast.say("$ python3 web.py   # агент с инструментами MCP", "dim")
    info = mcp_tools.BRIDGE.info()
    cast.say(f"MCP-сервер: {info['server'].get('name')} {info['server'].get('version')} "
             f"· протокол {info['protocol']} · stdio", "head")
    for tool in info["tools"]:
        schema = tool["schema"]
        required = set(schema.get("required") or [])
        args = ", ".join(f"{n}{'*' if n in required else ''}"
                         for n in (schema.get("properties") or {})) or "—"
        cast.say(f"  {tool['name']}({args})", "warn")
    cast.say()

    base = {"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 400,
            "router": False, "task_advisor": False, "audit": False,
            "use_invariants": False, "keep_last": 0,
            "system_prompt": "Ты помогаешь разобраться в репозитории. Отвечай кратко фактами."}

    question = "Когда было сделано задание 14 про инварианты и что в нём появилось?"

    cast.say("Без инструментов:", "head")
    blind = agents.create({**base, "name": "Без", "use_tools": False})
    cast.say(f"вы → {question}", "user")
    answer = blind.ask(question)
    for line in answer["text"].strip().splitlines()[:3]:
        cast.say(f"агент ← {line[:76]}", "agent")
    cast.say()

    cast.say("С инструментами:", "head")
    smart = agents.create({**base, "name": "С инструментами", "use_tools": True})
    cast.say(f"вы → {question}", "user")
    answer = smart.ask(question)
    for call in answer["tool_calls"]:
        cast.say(f"  🔧 круг {call['round']}: {call['name']}({call['arguments']})", "warn")
        cast.say(f"     → {call['result'][:66].splitlines()[0]}", "dim")
    for line in answer["text"].strip().splitlines()[:5]:
        cast.say(f"агент ← {line[:76]}", "good")
    cast.say()
    cast.say(f"вызовов инструментов: {smart.stats['tool_calls']} "
             f"за {smart.stats['tool_rounds']} круга", "dim")
    cast.say("ответ собран из настоящей истории git, а не придуман", "good")

    mcp_tools.BRIDGE.close()

    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    storage.DB_PATH.unlink(missing_ok=True)

    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, "
          f"{path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
