"""Сравнение четырёх режимов поиска на 14 контрольных вопросах по Конституции.

1. Калибровка порога — только эмбеддинги: косинус правильных статей и вопросов вне базы,
   развёртка порога 0.40…0.60 — сколько вопросов вне базы отсекается целиком и сколько
   правильных статей выживает в топ-K_BEFORE.
2. Режимы base / filter / rerank / rewrite+rerank — на каждом вопросе: нашлись ли нужные
   статьи в итоговом контексте (recall), какая доля контекста из них (precision), сколько
   чанков ушло в промпт, факты-регулярки, оценка судьи, расход. Для rewrite — не подсказал
   ли он ответ прямо в запросе (утечка).
3. Судья deepseek-reasoner видит вопрос, ожидание и 4 обезличенных ответа в случайном порядке.

Вопросы 1–10 — из дня 22 без изменений, 11–14 добавлены под фильтр и rewrite.
Весь прогон повторяется REPEATS раз при temperature=0.

    python3 rerank_demo.py              # ~25 минут, нужен index.db и запущенная Ollama
    python3 rerank_demo.py --repeats 1
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
import retrieval

JUDGE = "deepseek-reasoner"
RESULTS = pathlib.Path(__file__).with_name("data") / "rerank_result.json"
REFUSAL = (r"не нашлось|ответа нет|не содерж|не регулир|не устанавлива|не относ|не предусм"
           r"|не определя|не указ|не закрепл")

QUESTIONS = [
    {"q": "На какой срок избирается Президент России и сколько сроков может занимать должность один человек?",
     "expect": "Шесть лет; одно лицо не может занимать должность более двух сроков (слово «подряд» "
               "убрано поправкой 2020 года).",
     "facts": [r"шест\w* лет|6 лет", r"двух сроков|два срока"], "sources": ["81"], "kind": "день 22"},
    {"q": "Какие требования к проживанию и иностранному гражданству предъявляются к кандидату в президенты?",
     "expect": "Постоянно проживать в России не менее 25 лет; не иметь и не иметь ранее гражданства "
               "другого государства, вида на жительство или иного документа на постоянное проживание за рубежом.",
     "facts": [r"25 лет|двадцати пяти", r"вид\w* на жительство"], "sources": ["81"], "kind": "день 22"},
    {"q": "Может ли минимальный размер оплаты труда быть ниже прожиточного минимума?",
     "expect": "Нет: МРОТ гарантируется не менее величины прожиточного минимума трудоспособного "
               "населения в целом по РФ (ч. 5 ст. 75).",
     "facts": [r"прожиточн\w* минимум", r"не менее|не ниже|не может"], "sources": ["75"], "kind": "день 22"},
    {"q": "Как Конституция определяет брак?",
     "expect": "Как союз мужчины и женщины; защита института брака — совместное ведение РФ и "
               "субъектов (п. «ж.1» ч. 1 ст. 72).",
     "facts": [r"мужчин\w* и женщин"], "sources": ["72"], "kind": "день 22, провал поиска"},
    {"q": "Сколько представителей Российской Федерации в Совете Федерации может назначить Президент и сколько из них пожизненно?",
     "expect": "Не более 30 представителей, из них не более семи — пожизненно (п. «в» ч. 2 ст. 95).",
     "facts": [r"\b30\b|тридцат", r"\b7\b|сем[иь]\b", r"пожизненн"], "sources": ["95"], "kind": "день 22"},
    {"q": "С какого возраста можно стать Президентом и с какого — сенатором?",
     "expect": "Президент — не моложе 35 лет (ст. 81), сенатор — с 30 лет (ч. 4 ст. 95).",
     "facts": [r"\b35\b|тридцати пяти", r"\b30\b|тридцати"], "sources": ["81", "95"], "kind": "день 22"},
    {"q": "Допускаются ли действия, направленные на отчуждение части территории России?",
     "expect": "Нет, не допускаются, как и призывы к ним; исключение — делимитация, демаркация и "
               "редемаркация границы с соседями (ч. 2.1 ст. 67).",
     "facts": [r"не допуска|запрещ", r"делимитац|демаркац"], "sources": ["67"], "kind": "день 22"},
    {"q": "Какой статус у русского языка в Конституции и как это обосновано?",
     "expect": "Государственный язык на всей территории РФ как язык государствообразующего народа, "
               "входящего в многонациональный союз равноправных народов (ч. 1 ст. 68).",
     "facts": [r"государственн\w* язык", r"государствообразующ"], "sources": ["68"], "kind": "день 22"},
    {"q": "Как назначается Председатель Правительства?",
     "expect": "Президентом после утверждения кандидатуры Государственной Думой; после трёх "
               "отклонений Президент назначает сам и вправе распустить Думу (ст. 111).",
     "facts": [r"утвержд", r"государственн\w* дум"], "sources": ["111"], "kind": "день 22"},
    {"q": "Какой пенсионный возраст установлен Конституцией РФ?",
     "expect": "Никакой: пенсионный возраст Конституция не устанавливает. Есть только принципы "
               "пенсионной системы и индексация пенсий не реже раза в год (ч. 6 ст. 75).",
     "facts": [REFUSAL, r"индексац"], "sources": ["75"], "kind": "день 22, провал поиска"},
    {"q": "Какой рецепт классического борща?",
     "expect": "Вопрос не про Конституцию: ответа в ней нет, агент должен отказаться, ничего не выдумывая.",
     "facts": [REFUSAL], "sources": [], "kind": "вне базы"},
    {"q": "Какой штраф за превышение скорости на 40 км/ч?",
     "expect": "Конституция штрафов не устанавливает (это КоАП): агент должен сказать, что ответа "
               "в Конституции нет, и не называть сумму.",
     "facts": [REFUSAL], "sources": [], "kind": "вне базы, юридический"},
    {"q": "Могут ли меня держать под арестом без суда больше двух суток?",
     "expect": "Нет: арест и заключение под стражу — только по судебному решению; до решения суда "
               "задержание не более 48 часов (ч. 2 ст. 22).",
     "facts": [r"48 час|двое суток|двух суток|сорока восьми", r"судебн|суд"], "sources": ["22"],
     "kind": "бытовая формулировка"},
    {"q": "Обязан ли я давать показания против своей жены?",
     "expect": "Нет: никто не обязан свидетельствовать против себя, своего супруга и близких "
               "родственников (ч. 1 ст. 51).",
     "facts": [r"не обязан", r"супруг"], "sources": ["51"], "kind": "бытовая формулировка"},
]

JUDGE_PROMPT = """Ты проверяешь ответы справочника по Конституции РФ (действующая редакция с поправками 2020 года).
Дан вопрос, эталонное ожидание и несколько ответов разных систем в случайном порядке.
Оцени каждый ответ по ожиданию:
  2 — содержит всё главное из ожидания и ничего ему не противоречит;
  1 — частично: не хватает важной части или есть неточность;
  0 — неверно, противоречит ожиданию, выдумывает норму или отвечает там, где надо отказаться.
