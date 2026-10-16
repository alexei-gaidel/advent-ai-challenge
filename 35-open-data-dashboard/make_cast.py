"""Собирает demo.svg — анимированную запись работы конвейера на живом портале.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.

Каст ничего не выдумывает: темы, рецепты, вердикты и ответы чата берутся из
data/dashboard_result.json (прогон dashboard_demo.py).

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
          "tool": "#c084fc"}

HERE = pathlib.Path(__file__).resolve().parent
RESULTS = HERE / "data" / "dashboard_result.json"


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
                     f'<text x="76" y="21" fill="{COLORS["dim"]}" font-size="12">data.admhmao.ru → '
                     f'темы (AI) → рецепты сведения (AI) → цифры (код) → дашборд и чат</text>')
        for i, frame in enumerate(self.frames):
            rows = "".join(f'<text x="{PAD_X}" y="{PAD_Y + r * LINE}" fill="{COLORS[c]}" '
                           f'xml:space="preserve">{html.escape(t)}</text>'
                           for r, (t, c) in enumerate(frame))
            parts.append(f'<g class="frame" id="f{i}">{rows}</g>')
        parts.append("</svg>")
        return "\n".join(parts), total


def money(value):
    if value is None:
        return "—"
    if abs(value) >= 1e9:
        return f"{value / 1e9:.2f} млрд"
    if abs(value) >= 1e6:
        return f"{value / 1e6:.1f} млн"
    return f"{value:,.0f}".replace(",", " ")


def main():
    if not RESULTS.exists():
        print("нет data/dashboard_result.json — сначала python3 dashboard_demo.py")
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    truth = json.loads((HERE / "data" / "truth.json").read_text(encoding="utf-8"))
    cast = Cast()

    # 1. Категория → темы от модели
    fin = next(t for t in data["topics"] if t["slug"] == "finansy")
    cast.say(f"Категория портала «{fin['category']}»: {fin['count']} наборов, на портале — только издатели",
             "head", frame=False)
    cast.say("Модель группирует их в темы, код проверяет: каждый набор ровно в одной теме", "dim")
    for title in fin["titles"][0]:
        cast.say(f"  ■ {title}", "accent", frame=False)
    cast.say("", frame=True)
    cast.hold(2)

    # 2. Рецепты: три разных формата → одни показатели
    cast.clear()
    run = next(r for r in data["recipes"] if r["needle"] == "расходы" and r["attempt"] == 1)
    cast.say(f"Тема «{run['topic']}»: три города, три разных формата файла", "head", frame=False)
    cast.say("Модель пишет рецепт — где лежит показатель; цифры считает код по всему файлу", "dim")
    expected = truth["topics"]["расходы"]
    for ident, recipe in run["recipes"].items():
        t = expected.get(ident, {})
        cast.say(f"\n  {t.get('mo', ident)}", "user", frame=False)
        for line in textwrap.wrap(t.get("source", ""), WRAP - 6)[:2]:
            cast.say(f"    файл: {line}", "dim", frame=False)
        for key, rule in (recipe.get("values") or {}).items():
            where = f"колонка {rule.get('column')}"
            if isinstance(rule.get("match"), dict):
                where += f", строка «{rule['match'].get('contains')}»"
            if rule.get("sum"):
                where += ", сумма"
            if rule.get("multiplier", 1) != 1:
                where += f", ×{rule['multiplier']}"
            cast.say(f"    рецепт {key}: {where}", "tool", frame=False)
        verdicts = [v for v in run["verdicts"] if v["id"] == ident and v["kind"] in ("plan", "fact")]
        for v in verdicts:
            color = "good" if v["verdict"] == "верно" else "bad"
            cast.say(f"    {v['kind']}: эталон {money(t.get(v['kind']))} → {v['verdict']}", color, frame=False)
        cast.say("", frame=True)
    cast.hold(3)

    # 3. Чат
    cast.clear()
    cast.say("Чат меняет вид дашборда через set_view, цифры пересчитывает код", "head", frame=False)
    for item in [c for c in data["chat"] if c["attempt"] == 1][:4]:
        cast.say(f"\n> {item['message']}", "user", frame=False)
        for call in item["calls"]:
            cast.say(f"  set_view {json.dumps(call['arguments'], ensure_ascii=False)[:WRAP - 12]}", "tool", frame=False)
        for line in textwrap.wrap(item["answer"], WRAP - 4)[:3]:
            cast.say("  " + line, "plain", frame=False)
        cast.say(f"  {'✓ вид верный' if item['ok'] else '✗ вид не тот'}"
                 + (f" · чисел не с экрана: {len(item['unknown_numbers'])}" if item["unknown_numbers"] else ""),
                 "good" if item["ok"] else "bad", frame=True)
        cast.hold(1)

    # 4. Итог
    cast.clear()
    s = data["summary"]
    cast.say(f"=== Итог, {data['model']}, temperature=0, {data['passes']} прохода, живой портал", "head", frame=False)
    cast.say("", frame=False)
    cast.say("  Темы:  категория                    наборов  с 1-й попытки  стабильность (Рэнд)", "head", frame=False)
    for slug, t in s["topics"].items():
        cast.say(f"         {t['category'][:28]:28} {t['count']:>6}  {t['first_try']}/{data['passes']:<12} "
                 f"{', '.join(str(x) for x in t['rand'])}", "plain", frame=False)
    cast.say("", frame=False)
    cast.say("  Рецепты против ручного эталона:       значения     даты", "head", frame=False)
    for needle, r in s["recipes"].items():
        color = "good" if r["values_ok"] == r["values"] else "warn"
        cast.say(f"         {r['topic'][:30]:30} {r['values_ok']:>4}/{r['values']:<7} {r['dates_ok']:>4}/{r['dates']}",
                 color, frame=False)
    c = s["chat"]
    cast.say("", frame=False)
    cast.say(f"  Чат: вид верный {c['ok']}/{c['of']}, ответов с числами не с экрана — {c['answers_with_unknown']}",
             "good" if c["ok"] == c["of"] else "warn", frame=True)
    cast.hold(5)

    markup, seconds = cast.svg()
    path = HERE / "demo.svg"
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, {path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
