"""10 контрольных вопросов: обычный RAG дня 23 против RAG с цитатами и режимом «не знаю».

Для каждого ответа проверяется:
1. есть ли источники (source + chunk_id) — кодом;
2. есть ли цитаты и дословны ли они — кодом (citations.verify_quote), с учётом отбракованных
   и повторов;
3. совпадает ли смысл ответа с цитатами — судья deepseek-reasoner: каждый факт ответа
   подтверждён цитатами (cited) или найденными фрагментами (rag)? supported / partial / unsupported;
4. верен ли ответ по ожиданию — тот же судья, 0/1/2;
5. «не знаю» там, где его ждали, и не там, где не ждали.

Вопросы: 6 с ответом в тексте, 1 ловушка (пенсионный возраст в Конституции не установлен),
1 вне базы, 1 расплывчатый, 1 со слабым контекстом (тема рядом, ответа нет).
Весь прогон повторяется REPEATS раз при temperature=0.

    python3 citations_demo.py               # ~10 минут
    python3 citations_demo.py --repeats 1
"""

import argparse
import json
import pathlib
import random
import re
import sys
import time

import agent
import citations
import embeddings
import providers
import retrieval

JUDGE = "deepseek-reasoner"
RESULTS = pathlib.Path(__file__).with_name("data") / "citations_result.json"

# expect: answer — ответ в тексте есть; unknown — ждём «не знаю» + уточнение;
# either — годится и «в Конституции это не установлено», и «не знаю».
QUESTIONS = [
    {"q": "На какой срок избирается Президент России и сколько сроков может занимать должность один человек?",
     "expect": "Шесть лет; одно лицо не может занимать должность более двух сроков.",
     "sources": ["81"], "type": "answer"},
    {"q": "Может ли минимальный размер оплаты труда быть ниже прожиточного минимума?",
     "expect": "Нет: МРОТ гарантируется не менее величины прожиточного минимума трудоспособного "
               "населения в целом по РФ.", "sources": ["75"], "type": "answer"},
    {"q": "Как Конституция определяет брак?",
     "expect": "Как союз мужчины и женщины; защита института брака — совместное ведение РФ и субъектов.",
     "sources": ["72"], "type": "answer"},
    {"q": "Сколько представителей Российской Федерации в Совете Федерации может назначить Президент и сколько из них пожизненно?",
     "expect": "Не более 30 представителей, из них не более семи — пожизненно.",
     "sources": ["95"], "type": "answer"},
    {"q": "Могут ли меня держать под арестом без суда больше двух суток?",
     "expect": "Нет: арест — только по судебному решению; до решения суда задержание не более 48 часов.",
     "sources": ["22"], "type": "answer"},
    {"q": "Обязан ли я давать показания против своей жены?",
     "expect": "Нет: никто не обязан свидетельствовать против себя, своего супруга и близких родственников.",
     "sources": ["51"], "type": "answer"},
    {"q": "Какой пенсионный возраст установлен Конституцией РФ?",
     "expect": "Конкретного пенсионного возраста Конституция не устанавливает. Допустимо и «в Конституции "
               "этого нет», и «не знаю» с просьбой уточнить; называть цифры возраста — ошибка.",
     "sources": ["39", "75"], "type": "either"},
    {"q": "Какой штраф за превышение скорости на 40 км/ч?",
     "expect": "В Конституции этого нет. Агент должен сказать «не знаю» и попросить уточнить, "
               "не называя суммы.", "sources": [], "type": "unknown"},
    {"q": "Что там про сроки?",
     "expect": "Вопрос расплывчатый: непонятно, чьи сроки. Хороший ответ — «не знаю» и просьба уточнить "
               "(сроки Президента, Думы, задержания…). Перечень нескольких сроков с источниками — частично.",
     "sources": [], "type": "unknown"},
    {"q": "Сколько длится отпуск по уходу за ребёнком?",
     "expect": "В Конституции длительности отпуска нет (есть только защита материнства и детства, ст. 38). "
               "Агент должен сказать «не знаю» и попросить уточнить; называть сроки отпуска — ошибка.",
     "sources": [], "type": "unknown"},
]

