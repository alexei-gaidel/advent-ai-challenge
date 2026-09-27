"""Сценарий дня: длинный флоу через три MCP-сервера и проверка оркестрации.

    python3 orchestration_demo.py              # все модели, по 3 прогона
    python3 orchestration_demo.py --runs 1 --models deepseek-chat

1. Контроль проверяющего: ему подсовываются заведомо плохие ленты вызовов.
   Если он их пропустит, остальным цифрам верить нельзя.
2. Сценарий «поездка на матч»: 7 инструментов на 3 серверах, каждая модель по N раз,
   temperature=0. Считаем шаги, порядок, происхождение id, факты, лишние вызовы.
3. Маршрутизация: короткие запросы, где важен первый выбранный инструмент,
   включая коллизию search у погоды и заметок.

Результаты — в data/demo_results.json, лента первого прогона DeepSeek — для make_cast.py.
"""

import argparse
import json
import os
import pathlib
import tempfile
import time

# Заметки демо пишутся в свой файл: рабочие не трогаем. Через окружение,
# потому что сервер заметок — отдельный процесс.
NOTES = pathlib.Path(tempfile.gettempdir()) / "orchestration_demo_notes.json"
os.environ["ADVENT_NOTES"] = str(NOTES)

import agent as agents          # noqa: E402
import orchestration as orch    # noqa: E402
from registry import Registry   # noqa: E402

