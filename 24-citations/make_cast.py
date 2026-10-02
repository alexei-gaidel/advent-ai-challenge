"""Собирает demo.svg — анимированную запись прогона citations_demo.py.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.

Каст ничего не выдумывает: всё берётся из data/citations_result.json (первый проход).

    python3 make_cast.py           → demo.svg (≤60 секунд)
"""

import html
import json
import pathlib
import sys
import textwrap

import agent

WIDTH, HEIGHT = 1000, 650
PAD_X, PAD_Y = 22, 34
LINE = 19
MAX_LINES = 31
MAX_SECONDS = 58
WRAP = 106

COLORS = {"plain": "#d4d4d8", "dim": "#71717a", "user": "#60a5fa", "good": "#4ade80",
          "bad": "#f87171", "warn": "#fbbf24", "head": "#f4f4f5", "accent": "#22d3ee"}

RESULTS = pathlib.Path(__file__).with_name("data") / "citations_result.json"
SHOW = [2, 7, 8]   # брак (цитата), штраф (не знаю), «что там про сроки» (гейт не сработал)


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
        step = min(MAX_SECONDS / count, 3.0)
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
                     f'<text x="76" y="21" fill="{COLORS["dim"]}" font-size="12">python3 citations_demo.py '
                     f'— ответ + источники + цитаты, режим «не знаю»</text>')
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
        print("нет data/citations_result.json — сначала python3 citations_demo.py")
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    run, questions = data["runs"][0], data["questions"]
    p = data["params"]
    cast = Cast()
    cast.say(f"отбор: топ-{p['k_before']} → порог {p['threshold']} → реранкер → топ-{p['k_after']}; "
             f"«не знаю», если пусто или лучший реранк < {data['gate_min']}/10", "head")
    cast.say("цитата засчитывается, только если дословно найдена в своём чанке", "dim")
    cast.say()

    for n in SHOW:
        item, c = questions[n], run["rows"][n]["cited"]
        cast.say(f"? {item['q']}", "user")
        cast.say(f"  реранк лучшего фрагмента: {c['best_rerank'] if c['best_rerank'] is not None else '— (всё ниже порога)'}"
                 f"   судья: {c['correct']}/2, {c['support']}", "dim", frame=False)
        if c["unknown"]:
            cast.say("  НЕ ЗНАЮ", "warn", frame=False)
            wrapped(cast, c["answer"], "warn")
        else:
            wrapped(cast, c["answer"], "plain")
            for s in c["sources"][:3]:
                cast.say(f"  источник {s['chunk_id']:9} {s['source'].split('/')[-1]:30} {s['section'][:52]}",
                         "accent", frame=False)
            for q in c["quotes"][:3]:
                cast.say(f"  ✓ «{q['text'][:WRAP - 18]}»", "good", frame=False)
            for q in c["rejected"][:2]:
                cast.say(f"  ✗ «{q['text'][:WRAP - 30]}» — {q['status']}", "bad", frame=False)
        cast.say("", frame=True)
        cast.say()
        cast.clear()

    cast.say(f"=== 10 вопросов × 2 режима × {len(data['runs'])} прохода", "dim")
    for i, item in enumerate(questions):
        r, c = run["rows"][i]["rag"], run["rows"][i]["cited"]
        right = "не знаю" if c["unknown"] else f"ист {len(c['sources'])} · цит {len(c['quotes'])}"
        cast.say(f"  {i + 1:>2}. {item['type']:7}  rag {r['correct']}/2 ист {len(r['sources'])} цит 0   │   "
                 f"cited {c['correct']}/2 {right:16} {item['q'][:34]}",
                 "warn" if c["unknown"] else "plain", frame=False)
    cast.say("", frame=True)
    for mode in agent.MODES:
        per = [r["summary"][mode] for r in data["runs"]]
        s = per[0]
        cast.say(f"  {agent.MODE_LABELS[mode]:29} верно {' / '.join(str(x['correct_sum']) for x in per)} из 20 · "
                 f"с цитатами {s['with_quotes']}/{s['answered']} · «не знаю» {s['unknown_hit']}/{s['unknown_expected']} · "
                 f"отбраковано {s['rejected']}", "accent" if mode == "cited" else "plain")

    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, {path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
