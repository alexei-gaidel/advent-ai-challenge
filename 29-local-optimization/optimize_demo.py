"""Оптимизация локальной LLM под RAG по Конституции: до, после и вклад каждого шага.

Пять пресетов (presets.py) на одном пайплайне дня 28 и одном индексе:

    base        день 28: qwen2.5:7b Q4_K_M, num_ctx 8192, реранкеру 20 фрагментов, промпт дня 24
    params      + num_ctx 4096, реранкеру топ-10, фрагменты в реранке ≤ 600 символов, ответ ≤ 400 токенов
    prompt      + промпт ответа «сначала цитата, потом вывод»
    quant       + Q3_K_M, всё зашито в Modelfile (qwen-constitution) — итог
    quant-only  контроль: base, но на Q3_K_M — вклад одного квантования

Вопросы: dev — 10 вопросов дня 28 (на них настраивалось, результат оптимистичен) и holdout —
6 новых да/нет-вопросов с отрицанием в тексте, которые при настройке не смотрелись.

Качество — код (цитаты, нужная статья) + судья openai/gpt-oss-120b на Groq (ответы всех пресетов
обезличены и перемешаны). Скорость и ресурсы — из ответов Ollama, /api/ps и ps.
Стабильность — REPEATS проходов при temperature=0. Перед каждым пресетом модели выгружаются,
пайплайн идёт под блокировкой сети (день 28).

    python3 optimize_demo.py                    # ~1,5 часа на M1 8 ГБ
    python3 optimize_demo.py --repeats 1 --presets params quant --limit 3
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
import presets
import providers
import retrieval

JUDGE = "openai/gpt-oss-120b"
RESULTS = pathlib.Path(__file__).with_name("data") / "optimize_result.json"

# set: dev — вопросы дня 28; holdout — новые, при настройке промптов не использовались.
QUESTIONS = [
    {"q": "На какой срок избирается Президент России и сколько сроков может занимать должность один человек?",
     "expect": "Шесть лет; одно лицо не может занимать должность более двух сроков.",
     "sources": ["81"], "type": "answer", "set": "dev"},
    {"q": "Может ли минимальный размер оплаты труда быть ниже прожиточного минимума?",
     "expect": "Нет: МРОТ гарантируется не менее величины прожиточного минимума трудоспособного "
               "населения в целом по РФ.", "sources": ["75"], "type": "answer", "set": "dev"},
    {"q": "Как Конституция определяет брак?",
     "expect": "Как союз мужчины и женщины; защита института брака — совместное ведение РФ и субъектов.",
     "sources": ["72"], "type": "answer", "set": "dev"},
    {"q": "Сколько представителей Российской Федерации в Совете Федерации может назначить Президент и сколько из них пожизненно?",
     "expect": "Не более 30 представителей, из них не более семи — пожизненно.",
     "sources": ["95"], "type": "answer", "set": "dev"},
    {"q": "Могут ли меня держать под арестом без суда больше двух суток?",
     "expect": "Нет: арест — только по судебному решению; до решения суда задержание не более 48 часов.",
     "sources": ["22"], "type": "answer", "set": "dev"},
    {"q": "Обязан ли я давать показания против своей жены?",
     "expect": "Нет: никто не обязан свидетельствовать против себя, своего супруга и близких родственников.",
     "sources": ["51"], "type": "answer", "set": "dev"},
    {"q": "Какой пенсионный возраст установлен Конституцией РФ?",
     "expect": "Конкретного пенсионного возраста Конституция не устанавливает. Допустимо и «в Конституции "
               "этого нет», и «не знаю» с просьбой уточнить; называть цифры возраста — ошибка.",
     "sources": ["39", "75"], "type": "either", "set": "dev"},
    {"q": "Какой штраф за превышение скорости на 40 км/ч?",
     "expect": "В Конституции этого нет. Агент должен сказать «не знаю» и попросить уточнить, "
               "не называя суммы.", "sources": [], "type": "unknown", "set": "dev"},
    {"q": "Что там про сроки?",
     "expect": "Вопрос расплывчатый: непонятно, чьи сроки. Хороший ответ — «не знаю» и просьба уточнить "
               "(сроки Президента, Думы, задержания…). Перечень нескольких сроков с источниками — частично.",
     "sources": [], "type": "unknown", "set": "dev"},
    {"q": "Сколько длится отпуск по уходу за ребёнком?",
     "expect": "В Конституции длительности отпуска нет (есть только защита материнства и детства, ст. 38). "
               "Агент должен сказать «не знаю» и попросить уточнить; называть сроки отпуска — ошибка.",
     "sources": [], "type": "unknown", "set": "dev"},
    {"q": "Можно ли лишить человека российского гражданства?",
     "expect": "Нет: гражданин РФ не может быть лишён своего гражданства или права изменить его (ст. 6 ч. 3).",
     "sources": ["6"], "type": "answer", "set": "holdout"},
    {"q": "Может ли в России быть государственная религия?",
     "expect": "Нет: РФ — светское государство, никакая религия не может устанавливаться в качестве "
               "государственной или обязательной (ст. 14).", "sources": ["14"], "type": "answer", "set": "holdout"},
    {"q": "Разрешена ли в России цензура?",
     "expect": "Нет: свобода массовой информации гарантируется, цензура запрещается (ст. 29 ч. 5).",
     "sources": ["29"], "type": "answer", "set": "holdout"},
    {"q": "Могут ли заставить меня работать принудительно?",
     "expect": "Нет: принудительный труд запрещён (ст. 37 ч. 2).",
     "sources": ["37"], "type": "answer", "set": "holdout"},
    {"q": "Может ли новый закон, ужесточающий ответственность, применяться к уже совершённым поступкам?",
     "expect": "Нет: закон, устанавливающий или отягчающий ответственность, обратной силы не имеет (ст. 54).",
     "sources": ["54"], "type": "answer", "set": "holdout"},
    {"q": "Можно ли выслать гражданина России за границу или выдать другому государству?",
     "expect": "Нет: гражданин РФ не может быть выслан за пределы РФ или выдан другому государству (ст. 61).",
     "sources": ["61"], "type": "answer", "set": "holdout"},
]

JUDGE_PROMPT = """Ты проверяешь ответы справочника по Конституции РФ. Даны вопрос, эталонное ожидание
и ответы нескольких систем в случайном порядке. У каждого ответа — цитаты, на которые он опирался.

