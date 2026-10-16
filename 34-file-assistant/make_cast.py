"""Собирает demo.svg — анимированную запись работы файлового ассистента.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.

Каст ничего не выдумывает: трассы, diff и цифры берутся из data/file_result.json
(прогон file_demo.py).

    python3 make_cast.py           → demo.svg (≤60 секунд)
"""

import html
import json
import pathlib
import sys
import textwrap

import assistant as fa

WIDTH, HEIGHT = 1000, 650
PAD_X, PAD_Y = 22, 34
LINE = 19
MAX_LINES = 31
MAX_SECONDS = 58
WRAP = 106

COLORS = {"plain": "#d4d4d8", "dim": "#71717a", "user": "#60a5fa", "good": "#4ade80",
          "bad": "#f87171", "warn": "#fbbf24", "head": "#f4f4f5", "accent": "#22d3ee",
          "tool": "#c084fc"}

HERE = pathlib.Path(__file__).resolve().parent
RESULTS = HERE / "data" / "file_result.json"


class Cast:
    def __init__(self):
        self.lines, self.frames = [], []

    def say(self, text="", color="plain", frame=True):
        print(text)
        for chunk in (text.split("\n") if text else [""]):
            self.lines.append((chunk, color))
        if frame:
            self.frames.append(list(self.lines[-MAX_LINES:]))

    def hold(self, n):
        for _ in range(n):
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
                     f'cli.py — файловый ассистент: цель → MCP (чтение, поиск, правки) → diff</text>')
        for i, frame in enumerate(self.frames):
            rows = "".join(f'<text x="{PAD_X}" y="{PAD_Y + r * LINE}" fill="{COLORS[c]}" '
                           f'xml:space="preserve">{html.escape(t)}</text>'
                           for r, (t, c) in enumerate(frame))
            parts.append(f'<g class="frame" id="f{i}">{rows}</g>')
        parts.append("</svg>")
        return "\n".join(parts), total


def show_run(cast, row, diff_lines=12, trace_step=4):
    cast.clear()
    cast.say(f"$ python3 cli.py {row['scenario']}", "dim", frame=False)
    for line in textwrap.wrap(fa.PRESETS[row["scenario"]]["goal"], WRAP - 2):
        cast.say("  " + line, "user", frame=False)
    cast.say("", frame=True)
    trace = row["trace"]
    for i, t in enumerate(trace, 1):
        args = " ".join(f"{k}={v!r}" for k, v in t["arguments"].items())
        color = "warn" if t["name"] in ("write_file", "edit_file") else "tool"
        if not t["ok"]:
            color = "bad"
        mark = "" if t["ok"] else "  ✗ old не найден"
        cast.say(f"  {t['round']:>2} {t['name']:11} {args[:WRAP - 22]}{mark}", color,
                 frame=(i % trace_step == 0 or i == len(trace)))
    cast.say("", frame=False)
    shown = [line for line in row["diff"].splitlines() if line.startswith(("+++", "@@", "+", "-"))
             and not line.startswith("---")][:diff_lines]
    for line in shown:
        color = ("warn" if line.startswith("+++") else "accent" if line.startswith("@@")
                 else "good" if line.startswith("+") else "bad")
        cast.say("  " + line[:WRAP - 2], color, frame=False)
    cast.say(f"  {row['score']}/{row['of']} по эталону · вызовов {row['calls']} · "
             f"ошибок {row['tool_errors']} · ${row['cost']} · {row['seconds']} с · "
             f"черновик → python3 cli.py --apply", "head", frame=True)
    cast.hold(3)


def main():
    if not RESULTS.exists():
        print("нет data/file_result.json — сначала python3 file_demo.py")
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    first = {r["scenario"]: r for r in data["runs"] if r.get("attempt") == 1 and "error" not in r}
    cast = Cast()
    show_run(cast, first["docs"], diff_lines=14, trace_step=5)
    show_run(cast, first["invariants"], diff_lines=10, trace_step=6)

    cast.clear()
    cast.say(f"=== 4 сценария × 3 прохода, {data['model']}, temperature=0, слепок {data['snapshot']}",
             "head", frame=False)
    cast.say("", frame=False)
    cast.say(f"  {'сценарий':30} {'по эталону':>11} {'вызовов':>8} {'ошибок':>7} "
             f"{'токенов':>9} {'$':>7}  разных diff", "head", frame=False)
    for key, s in data["summary"].items():
        color = "good" if s["score"] == s["of"] else "warn"
        cast.say(f"  {fa.PRESETS[key]['title']:30} {str(s['score']) + '/' + str(s['of']):>11} "
                 f"{s['calls']:>8} {s['tool_errors']:>7} {s['tokens']:>9} {s['cost']:>7}  "
                 f"{s['distinct_diffs']} из {s['runs']}", color, frame=False)
    cast.say("", frame=True)
    cast.hold(5)

    markup, seconds = cast.svg()
    path = HERE / "demo.svg"
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, {path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
