"""Собирает demo.svg — анимированную запись прогона rerank_demo.py.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.

Каст ничего не выдумывает: всё берётся из data/rerank_result.json (первый проход).

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

RESULTS = pathlib.Path(__file__).with_name("data") / "rerank_result.json"
SHOW = [3, 10]   # «брак» (провал поиска дня 22) и «борщ» (вне базы)
STATUS = {"kept": "в промпт", "below_threshold": "ниже порога", "rerank_low": "реранкер ✗",
          "cut_k": "за топ-K"}


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
        step = min(MAX_SECONDS / count, 2.8)
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
                     f'<text x="76" y="21" fill="{COLORS["dim"]}" font-size="12">python3 rerank_demo.py '
                     f'— порог, LLM-реранкер и query rewrite поверх RAG дня 22</text>')
        for i, frame in enumerate(self.frames):
            rows = "".join(f'<text x="{PAD_X}" y="{PAD_Y + r * LINE}" fill="{COLORS[c]}" '
                           f'xml:space="preserve">{html.escape(t)}</text>'
                           for r, (t, c) in enumerate(frame))
            parts.append(f'<g class="frame" id="f{i}">{rows}</g>')
        parts.append("</svg>")
        return "\n".join(parts), total


def wrapped(cast, text, color, indent="      ", limit=2):
    lines = textwrap.wrap(" ".join(text.split()), WRAP - len(indent))
    if len(lines) > limit:
        lines = lines[:limit]
        lines[-1] = lines[-1][:WRAP - len(indent) - 1] + "…"
    for line in lines:
        cast.say(indent + line, color, frame=False)


def main():
    if not RESULTS.exists():
        print("нет data/rerank_result.json — сначала python3 rerank_demo.py")
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    run, questions, p = data["runs"][0], data["questions"], data["params"]
    cast = Cast()

    cast.say(f"порог косинуса {p['threshold']} · топ-{p['k_before']} до фильтра · топ-{p['k_after']} "
             f"после · реранкер deepseek-chat ≥ {p['rerank_min']}/10", "head")
    cast.say("=== калибровка порога: 12 вопросов из базы, 2 вне базы", "dim")
    for row in data["calibration"]["sweep"]:
        chosen = abs(row["threshold"] - p["threshold"]) < 1e-9
        cast.say(f"  порог {row['threshold']:.3f}   вне базы отсечено {row['outside_cut']}/2   "
                 f"нужная статья выжила {row['gold_alive']:>2}/12   кандидатов {row['avg_kept']:>4}"
                 + ("   ← выбран" if chosen else ""), "accent" if chosen else "plain",
                 frame=chosen or row is data["calibration"]["sweep"][-1])
    cast.say()
    cast.clear()

    for n in SHOW:
        item, row = questions[n], run["rows"][n]
        cast.say(f"? {item['q']}", "user")
        cast.say(f"  ожидание: {item['expect'][:WRAP - 12]}", "dim")
        for mode in agent.MODES:
            cell = row[mode]
            kept = [c for c in cell["candidates"] if c["status"] == "kept"]
            gold = [c for c in cell["candidates"] if c["article"] in item["sources"]]
            info = f"в промпт {len(kept)}"
            if gold:
                g = gold[0]
                rank = cell["candidates"].index(g) + 1
                info += f" · ст.{g['article']} косинус #{rank} → {STATUS[g['status']]}"
            cast.say(f"  {agent.MODE_LABELS[mode]:27} судья {cell['score']}/2 · {info}",
                     {2: "good", 1: "warn", 0: "bad"}.get(cell["score"], "dim"), frame=False)
            if cell["query"] != item["q"]:
                cast.say(f"      запрос: {cell['query'][:WRAP - 14]}", "warn", frame=False)
            wrapped(cast, cell["answer"], "plain")
            cast.say("", frame=True)
        cast.clear()

    cast.say(f"=== 14 вопросов × 4 режима × {len(data['runs'])} прохода", "dim")
    cast.say(f"  {'':27} {'судья по проходам':>18} {'статьи':>7} {'вне базы':>9} {'точность':>9} "
             f"{'чанков':>7} {'утечки':>7} {'$ за 14':>8}", "head")
    for mode in agent.MODES:
        sums = " / ".join(str(r["summary"][mode]["judge_sum"]) for r in data["runs"])
        s = run["summary"][mode]
        cast.say(f"  {agent.MODE_LABELS[mode]:27} {sums + ' /28':>18} {s['recall']:>4}/12 "
                 f"{s['outside_clean']:>6}/2 {s['precision']:>9} {s['avg_chunks']:>7} {s['leaks']:>7} "
                 f"{s['cost']:>8.4f}", "accent" if mode != "base" else "plain")

    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, {path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
