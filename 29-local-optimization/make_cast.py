"""Собирает demo.svg — анимированную запись прогона optimize_demo.py.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.

Каст ничего не выдумывает: всё берётся из data/optimize_result.json (первый проход).

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
          "bad": "#f87171", "warn": "#fbbf24", "head": "#f4f4f5", "accent": "#22d3ee"}

RESULTS = pathlib.Path(__file__).with_name("data") / "optimize_result.json"
SHOW = [5, 12]     # показания против жены (перевёрнутый смысл), цензура (holdout)
PAIR = ["base", "quant"]


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
                     f'<text x="76" y="21" fill="{COLORS["dim"]}" font-size="12">python3 optimize_demo.py '
                     f'— локальная qwen2.5:7b до и после оптимизации под RAG</text>')
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
        print("нет data/optimize_result.json — сначала python3 optimize_demo.py")
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    run, questions, names = data["runs"][0], data["questions"], list(data["presets"])
    cast = Cast()
    cast.say(f"RAG по Конституции РФ, Ollama {data['ollama']} на M1 8 ГБ, t=0, сеть заблокирована", "head")
    for n in names:
        p = data["presets"][n]
        cast.say(f"  {n:10} {p['label']:42} {p['model']}", "dim", frame=False)
    cast.say()

    for i in SHOW:
        item = questions[i]
        cast.say(f"? [{item['set']}] {item['q']}", "user")
        for n in PAIR:
            c = run["presets"][n]["cells"][i]
            t = c["timing"]
            cast.say(f"  [{n}] {c['seconds']} с (реранк {t['rerank']} · ответ {t['answer']} · загрузка {t['load']})"
                     f"  {c['model_gb']} ГБ {c['gpu_pct']}% GPU   судья {c.get('correct')}/2"
                     f"{'  ПРОТИВОРЕЧИТ ЦИТАТЕ' if c.get('contradicts') else ''}",
                     "bad" if c.get("contradicts") else "accent", frame=False)
            wrapped(cast, c["answer"], "warn" if c["unknown"] else "plain", limit=2)
            for q in c["quotes"][:1]:
                cast.say(f"    ✓ «{q['text'][:WRAP - 20]}»", "good", frame=False)
            cast.say("", frame=True)
        cast.say()
        cast.clear()

    cast.say(f"=== {len(questions)} вопросов (dev 10 + holdout 6) × {len(data['runs'])} прохода; "
             f"судья {data['judge']} (Groq)", "dim")
    cast.say(f"  {'пресет':10} {'dev/20':>7} {'hold/12':>8} {'противор.':>9} {'с/вопрос':>9} {'реранку ток':>11} "
             f"{'перезагр.':>9} {'память':>12}", "head", frame=False)
    for n in names:
        per = [r["presets"][n]["summary"] for r in data["runs"]]
        s = per[0]
        cast.say(f"  {n:10} {'/'.join(str(x['correct_dev']) for x in per):>7} "
                 f"{'/'.join(str(x['correct_holdout']) for x in per):>8} "
                 f"{'/'.join(str(x['contradicts']) for x in per):>9} {s['seconds_mean']:>9} "
                 f"{s['rerank_tokens']:>11} {s['reloads']:>9} {str(s['model_gb']) + ' ГБ ' + str(s['gpu_pct']) + '%':>12}",
                 "accent" if n == "quant" else "plain", frame=False)
    cast.say("", frame=True)
    st = data.get("stability", {})
    cast.say("  ответы дословно совпали во всех проходах: " +
             ", ".join(f"{n} {v['same_answer']}/{v['n']}" for n, v in st.items() if v), "dim")

    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, {path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