Лишние верные подробности не штрафуются. Номера статей не оцениваются.
Верни только JSON: {"A": {"score": 0-2, "note": "до 12 слов"}, "B": {...}, ...}

Вопрос: <<Q>>
Ожидание: <<EXPECT>>

<<ANSWERS>>"""


def norm(text):
    return text.lower().replace("ё", "е")


def facts_found(answer, facts):
    return [bool(re.search(pattern, norm(answer))) for pattern in facts]


def leaked(query, item):
    """Rewrite подсказал ответ: в запросе есть факт из ожидания или номер нужной статьи."""
    if not item["sources"]:
        return False
    text = norm(query)
    fact = any(re.search(p, text) for p in item["facts"] if p != REFUSAL)
    article = any(re.search(rf"(ст\.?|стать\w*)\s*{re.escape(s)}\b", text) for s in item["sources"])
    return fact or article


# ---------- 1. калибровка порога

def calibrate(bot, log):
    rows = []
    for item in QUESTIONS:
        hits, _ = bot.index.search(item["q"], 40)
        gold = [(rank, h["score"]) for rank, h in enumerate(hits, 1) if h["article"] in item["sources"]]
        rows.append({"q": item["q"], "top1": hits[0]["score"], "top20": hits[19]["score"],
                     "gold_rank": gold[0][0] if gold else None,
                     "gold_score": gold[0][1] if gold else None, "scores": [h["score"] for h in hits]})
    inside = [r for r, item in zip(rows, QUESTIONS) if item["sources"]]
    outside = [r for r, item in zip(rows, QUESTIONS) if not item["sources"]]
    log(f"  правильная статья: косинус {min(r['gold_score'] for r in inside):.3f}–"
        f"{max(r['gold_score'] for r in inside):.3f}, ранг "
        f"{', '.join(str(r['gold_rank']) for r in inside)}")
    log(f"  вопросы вне базы: лучший кандидат {', '.join(f'{r['top1']:.3f}' for r in outside)}")
    sweep = []
    log(f"\n  {'порог':>6} {'вне базы отсечены':>18} {'нужная статья выжила в топ-20':>30} "
        f"{'кандидатов в среднем':>21}")
    for step in range(9):
        t = round(0.40 + 0.025 * step, 3)
        cut = sum(r["top1"] < t for r in outside)
        alive = sum(r["gold_rank"] is not None and r["gold_rank"] <= 20 and r["gold_score"] >= t
                    for r in inside)
        avg = sum(sum(s >= t for s in r["scores"][:20]) for r in inside) / len(inside)
        sweep.append({"threshold": t, "outside_cut": cut, "gold_alive": alive, "avg_kept": round(avg, 1)})
        mark = "  ← выбран" if abs(t - retrieval.DEFAULTS["threshold"]) < 1e-9 else ""
        log(f"  {t:>6.3f} {cut:>14}/{len(outside)} {alive:>26}/{len(inside)} {avg:>21.1f}{mark}")
    return {"rows": rows, "sweep": sweep}


# ---------- 2–3. режимы и судья

def judge(item, answers, seed):
    order = list(answers)
    random.Random(seed).shuffle(order)
    letters = "ABCD"
    block = "\n\n".join(f"[{letters[i]}]\n{answers[mode]}" for i, mode in enumerate(order))
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
        if all(isinstance((verdict.get(x) or {}).get("score"), int) for x in letters[:len(order)]):
            break
    result = {mode: {"score": (verdict.get(letters[i]) or {}).get("score"),
                     "note": (verdict.get(letters[i]) or {}).get("note", "")}
              for i, mode in enumerate(order)}
    return result, tokens, cost


def run_once(bot, attempt, log):
    rows = []
    for i, item in enumerate(QUESTIONS):
        answers = bot.compare(item["q"])
        verdict, tokens, cost = judge(item, {m: a["answer"] for m, a in answers.items()},
                                      seed=attempt * 100 + i)
        row = {"n": i + 1, "judge_tokens": tokens, "judge_cost": cost}
        for mode, a in answers.items():
            final = sorted({h["article"] for h in a["hits"]})
            if item["sources"]:
                recall = all(s in final for s in item["sources"])
                precision = (sum(h["article"] in item["sources"] for h in a["hits"]) / len(a["hits"])
                             if a["hits"] else 0.0)
            else:
                recall = not a["hits"]          # вне базы «нашёл» = ничего не принёс
                precision = None
            row[mode] = {
                "answer": a["answer"], "facts": facts_found(a["answer"], item["facts"]),
                "query": a["trace"]["query"],
                "leak": leaked(a["trace"]["query"], item) if mode == "rewrite+rerank" else None,
                "final": a["trace"]["final"], "candidates": a["trace"]["candidates"],
                "rerank_failed": a["trace"].get("rerank_failed", False),
                "recall": recall, "precision": precision, "n_chunks": len(a["hits"]),
                "context_chars": a["context_chars"],
                "score": verdict[mode]["score"], "note": verdict[mode]["note"],
                "stages": a["stages"], "prompt_tokens": a["prompt_tokens"],
                "completion_tokens": a["completion_tokens"], "cost": a["cost"],
                "seconds": a["seconds"],
            }
        rows.append(row)
        cells = "  ".join(f"{m[:7]:7} {row[m]['score']} {'✓' if row[m]['recall'] else '✗'}"
                          f"{row[m]['n_chunks']}" for m in agent.MODES)
        log(f"  {i + 1:>2}. {cells}   {item['q'][:46]}")
    return rows


def summarize(rows):
    out = {}
    for mode in agent.MODES:
        cells = [row[mode] for row in rows]
        inside = [c for c, item in zip(cells, QUESTIONS) if item["sources"]]
        outside = [c for c, item in zip(cells, QUESTIONS) if not item["sources"]]
        scores = [c["score"] for c in cells if isinstance(c["score"], int)]
        precisions = [c["precision"] for c in inside if c["n_chunks"]]
        out[mode] = {
            "judge_sum": sum(scores), "judge_max": 2 * len(cells),
            "judge_failed": len(cells) - len(scores),
            "recall": sum(c["recall"] for c in inside), "recall_total": len(inside),
            "outside_clean": sum(c["recall"] for c in outside), "outside_total": len(outside),
            "precision": round(sum(precisions) / len(precisions), 2) if precisions else 0,
            "avg_chunks": round(sum(c["n_chunks"] for c in cells) / len(cells), 1),
            "context_chars": round(sum(c["context_chars"] for c in cells) / len(cells)),
            "all_facts": sum(all(c["facts"]) for c in cells),
            "leaks": sum(bool(c["leak"]) for c in cells),
            "rerank_failed": sum(c["rerank_failed"] for c in cells),
            "prompt_tokens": sum(c["prompt_tokens"] for c in cells),
            "completion_tokens": sum(c["completion_tokens"] for c in cells),
            "cost": round(sum(c["cost"] for c in cells), 5),
            "seconds": round(sum(c["seconds"] for c in cells) / len(cells), 2),
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
    print(f"параметры: порог {p['threshold']}, топ-{p['k_before']} до фильтра, "
          f"топ-{p['k_after']} после, реранкер ≥ {p['rerank_min']}/10\n")

    print("=== 1. Калибровка порога (только эмбеддинги)")
    calibration = calibrate(bot, print)
    print()

    runs = []
    for attempt in range(args.repeats):
        print(f"=== 2. Режимы, проход {attempt + 1}   (оценка судьи, ✓/✗ нужные статьи в контексте, "
              f"число чанков)")
        started = time.monotonic()
        rows = run_once(bot, attempt, print)
        runs.append({"rows": rows, "summary": summarize(rows),
                     "seconds": round(time.monotonic() - started, 1)})
        print()

    print(f"{'':28} {'судья':>7} {'статьи':>7} {'вне базы':>9} {'точность':>9} {'чанков':>7} "
          f"{'все факты':>10} {'утечки':>7} {'токены':>8} {'$':>8} {'с/вопрос':>9}")
    for attempt, run in enumerate(runs, 1):
        for mode in agent.MODES:
            s = run["summary"][mode]
            print(f"{agent.MODE_LABELS[mode]:>25} #{attempt} {s['judge_sum']:>4}/{s['judge_max']} "
                  f"{s['recall']:>4}/{s['recall_total']} {s['outside_clean']:>6}/{s['outside_total']} "
                  f"{s['precision']:>9} {s['avg_chunks']:>7} {s['all_facts']:>7}/14 {s['leaks']:>7} "
                  f"{s['prompt_tokens'] + s['completion_tokens']:>8} {s['cost']:>8.5f} {s['seconds']:>9}")
        print()
    judge_tokens = sum(r["judge_tokens"] for run in runs for r in run["rows"])
    judge_cost = sum(r["judge_cost"] for run in runs for r in run["rows"])
    failed = sum(run["summary"][m]["judge_failed"] for run in runs for m in agent.MODES)
    rerank_failed = sum(run["summary"][m]["rerank_failed"] for run in runs for m in agent.MODES)
    print(f"судья: {judge_tokens} токенов, ${judge_cost:.4f}, не разобрано оценок: {failed}; "
          f"реранкер не разобрался: {rerank_failed} раз")

    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps({
        "model": bot.model, "judge": JUDGE, "params": p, "questions": QUESTIONS,
        "calibration": calibration, "runs": runs,
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
