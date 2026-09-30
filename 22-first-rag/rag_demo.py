"""10 контрольных вопросов по Конституции: ответ без RAG против ответа с RAG.

Для каждого вопроса зафиксированы ожидание (что должно быть в ответе), факты-регулярки
для автоматической проверки и статьи-источники. Три замера:

1. факты — сколько обязательных фактов нашлось в ответе (регулярки, не «верно»:
   подстрока может найтись случайно, поэтому метку дублирует судья);
2. источники — нашёл ли поиск нужные статьи (RAG) и назвал ли их ответ (оба режима);
3. судья deepseek-reasoner — другая модель, чем отвечающая deepseek-chat. Видит вопрос,
   ожидание и два обезличенных ответа в случайном порядке, ставит 0/1/2.

Весь прогон (ответы + судья) повторяется REPEATS раз при temperature=0.

    python3 rag_demo.py              # ~5 минут, нужен index.db и запущенная Ollama
    python3 rag_demo.py --repeats 1
"""

import argparse
import json
import pathlib
import random
import re
import sys
import time

import agent
import embeddings
import providers

JUDGE = "deepseek-reasoner"
RESULTS = pathlib.Path(__file__).with_name("data") / "rag_result.json"

# Набор составлен до первого прогона: классика 1993 года, поправки 2020, мелкие детали,
# вопрос на две статьи сразу и вопрос, ответа на который в Конституции нет.
QUESTIONS = [
    {"q": "На какой срок избирается Президент России и сколько сроков может занимать должность один человек?",
     "expect": "Шесть лет; одно лицо не может занимать должность более двух сроков (слово «подряд» "
               "убрано поправкой 2020 года).",
     "facts": [r"шест\w* лет|6 лет", r"двух сроков|два срока"],
     "sources": ["81"], "kind": "поправка 2020"},
    {"q": "Какие требования к проживанию и иностранному гражданству предъявляются к кандидату в президенты?",
     "expect": "Постоянно проживать в России не менее 25 лет; не иметь и не иметь ранее гражданства "
               "другого государства, вида на жительство или иного документа на постоянное проживание за рубежом.",
     "facts": [r"25 лет|двадцати пяти", r"вид\w* на жительство"],
     "sources": ["81"], "kind": "поправка 2020"},
    {"q": "Может ли минимальный размер оплаты труда быть ниже прожиточного минимума?",
     "expect": "Нет: МРОТ гарантируется не менее величины прожиточного минимума трудоспособного "
               "населения в целом по РФ (ч. 5 ст. 75).",
     "facts": [r"прожиточн\w* минимум", r"не менее|не ниже|не может"],
     "sources": ["75"], "kind": "поправка 2020"},
    {"q": "Как Конституция определяет брак?",
     "expect": "Как союз мужчины и женщины; защита института брака — совместное ведение РФ и "
               "субъектов (п. «ж.1» ч. 1 ст. 72).",
     "facts": [r"мужчин\w* и женщин"],
     "sources": ["72"], "kind": "поправка 2020"},
    {"q": "Сколько представителей Российской Федерации в Совете Федерации может назначить Президент и сколько из них пожизненно?",
     "expect": "Не более 30 представителей, из них не более семи — пожизненно (п. «в» ч. 2 ст. 95).",
     "facts": [r"\b30\b|тридцат", r"\b7\b|сем[иь]\b", r"пожизненн"],
     "sources": ["95"], "kind": "деталь"},
    {"q": "С какого возраста можно стать Президентом и с какого — сенатором?",
     "expect": "Президент — не моложе 35 лет (ст. 81), сенатор — с 30 лет (ч. 4 ст. 95).",
     "facts": [r"\b35\b|тридцати пяти", r"\b30\b|тридцати"],
     "sources": ["81", "95"], "kind": "две статьи"},
    {"q": "Допускаются ли действия, направленные на отчуждение части территории России?",
     "expect": "Нет, не допускаются, как и призывы к ним; исключение — делимитация, демаркация и "
               "редемаркация границы с соседями (ч. 2.1 ст. 67).",
     "facts": [r"не допуска|запрещ", r"делимитац|демаркац"],
     "sources": ["67"], "kind": "поправка 2020"},
    {"q": "Какой статус у русского языка в Конституции и как это обосновано?",
     "expect": "Государственный язык на всей территории РФ как язык государствообразующего народа, "
               "входящего в многонациональный союз равноправных народов (ч. 1 ст. 68).",
     "facts": [r"государственн\w* язык", r"государствообразующ"],
     "sources": ["68"], "kind": "поправка 2020"},
    {"q": "Как назначается Председатель Правительства?",
     "expect": "Президентом после утверждения кандидатуры Государственной Думой; после трёх "
               "отклонений Президент назначает сам и вправе распустить Думу (ст. 111).",
     "facts": [r"утвержд", r"государственн\w* дум"],
     "sources": ["111"], "kind": "поправка 2020"},
    {"q": "Какой пенсионный возраст установлен Конституцией РФ?",
     "expect": "Никакой: пенсионный возраст Конституция не устанавливает. Есть только принципы "
               "пенсионной системы и индексация пенсий не реже раза в год (ч. 6 ст. 75).",
     "facts": [r"не устанавлива|не определя|не указ|не содерж|не закрепл|не предусм|ответа нет"
               r"|не фиксир|нет (конкретн|норм|положен)",
               r"индексац"],
     "sources": ["75"], "kind": "нет в тексте"},
]

