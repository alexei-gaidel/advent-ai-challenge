"""Доказательство дня: /help в трёх режимах на вопросах о проекте.

    llm   — только модель, без документации и без git;
    rag   — документация проекта через RAG, без MCP;
    full  — RAG + MCP: ветка всегда, остальные инструменты модель зовёт сама.

Вопросы трёх типов: про документацию (ответ есть в README/CLAUDE.md/docs), про текущее
состояние репозитория (ответ есть только в git) и про код (ответа нет ни в одной
документации — только в файле).

Две метки качества, как велит CLAUDE.md:
    «эталон найден» — в ответе есть все обязательные подстроки (грубо, может врать);
    судья            — openai/gpt-oss-120b на Groq, провайдер не участвует в сравнении;
                       ответы трёх режимов обезличены и перемешаны, оценка 0–2.

Эталоны для git-вопросов вычисляются в момент прогона прямо из git, а не вписаны руками.

    python3 help_demo.py              # 3 прохода, ~6 минут
    python3 help_demo.py --passes 1
"""

import argparse
import json
import pathlib
import random
import re
import time

import assistant as dev
import project_server
import providers

JUDGE = "openai/gpt-oss-120b"
OUT = pathlib.Path(__file__).with_name("data") / "help_result.json"


def git(name, **arguments):
    return project_server.run_tool(name, arguments)


def questions():
    """(id, тип, вопрос, обязательные подстроки, эталон для судьи)."""
    branch = git("git_branch").splitlines()[0].replace("Ветка: ", "")
    last = git("git_log", limit=1).split(" · ")[0]
    status = [line[3:].split("/")[0] for line in git("git_status").splitlines()
              if line[:2] in (" M", "M ", "??", "A ")]
    dirty = sorted(set(status))
    files_26 = [f.split("/")[-1] for f in git("git_files", path="26-local-llm").splitlines()
                if f.endswith(".py")]
    return [
        ("mcp-client", "docs", "Где в проекте лежит MCP-клиент и какие транспорты он поддерживает?",
         ["16-mcp-client", "stdio", "HTTP"],
         "16-mcp-client/mcp_client.py (копируется в 17+), транспорты stdio и Streamable HTTP"),
        ("port-23", "docs", "На каком порту поднимается веб-интерфейс дня 23?",
         ["8023"], "8023 (правило: 8000 + номер дня)"),
        ("embedder", "docs", "Какой моделью в проекте считаются эмбеддинги и где она работает?",
         ["bge-m3", "Ollama"], "bge-m3 в локальной Ollama (localhost:11434), 1024 измерения"),
        ("schema", "docs", "Какие колонки у таблицы chunks в индексе Конституции дня 25?",
         ["article", "amended"],
         "chunk_id, article, chapter, parts, amended, text, n_chars, vector"),
        ("weather", "docs", "Какие инструменты у MCP-сервера погоды и в каком порядке их вызывать?",
         ["search", "forecast"], "search (город → place_id), затем forecast(place_id, date)"),
        ("groq-403", "docs", "Groq отвечает 403 error code 1010 — в чём причина и как лечить?",
         ["User-Agent"], "Cloudflare банит Python-urllib; передавать свой User-Agent"),
        ("keys", "docs", "Где хранятся API-ключи и как скрипты их находят?",
         [".env", "find_env_file"], "корневой .env в .gitignore, ищется вверх по дереву find_env_file()"),
        ("missing", "docs", "Каких номеров дней нет в репозитории?",
         ["08", "27"], "нет дней 08 и 27"),
        ("branch", "git", "На какой git-ветке я сейчас работаю?",
         [branch], f"ветка {branch}"),
        ("dirty", "git", "Что сейчас не закоммичено в репозитории?",
         dirty, "изменены или не отслеживаются: " + ", ".join(dirty)),
        ("last", "git", "Какой последний коммит в репозитории? Назови хеш.",
         [last], f"коммит {last}"),
        ("files-26", "git", "Какие Python-файлы лежат в папке 26-local-llm?",
         files_26, ", ".join(files_26)),
        ("timeout", "code", "Какой таймаут по умолчанию у HttpTransport в mcp_client.py дня 16?",
         ["30"], "30 секунд (def __init__(self, url, timeout=30))"),
        ("port-01", "code", "На каком порту запускается web.py дня 01 — тоже по правилу 8000 + номер?",
         ["8000"], "8000, а не 8001: день 01 написан до правила"),
    ]


def found(answer, needles):
    low = answer.lower()
    return all(n.lower() in low for n in needles)


