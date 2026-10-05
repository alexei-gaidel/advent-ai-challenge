"""Собирает demo.svg — анимированную запись прогона local_demo.py.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.

Каст ничего не выдумывает: всё берётся из data/local_result.json.

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

RESULTS = pathlib.Path(__file__).with_name("data") / "local_result.json"


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
                     f'<text x="76" y="21" fill="{COLORS["dim"]}" font-size="12">python3 local_demo.py '
                     f'— локальная LLM через Ollama, без облака</text>')
        for i, frame in enumerate(self.frames):
            rows = "".join(f'<text x="{PAD_X}" y="{PAD_Y + r * LINE}" fill="{COLORS[c]}" '
                           f'xml:space="preserve">{html.escape(t)}</text>'
                           for r, (t, c) in enumerate(frame))
            parts.append(f'<g class="frame" id="f{i}">{rows}</g>')
        parts.append("</svg>")
        return "\n".join(parts), total


def wrapped(cast, text, color, indent="    ", limit=4):
    lines = []
    for paragraph in text.strip().split("\n"):
        lines += textwrap.wrap(paragraph, WRAP - len(indent)) or [""]
    lines = [line for line in lines if line.strip()]
    if len(lines) > limit:
        lines = lines[:limit]
        lines[-1] = lines[-1][:WRAP - len(indent) - 1] + "…"
    for line in lines:
        cast.say(indent + line, color, frame=False)


def main():
    if not RESULTS.exists():
        print("нет data/local_result.json — сначала python3 local_demo.py")
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    cast = Cast()
    info = data["info"]
    cast.say(f"Ollama {data['ollama']} · {data['model']} · {info['params']} · "
             f"{info['size_gb']} ГБ · Mac M1 8 ГБ · интернет не нужен", "head")
    cast.say()

    cold = data["cold"]
    cast.say("$ ollama ps   → пусто, модель выгружена", "dim")
    cast.say(f"холодный запрос: загрузка {cold['cold']['load_s']} с, всего {cold['cold']['total_s']} с", "warn")
    cast.say(f"тёплый запрос:   загрузка {cold['warm']['load_s']} с, всего {cold['warm']['total_s']} с", "good")
    cast.say()

    p = data["paths"]
    cast.say(f"$ {p['cli']['command']}", "user", frame=False)
    cast.say(f"  {p['cli']['answer']}", "good")
    cast.say(f"$ curl -d '{{\"model\":\"{data['model']}\",\"prompt\":…}}' {p['http']['url']}", "user", frame=False)
    cast.say(f"  \"response\": \"{p['http']['answer']}\", \"eval_count\": {p['http']['eval_count']}", "good")
    cast.say(">>> local_llm.ask(...)", "user", frame=False)
    cast.say(f"  {p['python']['answer']}  ({p['python']['total_s']} с, {p['python']['tok_per_s']} ток/с)", "good")
    cast.say("", frame=True)

    for task in data["tasks"]:
        cast.clear()
        run = task["runs"][0]
        cast.say(f"[{task['level']}] {task['name']} — temperature=0, {len(task['runs'])} прогона", "head", frame=False)
        cast.say(f"> {task['prompt']}", "user")
        wrapped(cast, run["text"], "plain", limit=14)
        verdict = "✓" if run["ok"] else "✗"
        note = f" · {run['note']}" if run["note"] else ""
        cast.say(f"  {verdict} эталон «{task['expect']}»{note}", "good" if run["ok"] else "bad", frame=False)
        cast.say(f"  {run['prompt_tokens']}→{run['output_tokens']} ток · {run['total_s']} с · "
                 f"{run['tok_per_s']} ток/с · верно {task['passed']}/{len(task['runs'])} · "
                 f"{'ответы дословно совпали' if task['identical'] else 'ответы разные'}", "dim")
        cast.say("", frame=True)

    cast.clear()
    rc = data["recheck"]
    cast.say("=== Перепроверка провалов", "head", frame=False)
    cast.say(f"  столица, temperature=0:  по-русски «{rc['language']['ru']}», "
             f"по-английски «{rc['language']['en']}»", "warn")
    for name, got in rc["hot"].items():
        cast.say(f"  {name}, temperature=0.8 ×{len(got)}: верно {sum(g['ok'] for g in got)}/{len(got)}", "bad", frame=False)
        cast.say(f"    {', '.join(str(g['answer'])[:14] for g in got)}", "dim")
    cast.say("", frame=False)
    cast.say("=== Итог", "head")
    cast.say(f"  {'запрос':24} {'верно':>6} {'вход':>5} {'выход':>6} {'время, с':>9} {'ток/с':>6}  повтор", "dim")
    for row in data["tasks"]:
        avg = lambda key: sum(r[key] for r in row["runs"]) / len(row["runs"])
        color = "good" if row["passed"] == len(row["runs"]) else ("bad" if not row["passed"] else "warn")
        cast.say(f"  {row['level']}. {row['name']:21} {row['passed']:>3}/{len(row['runs']):<2} "
                 f"{avg('prompt_tokens'):>5.0f} {avg('output_tokens'):>6.0f} {avg('total_s'):>9.2f} "
                 f"{avg('tok_per_s'):>6.1f}  {'дословно' if row['identical'] else 'разные'}", color, frame=False)
    mem = ", ".join(f"{m['name']} {m['vram_gb']} ГБ" for m in data["memory"])
    cast.say(f"  в памяти: {mem}", "dim")
    cast.say("", frame=True)
    cast.say("", frame=True)

    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, {path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
