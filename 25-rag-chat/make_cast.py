"""Собирает demo.svg — анимированную запись прогона chat_demo.py.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.

Каст ничего не выдумывает: всё берётся из data/chat_result.json (первый проход).

    python3 make_cast.py           → demo.svg (≤60 секунд)
"""

import html
import json
import pathlib
import sys
import textwrap

import chat

WIDTH, HEIGHT = 1000, 650
PAD_X, PAD_Y = 22, 34
LINE = 19
MAX_LINES = 31
MAX_SECONDS = 58
WRAP = 106

COLORS = {"plain": "#d4d4d8", "dim": "#71717a", "user": "#60a5fa", "good": "#4ade80",
          "bad": "#f87171", "warn": "#fbbf24", "head": "#f4f4f5", "accent": "#22d3ee",
          "mem": "#c084fc"}

RESULTS = pathlib.Path(__file__).with_name("data") / "chat_result.json"
SHOW = [1, 7, 8, 11, 13]   # ходы сценария «Экзамен»: старт, «кстати», возврат, поздний ход, итог


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
        step = min(MAX_SECONDS / count, 3.2)
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
                     f'<text x="76" y="21" fill="{COLORS["dim"]}" font-size="12">python3 chat_demo.py '
                     f'— RAG-чат: история + источники + память задачи</text>')
        for i, frame in enumerate(self.frames):
            rows = "".join(f'<text x="{PAD_X}" y="{PAD_Y + r * LINE}" fill="{COLORS[c]}" '
                           f'xml:space="preserve">{html.escape(t)}</text>'
                           for r, (t, c) in enumerate(frame))
            parts.append(f'<g class="frame" id="f{i}">{rows}</g>')
        parts.append("</svg>")
        return "\n".join(parts), total


def wrapped(cast, text, color, indent="    ", limit=3):
    lines = textwrap.wrap(" ".join(text.split()), WRAP - len(indent))
    if len(lines) > limit:
        lines = lines[:limit]
        lines[-1] = lines[-1][:WRAP - len(indent) - 1] + "…"
    for line in lines:
        cast.say(indent + line, color, frame=False)


def main():
    if not RESULTS.exists():
        print("нет data/chat_result.json — сначала python3 chat_demo.py")
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    run = data["runs"][0]["exam"]
    rows = {row["turn"]: row for row in run["rows"]}
    cast = Cast()
    cast.say(f"сценарий «Экзамен», 13 реплик; окно истории — {data['window']} сообщений, "
             f"дальше держит только память задачи", "head")
    cast.say()

    for n in SHOW:
        row = rows[n]
        cast.say(f"[{n}] {row['message'][:WRAP - 5]}", "user")
        m, h = row["memory"], row["history"]
        state = m["state"]
        cast.say(f"  память: цель — {state['goal'][:80]}", "mem", frame=False)
        if state["constraints"]:
            cast.say(f"          ограничения — {'; '.join(state['constraints'])[:84]}", "mem", frame=False)
        cast.say(f"  поиск: «{m['query'][:90]}»{' · побочный вопрос' if m['side'] else ''}", "dim", frame=False)
        for mode, cell in (("memory", m), ("history", h)):
            tag = "с памятью " if mode == "memory" else "без памяти"
            bad = f"  ⚠ {', '.join(cell['violations'])}" if cell["violations"] else ""
            cast.say(f"  {tag} · судья цель {cell.get('goal')}/2 · источники: "
                     f"{', '.join(cell['sources']) or 'нет'}{bad}",
                     "bad" if cell["violations"] else ("accent" if mode == "memory" else "plain"), frame=False)
            wrapped(cast, cell["answer"], "plain", limit=2)
        cast.say("", frame=True)
        cast.clear()

    cast.say(f"=== 2 сценария × 13 реплик × {len(data['runs'])} прохода", "dim")
    cast.say(f"  {'':34} {'цель (судья)':>14} {'источники':>10} {'нарушений после 5-го хода':>27} {'$':>8}", "head")
    for key, title in (("exam", "Экзамен"), ("detention", "Задержание")):
        for mode in chat.MODES:
            per = [r[key]["summary"][mode] for r in data["runs"]]
            s = per[0]
            goal = " / ".join(str(p["goal"]) for p in per)
            late = " / ".join(f"{p['violations_late']}" for p in per)
            label = "с памятью" if mode == "memory" else "без памяти"
            cast.say(f"  {title:10} {label:23} {goal + ' /26':>14} "
                     f"{s['with_sources']:>5}/{s['answered']:<4} {late + ' из ' + str(s['late_total']):>27} "
                     f"{s['cost']:>8.4f}", "accent" if mode == "memory" else "plain")

    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, {path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
