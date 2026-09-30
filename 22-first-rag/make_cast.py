"""Собирает demo.svg — анимированную запись прогона rag_demo.py.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.

Каст ничего не выдумывает: ответы, оценки и цифры берутся из data/rag_result.json
(первый проход прогона), поиск по вопросам выполняется вживую по index.db.

    python3 make_cast.py           → demo.svg (≤60 секунд)
"""

import html
import json
import pathlib
import sys
import textwrap

import agent
import index

WIDTH, HEIGHT = 980, 640
PAD_X, PAD_Y = 22, 34
LINE = 19
MAX_LINES = 30
MAX_SECONDS = 58
WRAP = 104

COLORS = {
    "plain": "#d4d4d8", "dim": "#71717a", "user": "#60a5fa", "good": "#4ade80",
    "bad": "#f87171", "warn": "#fbbf24", "head": "#f4f4f5", "rag": "#22d3ee",
}

RESULTS = pathlib.Path(__file__).with_name("data") / "rag_result.json"
SHOW = [4, 3]   # номера вопросов (с нуля), которые проигрываются подробно


class Cast:
    """Накапливает строки и снимает кадр после каждого шага."""

    def __init__(self):
        self.lines = []
        self.frames = []

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
        step = min(MAX_SECONDS / count, 2.6)
        total = round(step * count, 2)
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
            f'width="{WIDTH}" height="{HEIGHT}" font-family="ui-monospace, SFMono-Regular, '
            f'Menlo, Consolas, monospace" font-size="14">',
            "<style>", ".bg{fill:#18181b}.frame{opacity:0}",
        ]
        for i in range(count):
            start, end, gap = 100 * i / count, 100 * (i + 1) / count, 0.01
            parts.append(
                f"#f{i}{{animation:a{i} {total}s steps(1,end) infinite}}"
                f"@keyframes a{i}{{0%,{max(start - gap, 0):.3f}%{{opacity:0}}"
                f"{start:.3f}%,{max(end - gap, start):.3f}%{{opacity:1}}"
                f"{end:.3f}%,100%{{opacity:0}}}}")
        parts.append("</style>")
        parts.append(f'<rect class="bg" width="{WIDTH}" height="{HEIGHT}" rx="10"/>')
        parts.append(f'<circle cx="20" cy="16" r="5" fill="#ef4444"/>'
                     f'<circle cx="38" cy="16" r="5" fill="#fbbf24"/>'
                     f'<circle cx="56" cy="16" r="5" fill="#22c55e"/>'
                     f'<text x="76" y="21" fill="{COLORS["dim"]}" font-size="12">'
                     f'python3 rag_demo.py — Конституция РФ, ответ без RAG против ответа с RAG</text>')
        for i, frame in enumerate(self.frames):
            rows = "".join(
                f'<text x="{PAD_X}" y="{PAD_Y + row * LINE}" fill="{COLORS[color]}" '
                f'xml:space="preserve">{html.escape(text)}</text>'
                for row, (text, color) in enumerate(frame))
            parts.append(f'<g class="frame" id="f{i}">{rows}</g>')
        parts.append("</svg>")
        return "\n".join(parts), total


def wrapped(cast, text, color, indent="    ", limit=4):
    lines = textwrap.wrap(" ".join(text.split()), WRAP - len(indent))
    if len(lines) > limit:
        lines = lines[:limit]
        lines[-1] = lines[-1][:WRAP - len(indent) - 1] + "…"
    for line in lines:
        cast.say(indent + line, color, frame=False)


def score_color(score):
    return {2: "good", 1: "warn", 0: "bad"}.get(score, "dim")


def main():
    if not RESULTS.exists():
        print("нет data/rag_result.json — сначала python3 rag_demo.py")
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    run = data["runs"][0]
    questions = data["questions"]
    cast = Cast()
    idx = index.Index()

    cast.say("$ python3 constitution.py && python3 index.py", "dim")
    cast.say(f"kremlin.ru/acts/constitution → {len(idx.rows)} чанков (статья или её части), "
             f"эмбеддинги {data['index']['model']}", "head")
    cast.say(f"отвечает {data['model']}, temperature=0, RAG = топ-{data['k']} чанков; "
             f"судья {data['judge']}", "dim")
    cast.say()

    for n in SHOW:
        item, row = questions[n], run["rows"][n]
        cast.say(f"? {item['q']}", "user")
        cast.say(f"  ожидание: {item['expect'][:WRAP - 12]}", "dim")
        hits, _ = idx.search(item["q"], data["k"])
        found = ", ".join(f"{h['chunk_id']} {h['score']:.2f}" for h in hits)
        ok = row["rag"]["retrieved_ok"]
        cast.say(f"  поиск: {found}   {'✓ ст. ' + ', '.join(item['sources']) if ok else '✗ нужной статьи нет'}",
                 "good" if ok else "bad")
        for mode in agent.MODES:
            cell = row[mode]
            cast.say(f"  {agent.MODE_LABELS[mode]:8} судья {cell['score']}/2, факты "
                     f"{sum(cell['facts'])}/{len(cell['facts'])}, промпт {cell['prompt_tokens']} ток.",
                     score_color(cell["score"]), frame=False)
            wrapped(cast, cell["answer"], "rag" if mode == "rag" else "plain", limit=3)
            cast.say("", frame=True)
        cast.clear()

    cast.say(f"$ python3 rag_demo.py   (10 вопросов × 2 режима × {len(data['runs'])} прохода)", "dim")
    for i, item in enumerate(questions):
        p, r = run["rows"][i]["plain"], run["rows"][i]["rag"]
        cast.say(f"  {i + 1:>2}. без RAG {p['score']}/2  с RAG {r['score']}/2  "
                 f"поиск {'✓' if r['retrieved_ok'] else '✗'}  {item['kind']:13} {item['q'][:52]}",
                 "good" if (r["score"] or 0) > (p["score"] or 0)
                 else "bad" if (r["score"] or 0) < (p["score"] or 0) else "plain")
    cast.say()
    cast.say(f"  {'':9} {'судья Σ по проходам':>22} {'все факты':>10} {'статья названа':>15} "
             f"{'промпт, ток.':>13}", "head")
    for mode in agent.MODES:
        sums = " / ".join(f"{r['summary'][mode]['judge_sum']}" for r in data["runs"])
        s = run["summary"][mode]
        cast.say(f"  {agent.MODE_LABELS[mode]:9} {sums + ' из 20':>22} {s['all_facts']:>7}/10 "
                 f"{s['cited_ok']:>12}/10 {s['prompt_tokens']:>13}",
                 "rag" if mode == "rag" else "plain")

    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, {path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