Для каждого ответа поставь оценки:
  correct: 2 — соответствует ожиданию; 1 — частично; 0 — неверно, выдумано или ответ там, где надо было отказаться;
  support: "supported" — каждый факт ответа есть в его цитатах; "partial" — часть фактов не подтверждена;
           "unsupported" — главный факт ответа в цитатах отсутствует; "n/a" — ответ «не знаю» без утверждений;
  contradicts: true — вывод ответа противоречит его собственной цитате (например, цитата «никто не обязан»,
           а ответ «обязаны»); иначе false.
Верни только JSON: {<<KEYS>>}, где каждое значение — {"correct": 0-2, "support": "...", "contradicts": true|false,
"note": "до 12 слов"}.

Вопрос: <<Q>>
Ожидание: <<EXPECT>>

<<ANSWERS>>"""

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


def ask_safely(bot, question):
    """Сбой модели — тоже результат: записываем ошибку, а не роняем весь прогон."""
    started = time.monotonic()
    try:
        return bot.ask(question)
    except Exception as error:          # noqa: BLE001 — стабильность меряем, а не прячем
        return {"error": str(error)[:300], "answer": f"ОШИБКА: {error}"[:300], "unknown": False,
                "quotes": [], "rejected": [], "sources": [], "final": [], "clarify": [],
                "stage": "error", "best_rerank": None, "rerank_missing": 0, "json_failed": 0,
                "rerank_failed": False, "rerank_prompt_tokens": 0, "prompt_tokens": 0,
                "completion_tokens": 0, "timing": {"embed": 0, "rerank": 0, "answer": 0, "load": 0},
                "reloads": 0, "tok_per_s": 0, "prompt_tok_per_s": 0,
                "memory": {"ps": [], "rss_mb": None},
                "seconds": round(time.monotonic() - started, 2), "llm_calls": 0}


def evidence(result):
    if result["quotes"]:
        return "\n".join(f"- [{q['chunk_id']}] «{q['text']}»" for q in result["quotes"])
    return "(цитат нет)"


def judge(item, results, seed):
    order = list(results)
    random.Random(seed).shuffle(order)
    letters = "ABCDEFG"[:len(order)]
    block = "\n\n".join(f"[{letter}] Ответ:\n{results[name]['answer']}\n\n[{letter}] Цитаты:\n"
                        f"{evidence(results[name])}" for letter, name in zip(letters, order))
    prompt = (JUDGE_PROMPT.replace("<<Q>>", item["q"]).replace("<<EXPECT>>", item["expect"])
              .replace("<<KEYS>>", ", ".join(f'"{x}": {{...}}' for x in letters))
              .replace("<<ANSWERS>>", block))
    tokens, cost, verdict = 0, 0.0, {}
    for _ in range(5):
        try:
            reply = providers.call(JUDGE, [{"role": "user", "content": prompt}],
                                   temperature=0, max_tokens=5000)
        except RuntimeError as error:
            print(f"     судья: {str(error)[:120]}")
            time.sleep(10)
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
    for letter, name in zip(letters, order):
        v = verdict.get(letter) or {}
        out[name] = {"correct": v.get("correct"), "support": v.get("support"),
                     "contradicts": v.get("contradicts"), "note": v.get("note", "")}
    return out, tokens, cost


def articles(chunk_ids):
    return {c.split("#")[0].replace("ст.", "") for c in chunk_ids}


def cell(item, r):
    ps = r["memory"]["ps"]
    main = next((m for m in ps if not m["name"].startswith("bge-m3")), {})
    return {
        "answer": r["answer"], "error": r.get("error"), "unknown": r["unknown"], "stage": r["stage"],
        "sources": [s["chunk_id"] for s in r["sources"]], "final": r["final"],
        "quotes": r["quotes"], "rejected": r["rejected"], "attempts": r.get("attempts"),
        "best_rerank": r["best_rerank"], "rerank_missing": r["rerank_missing"],
        "json_failed": r["json_failed"], "rerank_prompt_tokens": r["rerank_prompt_tokens"],
        "retrieval_ok": bool(articles(r["final"]) & set(item["sources"])) if item["sources"] else None,
        "prompt_tokens": r["prompt_tokens"], "completion_tokens": r["completion_tokens"],
        "timing": r["timing"], "seconds": r["seconds"], "reloads": r["reloads"],
        "tok_per_s": r["tok_per_s"], "prompt_tok_per_s": r["prompt_tok_per_s"],
        "model_gb": main.get("size_gb"), "vram_gb": main.get("vram_gb"),
        "gpu_pct": main.get("gpu_pct"), "context": main.get("context"),
        "bge_loaded": any(m["name"].startswith("bge-m3") for m in ps),
        "rss_mb": r["memory"]["rss_mb"], "llm_calls": r["llm_calls"],
    }


def run_preset(name, questions, log):
    try:
        local_llm.unload_all()          # одинаковый холодный старт для каждого пресета
    except RuntimeError:
        pass
    bot = agent.Agent(name)
    blocked, cells = [], []
    with offline(blocked):
        for i, item in enumerate(questions):
            c = cell(item, ask_safely(bot, item["q"]))
            cells.append(c)
            t = c["timing"]
            flag = ("ОШИБКА" if c["error"] else "не знаю" if c["unknown"]
                    else f"цит {len(c['quotes'])}✓ {len(c['rejected'])}✗")
            log(f"   {name:10} {i + 1:>2}. {c['seconds']:6.1f} с (реранк {t['rerank']:5.1f} · ответ "
                f"{t['answer']:5.1f} · загрузка {t['load']:4.1f}) {c['rerank_prompt_tokens']:>5} ток  "
                f"{str(c['model_gb'])}ГБ {str(c['gpu_pct'])}%GPU  {flag:12} {c['answer'][:50]!r}")
    return cells, blocked


def summarize(cells, questions):
    pairs = list(zip(cells, questions))
    times = [c["seconds"] for c in cells]
    llm_cells = [c for c in cells if c["llm_calls"]]
    by_set = {s: sum(c.get("correct") or 0 for c, q in pairs if q["set"] == s) for s in ("dev", "holdout")}
    max_set = {s: 2 * sum(q["set"] == s for q in questions) for s in ("dev", "holdout")}
    rss = [c["rss_mb"] for c in cells if c["rss_mb"]]
    return {
        "correct_dev": by_set["dev"], "max_dev": max_set["dev"],
        "correct_holdout": by_set["holdout"], "max_holdout": max_set["holdout"],
        "contradicts": sum(bool(c.get("contradicts")) for c in cells),
        "retrieval_ok": sum(bool(c["retrieval_ok"]) for c, q in pairs if q["type"] == "answer"),
        "n_answer": sum(q["type"] == "answer" for q in questions),
        "unknown_false": sum(c["unknown"] for c, q in pairs if q["type"] == "answer"),
        "unknown_hit": sum(c["unknown"] for c, q in pairs if q["type"] == "unknown"),
        "unknown_expected": sum(q["type"] == "unknown" for q in questions),
        "quotes": sum(len(c["quotes"]) for c in cells),
        "rejected": sum(len(c["rejected"]) for c in cells),
        "errors": sum(bool(c["error"]) for c in cells),
        "json_failed": sum(c["json_failed"] for c in cells),
        "seconds_total": round(sum(times), 1),
        "seconds_mean": round(statistics.mean(times), 1),
        "seconds_max": round(max(times), 1),
        "rerank_s": round(statistics.mean(c["timing"]["rerank"] for c in llm_cells), 1) if llm_cells else 0,
        "answer_s": round(statistics.mean([c["timing"]["answer"] for c in llm_cells if c["timing"]["answer"]] or [0]), 1),
        "load_s": round(sum(c["timing"]["load"] for c in cells), 1),
        "embed_s": round(sum(c["timing"]["embed"] for c in cells), 1),
        "reloads": sum(c["reloads"] for c in cells),
        "rerank_tokens": round(statistics.mean(c["rerank_prompt_tokens"] for c in llm_cells)) if llm_cells else 0,
        "tok_per_s": round(statistics.median([c["tok_per_s"] for c in llm_cells if c["tok_per_s"]] or [0]), 1),
        "prompt_tok_per_s": round(statistics.median([c["prompt_tok_per_s"] for c in llm_cells if c["prompt_tok_per_s"]] or [0]), 1),
        "model_gb": max((c["model_gb"] or 0 for c in cells), default=0),
        "gpu_pct": min((c["gpu_pct"] for c in cells if c["gpu_pct"] is not None), default=None),
        "bge_resident": sum(c["bge_loaded"] for c in llm_cells),
        "rss_max_mb": max(rss) if rss else None,
        "tokens": sum(c["prompt_tokens"] + c["completion_tokens"] for c in cells),
    }


def stability(runs, name):
    if len(runs) < 2:
        return {}
    per_q = list(zip(*[run["presets"][name]["cells"] for run in runs]))
    return {"same_answer": sum(len({c["answer"] for c in cells}) == 1 for cells in per_q),
            "same_score": sum(len({c.get("correct") for c in cells}) == 1 for cells in per_q),
            "n": len(per_q),
            "totals": [run["presets"][name]["summary"]["seconds_total"] for run in runs]}


def save(payload):
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--presets", nargs="+", default=presets.ORDER, choices=presets.ORDER)
    parser.add_argument("--limit", type=int, help="только первые N вопросов (пробный прогон)")
    args = parser.parse_args()

    embeddings.check()
    for name in args.presets:
        llm.check(name)
    questions = QUESTIONS[:args.limit] if args.limit else QUESTIONS
    disk = {m["name"]: m["size_gb"] for m in local_llm.models()}
    print(f"Ollama {local_llm.version()}; судья {JUDGE}; вопросов {len(questions)} "
          f"(dev {sum(q['set'] == 'dev' for q in questions)}, holdout "
          f"{sum(q['set'] == 'holdout' for q in questions)}); t=0")
    for name in args.presets:
        p = presets.PRESETS[name]
        print(f"  {name:10} {p['label']:34} {p['model']:28} диск "
              f"{disk.get(p['model'], disk.get(p['model'] + ':latest'))} ГБ  "
              f"опции {p['options'] or 'из Modelfile'}  реранкеру топ-{p['rerank_k']}, "
              f"фрагмент ≤{p['frag_chars'] or '∞'}  промпт {p['prompt']}")
    print()

    payload = {"presets": {n: presets.PRESETS[n] for n in args.presets}, "judge": JUDGE,
               "params": retrieval.DEFAULTS, "gate_min": citations.GATE_MIN, "disk_gb": disk,
               "ollama": local_llm.version(), "questions": questions, "runs": []}
    for attempt in range(args.repeats):
        print(f"=== проход {attempt + 1}/{args.repeats}")
        run = {"presets": {}}
        for name in args.presets:
            cells, blocked = run_preset(name, questions, print)
            run["presets"][name] = {"cells": cells, "blocked": blocked}
            print(f"   {name}: попыток выйти в сеть — {len(blocked)}")
        judge_tokens, judge_cost = 0, 0.0
        for i, item in enumerate(questions):
            results = {n: run["presets"][n]["cells"][i] for n in args.presets}
            verdict, tokens, cost = judge(item, results, seed=attempt * 100 + i)
            judge_tokens += tokens
            judge_cost += cost
            for n in args.presets:
                results[n].update(verdict[n])
            print(f"   судья {i + 1:>2}. " + "  ".join(
                f"{n} {verdict[n]['correct']}{'!' if verdict[n]['contradicts'] else ''}"
                for n in args.presets) + f"  {item['q'][:40]}")
        for n in args.presets:
            run["presets"][n]["summary"] = summarize(run["presets"][n]["cells"], questions)
        run["judge_tokens"], run["judge_cost"] = judge_tokens, round(judge_cost, 5)
        payload["runs"].append(run)
        save(payload)
        print()

    payload["stability"] = {n: stability(payload["runs"], n) for n in args.presets}
    save(payload)
    for attempt, run in enumerate(payload["runs"], 1):
        for n in args.presets:
            s = run["presets"][n]["summary"]
            print(f"{n:10} #{attempt}  dev {s['correct_dev']}/{s['max_dev']}  holdout "
                  f"{s['correct_holdout']}/{s['max_holdout']}  противоречий {s['contradicts']}  "
                  f"топ-5 {s['retrieval_ok']}/{s['n_answer']}  ложных «не знаю» {s['unknown_false']}  "
                  f"время {s['seconds_total']} с (ср. {s['seconds_mean']}, реранк {s['rerank_s']}, "
                  f"ответ {s['answer_s']})  перезагрузок {s['reloads']}  {s['model_gb']} ГБ "
                  f"{s['gpu_pct']}% GPU  RSS {s['rss_max_mb']} МБ")
    for n, st in payload["stability"].items():
        if st:
            print(f"{n:10} стабильность: ответ дословно {st['same_answer']}/{st['n']}, "
                  f"оценка {st['same_score']}/{st['n']}, время проходов {st['totals']}")
    print(f"→ {RESULTS.relative_to(pathlib.Path(__file__).parent)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        print(f"ошибка: {error}", file=sys.stderr)
        sys.exit(1)