JUDGE_PROMPT = """Ты проверяешь ответы справочника по Конституции РФ (действующая редакция с поправками 2020 года).
Дан вопрос, эталонное ожидание и два ответа разных систем в случайном порядке.
Оцени каждый ответ по ожиданию:
  2 — содержит всё главное из ожидания и ничего ему не противоречит;
  1 — частично: не хватает важной части или есть неточность;
  0 — неверно, противоречит ожиданию или выдумывает норму, которой нет.
Лишние верные подробности не штрафуются. Номера статей не оцениваются.
Верни только JSON: {"A": {"score": 0-2, "note": "до 12 слов"}, "B": {...}}

Вопрос: <<Q>>
Ожидание: <<EXPECT>>

[A]
<<A>>

[B]
<<B>>"""


def norm(text):
    return text.lower().replace("ё", "е")


def facts_found(answer, facts):
    return [bool(re.search(pattern, norm(answer))) for pattern in facts]


def judge(item, answers, seed):
    """answers: {mode: text} → {mode: {score, note}}, ответ судьи."""
    order = list(answers)
    random.Random(seed).shuffle(order)
    prompt = (JUDGE_PROMPT.replace("<<Q>>", item["q"]).replace("<<EXPECT>>", item["expect"])
              .replace("<<A>>", answers[order[0]]).replace("<<B>>", answers[order[1]]))
    # Рассуждающей модели нужен запас: бюджет съедают скрытые reasoning-токены.
    # Если JSON не разобрался (обрезан или битые кавычки) — ещё одна попытка, токены суммируем.
    tokens, cost, verdict, raw = 0, 0.0, {}, ""
    for _ in range(3):
        reply = providers.call(JUDGE, [{"role": "user", "content": prompt}],
                               temperature=0, max_tokens=6000)
        tokens += reply["prompt_tokens"] + reply["completion_tokens"]
        cost += reply["cost"] or 0
        raw = reply["text"]
        match = re.search(r"\{.*\}", raw, flags=re.S)
        try:
            verdict = json.loads(match.group(0)) if match else {}
        except json.JSONDecodeError:
            verdict = {}
        if all(isinstance((verdict.get(x) or {}).get("score"), int) for x in "AB"):
            break
    reply = {"tokens": tokens, "cost": cost, "raw": raw}
    result = {}
    for letter, mode in zip("AB", order):
        v = verdict.get(letter) or {}
        result[mode] = {"score": v.get("score"), "note": v.get("note", "")}
    return result, reply


def run_once(bot, attempt, log):
    rows = []
    for i, item in enumerate(QUESTIONS):
        answers = bot.compare(item["q"])
        verdict, reply = judge(item, {m: a["answer"] for m, a in answers.items()},
                               seed=attempt * 100 + i)
        row = {"n": i + 1, "judge_tokens": reply["tokens"], "judge_cost": reply["cost"]}
        if any(v["score"] is None for v in verdict.values()):
            row["judge_raw"] = reply["raw"]
        for mode, a in answers.items():
            found = facts_found(a["answer"], item["facts"])
            retrieved = sorted({h["article"] for h in a["hits"]})
            row[mode] = {
                "answer": a["answer"], "facts": found,
                "cited": a["cited"], "cited_ok": all(s in a["cited"] for s in item["sources"]),
                "retrieved": [h["chunk_id"] for h in a["hits"]],
                "retrieved_ok": all(s in retrieved for s in item["sources"]) if a["hits"] else None,
                "score": verdict[mode]["score"], "note": verdict[mode]["note"],
                "prompt_tokens": a["prompt_tokens"], "completion_tokens": a["completion_tokens"],
                "cost": a["cost"] or 0, "seconds": a["seconds"],
            }
        rows.append(row)
        p, r = row["plain"], row["rag"]
        log(f"  {i + 1:>2}. без RAG факты {sum(p['facts'])}/{len(p['facts'])} судья {p['score']}"
            f"  |  RAG факты {sum(r['facts'])}/{len(r['facts'])} судья {r['score']}"
            f" поиск {'✓' if r['retrieved_ok'] else '✗'}   {item['q'][:52]}")
    return rows


