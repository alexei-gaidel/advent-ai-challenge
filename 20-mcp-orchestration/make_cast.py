"""Собирает demo.svg — анимированную запись оркестрации трёх MCP-серверов.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.

Каст ничего не выдумывает и не зовёт модель повторно: реестр поднимается вживую,
а ленты вызовов и цифры берутся из data/demo_results.json — прогона
orchestration_demo.py.

    python3 make_cast.py           → demo.svg (≤60 секунд)
"""

import html
import json
import pathlib
import sys

from registry import Registry

WIDTH, HEIGHT = 960, 620
PAD_X, PAD_Y = 22, 34
LINE = 19
MAX_LINES = 29
MAX_SECONDS = 58

COLORS = {
    "plain": "#d4d4d8", "dim": "#71717a", "user": "#60a5fa", "good": "#4ade80",
    "bad": "#f87171", "warn": "#fbbf24", "head": "#f4f4f5",
    "weather": "#38bdf8", "money": "#86efac", "notes": "#c084fc",
}

RESULTS = pathlib.Path(__file__).with_name("data") / "demo_results.json"


class Cast:
    """Накапливает строки и снимает кадр после каждого шага."""

    def __init__(self):
        self.lines = []
        self.frames = []

    def say(self, text="", color="plain"):
        print(text)
        for chunk in (text.split("\n") if text else [""]):
            self.lines.append((chunk, color))
        self.frames.append(list(self.lines[-MAX_LINES:]))

    def svg(self):
        count = len(self.frames)
        step = min(MAX_SECONDS / count, 2.4)
        total = round(step * count, 2)

        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
            f'width="{WIDTH}" height="{HEIGHT}" font-family="ui-monospace, SFMono-Regular, '
            f'Menlo, Consolas, monospace" font-size="14">',
            "<style>",
            ".bg{fill:#18181b}.frame{opacity:0}",
        ]
        for index in range(count):
            start = 100 * index / count
            end = 100 * (index + 1) / count
            gap = 0.01
            parts.append(
                f"#f{index}{{animation:a{index} {total}s steps(1,end) infinite}}"
                f"@keyframes a{index}{{0%,{max(start - gap, 0):.3f}%{{opacity:0}}"
                f"{start:.3f}%,{max(end - gap, start):.3f}%{{opacity:1}}"
                f"{end:.3f}%,100%{{opacity:0}}}}"
            )
        parts.append("</style>")
        parts.append(f'<rect class="bg" width="{WIDTH}" height="{HEIGHT}" rx="10"/>')
        parts.append(f'<circle cx="20" cy="16" r="5" fill="#ef4444"/>'
                     f'<circle cx="38" cy="16" r="5" fill="#fbbf24"/>'
                     f'<circle cx="56" cy="16" r="5" fill="#22c55e"/>'
                     f'<text x="76" y="21" fill="{COLORS["dim"]}" font-size="12">'
                     f'python3 orchestration_demo.py — три MCP-сервера, один агент</text>')

        for index, frame in enumerate(self.frames):
            rows = "".join(
                f'<text x="{PAD_X}" y="{PAD_Y + row * LINE}" fill="{COLORS[color]}" '
                f'xml:space="preserve">{html.escape(text)}</text>'
                for row, (text, color) in enumerate(frame)
            )
            parts.append(f'<g class="frame" id="f{index}">{rows}</g>')

        parts.append("</svg>")
        return "\n".join(parts), total


def short_args(arguments, limit=46):
    text = json.dumps(arguments, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def tape(cast, run):
    for call in run["calls"]:
        mark = "✓" if call["ok"] else "✗"
        cast.say(f"  {mark} круг {call['round']} {call['server'] or '?':7} "
                 f"{call['name']:21} {short_args(call['arguments'])}",
                 call["server"] if call["ok"] else "bad")
    report = run["report"]
    verdict = "пройден" if report["passed"] else "есть нарушения"
    cast.say(f"  → {verdict}: шагов {report['steps_ok']}/7, кругов {report['rounds']}, "
             f"ошибок {report['errors']}, {run['tokens']} ток., {run['seconds']} с",
             "good" if report["passed"] else "bad")


def main():
    if not RESULTS.exists():
        print("нет data/demo_results.json — сначала python3 orchestration_demo.py")
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    cast = Cast()

    cast.say("$ python3 orchestration_demo.py", "dim")
    registry = Registry().connect()
    cast.say("Зарегистрировано серверов: 3, инструментов: "
             f"{len(registry.catalog)}", "head")
    for server in registry.info()["servers"]:
        names = ", ".join(t["tool"] for t in server["tools"])
        cast.say(f"  {server['name']:8} {names}", server["name"])
    for tool, servers in registry.collisions().items():
        cast.say(f"  коллизия: {tool} есть на {' и '.join(servers)} → "
                 f"{', '.join(s + '__' + tool for s in servers)}", "warn")
    registry.close()
    cast.say()

    cast.say("вы → Еду на матч в Екатеринбург 3 октября: погода, бюджет в евро,", "user")
    cast.say("     заметка со ссылками на прогноз и бюджет, чек-лист, покажи итог", "user")
    cast.say()

    runs = data["runs"]
    shown = []
    for model in ("deepseek-chat", "openai/gpt-oss-120b"):
        run = next((r for r in runs if r["model"] == model and not r["error"]), None)
        if not run:
            continue
        shown.append(model)
        cast.say(f"{model}:", "head")
        tape(cast, run)
        cast.say()

    cast.say("Сводка: модель · прошёл · порядок · вызовов · кругов · токенов", "head")
    for row in data["summary"]:
        cast.say(f"  {row['model']:21} {row['passed']}/{row['runs']}   "
                 f"{row['order']}/{row['runs']}   {row['calls']:>4}  {row['rounds']:>5} "
                 f"{row['tokens']:>8}", "good" if row["passed"] == row["runs"] else "warn")

    probes = data.get("probes") or []
    if probes:
        cast.say()
        ok = sum(p["ok"] for p in probes)
        search = [p for p in probes if p["expected"].endswith("__search")]
        cast.say(f"Маршрутизация: первый инструмент верный в {ok}/{len(probes)}, "
                 f"на коллизии search — {sum(p['ok'] for p in search)}/{len(search)}",
                 "good" if ok == len(probes) else "warn")

    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, "
          f"{path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
