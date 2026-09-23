"""Собирает demo.svg — анимированную запись работы инструмента по расписанию.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией —
он крутится в браузере и прямо в README на GitHub, зависимостей не требует.

    python3 make_cast.py           → demo.svg (≤60 секунд)
"""

import html
import pathlib
import sys
import tempfile

import storage

storage.DB_PATH = pathlib.Path(tempfile.gettempdir()) / "cast.db"

import agent as agents            # noqa: E402
import tools as mcp_tools         # noqa: E402
import os                         # noqa: E402
import profiles                   # noqa: E402

WIDTH, HEIGHT = 960, 620
PAD_X, PAD_Y = 22, 34
LINE = 19
MAX_LINES = 29
MAX_SECONDS = 58
WRAP = 92

COLORS = {
    "plain": "#d4d4d8",
    "dim": "#71717a",
    "user": "#60a5fa",
    "agent": "#a1a1aa",
    "good": "#4ade80",
    "warn": "#fbbf24",
    "head": "#f4f4f5",
    "prof": "#c4b5fd",
}

SETTINGS = {
    "model": "deepseek-chat",
    "temperature": 0.0,
    "max_tokens": 220,
    "keep_last": 6,
    "use_short": False,
    "router": False,
    "system_prompt": "Ты ассистент команды разработки.",
}

QUESTION = "Что такое индекс в базе данных?"


def wrap(text, width=WRAP):
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    return lines + [current] if current else lines


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

    def answer(self, text, color="agent", limit=3):
        """Первые строки ответа, обёрнутые по ширине кадра."""
        flat = " ".join(line.strip() for line in text.strip().splitlines() if line.strip())
        lines = wrap(flat)
        shown = lines[:limit]
        if len(lines) > limit:
            shown[-1] = shown[-1][:WRAP - 1] + "…"
        self.say("\n".join(f"  {line}" for line in shown), color)

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
                     f'python3 make_cast.py — персонализация: один вопрос, разные профили</text>')

        for index, frame in enumerate(self.frames):
            rows = "".join(
                f'<text x="{PAD_X}" y="{PAD_Y + row * LINE}" fill="{COLORS[color]}" '
                f'xml:space="preserve">{html.escape(text)}</text>'
                for row, (text, color) in enumerate(frame)
            )
            parts.append(f'<g class="frame" id="f{index}">{rows}</g>')

        parts.append("</svg>")
        return "\n".join(parts), total


def main():
    import pathlib as _pathlib
    import tempfile
    import time

    # Каст работает на своих данных, чтобы не мешать рабочему ряду наблюдений.
    temp = _pathlib.Path(tempfile.gettempdir())
    os.environ["ADVENT_SCHEDULE_DB"] = str(temp / "cast_schedule.db")
    os.environ["ADVENT_SAMPLES"] = str(temp / "cast_samples.jsonl")
    for path in (temp / "cast_schedule.db", temp / "cast_samples.jsonl"):
        path.unlink(missing_ok=True)

    import digest, scheduler, store
    store.init()
    cast = Cast()

    cast.say("$ python3 scheduler.py   # инструмент с расписанием", "dim")
    scheduler.setup(interval=20, digest_every=30)
    store.add_job("remind-demo", "remind", 0, {"text": "проверить сводку"},
                  first_run=store.now() + store.timedelta(seconds=12))
    for job in store.list_jobs():
        period = f"каждые {job['every']} c" if job["every"] else "одноразовое"
        cast.say(f"  {job['name']:<12} {job['kind']:<8} {period}", "warn")
    cast.say("  (в жизни: наблюдение раз в час, сводка раз в 8 часов)", "dim")
    cast.say()

    started = time.monotonic()
    while time.monotonic() - started < 46:
        for item in scheduler.tick(verbose=False):
            mark = "✓" if item["status"] == "ok" else "✕"
            cast.say(f"{int(time.monotonic() - started):>3} c  {mark} {item['job']:<12} "
                     f"{item['detail'][:52]}", "good" if item["status"] == "ok" else "warn")
        time.sleep(0.5)
    cast.say()

    cast.say("одноразовое напоминание после срабатывания: " +
             ("активно" if any(j["name"] == "remind-demo" for j in store.list_jobs())
              else "выключено"), "dim")
    cast.say()

    cast.say("Агрегированный результат:", "head")
    for line in digest.as_text(digest.build("24h")).splitlines()[:12]:
        cast.say(f"  {line[:74]}", "agent")
    cast.say()

    cast.say("Агент спрашивает сводку через MCP:", "head")
    import agent as agents
    agents.storage.DB_PATH = temp / "cast_agent.db"
    agents.storage.DB_PATH.unlink(missing_ok=True)
    agents.storage.init()
    agent = agents.create({
        "model": "deepseek-chat", "temperature": 0.0, "max_tokens": 300,
        "router": False, "task_advisor": False, "audit": False,
        "use_invariants": False, "keep_last": 0, "name": "Рынок",
        "system_prompt": "Ты следишь за рынком. Отвечай кратко, цифрами из инструментов."})
    question = "Что выросло и что упало за сутки?"
    cast.say(f"вы → {question}", "user")
    reply = agent.ask(question)
    for call in reply["tool_calls"]:
        cast.say(f"  🔧 {call['name']}({call['arguments']})", "warn")
    for line in reply["text"].strip().splitlines()[:4]:
        cast.say(f"агент ← {line[:74]}", "good")
    cast.say()
    cast.say("расписание крутится, данные копятся, агент берёт факты из ряда", "good")
    mcp_tools.BRIDGE.close()

    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    storage.DB_PATH.unlink(missing_ok=True)

    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, "
          f"{path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
