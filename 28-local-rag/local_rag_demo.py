"""Локальный RAG против облачного: качество, скорость, стабильность.

Три стека на одном пайплайне дня 24 (топ-20 → порог → LLM-реранкер → топ-5 → JSON с цитатами
→ проверка цитат кодом) и одном индексе (bge-m3 в Ollama + SQLite):

    cloud     реранкер и ответ — deepseek-chat (API)
    local-7b  реранкер и ответ — qwen2.5:7b (Ollama, этот Mac)
    local-3b  реранкер и ответ — qwen2.5:3b (Ollama, этот Mac)

Локальные стеки идут под блокировкой сети: любой connect() не к localhost падает и
считается. 0 попыток = RAG действительно полностью локальный.

Качество — код (цитаты дословны? нужная статья в источниках и в топ-5?) + судья
openai/gpt-oss-120b на Groq: провайдер не участвует в сравнении, ответы обезличены и перемешаны.
Скорость — секунды по этапам из ответов Ollama/API. Стабильность — REPEATS проходов при
temperature=0: совпадают ли ответы дословно, есть ли сбои JSON, ошибки, разброс времени.

    python3 local_rag_demo.py                 # ~1 час на M1 8 ГБ
    python3 local_rag_demo.py --repeats 1 --stacks local-3b
"""

import argparse
import contextlib
import json
import pathlib
import random
import re
import socket
import statistics
import sys
import time

import agent
import citations
import embeddings
import llm
import local_llm
import providers
import retrieval

JUDGE = "openai/gpt-oss-120b"
RESULTS = pathlib.Path(__file__).with_name("data") / "local_rag_result.json"

# Те же 10 вопросов, что в дне 24. expect: answer — ответ в тексте есть; unknown — ждём «не знаю»;
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
и ответы нескольких систем в случайном порядке. У каждого ответа — цитаты, на которые он опирался.

Для каждого ответа поставь две оценки:
  correct: 2 — соответствует ожиданию; 1 — частично; 0 — неверно, выдумано или ответ там, где надо было отказаться;
  support: "supported" — каждый факт ответа есть в его цитатах; "partial" — часть фактов не подтверждена;
           "unsupported" — главный факт ответа в цитатах отсутствует; "n/a" — ответ «не знаю» без утверждений.
Верни только JSON: {<<KEYS>>}, где каждое значение — {"correct": 0-2, "support": "...", "note": "до 12 слов"}.

Вопрос: <<Q>>
Ожидание: <<EXPECT>>

