"""Собирает demo.svg — анимированную запись прогона service_demo.py.

Настоящего экранного видео тут не снять: на машине нет ни ffmpeg, ни ImageMagick,
а системному screencapture нужно разрешение Screen Recording, которое выдаёт только
пользователь. Поэтому кадры терминала собираются в самодостаточный SVG с CSS-анимацией.

Каст ничего не выдумывает: всё берётся из data/service_result.json. Ключей в нём нет.

    python3 make_cast.py           → demo.svg (≤60 секунд)
"""

import html
import json
import pathlib
import sys

WIDTH, HEIGHT = 1000, 650
PAD_X, PAD_Y = 22, 34
LINE = 19
MAX_LINES = 31
MAX_SECONDS = 58

COLORS = {"plain": "#d4d4d8", "dim": "#71717a", "user": "#60a5fa", "good": "#4ade80",
          "bad": "#f87171", "warn": "#fbbf24", "head": "#f4f4f5", "accent": "#22d3ee"}

RESULTS = pathlib.Path(__file__).with_name("data") / "service_result.json"
ERR = lambda s: "good" if s == 200 else "warn"


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
                     f'<text x="76" y="21" fill="{COLORS["dim"]}" font-size="12">python3 service_demo.py '
                     f'— приватный AI-сервис на локальной qwen2.5:3b, проверка по сети</text>')
        for i, frame in enumerate(self.frames):
            rows = "".join(f'<text x="{PAD_X}" y="{PAD_Y + r * LINE}" fill="{COLORS[c]}" '
                           f'xml:space="preserve">{html.escape(t)}</text>'
                           for r, (t, c) in enumerate(frame))
            parts.append(f'<g class="frame" id="f{i}">{rows}</g>')
        parts.append("</svg>")
        return "\n".join(parts), total


def load_table(cast, block, title):
    cast.say(title, "head", frame=False)
    cast.say(f"  {'парал.':>6} {'коды':>14} {'«42»':>6} {'стена':>7} {'p50':>6} {'p95':>6} {'очередь≤':>9} {'пик раб/ожид':>13}",
             "dim", frame=False)
    for w in block["waves"]:
        codes = " ".join(f"{k}×{v}" for k, v in sorted(w["codes"].items()))
        cast.say(f"  {w['n']:>6} {codes:>14} {str(w['right']) + '/' + str(w['ok']):>6} {w['wall_s']:>6}с "
                 f"{w['p50']:>6} {w['p95']:>6} {w['queue_wait_max']:>8}с "
                 f"{str(w['peak_running']) + '/' + str(w['peak_waiting']):>13}",
                 "warn" if "503" in w["codes"] else "plain")
    s = block["sequential"]
    cast.say(f"  {s['n']} подряд: коды {s['codes']}, «42» {s['right']}/{s['n']}, "
             f"первые 10 — {s['first10_mean']} с, последние 10 — {s['last10_mean']} с", "good")


def main():
    if not RESULTS.exists():
        print("нет data/service_result.json — сначала python3 service_demo.py")
        return 1
    d = json.loads(RESULTS.read_text(encoding="utf-8"))
    lim = d["limits"]
    cast = Cast()
    cast.say(f"{d['model']} · Ollama {d['ollama']} · Mac M1 8 ГБ как домашний сервер", "head")
    cast.say(f"  окно {lim['context']} · ответ ≤{lim['max_output']} · в модели {lim['inflight']} · очередь {lim['queue']} · "
             f"owner {lim['owner_rpm']}/мин · loadtest {lim['loadtest_rpm']}/мин", "dim")
    cast.say()

    n = d["network"]
    cast.say(f"$ curl {d['url']}/health", "user")
    cast.say(f"  {n['health']['status']} {json.dumps(n['health']['body'], ensure_ascii=False)}", "good")
    cast.say(f"  Ollama из сети ({d['url'].split('//')[1].split(':')[0]}:11434): {n['ollama_lan']}", "good", frame=False)
    cast.say(f"  Ollama на 127.0.0.1:11434: {n['ollama_local']} — только для шлюза", "dim")
    cast.say()
    cast.say("ключи:", "user", frame=False)
    for a in d["auth"]:
        cast.say(f"  {a['case']:16} → {a['status']} {a['error']}", ERR(a["status"]), frame=False)
    cast.say("", frame=True)
    cast.clear()

    load_table(cast, d["load"], f"=== нагрузка: «17 + 25?», ключ loadtest, по {d['url']}")
    if "load_parallel2" in d:
        cast.say()
        load_table(cast, d["load_parallel2"], "=== то же при OLLAMA_NUM_PARALLEL=2")
    cast.say()
    cast.clear()

    r = d["rate"]
    cast.say(f"=== rate limit: ключ owner, {r['rpm']}/мин, burst {r['burst']}", "head")
    cast.say("  " + " ".join(str(x["status"]) for x in r["rows"]), "plain")
    cast.say(f"  {r['ok']} прошли, {r['limited']} отклонены 429, Retry-After: {r['retry_after']} с → "
             f"после паузы {r['after_wait']}", "warn")
    cast.say()

    c = d["context"]
    o = c["ollama_direct"]
    cast.say(f"=== контекст: секрет в начале текста, вопрос в конце", "head")
    cast.say(f"  Ollama напрямую, ~5000 ток в окно 4096 → prompt_eval_count {o['prompt_tokens']}, "
             f"ответ «{o['answer'][:30]}»", "bad")
    cast.say(f"    секрет {'найден' if o['found'] else 'потерян'}, ошибки нет — Ollama молча обрезала начало", "bad")
    g = c["gateway_big"]
    cast.say(f"  через шлюз → {g['status']} {g['error']} за {g['seconds']} с", "warn")
    cast.say(f"    {g['message'][:96]}", "dim")
    f = c["gateway_fit"]
    if f.get("status") == 200:
        cast.say(f"  ~3300 ток через шлюз → 200, оценка {f['estimate']} / реально {f['real']}, "
                 f"ответ «{f['answer'][:24]}» {'✓' if f['found'] else '✗'}", "good")
    m = c["max_tokens"]
    cast.say(f"  max_tokens=5000 → сгенерировано {m['got']}, finish_reason={m['finish']}", "accent")
    cast.say(f"  41 сообщение → {c['many_messages']['status']} · тело 300 КБ → {c['big_body']['status']} · "
             f"битый JSON → {c['broken_json']['status']}", "accent")
    cast.say("", frame=True)

    markup, seconds = cast.svg()
    path = pathlib.Path(__file__).with_name("demo.svg")
    path.write_text(markup, encoding="utf-8")
    print(f"\n{path.name}: {len(cast.frames)} кадров, {seconds} с, {path.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
