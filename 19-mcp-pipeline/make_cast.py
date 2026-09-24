"""Собирает demo.svg — анимированную запись пайплайна из трёх MCP-инструментов.

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
import os                         # noqa: E402
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
    import os
    import pathlib as _pathlib
    import re
    import tempfile

    # Каст работает на своих данных, чтобы не мешать рабочему мониторингу.
    temp = _pathlib.Path(tempfile.gettempdir())
    os.environ["ADVENT_PIPELINE_DB"] = str(temp / "cast_pipeline.db")
    os.environ["ADVENT_SNAPSHOTS"] = str(temp / "cast_snapshots.jsonl")
    os.environ["ADVENT_REPORTS"] = str(temp / "cast_reports")
    for path in (temp / "cast_pipeline.db", temp / "cast_snapshots.jsonl"):
        path.unlink(missing_ok=True)

    import pipeline, pipeline_server, store, tickets
    import tools as mcp_tools
    store.init()
    cast = Cast()

    cast.say("$ python3 pipeline.py   # цепочка из трёх MCP-инструментов", "dim")
    cast.say(f"событие: {tickets.EVENT_TITLE}", "head")
    cast.say()

    info = mcp_tools.BRIDGE.info()
    cast.say("Инструменты:", "head")
    for tool in info["tools"][:3]:
        schema = tool["schema"]
        required = set(schema.get("required") or [])
        args = ", ".join(f"{n}{'*' if n in required else ''}"
                         for n in (schema.get("properties") or {})) or "—"
        cast.say(f"  {tool['name']}({args})", "warn")
    cast.say("  шаги связаны только идентификаторами", "dim")
    cast.say()

    cast.say("Способ 1: порядок задан в коде", "head")
    first = pipeline.run(notify_on_change=False, verbose=False)
    cast.say(f"  1. tickets_fetch      → snapshot_id={first['snapshot_id']}", "good")
    cast.say(f"  2. changes_summarize  ← snapshot_id={first['snapshot_id']}", "good")
    cast.say(f"                        → report_id={first['report_id']}", "good")
    cast.say(f"  3. report_save        ← report_id={first['report_id']}", "good")
    cast.say(f"  готово за {first['seconds']} c · изменения: "
             f"{'да' if first['changed'] else 'нет'}", "dim")
    cast.say()

    cast.say("Способ 2: цепочку собирает агент", "head")
    import agent as agents
    agents.storage.DB_PATH = temp / "cast_agent.db"
    agents.storage.DB_PATH.unlink(missing_ok=True)
    agents.storage.init()
    agent = agents.create({
        "model": "deepseek-chat", "temperature": 0.0, "max_tokens": 450,
        "router": False, "task_advisor": False, "audit": False,
        "use_invariants": False, "keep_last": 0, "max_tool_rounds": 5,
        "name": "Оператор",
        "system_prompt": ("Ты оператор мониторинга. Инструменты строго по порядку: "
                          "шаг 1 даёт snapshot_id, шаг 2 принимает его и даёт report_id, "
                          "шаг 3 принимает report_id. Идентификаторы бери из ответов.")})
    task = "Проверь билеты и сохрани отчёт, если предложение изменилось."
    cast.say(f"вы → {task}", "user")
    reply = agent.ask(task)
    for call in reply["tool_calls"]:
        argument = next(iter(call["arguments"].values()), "")
        cast.say(f"  🔧 {call['name']}({str(argument)[:34]})", "warn")
    order = [c["name"] for c in reply["tool_calls"]][:3]
    cast.say(f"  порядок верный: "
             f"{order == ['tickets_fetch', 'changes_summarize', 'report_save']}", "good")
    cast.say()

    cast.say("Проверка связей: подменяем идентификатор", "head")
    for name, arguments in (("changes_summarize", {"snapshot_id": first["report_id"]}),
                            ("report_save", {"report_id": first["snapshot_id"]})):
        try:
            pipeline_server.run_tool(name, arguments)
            cast.say(f"  {name}: прошло, хотя не должно", "warn")
        except ValueError as error:
            cast.say(f"  {name}: отказ — {str(error)[:56]}", "good")
    cast.say()
    cast.say("проверка каждый час в :02, уведомление только при изменениях", "good")
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
