"""Собирает demo.svg — анимированную запись сессии ассистента разработчика.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.

Каст ничего не выдумывает: первая часть — живые команды ассистента в момент сборки,
вторая — итоговая таблица из data/help_result.json (прогон help_demo.py).

    python3 make_cast.py           → demo.svg (≤60 секунд)
"""

import html
import json
import pathlib
import sys
import textwrap

import assistant as dev

WIDTH, HEIGHT = 1000, 650
PAD_X, PAD_Y = 22, 34
LINE = 19
MAX_LINES = 31
MAX_SECONDS = 58
WRAP = 106

COLORS = {"plain": "#d4d4d8", "dim": "#71717a", "user": "#60a5fa", "good": "#4ade80",
          "bad": "#f87171", "warn": "#fbbf24", "head": "#f4f4f5", "accent": "#22d3ee",
          "tool": "#c084fc"}

RESULTS = pathlib.Path(__file__).with_name("data") / "help_result.json"
SESSION = [
    "/branch",
    "/help Где лежит MCP-клиент и какие транспорты он поддерживает?",
    "/help Что сейчас не закоммичено?",
    "/help Какой таймаут по умолчанию у HttpTransport в mcp_client.py дня 16?",
]


class Cast:
    def __init__(self):
        self.lines, self.frames = [], []

    def say(self, text="", color="plain", frame=True):
        print(text)
        for chunk in (text.split("\n") if text else [""]):
            self.lines.append((chunk, color))
        if frame:
            self.frames.append(list(self.lines[-MAX_LINES:]))

    def clear(self):
        self.lines = []

    def svg(self):
        count = len(self.frames)
        step = min(MAX_SECONDS / count, 4.0)
        total = round(step * count, 2)
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
                 f'width="{WIDTH}" height="{HEIGHT}" font-family="ui-monospace, SFMono-Regular, '
                 f'Menlo, Consolas, monospace" font-size="13.5">',
                 "<style>", ".bg{fill:#18181b}.frame{opacity:0}"]
        for i in range(count):
            start, end, gap = 100 * i / count, 100 * (i + 1) / count, 0.01
            parts.append(f"#f{i}{{animation:a{i} {total}s steps(1,end) infinite}}"
                         f"@keyframes a{i}{{0%,{max(start - gap, 0):.3f}%{{opacity:0}}"
                         f"{start:.3f}%,{max(end - gap, start):.3f}%{{opacity:1}}"
                         f"{end:.3f}%,100%{{opacity:0}}}}")
        parts.append("</style>")
        parts.append(f'<rect class="bg" width="{WIDTH}" height="{HEIGHT}" rx="10"/>')
        parts.append(f'<circle cx="20" cy="16" r="5" fill="#ef4444"/><circle cx="38" cy="16" r="5" '
                     f'fill="#fbbf24"/><circle cx="56" cy="16" r="5" fill="#22c55e"/>'
                     f'<text x="76" y="21" fill="{COLORS["dim"]}" font-size="12">python3 cli.py '
                     f'— ассистент разработчика: RAG по документации + MCP к git</text>')
        for i, frame in enumerate(self.frames):
            rows = "".join(f'<text x="{PAD_X}" y="{PAD_Y + r * LINE}" fill="{COLORS[c]}" '
                           f'xml:space="preserve">{html.escape(t)}</text>'
                           for r, (t, c) in enumerate(frame))
            parts.append(f'<g class="frame" id="f{i}">{rows}</g>')
        parts.append("</svg>")
        return "\n".join(parts), total


def wrapped(cast, text, color, indent="  ", limit=4):
    lines = []
    for paragraph in text.splitlines():
        lines += textwrap.wrap(paragraph, WRAP - len(indent)) or []
    if len(lines) > limit:
        lines = lines[:limit]
        lines[-1] = lines[-1][:WRAP - len(indent) - 1] + "…"
    for line in lines:
        cast.say(indent + line, color, frame=False)


def session(cast):
    bot = dev.DevAssistant()
    for command in SESSION:
        cast.clear()
        cast.say(f"› {command}", "user")
        r = bot.handle(command)
        wrapped(cast, r["answer"], "plain", limit=6 if r["kind"] == "answer" else 3)
        cast.say("", frame=False)
        for t in r.get("tools", []):
            args = f" {json.dumps(t['arguments'], ensure_ascii=False)}" if t.get("arguments") else ""
            cast.say(f"  MCP {t['name']}{args} · вызвал: {t['by']}",
                     "tool" if t["by"] == "модель" else "good", frame=False)
        for s in r.get("sources", [])[:4]:
            cast.say(f"  RAG [{s['n']}] {s['source']} › {s['section'][:52]} · {s['score']}",
                     "dim", frame=False)
        if r["kind"] == "answer":
            cast.say(f"  {r['prompt_tokens']}+{r['completion_tokens']} токенов · ${r['cost']} · "
                     f"{r['seconds']} с", "dim", frame=False)
        cast.say("", frame=True)
        cast.say("", frame=True)       # держим кадр с ответом подольше


def table(cast):
    if not RESULTS.exists():
        cast.say("нет data/help_result.json — сначала python3 help_demo.py", "warn")
        return
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    cast.clear()
    cast.say(f"=== help_demo.py: 14 вопросов × 3 режима × {data['passes']} прохода, "
             f"судья {data['judge']} (Groq)", "head")
    cast.say("    docs — ответ в README/docs · git — только в репозитории · code — только в файле",
             "dim", frame=False)
    cast.say("", frame=False)
    cast.say(f"  {'режим':14} {'тип':5} {'эталон найден':>14} {'судья':>10} {'токенов':>9} "
             f"{'с/вопрос':>9}", "head", frame=False)
    for line in data["summary"]:
        color = {"llm": "bad", "rag": "plain", "full": "accent"}[line["mode"]]
        if line["kind"] == "all":
            color = "head" if line["mode"] != "full" else "good"
        cast.say(f"  {dev.MODES[line['mode']]:14} {line['kind']:5} "
                 f"{str(line['found']) + '/' + str(line['n']):>14} "
                 f"{str(line['judge']) + '/' + str(line['judge_max']):>10} "
                 f"{line['tokens']:>9} {line['seconds']:>9}", color, frame=False)
        if line["kind"] == "all":
            cast.say("", frame=False)
    cast.say("", frame=True)
    cast.say("", frame=True)
    cast.say("", frame=True)


def main():
    cast = Cast()
    session(cast)
    table(cast)
    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, {path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
