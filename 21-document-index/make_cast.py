"""Собирает demo.svg — анимированную запись индексации и сравнения стратегий chunking.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.

Каст ничего не выдумывает: цифры берутся из data/compare_result.json (прогон
compare_chunking.py), а поиск по двум вопросам выполняется вживую по index.db.

    python3 make_cast.py           → demo.svg (≤60 секунд)
"""

import html
import json
import pathlib
import sys

import compare_chunking
import embeddings
import index_store

WIDTH, HEIGHT = 960, 620
PAD_X, PAD_Y = 22, 34
LINE = 19
MAX_LINES = 29
MAX_SECONDS = 58

COLORS = {
    "plain": "#d4d4d8", "dim": "#71717a", "user": "#60a5fa", "good": "#4ade80",
    "bad": "#f87171", "warn": "#fbbf24", "head": "#f4f4f5",
    "fixed": "#fbbf24", "structure": "#22d3ee",
}

RESULTS = pathlib.Path(__file__).with_name("data") / "compare_result.json"
SHOW = [9, 4]   # «9 206» и «User-Agent»: вопросы, где стратегии разошлись


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
                     f'python3 compare_chunking.py — индекс документов, fixed против structure</text>')

        for index, frame in enumerate(self.frames):
            rows = "".join(
                f'<text x="{PAD_X}" y="{PAD_Y + row * LINE}" fill="{COLORS[color]}" '
                f'xml:space="preserve">{html.escape(text)}</text>'
                for row, (text, color) in enumerate(frame)
            )
            parts.append(f'<g class="frame" id="f{index}">{rows}</g>')

        parts.append("</svg>")
        return "\n".join(parts), total


def live_search(cast, question, fact, sources):
    vector, _ = embeddings.embed_one(question)
    db = index_store.connect()
    cast.say(f"? {question}", "user")
    for name in ("fixed", "structure"):
        top = index_store.Index(db, name).search(vector, 3)
        for rank, row in enumerate(top, 1):
            found = compare_chunking.hit(row, sources, fact)
            label = row["chunk_id"].split(":", 1)[1].replace("/README.md", "")
            mark = "✓" if found else " "
            cast.say(f"  {name:9} {rank}. {row['score']:.3f} {mark} {label:28} "
                     f"{row['section'][:34]}", name if found else "dim")
    cast.say()


def main():
    if not RESULTS.exists():
        print("нет data/compare_result.json — сначала python3 compare_chunking.py")
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    cast = Cast()

    corpus = data["corpus"]
    cast.say("$ python3 build_index.py", "dim")
    cast.say(f"корпус: {corpus['documents']} документов, {corpus['chars']} символов "
             f"≈ {corpus['chars'] // 1800} стр.  эмбеддинги: {data['model']} (Ollama)", "head")
    for name, s in data["chunking"].items():
        cast.say(f"  {name:9} {s['chunks']:>4} чанков  средний {s['avg']:>4}  "
                 f"{s['min']}–{s['max']} симв.  разрезано таблиц/кода {s['broken']:>3} "
                 f"({s['broken_pct']}%)  {s['embed_seconds']} с", name)
    cast.say()

    cast.say("$ python3 search.py …", "dim")
    for index in SHOW:
        question, sources, fact = compare_chunking.QUESTIONS[index]
        live_search(cast, question, fact, sources)

    cast.say(f"$ python3 compare_chunking.py   ({len(data['per_question'])} вопросов, "
             f"✓ = эталон найден)", "dim")
    cast.say(f"  {'':10} {'топ-1':>6} {'топ-3':>6} {'топ-10':>7} {'MRR':>6} "
             f"{'симв. в топ-3':>14}", "head")
    for name, s in data["retrieval"].items():
        cast.say(f"  {name:10} {s['top1']:>6} {s['top3']:>6} {s['top10']:>7} "
                 f"{s['mrr']:>6} {s['context_chars']:>14}", name)
    stable = data["stable"]
    cast.say(f"  3 прохода: расхождение векторов {stable['vector_drift']:.0e}, "
             f"топы совпали: {'да' if stable['same_top'] else 'нет'}", "dim")
    cast.say()

    judge = data["judge"]
    cast.say(f"DeepSeek-судья, фрагменты вперемешку — вопросов с ответом в топ-3:", "head")
    for name in data["retrieval"]:
        per_run = [run["answered"][name] for run in judge["runs"]]
        cast.say(f"  {name:10} {' / '.join(map(str, per_run))} из "
                 f"{len(data['per_question'])}", name)
    cast.say(f"  судья: {judge['tokens']} токенов, ${judge['cost']}", "dim")

    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, "
          f"{path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
