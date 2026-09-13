"""Собирает demo.svg — анимированную запись живого прогона ветвления.

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

WIDTH, HEIGHT = 960, 600
PAD_X, PAD_Y = 22, 34
LINE = 19
MAX_LINES = 28
MAX_SECONDS = 58

COLORS = {
    "plain": "#d4d4d8",
    "dim": "#71717a",
    "user": "#60a5fa",
    "agent": "#a1a1aa",
    "good": "#4ade80",
    "warn": "#fbbf24",
    "head": "#f4f4f5",
}

SETTINGS = {
    "model": "deepseek-chat",
    "temperature": 0.0,
    "max_tokens": 120,
    "strategy": "branching",
    "system_prompt": "Ты аналитик, собираешь ТЗ. Отвечай одной короткой фразой.",
}


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
        step = min(MAX_SECONDS / count, 2.2)
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
                     f'python3 make_cast.py — ветвление диалога</text>')

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
    storage.DB_PATH.unlink(missing_ok=True)
    storage.init()
    cast = Cast()

    agent = agents.create({**SETTINGS, "name": "ТЗ"})
    cast.say("$ python3 web.py   # стратегия Branching", "dim")
    cast.say(f"агент {agent.id}: ветка main, стратегия Branching", "head")
    cast.say()

    for text in ("Собираем ТЗ: приложение для аренды велосипедов в Казани.",
                 "Бюджет 2,5 млн, запуск 1 декабря."):
        cast.say(f"вы → {text}", "user")
        cast.say(f"агент ← {agent.ask(text)['text'][:88]}", "agent")

    cast.say()
    agent.checkpoint("требования собраны")
    cast.say(f"⎇ чекпоинт «требования собраны» на {len(agent.chain())} сообщениях", "warn")
    cast.say()

    answers = {}
    for title, decision in (("Тюльпан", "Кодовое имя проекта — «Тюльпан». Запомни."),
                            ("Кедр", "Кодовое имя проекта — «Кедр». Запомни.")):
        agent.switch(storage.MAIN_BRANCH)
        agent.fork(title=title)
        cast.say(f"⎇ ветка «{title}» от чекпоинта ({agent.settings['branch']})", "warn")
        cast.say(f"вы → {decision}", "user")
        cast.say(f"агент ← {agent.ask(decision)['text'][:88]}", "agent")
        answers[title] = agent.settings["branch"]
        cast.say()

    cast.say("Один и тот же вопрос в обе ветки:", "head")
    question = "Как называется наш проект? Ответь только названием."
    for title, branch in answers.items():
        agent.switch(branch)
        cast.say(f"[{title}] вы → {question}", "user")
        cast.say(f"[{title}] агент ← {agent.ask(question)['text'][:80]}", "good")

    cast.say()
    cast.say(f"веток: {len(agent.branches)}, общий префикс: "
             f"{agent.branches[agent.settings['branch']]['fork_at']} сообщений", "dim")
    cast.say("решения ветвей не протекают друг в друга", "good")

    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    storage.DB_PATH.unlink(missing_ok=True)

    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, "
          f"{path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
