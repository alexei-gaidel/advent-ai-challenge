"""Собирает demo.svg — анимированную запись живого прогона персонализации.

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
    storage.DB_PATH.unlink(missing_ok=True)
    storage.init()
    known = list(profiles.load_all().values())
    cast = Cast()

    agent = agents.create({**SETTINGS, "name": "Стенд", "profile": ""})
    cast.say("$ python3 web.py   # профиль подмешивается в каждый запрос", "dim")
    cast.say(f"профили: {' · '.join(p['name'] for p in known)}", "head")
    cast.say()
    cast.say(f"вы → {QUESTION}", "user")
    cast.say("(в вопросе ни слова о том, как отвечать)", "dim")
    cast.say()

    for profile in known:
        agent.set_profile(profile["id"])
        cast.say(f"[{profile['name']}] {profile['about']}", "prof")
        cast.say(f"  стиль: {profile['style']}"[:WRAP], "dim")
        result = agent.ask(QUESTION)
        cast.answer(result["text"])
        m = profiles.measure(result["text"])
        cast.say(f"  {m['chars']} симв. · пунктов {m['bullets']} · латиницы {m['latin']}% · "
                 f"обращение: {m['address']}", "dim")
        cast.say()

    alex = known[0]
    agent.set_profile(alex["id"])
    agent.update({"router": True, "use_short": True})
    remark = "Кстати, я перешёл на Go. И давай без списков, сплошным текстом."
    cast.say(f"вы → {remark}", "user")
    result = agent.ask(remark)
    for proposal in result["proposed"]:
        agent.decide(proposal["id"], "accept")
        cast.say(f"  роутер → профиль · {proposal['kind']}: {proposal['key']} = "
                 f"{proposal['value'][:45]}   ✓", "warn")
    alex = profiles.get(alex["id"])
    cast.say(f"  формат теперь: {alex['format'][:70]}", "prof")
    cast.say()

    agent.update({"router": False, "use_short": False})
    cast.say(f"вы → {QUESTION}   (снова, только профиль)", "user")
    result = agent.ask(QUESTION)
    cast.answer(result["text"], "good")
    m = profiles.measure(result["text"])
    cast.say(f"  пунктов {m['bullets']} — предпочтение из диалога сработало", "good")

    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    storage.DB_PATH.unlink(missing_ok=True)

    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, "
          f"{path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