JUDGE_PROMPT = """Ты проверяешь ответы ассистента разработчика на вопрос о репозитории.
Эталон составлен по реальному состоянию репозитория. Оцени каждый ответ:
  2 — верен и полон по существу эталона;
  1 — частично верен или верен, но с лишними неверными утверждениями;
  0 — неверен, выдуман или «не знаю».
Верни только JSON {"A": 0-2, "B": 0-2, "C": 0-2}.

Вопрос: <<Q>>
Эталон: <<REF>>

<<ANSWERS>>"""


def judge(question, reference, answers, seed):
    """answers: {mode: текст} → ({mode: оценка}, токены). Порядок перемешан."""
    modes = list(answers)
    random.Random(seed).shuffle(modes)
    letters = dict(zip("ABC", modes))
    block = "\n\n".join(f"Ответ {letter}:\n{answers[mode]}" for letter, mode in letters.items())
    prompt = (JUDGE_PROMPT.replace("<<Q>>", question).replace("<<REF>>", reference)
              .replace("<<ANSWERS>>", block))
    reply = providers.call(JUDGE, [{"role": "user", "content": prompt}],
                           temperature=0, max_tokens=3000)
    match = re.search(r"\{[^{}]*\}", reply["text"])
    raw = json.loads(match.group(0)) if match else {}
    scores = {mode: raw.get(letter) for letter, mode in letters.items()}
    return scores, reply["prompt_tokens"] + reply["completion_tokens"]


def run(passes):
    bot = dev.DevAssistant()
    items = questions()
    rows = []
    for number in range(1, passes + 1):
        for qid, kind, question, needles, reference in items:
            row = {"pass": number, "id": qid, "kind": kind, "question": question,
                   "needles": needles, "modes": {}}
            for mode in dev.MODES:
                try:
                    r = bot.help(question, mode=mode, temperature=0)
                except Exception as error:          # сеть, лимит — фиксируем, а не падаем
                    r = {"answer": f"(ошибка: {error})", "tools": [], "prompt_tokens": 0,
                         "completion_tokens": 0, "cost": 0, "seconds": 0}
                row["modes"][mode] = {
                    "answer": r["answer"], "found": found(r["answer"], needles),
                    "tools": [t["name"] for t in r["tools"] if t.get("by") == "модель"],
                    "tokens": r["prompt_tokens"] + r["completion_tokens"],
                    "cost": r["cost"], "seconds": r["seconds"], "leak": r.get("leak", False),
                }
            try:
                scores, tokens = judge(question, reference,
                                       {m: v["answer"] for m, v in row["modes"].items()},
                                       seed=f"{number}-{qid}")
            except Exception as error:
                scores, tokens = {m: None for m in dev.MODES}, 0
                row["judge_error"] = str(error)
            for mode, score in scores.items():
                row["modes"][mode]["judge"] = score
            row["judge_tokens"] = tokens
            rows.append(row)
            marks = "  ".join(f"{m}:{'✓' if v['found'] else '·'}{v['judge']}"
                              f"{'+' + ','.join(v['tools']) if v['tools'] else ''}"
                              for m, v in row["modes"].items())
            print(f"[{number}] {qid:10} {marks}", flush=True)
    return rows


def summary(rows, passes):
    print("\nИтог по режимам (сумма за все проходы):")
    print(f"{'режим':12} {'тип':5} {'эталон':>8} {'судья':>9} {'токенов':>9} {'$':>8} {'с/вопр':>7}")
    table = []
    for mode in dev.MODES:
        for kind in ("docs", "git", "code", "all"):
            sel = [r["modes"][mode] for r in rows if kind == "all" or r["kind"] == kind]
            n = len(sel)
            judged = [v["judge"] for v in sel if isinstance(v["judge"], (int, float))]
            line = {"mode": mode, "kind": kind, "n": n,
                    "found": sum(v["found"] for v in sel),
                    "judge": sum(judged), "judge_max": 2 * len(judged),
                    "tokens": sum(v["tokens"] for v in sel),
                    "cost": round(sum(v["cost"] or 0 for v in sel), 4),
                    "seconds": round(sum(v["seconds"] for v in sel) / max(n, 1), 1)}
            table.append(line)
            print(f"{dev.MODES[mode]:12} {kind:5} {line['found']:>4}/{n:<3} "
                  f"{line['judge']:>4}/{line['judge_max']:<4} {line['tokens']:>9} "
                  f"{line['cost']:>8} {line['seconds']:>7}")
    return table


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--passes", type=int, default=3)
    args = parser.parse_args()
    started = time.monotonic()
    rows = run(args.passes)
    table = summary(rows, args.passes)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"passes": args.passes, "judge": JUDGE, "rows": rows,
                               "summary": table,
                               "minutes": round((time.monotonic() - started) / 60, 1)},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {OUT.relative_to(OUT.parent.parent)} за "
          f"{(time.monotonic() - started) / 60:.1f} мин")


if __name__ == "__main__":
    main()
