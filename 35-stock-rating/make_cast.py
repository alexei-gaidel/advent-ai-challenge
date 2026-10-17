"""Собирает demo.svg — анимированную запись разбора акций и итогов замера.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.
Скриншот самого дашборда лежит рядом — dashboard.png.

Каст ничего не выдумывает: формула, поправки AI, рейтинги и итоги проверки задним числом
берутся из data/rating_result.json (прогон rating_demo.py).

    python3 make_cast.py           → demo.svg (≤60 секунд)
"""

import html
import json
import pathlib
import sys
import textwrap

import scoring

WIDTH, HEIGHT = 1000, 650
PAD_X, PAD_Y = 22, 34
LINE = 19
MAX_LINES = 31
MAX_SECONDS = 58
WRAP = 104

COLORS = {"plain": "#d4d4d8", "dim": "#71717a", "user": "#60a5fa", "good": "#4ade80",
          "bad": "#f87171", "warn": "#fbbf24", "head": "#f4f4f5", "accent": "#22d3ee"}

HERE = pathlib.Path(__file__).resolve().parent
RESULTS = HERE / "data" / "rating_result.json"
SHOW = ["LKOH", "MGNT"]


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
                     f'<text x="76" y="21" fill="{COLORS["dim"]}" font-size="12">Рейтинг акций: Мосбиржа + '
                     f'МСФО + ставка ЦБ → формула (код) → поправки и рейтинг (AI)</text>')
        for i, frame in enumerate(self.frames):
            rows = "".join(f'<text x="{PAD_X}" y="{PAD_Y + r * LINE}" fill="{COLORS[c]}" '
                           f'xml:space="preserve">{html.escape(t)}</text>'
                           for r, (t, c) in enumerate(frame))
            parts.append(f'<g class="frame" id="f{i}">{rows}</g>')
        parts.append("</svg>")
        return "\n".join(parts), total


def comma(x):
    return f"{x:.1f}".replace(".", ",")


def rec_color(rec):
    return {"покупать": "good", "держать": "warn"}.get(rec, "bad")


def show(cast, row, model):
    m = row["models"][model]
    sample = m["sample"]
    cast.clear()
    cast.say(f"> стоит ли покупать {row['name']}?", "user", frame=False)
    cast.say(f"  формула (код): {comma(row['formula'])} — {row['formula_rec']}", "accent", frame=True)
    for a in sample["adjustments"][:3]:
        name = scoring.FACTOR_NAMES[a["factor"]].split(" (")[0]
        cast.say(f"  AI поправил «{name}»: {comma(a['from'])} → {comma(a['to'])}", "warn", frame=False)
        for chunk in textwrap.wrap(a["why"], WRAP - 6)[:3]:
            cast.say(f"     {chunk}", "dim", frame=False)
        cast.say("", frame=True)
    rating = m["ratings"][0]
    cast.say(f"  ИТОГ AI ({model}): {comma(rating)} из 10 — {m['recs'][0].upper()}",
             rec_color(m["recs"][0]), frame=False)
    for chunk in textwrap.wrap(sample["summary"], WRAP - 2)[:4]:
        cast.say(f"  {chunk}", "plain", frame=False)
    cast.say(f"  три прогона при temperature=0: {', '.join(comma(r) for r in m['ratings'])}", "dim", frame=True)
    cast.hold(3)


def main():
    if not RESULTS.exists():
        print("нет data/rating_result.json — сначала python3 rating_demo.py")
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    live = {r["ticker"]: r for r in data["live"]}
    cast = Cast()
    for ticker in SHOW:
        show(cast, live[ticker], data["models"][0])

    cast.clear()
    cast.say(f"=== Часть A: {len(data['live'])} акций × DeepSeek × {data['runs']} прогона, temperature=0", "head", frame=False)
    for model, s in data["summary_live"].items():
        cast.say(f"  {model:20} разброс в среднем {comma(s['mean_spread'])} (макс {comma(s['max_spread'])}), "
                 f"рекомендация менялась у {s['unstable_rec']}", "plain", frame=False)
        cast.say(f"  {'':20} от формулы {s['mean_delta']:+.2f} (|Δ| {comma(s['mean_abs_delta'])}), "
                 f"совпала с формулой {s['rec_vs_formula']}/{len(data['live'])}, ${s['cost_per_call']} за разбор",
                 "dim", frame=False)
    cast.hold(1)
    cast.say("", frame=False)
    cast.say("=== Часть B: задним числом, доходность следующих 12 мес. минус IMOEX", "head", frame=False)
    names = {"formula": "формула", "deepseek-chat|anon": "DeepSeek, без названия",
             "deepseek-chat|named": "DeepSeek, с названием"}
    for key, s in data["summary_backtest"].items():
        a = s["all"]
        color = "good" if a["spearman"] > 0.2 else ("bad" if a["spearman"] < 0 else "plain")
        cast.say(f"  {names[key]:24} корреляция {a['spearman']:+.2f} · верхняя треть {a['top_third']:+.1f} п.п., "
                 f"нижняя {a['bottom_third']:+.1f} п.п.", color, frame=True)
    cast.say(f"  с названием минус без: {data['leak']['mean']:+.2f} в среднем", "dim", frame=False)
    cast.say(f"  всего ${data['total_cost']} за замер", "dim", frame=True)
    cast.hold(5)

    markup, seconds = cast.svg()
    path = HERE / "demo.svg"
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, {path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