MODELS = ["deepseek-chat", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
# Пауза между прогонами на Groq: минутное окно лимита токенов успевает сброситься.
GROQ_PAUSE = 60
RESULTS = pathlib.Path(__file__).with_name("data") / "demo_results.json"


def head(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def mark(value):
    return {True: "да", False: "НЕТ", None: "—"}[value]


# --- 1. контроль проверяющего ------------------------------------------------------

def fake(seq, rnd, name, args, result, ok=True):
    return {"seq": seq, "round": rnd, "name": name, "server": name.split("__")[0],
            "tool": name.split("__")[1], "arguments": args, "ok": ok, "result": result}


def control_tapes():
    good = [
        fake(1, 1, "weather__search", {"query": "Екатеринбург"}, "place_id: place-1 — Екатеринбург"),
        fake(2, 1, "money__rates", {"currency": "EUR"}, "rate_id: rate-EUR-1"),
        fake(3, 2, "weather__forecast", {"place_id": "place-1", "date": orch.TRIP_DATE},
             "forecast_id: fc-1"),
        fake(4, 2, "money__budget", {"rate_id": "rate-EUR-1", "items": []},
             f"budget_id: bud-1\nитого: {orch.TRIP_TOTAL:,} ₽".replace(",", " ")),
        fake(5, 3, "notes__create", {"sources": ["fc-1", "bud-1"]}, "note_id: note-1"),
        fake(6, 4, "notes__checklist_add", {"note_id": "note-1"}, "добавлено"),
        fake(7, 5, "notes__read", {"note_id": "note-1"}, "note-1: …"),
    ]
    # Прогноз в том же круге, что и поиск: по номерам «после», но id выдуман.
    same_round = [dict(c) for c in good]
    same_round[2] = dict(same_round[2], round=1)
    # Заметка прочитана до чек-листа.
    read_early = [dict(c) for c in good]
    read_early[5], read_early[6] = dict(good[6], seq=6), dict(good[5], seq=7)
    # Бюджет посчитан без статьи «гостиница».
    wrong_sum = [dict(c) for c in good]
    wrong_sum[3] = dict(good[3], result="budget_id: bud-1\nитого: 27 400 ₽")
    # Заметка без ссылки на бюджет.
    no_source = [dict(c) for c in good]
    no_source[4] = dict(good[4], arguments={"sources": ["fc-1"]})
    return [("правильная лента", good, True),
            ("прогноз в одном круге с поиском", same_round, False),
            ("заметка прочитана до чек-листа", read_early, False),
            ("бюджет без гостиницы", wrong_sum, False),
            ("заметка без ссылки на бюджет", no_source, False)]


def run_control():
    head("1. Контроль проверяющего: заведомо плохие ленты должны быть пойманы")
    rows = []
    for title, tape, should_pass in control_tapes():
        report = orch.check(tape)
        caught = report["passed"] == should_pass
        reason = report["violations"][0] if report["violations"] else "нарушений нет"
        print(f"  {'✓' if caught else '✗'} {title:34} → {reason}")
        rows.append({"title": title, "expected": should_pass, "passed": report["passed"],
                     "caught": caught, "reason": reason})
    return rows


# --- 2. длинный сценарий -----------------------------------------------------------

def run_scenario(model):
    NOTES.unlink(missing_ok=True)
    registry = Registry()
    agent = agents.Agent({"model": model}, registry)
    started = time.monotonic()
    try:
        reply = agent.ask(orch.TASK)
        error = ""
    except RuntimeError as failure:
        reply, error = {"calls": list(registry.log), "text": "", "turn": dict(agent.stats)}, str(failure)
    registry.close()

    report = orch.check(reply["calls"])
    turn = reply["turn"]
    waited = turn.get("waited", agent.stats["waited"])
    seconds = round(time.monotonic() - started - waited, 1)
    return {"model": model, "seconds": seconds, "error": error, "waited": waited,
            "waits": turn.get("rate_limit_waits", agent.stats["rate_limit_waits"]),
            "tokens": turn.get("prompt_tokens", 0) + turn.get("completion_tokens", 0),
            "cost": turn.get("cost", 0), "answer": reply["text"],
            "calls": reply["calls"], "report": report}


def print_tape(result):
    for call in result["calls"]:
        args = json.dumps(call["arguments"], ensure_ascii=False)
        args = args if len(args) <= 70 else args[:69] + "…"
        status = "ok" if call["ok"] else "ОШИБКА: " + call["result"][:60]
        print(f"    круг {call['round']:>2} · {call['server'] or '?':7} · "
              f"{call['name']:22} {args}  {status}")


def run_scenarios(models, runs):
    head("2. Сценарий «поездка на матч»: 7 инструментов на 3 серверах")
    print("Задача: " + orch.TASK)
    results = []
    for model in models:
        for attempt in range(1, runs + 1):
            if model != "deepseek-chat" and results:
                time.sleep(GROQ_PAUSE)
            print(f"\n→ {model}, прогон {attempt}")
            result = run_scenario(model)
            result["attempt"] = attempt
            results.append(result)
            print_tape(result)
            report = result["report"]
            if result["error"]:
                print(f"    сбой API: {result['error'][:120]}")
            if result["waits"]:
                print(f"    ждали лимит Groq: {result['waits']} раз, {result['waited']} с "
                      f"(в время прогона не входит)")
            print(f"    шагов {report['steps_ok']}/7 · порядок {mark(report['order_ok'])} · "
                  f"вызовов {report['calls']} · ошибок {report['errors']} · кругов "
                  f"{report['rounds']} · {result['tokens']} ток. · {result['seconds']} с")
            for violation in report["violations"]:
                print(f"    ! {violation}")
    return results


def summary(results):
    head("Сводка по сценарию")
    print(f"{'модель':22} {'прошёл':>7} {'шаги':>6} {'порядок':>8} {'факты':>6} "
          f"{'вызовов':>8} {'ошибок':>7} {'кругов':>7} {'токенов':>8} {'$':>8} {'сек':>6}")
    rows = []
    for model in dict.fromkeys(r["model"] for r in results):
        mine = [r for r in results if r["model"] == model]
        n = len(mine)
        facts_ok = sum(all(v is True for v in r["report"]["facts"].values()) for r in mine)
        row = {
            "model": model, "runs": n,
            "passed": sum(r["report"]["passed"] for r in mine),
            "steps": round(sum(r["report"]["steps_ok"] for r in mine) / n, 1),
            "order": sum(r["report"]["order_ok"] for r in mine),
            "facts": facts_ok,
            "calls": round(sum(r["report"]["calls"] for r in mine) / n, 1),
            "errors": sum(r["report"]["errors"] for r in mine),
            "rounds": round(sum(r["report"]["rounds"] for r in mine) / n, 1),
            "tokens": round(sum(r["tokens"] for r in mine) / n),
            "cost": round(sum(r["cost"] or 0 for r in mine) / n, 5),
            "seconds": round(sum(r["seconds"] for r in mine) / n, 1),
            "api_errors": sum(1 for r in mine if r["error"]),
        }
        rows.append(row)
        print(f"{model:22} {row['passed']:>4}/{n} {row['steps']:>6} {row['order']:>5}/{n} "
              f"{row['facts']:>4}/{n} {row['calls']:>8} {row['errors']:>7} {row['rounds']:>7} "
              f"{row['tokens']:>8} {row['cost']:>8} {row['seconds']:>6}"
              + (f"  сбоев API: {row['api_errors']}" if row["api_errors"] else ""))
    print("\nшаги — сколько из 7 инструментов отработали успешно (среднее);")
    print("порядок — все id пришли из ответов более ранних кругов; факты — город, дата,")
    print("валюта и сумма 36 400 ₽; ошибок — вызовов, вернувших ошибку, за все прогоны.")
    return rows


# --- 3. маршрутизация --------------------------------------------------------------

def run_probes(models):
    head("3. Маршрутизация: какой инструмент выбран первым")
    rows = []
    for model in models:
        if model != "deepseek-chat":
            time.sleep(GROQ_PAUSE)
        # Заметка про матч должна существовать, чтобы notes__search было что искать.
        NOTES.unlink(missing_ok=True)
        registry = Registry()
        registry.call("notes__create", {"title": "Поездка на матч Россия — Намибия",
                                        "text": "Екатеринбург, 3 октября",
                                        "sources": []})
        registry.call("notes__checklist_add", {"note_id": "note-1",
                                               "items": ["паспорт", "билет", "зонт"]})
        print(f"\n→ {model}")
        for question, expected in orch.PROBES:
            registry.log.clear()
            agent = agents.Agent({"model": model, "max_tool_rounds": 4}, registry)
            try:
                reply = agent.ask(question)
                calls, error = reply["calls"], ""
            except RuntimeError as failure:
                calls, error = list(registry.log), str(failure)
            verdict = orch.check_probe(calls, expected)
            chain = " → ".join(c["name"] for c in calls) or "(без инструментов)"
            print(f"  {'✓' if verdict['ok'] else '✗'} {question:44} {chain}"
                  + (f"  [сбой: {error[:60]}]" if error else ""))
            rows.append({"model": model, "question": question, "expected": expected,
                         "chain": [c["name"] for c in calls], "error": error, **verdict})
        registry.close()

    head("Сводка по маршрутизации")
    for model in models:
        mine = [r for r in rows if r["model"] == model]
        collision = [r for r in mine if r["expected"].endswith("__search")]
        print(f"  {model:22} первый инструмент верный: {sum(r['ok'] for r in mine)}/{len(mine)}"
              f" · из них на коллизии search: {sum(r['ok'] for r in collision)}/{len(collision)}")
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--models", nargs="*", default=MODELS)
    parser.add_argument("--skip-probes", action="store_true")
    options = parser.parse_args()

    control = run_control()
    results = run_scenarios(options.models, options.runs)
    rows = summary(results)
    probes = [] if options.skip_probes else run_probes(options.models)

    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps({
        "date": time.strftime("%Y-%m-%d %H:%M"), "task": orch.TASK, "control": control,
        "summary": rows, "runs": results, "probes": probes,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    NOTES.unlink(missing_ok=True)
    print(f"\nрезультаты: {RESULTS.relative_to(pathlib.Path(__file__).parent)}")


if __name__ == "__main__":
    main()