JUDGE_PROMPT = """Ты проверяешь ответы справочника по Конституции РФ. Даны вопрос, эталонное ожидание
и ответы двух систем в случайном порядке. У каждого ответа — материалы, на которые он опирался
(цитаты или найденные фрагменты Конституции).

Для каждого ответа поставь две оценки:
  correct: 2 — соответствует ожиданию; 1 — частично; 0 — неверно, выдумано или ответ там, где надо было отказаться;
  support: "supported" — каждый факт ответа есть в его материалах; "partial" — часть фактов не подтверждена;
           "unsupported" — главный факт ответа в материалах отсутствует; "n/a" — ответ «не знаю» без утверждений.
Верни только JSON: {"A": {"correct": 0-2, "support": "...", "note": "до 12 слов"}, "B": {...}}

Вопрос: <<Q>>
Ожидание: <<EXPECT>>

<<ANSWERS>>"""


def evidence(result):
    if result["quotes"]:
        return "\n".join(f"- [{q['chunk_id']}] «{q['text']}»" for q in result["quotes"])
    if result["unknown"] or not result["context"]:
        return "(материалов нет)"
    return "\n\n".join(f"[{c['chunk_id']}]\n{c['text']}" for c in result["context"])


def judge(item, results, seed):
    order = list(results)
    random.Random(seed).shuffle(order)
    block = "\n\n".join(
        f"[{letter}] Ответ:\n{results[mode]['answer']}\n\n[{letter}] Материалы:\n{evidence(results[mode])}"
        for letter, mode in zip("AB", order))
    prompt = (JUDGE_PROMPT.replace("<<Q>>", item["q"]).replace("<<EXPECT>>", item["expect"])
              .replace("<<ANSWERS>>", block))
    tokens, cost, verdict = 0, 0.0, {}
    for _ in range(3):
        reply = providers.call(JUDGE, [{"role": "user", "content": prompt}],
                               temperature=0, max_tokens=6000)
        tokens += reply["prompt_tokens"] + reply["completion_tokens"]
        cost += reply["cost"] or 0
        match = re.search(r"\{.*\}", reply["text"], flags=re.S)
        try:
            verdict = json.loads(match.group(0)) if match else {}
        except json.JSONDecodeError:
            verdict = {}
        if all(isinstance((verdict.get(x) or {}).get("correct"), int) for x in "AB"):
            break
    out = {}
    for letter, mode in zip("AB", order):
        v = verdict.get(letter) or {}
        out[mode] = {"correct": v.get("correct"), "support": v.get("support"), "note": v.get("note", "")}
    return out, tokens, cost


def run_once(bot, attempt, log):
    rows = []
    for i, item in enumerate(QUESTIONS):
        results = bot.compare(item["q"])
        verdict, tokens, cost = judge(item, results, seed=attempt * 100 + i)
        row = {"n": i + 1, "judge_tokens": tokens, "judge_cost": cost}
        for mode, r in results.items():
            articles = {s["chunk_id"].split("#")[0].replace("ст.", "") for s in r["sources"]}
            row[mode] = {
                "answer": r["answer"], "unknown": r["unknown"], "stage": r["stage"],
                "reason": r.get("reason"), "clarify": r["clarify"],
                "sources": r["sources"], "quotes": r["quotes"], "rejected": r["rejected"],
                "attempts": r.get("attempts"), "best_rerank": r["best_rerank"],
                "has_sources": bool(r["sources"]), "has_quotes": bool(r["quotes"]),
                "source_ok": (bool(articles & set(item["sources"])) if item["sources"] else None),
                "correct": verdict[mode]["correct"], "support": verdict[mode]["support"],
                "note": verdict[mode]["note"],
                "prompt_tokens": r["prompt_tokens"], "completion_tokens": r["completion_tokens"],
                "cost": r["cost"], "seconds": r["seconds"], "llm_calls": r["llm_calls"],
            }
        rows.append(row)
        cells = []
        for mode in agent.MODES:
            c = row[mode]
            flag = "не знаю" if c["unknown"] else f"ист {len(c['sources'])} цит {len(c['quotes'])}"
            if c["rejected"]:
                flag += f" ✗{len(c['rejected'])}"
            cells.append(f"{mode:5} {c['correct']} {str(c['support'])[:5]:5} {flag:18}")
        log(f"  {i + 1:>2}. реранк {str(row['cited']['best_rerank']):>4}  " + "  ".join(cells)
            + f"  {item['q'][:40]}")
    return rows


