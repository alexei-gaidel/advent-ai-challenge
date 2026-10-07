"""Собирает demo.svg — анимированную запись прогона local_rag_demo.py.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.

Каст ничего не выдумывает: всё берётся из data/local_rag_result.json (первый проход).

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

RESULTS = pathlib.Path(__file__).with_name("data") / "local_rag_result.json"
SHOW = [5, 1]      # показания против жены (7b переворачивает смысл), МРОТ (верно у всех, разная скорость)


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
                     f'<text x="76" y="21" fill="{COLORS["dim"]}" font-size="12">python3 local_rag_demo.py '
                     f'— RAG на локальной qwen2.5 против облачного deepseek-chat</text>')
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
        print("нет data/local_rag_result.json — сначала python3 local_rag_demo.py")
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    run, questions, stacks = data["runs"][0], data["questions"], list(data["stacks"])
    p = data["params"]
    cast = Cast()
    cast.say(f"индекс: Конституция РФ, bge-m3 в Ollama {data['ollama']} + SQLite — поиск всегда локальный", "head")
    cast.say(f"топ-{p['k_before']} → порог {p['threshold']} → LLM-реранкер → топ-{p['k_after']} → JSON с цитатами;"
             f" t=0, num_ctx={data['num_ctx']}", "dim")
    for stack in stacks:
        blocked = run["stacks"][stack]["blocked"]
        net = f"сеть заблокирована, попыток выйти: {len(blocked)}" if stack != "cloud" else "API DeepSeek"
        cast.say(f"  {stack:8} {data['stacks'][stack]['label']:24} {net}", "dim", frame=False)
    cast.say()

    for n in SHOW:
        item = questions[n]
        cast.say(f"? {item['q']}", "user")
        for stack in stacks:
            c = run["stacks"][stack]["cells"][n]
            t = c["timing"]
            cast.say(f"  [{stack}] {c['seconds']} с (реранк {t['rerank']} · ответ {t['answer']} · загрузка {t['load']})"
                     f"   судья {c.get('correct')}/2", "accent", frame=False)
            if c["unknown"] or c["error"]:
                wrapped(cast, c["answer"], "warn", limit=2)
            else:
                wrapped(cast, c["answer"], "plain", limit=2)
                for q in c["quotes"][:1]:
                    cast.say(f"    ✓ «{q['text'][:WRAP - 20]}»", "good", frame=False)
                for q in c["rejected"][:1]:
                    cast.say(f"    ✗ «{q['text'][:WRAP - 32]}» — {q['status']}", "bad", frame=False)
            cast.say("", frame=True)
        cast.say()
        cast.clear()

    cast.say(f"=== 10 вопросов × {len(stacks)} стека × {len(data['runs'])} прохода; судья {data['judge']} (Groq)", "dim")
    cast.say("  " + "".join(f"{s:>22}" for s in stacks), "head", frame=False)
    for i, item in enumerate(questions):
        cells = []
        for stack in stacks:
            c = run["stacks"][stack]["cells"][i]
            mark = "ошибка" if c["error"] else "не знаю" if c["unknown"] else f"{len(c['quotes'])}✓{len(c['rejected'])}✗"
            cells.append(f"{c.get('correct')}/2 {mark:8} {c['seconds']:5.0f}с")
        cast.say(f"  {i + 1:>2}. " + "".join(f"{x:>22}" for x in cells) + f"   {item['q'][:30]}", frame=False)
    cast.say("", frame=True)
    for stack in stacks:
        per = [r["stacks"][stack]["summary"] for r in data["runs"]]
        st = data.get("stability", {}).get(stack) or {}
        s = per[0]
        cast.say(f"  {stack:8} верно {' / '.join(str(x['correct_sum']) for x in per)} из 20 · нужная статья в топ-5 "
                 f"{s['retrieval_ok']}/{s['n_answer']} · {s['seconds_total']:.0f} с на 10 вопросов · "
                 f"повтор дословно {st.get('same_answer', '—')}/{st.get('n', 10)} · ${s['cost']:.4f}",
                 "accent" if stack != "cloud" else "plain")

    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, {path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