<<ANSWERS>>"""


# ── блокировка сети ────────────────────────────────────────────────────────────

LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost")


@contextlib.contextmanager
def offline(counter):
    """Пока активно, любое соединение не к localhost падает, а попытка записывается."""
    original = socket.socket.connect

    def guarded(sock, address):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in LOCAL_HOSTS:
            counter.append(host)
            raise OSError(f"сеть заблокирована: попытка соединения с {host}")
        return original(sock, address)

    socket.socket.connect = guarded
    try:
        yield
    finally:
        socket.socket.connect = original


# ── прогон ─────────────────────────────────────────────────────────────────────

def ask_safely(bot, question):
    """Сбой модели — тоже результат: записываем ошибку, а не роняем весь прогон."""
    started = time.monotonic()
    try:
        return bot.ask(question)
    except Exception as error:          # noqa: BLE001 — стабильность меряем, а не прячем
        return {"error": str(error)[:300], "answer": f"ОШИБКА: {error}"[:300], "unknown": False,
                "quotes": [], "rejected": [], "sources": [], "final": [], "clarify": [],
                "stage": "error", "best_rerank": None, "rerank_missing": 0, "json_failed": 0,
                "rerank_failed": False, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0,
                "timing": {"embed": 0, "rerank": 0, "answer": 0, "load": 0},
                "seconds": round(time.monotonic() - started, 2), "llm_calls": 0, "network": False}


def evidence(result):
    if result["quotes"]:
        return "\n".join(f"- [{q['chunk_id']}] «{q['text']}»" for q in result["quotes"])
    return "(цитат нет)"


def judge(item, results, seed):
    order = list(results)
    random.Random(seed).shuffle(order)
    letters = "ABCDE"[:len(order)]
    block = "\n\n".join(f"[{letter}] Ответ:\n{results[stack]['answer']}\n\n[{letter}] Цитаты:\n"
                        f"{evidence(results[stack])}" for letter, stack in zip(letters, order))
    prompt = (JUDGE_PROMPT.replace("<<Q>>", item["q"]).replace("<<EXPECT>>", item["expect"])
              .replace("<<KEYS>>", ", ".join(f'"{x}": {{...}}' for x in letters))
              .replace("<<ANSWERS>>", block))
    tokens, cost, verdict = 0, 0.0, {}
    for _ in range(3):
        try:
            reply = providers.call(JUDGE, [{"role": "user", "content": prompt}],
                                   temperature=0, max_tokens=4000)
        except RuntimeError as error:
            print(f"     судья: {error}")
            time.sleep(5)
            continue
        tokens += reply["prompt_tokens"] + reply["completion_tokens"]
        cost += reply["cost"] or 0
        match = re.search(r"\{.*\}", reply["text"], flags=re.S)
        try:
            verdict = json.loads(match.group(0)) if match else {}
        except json.JSONDecodeError:
            verdict = {}
        if all(isinstance((verdict.get(x) or {}).get("correct"), int) for x in letters):
            break
    out = {}
    for letter, stack in zip(letters, order):
        v = verdict.get(letter) or {}
        out[stack] = {"correct": v.get("correct"), "support": v.get("support"), "note": v.get("note", "")}
    return out, tokens, cost


def articles(chunk_ids):
    return {c.split("#")[0].replace("ст.", "") for c in chunk_ids}


def cell(item, r):
    return {
        "answer": r["answer"], "error": r.get("error"), "unknown": r["unknown"], "stage": r["stage"],
        "sources": [s["chunk_id"] for s in r["sources"]], "final": r["final"],
        "quotes": r["quotes"], "rejected": r["rejected"], "attempts": r.get("attempts"),
        "best_rerank": r["best_rerank"], "rerank_missing": r["rerank_missing"],
        "rerank_failed": r["rerank_failed"], "json_failed": r["json_failed"],
        "retrieval_ok": bool(articles(r["final"]) & set(item["sources"])) if item["sources"] else None,
        "source_ok": bool(articles(s["chunk_id"] for s in r["sources"]) & set(item["sources"]))
                     if item["sources"] else None,
        "prompt_tokens": r["prompt_tokens"], "completion_tokens": r["completion_tokens"],
        "cost": r["cost"], "timing": r["timing"], "seconds": r["seconds"],
        "llm_calls": r["llm_calls"], "network": r["network"],
    }


def run_stack(stack, log):
    bot = agent.Agent(stack)
    blocked = []
    guard = offline(blocked) if llm.is_local_stack(stack) else contextlib.nullcontext()
    cells = []
    started = time.monotonic()
    with guard:
        for i, item in enumerate(QUESTIONS):
            r = ask_safely(bot, item["q"])
            c = cell(item, r)
            cells.append(c)
            t = c["timing"]
            flag = ("ОШИБКА" if c["error"] else "не знаю" if c["unknown"]
                    else f"цит {len(c['quotes'])}✓ {len(c['rejected'])}✗")
            log(f"   {stack:8} {i + 1:>2}. {c['seconds']:6.1f} с (эмб {t['embed']:.1f} · реранк "
                f"{t['rerank']:5.1f} · ответ {t['answer']:5.1f} · загрузка {t['load']:4.1f})  "
                f"топ5 {'✓' if c['retrieval_ok'] else '·' if c['retrieval_ok'] is None else '✗'}  "
                f"{flag:12} {c['answer'][:60]!r}")
    return cells, blocked, round(time.monotonic() - started, 1)


def summarize(cells):
    answered = [c for c in cells if not c["unknown"] and not c["error"]]
    pairs = list(zip(cells, QUESTIONS))
    times = [c["seconds"] for c in cells]
    return {
        "correct_sum": sum(c.get("correct") or 0 for c in cells), "correct_max": 2 * len(cells),
        "answered": len(answered),
        "with_quotes": sum(bool(c["quotes"]) for c in answered),
        "quotes": sum(len(c["quotes"]) for c in cells),
        "rejected": sum(len(c["rejected"]) for c in cells),
        "retried": sum((c["attempts"] or 1) > 1 for c in cells),
        "verify_unknown": sum(c["stage"] == "verify" for c in cells),
        "supported": sum(c.get("support") == "supported" for c in answered),
        "retrieval_ok": sum(bool(c["retrieval_ok"]) for c, q in pairs if q["type"] == "answer"),
        "source_ok": sum(bool(c["source_ok"]) for c, q in pairs if q["type"] == "answer"),
        "n_answer": sum(q["type"] == "answer" for q in QUESTIONS),
        "unknown_hit": sum(c["unknown"] for c, q in pairs if q["type"] == "unknown"),
        "unknown_expected": sum(q["type"] == "unknown" for q in QUESTIONS),
        "unknown_false": sum(c["unknown"] for c, q in pairs if q["type"] == "answer"),
        "errors": sum(bool(c["error"]) for c in cells),
        "json_failed": sum(c["json_failed"] for c in cells),
        "rerank_missing": sum(c["rerank_missing"] for c in cells),
        "seconds_total": round(sum(times), 1),
        "seconds_median": round(statistics.median(times), 1),
        "seconds_max": round(max(times), 1),
        "rerank_s": round(sum(c["timing"]["rerank"] for c in cells), 1),
        "answer_s": round(sum(c["timing"]["answer"] for c in cells), 1),
        "embed_s": round(sum(c["timing"]["embed"] for c in cells), 1),
        "load_s": round(sum(c["timing"]["load"] for c in cells), 1),
        "prompt_tokens": sum(c["prompt_tokens"] for c in cells),
        "completion_tokens": sum(c["completion_tokens"] for c in cells),
        "cost": round(sum(c["cost"] for c in cells), 5),
    }


def stability(runs, stack):
    """Сравнение проходов между собой: дословные ответы, тот же топ-5, разброс времени."""
    if len(runs) < 2:
        return {}
    per_q = list(zip(*[run["stacks"][stack]["cells"] for run in runs]))
    same_answer = sum(len({c["answer"] for c in cells}) == 1 for cells in per_q)
    same_final = sum(len({tuple(c["final"]) for c in cells}) == 1 for cells in per_q)
    same_score = sum(len({c.get("correct") for c in cells}) == 1 for cells in per_q)
    totals = [run["stacks"][stack]["summary"]["seconds_total"] for run in runs]
    spreads = [max(c["seconds"] for c in cells) / max(min(c["seconds"] for c in cells), 0.01)
               for cells in per_q]
    return {"same_answer": same_answer, "same_final": same_final, "same_score": same_score,
            "n": len(per_q), "totals": totals, "max_spread": round(max(spreads), 2)}


def save(payload):
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--stacks", nargs="+", default=list(llm.STACKS), choices=list(llm.STACKS))
    args = parser.parse_args()

    embeddings.check()
    for stack in args.stacks:
        llm.check(stack)
    p = retrieval.DEFAULTS
    print(f"Ollama {local_llm.version()}; стеки: " +
          ", ".join(f"{s} ({llm.STACKS[s]['answer']})" for s in args.stacks) + f"; судья {JUDGE}")
    print(f"отбор: топ-{p['k_before']} → порог {p['threshold']} → реранкер ≥ {p['rerank_min']} → "
          f"топ-{p['k_after']}; «не знаю», если лучший реранк < {citations.GATE_MIN}; t=0\n")

    payload = {"stacks": {s: llm.STACKS[s] for s in args.stacks}, "judge": JUDGE, "params": p,
               "gate_min": citations.GATE_MIN, "num_ctx": local_llm.NUM_CTX,
               "ollama": local_llm.version(), "questions": QUESTIONS, "runs": []}
    for attempt in range(args.repeats):
        print(f"=== проход {attempt + 1}/{args.repeats}")
        run = {"stacks": {}}
        for stack in args.stacks:
            cells, blocked, seconds = run_stack(stack, print)
            run["stacks"][stack] = {"cells": cells, "blocked": blocked, "seconds": seconds}
            if llm.is_local_stack(stack):
                print(f"   {stack}: попыток выйти в сеть — {len(blocked)}")
        judge_tokens, judge_cost = 0, 0.0
        for i, item in enumerate(QUESTIONS):
            results = {s: run["stacks"][s]["cells"][i] for s in args.stacks}
            verdict, tokens, cost = judge(item, results, seed=attempt * 100 + i)
            judge_tokens += tokens
            judge_cost += cost
            for stack in args.stacks:
                results[stack].update(verdict[stack])
            print(f"   судья {i + 1:>2}. " + "  ".join(
                f"{s} {verdict[s]['correct']} {str(verdict[s]['support'])[:5]:5}" for s in args.stacks)
                + f"  {item['q'][:40]}")
        for stack in args.stacks:
            run["stacks"][stack]["summary"] = summarize(run["stacks"][stack]["cells"])
        run["judge_tokens"], run["judge_cost"] = judge_tokens, round(judge_cost, 5)
        payload["runs"].append(run)
        save(payload)
        print()

    payload["stability"] = {s: stability(payload["runs"], s) for s in args.stacks}
    save(payload)
    for attempt, run in enumerate(payload["runs"], 1):
        for stack in args.stacks:
            s = run["stacks"][stack]["summary"]
            print(f"{stack:8} #{attempt}  верно {s['correct_sum']}/{s['correct_max']}  "
                  f"ответов {s['answered']} (цитаты {s['with_quotes']}, подтверждено {s['supported']})  "
                  f"нужная статья в топ-5 {s['retrieval_ok']}/{s['n_answer']}, в источниках {s['source_ok']}  "
                  f"«не знаю» {s['unknown_hit']}/{s['unknown_expected']} (ложных {s['unknown_false']})  "
                  f"цитат {s['quotes']}✓ {s['rejected']}✗  ошибок {s['errors']}  "
                  f"время {s['seconds_total']} с (медиана {s['seconds_median']})  ${s['cost']:.4f}")
    for stack, st in payload["stability"].items():
        if st:
            print(f"{stack:8} стабильность: ответ дословно {st['same_answer']}/{st['n']}, топ-5 "
                  f"{st['same_final']}/{st['n']}, оценка {st['same_score']}/{st['n']}, "
                  f"время проходов {st['totals']}")
    print(f"→ {RESULTS.relative_to(pathlib.Path(__file__).parent)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        print(f"ошибка: {error}", file=sys.stderr)
        sys.exit(1)