def summarize(rows):
    out = {}
    for mode in agent.MODES:
        cells = [row[mode] for row in rows]
        answered = [c for c in cells if not c["unknown"]]
        pairs = list(zip(cells, QUESTIONS))
        out[mode] = {
            "correct_sum": sum(c["correct"] or 0 for c in cells), "correct_max": 2 * len(cells),
            "answered": len(answered),
            "with_sources": sum(c["has_sources"] for c in answered),
            "with_quotes": sum(c["has_quotes"] for c in answered),
            "quotes": sum(len(c["quotes"]) for c in cells),
            "rejected": sum(len(c["rejected"]) for c in cells),
            "retried": sum((c["attempts"] or 1) > 1 for c in cells),
            "supported": sum(c["support"] == "supported" for c in answered),
            "partial": sum(c["support"] == "partial" for c in answered),
            "unsupported": sum(c["support"] == "unsupported" for c in answered),
            "unknown_hit": sum(c["unknown"] for c, q in pairs if q["type"] == "unknown"),
            "unknown_expected": sum(q["type"] == "unknown" for q in QUESTIONS),
            "unknown_false": sum(c["unknown"] for c, q in pairs if q["type"] == "answer"),
            "source_ok": sum(bool(c["source_ok"]) for c, q in pairs if q["type"] == "answer"),
            "prompt_tokens": sum(c["prompt_tokens"] for c in cells),
            "completion_tokens": sum(c["completion_tokens"] for c in cells),
            "cost": round(sum(c["cost"] for c in cells), 5),
            "llm_calls": sum(c["llm_calls"] for c in cells),
        }
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    embeddings.check()
    bot = agent.Agent()
    p = retrieval.DEFAULTS
    print(f"индекс: {len(bot.index.rows)} чанков, отвечает {bot.model} (t=0), судья {JUDGE}")
    print(f"отбор: топ-{p['k_before']} → порог {p['threshold']} → реранкер ≥ {p['rerank_min']} → "
          f"топ-{p['k_after']};  гейт «не знаю»: лучший реранк < {citations.GATE_MIN}\n")

    runs = []
    for attempt in range(args.repeats):
        print(f"=== проход {attempt + 1}   (оценка 0–2, подтверждение смыслом, источники / цитаты, "
              f"✗ отбраковано цитат)")
        started = time.monotonic()
        rows = run_once(bot, attempt, print)
        runs.append({"rows": rows, "summary": summarize(rows),
                     "seconds": round(time.monotonic() - started, 1)})
        print()

    for attempt, run in enumerate(runs, 1):
        for mode in agent.MODES:
            s = run["summary"][mode]
            print(f"{agent.MODE_LABELS[mode]:>30} #{attempt}  верно {s['correct_sum']}/{s['correct_max']}  "
                  f"ответов {s['answered']}: с источниками {s['with_sources']}, с цитатами {s['with_quotes']}, "
                  f"подтверждено {s['supported']}/{s['partial']}/{s['unsupported']}  "
                  f"«не знаю» {s['unknown_hit']}/{s['unknown_expected']} (ложных {s['unknown_false']})  "
                  f"цитат {s['quotes']} ✗{s['rejected']} повторов {s['retried']}  "
                  f"токены {s['prompt_tokens'] + s['completion_tokens']}  ${s['cost']:.5f}")
        print()
    judge_tokens = sum(r["judge_tokens"] for run in runs for r in run["rows"])
    judge_cost = sum(r["judge_cost"] for run in runs for r in run["rows"])
    failed = sum(r[m]["correct"] is None for run in runs for r in run["rows"] for m in agent.MODES)
    print(f"судья: {judge_tokens} токенов, ${judge_cost:.4f}, не разобрано оценок: {failed}")

    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps({
        "model": bot.model, "judge": JUDGE, "params": p, "gate_min": citations.GATE_MIN,
        "questions": QUESTIONS, "runs": runs,
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
