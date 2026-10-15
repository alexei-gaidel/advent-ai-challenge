"""Собирает demo.svg — анимированную запись ревью демо-PR с подсаженными проблемами.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.

Каст ничего не выдумывает: всё берётся из data/review_result.json (прогон review_demo.py).

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
RESULTS = HERE / "data" / "review_result.json"
SEEDED = json.loads((HERE / "demo" / "seeded.json").read_text(encoding="utf-8"))
MODES = {"no-rag": "без RAG", "bm25": "RAG · BM25", "bge-m3": "RAG · bge-m3",
         "pinned": "bge-m3 + правила"}


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
                     f'review_demo.py — AI-ревью PR с 7 подсаженными проблемами</text>')
        for i, frame in enumerate(self.frames):
            rows = "".join(f'<text x="{PAD_X}" y="{PAD_Y + r * LINE}" fill="{COLORS[c]}" '
                           f'xml:space="preserve">{html.escape(t)}</text>'
                           for r, (t, c) in enumerate(frame))
            parts.append(f'<g class="frame" id="f{i}">{rows}</g>')
        parts.append("</svg>")
        return "\n".join(parts), total


def show_review(cast, row, lines):
    """Замечания одного ревью; совпавшие с подсаженными помечены ✓ и id проблемы."""
    result = row["result"]
    matched = {v: k for k, v in row["found"].items() if isinstance(v, int)}
    number = 0
    for section, title, color in (("bugs", "Потенциальные баги", "bad"),
                                  ("architecture", "Архитектурные проблемы", "arch"),
                                  ("recommendations", "Рекомендации", "plain")):
        items = result.get(section) or []
        cast.say(f"  ### {title} ({len(items)})", "head", frame=False)
        for item in items:
            number += 1
            hit = matched.get(number)
            mark = f"✓ {hit}" if hit else "·"
            text = f"{item.get('file', '?').split('/')[-1]}:{item.get('line')} {item.get('title', '')}"
            cast.say(f"  {mark:<22} {text[:WRAP - 26]}", "good" if hit else color, frame=False)
    missed = [k for k, v in row["found"].items() if not isinstance(v, int)]
    if missed:
        cast.say(f"  пропущено: {', '.join(missed)}", "warn", frame=False)


def main():
    if not RESULTS.exists():
        print("нет data/review_result.json — сначала python3 review_demo.py")
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    rows = data["rows"]
    cast = Cast()
    cast.say(f"PR: {SEEDED['title']}", "user")
    cast.say("  diff: 4 файла, +97 строк · подсажено 7 проблем (5 багов, 2 архитектурные):", "dim",
             frame=False)
    for issue in SEEDED["issues"]:
        where = f"{issue['file'].split('/')[-1]}:{data['seeded_lines'][issue['id']]}"
        rule = " · правило CLAUDE.md" if issue["rule"] else ""
        cast.say(f"    {issue['id']:20} {where:22} {issue['what'][:52]}{rule}", "dim", frame=False)
    cast.say("", frame=True)
    cast.say("", frame=True)

    for mode in ("no-rag", "pinned"):
        row = next(r for r in rows if r["mode"] == mode and r["pass"] == 1)
        cast.clear()
        cast.say(f"› ревью {MODES[mode]}: найдено {row['recall']}/7 · замечаний {row['findings']} · "
                 f"{row['tokens']} токенов · {row['seconds']} с", "accent")
        if row["context"]:
            cast.say(f"  RAG: {len(row['context'])} фрагментов, в т.ч. "
                     f"{'; '.join(c for c in row['context'] if 'CLAUDE' in c)[:80] or '—'}",
                     "dim", frame=False)
        show_review(cast, row, data["seeded_lines"])
        cast.say("", frame=True)
        cast.say("", frame=True)
        cast.say("", frame=True)

    cast.clear()
    cast.say(f"=== {data['passes']} прохода × 4 режима, судья {data['judge']} (Groq)", "head")
    cast.say("", frame=False)
    cast.say(f"  {'режим':17} {'найдено из 7':>12} {'правила из 5':>14} {'сверх: по делу':>15} "
             f"{'ложных':>7} {'ток/ревью':>10}", "head", frame=False)
    for line in data["summary"]:
        cast.say(f"  {MODES[line['mode']]:17} {' / '.join(map(str, line['recall'])):>12} "
                 f"{' / '.join(map(str, line['recall_rule'])):>14} {line['extra_valid']:>15} "
                 f"{line['extra_wrong']:>7} {line['tokens']:>10}",
                 "good" if line["mode"] == "pinned" else "plain", frame=False)
    cast.say("", frame=False)
    cast.say("  находки по проблемам, проходов из 3:   без RAG  BM25  bge-m3  +правила", "dim",
             frame=False)
    for issue in SEEDED["issues"]:
        cells = [l["per_issue"][issue["id"]] for l in data["summary"]]
        cast.say(f"    {issue['id']:35} {cells[0]:>7} {cells[1]:>5} {cells[2]:>7} {cells[3]:>9}",
                 "warn" if cells[3] > cells[2] else "plain", frame=False)
    for _ in range(4):
        cast.say("", frame=True)

    markup, seconds = cast.svg()
    path = HERE / "demo.svg"
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, {path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
