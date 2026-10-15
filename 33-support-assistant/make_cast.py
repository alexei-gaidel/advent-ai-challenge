"""Собирает demo.svg — анимированную запись ответов ассистента поддержки.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.

Каст ничего не выдумывает: всё берётся из data/support_result.json (прогон support_demo.py).

    python3 make_cast.py           → demo.svg (≤60 секунд)
"""

import html
import json
import pathlib
import sys
import textwrap

WIDTH, HEIGHT = 1000, 650
PAD_X, PAD_Y = 22, 34
LINE = 19
MAX_LINES = 31
MAX_SECONDS = 58
WRAP = 106

COLORS = {"plain": "#d4d4d8", "dim": "#71717a", "user": "#60a5fa", "good": "#4ade80",
          "bad": "#f87171", "warn": "#fbbf24", "head": "#f4f4f5", "accent": "#22d3ee",
          "arch": "#c084fc"}

HERE = pathlib.Path(__file__).resolve().parent
RESULTS = HERE / "data" / "support_result.json"
MODES = {"llm": "только модель", "rag": "RAG без CRM", "full": "RAG + CRM"}


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
                     f'<text x="76" y="21" fill="{COLORS["dim"]}" font-size="12">python3 '
                     f'support_demo.py — ассистент поддержки: RAG по FAQ + CRM через MCP</text>')
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
        print("нет data/support_result.json — сначала python3 support_demo.py")
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    rows = {r["ticket"]: r for r in data["rows"] if r["pass"] == 1}
    cast = Cast()
    cast.say("Три тикета, один и тот же вопрос: «Почему не работает авторизация?»", "head")
    cast.say("", frame=True)
    for ticket in ("T-101", "T-102", "T-103"):
        row = rows[ticket]
        cast.clear()
        cast.say(f"{ticket} · «{row['subject']}»", "user")
        for mode, color in (("llm", "bad"), ("rag", "plain"), ("full", "good")):
            v = row["modes"][mode]
            flag = " · ⚠ выдумка" if v["invented"] else ""
            cast.say(f"  {MODES[mode]:14} судья: причина {v['cause']}/2, решение {v['fix']}/2{flag}",
                     color, frame=False)
            wrapped(cast, v["diagnosis"] or v["reply"], "dim" if mode != "full" else "plain",
                    limit=3 if mode == "full" else 2)
        cast.say("", frame=True)
        for _ in range(3):
            cast.say("", frame=True)

    cast.clear()
    cast.say(f"=== 10 тикетов × 3 режима × {data['passes']} прохода, судья {data['judge']} (Groq)", "head")
    cast.say("", frame=False)
    cast.say(f"  {'режим':15} {'причина':>9} {'решение':>9} {'«авторизация»':>14} {'выдумки':>9} {'ток':>6}",
             "head", frame=False)
    for line in data["summary"]:
        n = line["n"]
        cast.say(f"  {MODES[line['mode']]:15} {str(line['cause']) + '/' + str(2 * n):>9} "
                 f"{str(line['fix']) + '/' + str(2 * n):>9} "
                 f"{str(line['auth_cause']) + '/' + str(2 * line['auth_n']):>14} "
                 f"{str(line['invented']) + '/' + str(n):>9} {line['tokens']:>6}",
                 "good" if line["mode"] == "full" else "plain", frame=False)
    cast.say("", frame=False)
    cast.say("  по тикетам, причина за 3 прохода:        только модель  RAG  RAG+CRM", "dim", frame=False)
    for ticket, row in sorted(rows.items()):
        per = [sum(r["modes"][m]["cause"] or 0 for r in data["rows"] if r["ticket"] == ticket)
               for m in MODES]
        color = "warn" if per[2] < per[1] else "plain"
        cast.say(f"    {ticket} {row['subject'][:30]:30} {per[0]:>12} {per[1]:>5} {per[2]:>8}",
                 color, frame=False)
    for _ in range(5):
        cast.say("", frame=True)

    markup, seconds = cast.svg()
    path = HERE / "demo.svg"
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, {path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