def summarize(rows):
    out = {}
    for mode in agent.MODES:
        cells = [row[mode] for row in rows]
        scores = [c["score"] for c in cells if isinstance(c["score"], int)]
        out[mode] = {
            "facts": sum(sum(c["facts"]) for c in cells),
            "facts_total": sum(len(c["facts"]) for c in cells),
            "all_facts": sum(all(c["facts"]) for c in cells),
            "cited_ok": sum(c["cited_ok"] for c in cells),
            "retrieved_ok": sum(bool(c["retrieved_ok"]) for c in cells),
            "judge_sum": sum(scores), "judge_max": 2 * len(cells),
            "judge_2": scores.count(2), "judge_0": scores.count(0),
            "judge_failed": len(cells) - len(scores),
            "prompt_tokens": sum(c["prompt_tokens"] for c in cells),
            "completion_tokens": sum(c["completion_tokens"] for c in cells),
            "cost": round(sum(c["cost"] for c in cells), 5),
            "seconds": round(sum(c["seconds"] for c in cells), 1),
        }
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    embeddings.check()
    bot = agent.Agent()
    info = bot.index.info
    print(f"индекс: {len(bot.index.rows)} чанков, {info['model']}, источник {info['source']}")
    print(f"отвечает {bot.model} (temperature={bot.temperature}, k={bot.k}), судья {JUDGE}\n")

    runs = []
    for attempt in range(args.repeats):
        print(f"=== проход {attempt + 1}")
        started = time.monotonic()
        rows = run_once(bot, attempt, print)
        runs.append({"rows": rows, "summary": summarize(rows),
                     "seconds": round(time.monotonic() - started, 1)})
        print()

    print(f"{'':10} {'факты':>9} {'все факты':>10} {'статья названа':>15} "
          f"{'судья Σ':>8} {'2':>3} {'0':>3} {'токены вх/вых':>15} {'$':>8}")
    for attempt, run in enumerate(runs, 1):
        for mode in agent.MODES:
            s = run["summary"][mode]
            print(f"{agent.MODE_LABELS[mode]:>7} #{attempt} {s['facts']:>5}/{s['facts_total']:<3} "
                  f"{s['all_facts']:>7}/10 {s['cited_ok']:>12}/10 "
                  f"{s['judge_sum']:>5}/{s['judge_max']} {s['judge_2']:>3} {s['judge_0']:>3} "
                  f"{s['prompt_tokens']:>7}/{s['completion_tokens']:<7} {s['cost']:>8.5f}")
    retrieved = [run["summary"]["rag"]["retrieved_ok"] for run in runs]
    judge_tokens = sum(r["judge_tokens"] for run in runs for r in run["rows"])
    judge_cost = sum(r["judge_cost"] for run in runs for r in run["rows"])
    print(f"\nпоиск нашёл все нужные статьи в топ-{bot.k}: {' / '.join(map(str, retrieved))} из 10")
    failed = sum(run["summary"][m]["judge_failed"] for run in runs for m in agent.MODES)
    print(f"судья: {judge_tokens} токенов, ${judge_cost:.4f}, не разобрано оценок: {failed}")

    same = all(run["rows"][i][m]["answer"] == runs[0]["rows"][i][m]["answer"]
               for run in runs for i in range(len(QUESTIONS)) for m in agent.MODES)
    print(f"ответы всех проходов совпали дословно: {'да' if same else 'нет'}")

    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps({
        "model": bot.model, "judge": JUDGE, "k": bot.k, "index": info,
        "questions": QUESTIONS, "runs": runs, "same_answers": same,
        "judge_tokens": judge_tokens, "judge_cost": round(judge_cost, 5),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {RESULTS.relative_to(pathlib.Path(__file__).parent)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        print(f"ошибка: {error}", file=sys.stderr)
        sys.exit(1)
