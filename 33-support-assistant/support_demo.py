"""Доказательство дня: ответы поддержки без контекста, с RAG и с RAG + CRM.

10 тикетов из data/crm.json. Три из них задают один и тот же вопрос «Почему не работает
авторизация?», но причины разные (отозванный ключ / на деле 429 / ключ заменён ротацией) —
различить их можно только по данным клиента.

    llm   — только текст тикета;
    rag   — текст тикета + документация продукта;
    full  — + тикет, карточка пользователя и журнал шлюза из CRM через MCP.

Эталоны причины и решения — в demo/cases.json, в CRM их нет. Оценивает судья
openai/gpt-oss-120b на Groq (провайдер не участвует в ответах): три ответа на тикет
обезличены и перемешаны; за каждый — причина 0–2, решение 0–2 и флаг «выдуманные факты
о клиенте». Судья видит эталон и данные CRM, чтобы отличить вывод из данных от выдумки.

    python3 support_demo.py              # 3 прохода, ~5 мин
    python3 support_demo.py --passes 1
"""

import argparse
import json
import pathlib
import random
import re
import time

import assistant as support
import providers

HERE = pathlib.Path(__file__).resolve().parent
CASES = json.loads((HERE / "demo" / "cases.json").read_text(encoding="utf-8"))["cases"]
OUT = HERE / "data" / "support_result.json"
JUDGE = "openai/gpt-oss-120b"

JUDGE_PROMPT = """Ты проверяешь черновики ответов службы поддержки на один тикет.
Эталон составлен по реальным данным клиента. Для каждого ответа поставь:
  cause — 0..2: 2 — названа именно эталонная причина; 1 — частично или среди нескольких
          догадок; 0 — неверно или «не знаю»;
  fix   — 0..2: 2 — шаги решают проблему клиента; 1 — общие советы, частично; 0 — не помогут;
  invented — true, если ответ утверждает о клиенте факты, которых нет в данных CRM
             (даты, статусы, параметры запросов). Общие советы — не выдумка.
Верни только JSON: {"A": {"cause": 0, "fix": 0, "invented": false}, "B": {...}, "C": {...}}

Тикет: <<TICKET>>
Данные CRM клиента: <<CRM>>
Эталон: причина — <<CAUSE>>; решение — <<FIX>>

<<ANSWERS>>"""


def judge(case, card, answers, seed):
    modes = list(answers)
    random.Random(seed).shuffle(modes)
    letters = dict(zip("ABC", modes))
    block = "\n\n".join(f"Ответ {l}:\n{answers[m]}" for l, m in letters.items())
    prompt = (JUDGE_PROMPT.replace("<<TICKET>>", json.dumps(card["ticket"], ensure_ascii=False))
              .replace("<<CRM>>", json.dumps({"user": card["user"], "events": card["events"]},
                                             ensure_ascii=False))
              .replace("<<CAUSE>>", case["cause"]).replace("<<FIX>>", case["fix"])
              .replace("<<ANSWERS>>", block))
    for attempt in range(8):
        try:
            reply = providers.call(JUDGE, [{"role": "user", "content": prompt}], temperature=0,
                                   max_tokens=3000, response_format={"type": "json_object"})
            break
        except RuntimeError as error:
            # Groq на бесплатном тарифе — 8000 токенов в минуту, судья упирается в лимит.
            if "429" not in str(error) or attempt == 7:
                raise
            time.sleep(20)
    match = re.search(r"\{.*\}", reply["text"], flags=re.S)
    raw = json.loads(match.group(0)) if match else {}
    return {m: raw.get(l) or {} for l, m in letters.items()}


def run(passes):
    bot = support.SupportAssistant()
    rows = []
    for number in range(1, passes + 1):
        for case in CASES:
            card = bot.ticket_card(case["ticket"])
            row = {"pass": number, "ticket": case["ticket"], "subject": card["ticket"]["subject"],
                   "modes": {}}
            for mode in support.MODES:
                r = bot.answer(question=card["ticket"]["text"],
                               ticket_id=case["ticket"] if mode == "full" else None,
                               mode=mode, temperature=0)
                row["modes"][mode] = {
                    "diagnosis": r["diagnosis"], "reply": r["reply"],
                    "tools": [t["name"] for t in r["tools"] if t["by"] == "модель"],
                    "tokens": r["prompt_tokens"] + r["completion_tokens"],
                    "cost": r["cost"], "seconds": r["seconds"]}
            scores = judge(case, card, {m: v["diagnosis"] + "\n" + v["reply"]
                                        for m, v in row["modes"].items()},
                           seed=f"{number}-{case['ticket']}")
            for mode, s in scores.items():
                row["modes"][mode].update(cause=s.get("cause"), fix=s.get("fix"),
                                          invented=bool(s.get("invented")))
            rows.append(row)
            print(f"[{number}] {case['ticket']} {row['subject'][:28]:28} " + "  ".join(
                f"{m}: {v['cause']}/{v['fix']}{' ⚠' if v['invented'] else ''}"
                for m, v in row["modes"].items()), flush=True)
    return rows


def summary(rows):
    table = []
    print("\nИтог (причина и решение — сумма баллов, максимум 2 за ответ):")
    for mode in support.MODES:
        sel = [r["modes"][mode] for r in rows]
        auth = [r["modes"][mode] for r in rows if r["ticket"] in ("T-101", "T-102", "T-103")]
        n = len(sel)
        line = {"mode": mode, "n": n,
                "cause": sum(v["cause"] or 0 for v in sel), "fix": sum(v["fix"] or 0 for v in sel),
                "auth_cause": sum(v["cause"] or 0 for v in auth), "auth_n": len(auth),
                "invented": sum(v["invented"] for v in sel),
                "tokens": round(sum(v["tokens"] for v in sel) / n),
                "cost": round(sum(v["cost"] for v in sel), 4),
                "seconds": round(sum(v["seconds"] for v in sel) / n, 1),
                "tool_calls": sum(len(v["tools"]) for v in sel)}
        table.append(line)
        print(f"{support.MODES[mode]:14} причина {line['cause']}/{2 * n} · решение {line['fix']}/{2 * n}"
              f" · «авторизация» ×3 тикета: причина {line['auth_cause']}/{2 * len(auth)}"
              f" · выдумки {line['invented']}/{n} · {line['tokens']} ток · {line['seconds']} с")
    return table


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--passes", type=int, default=3)
    args = parser.parse_args()
    started = time.monotonic()
    rows = run(args.passes)
    table = summary(rows)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"passes": args.passes, "judge": JUDGE, "summary": table,
                               "rows": rows, "minutes": round((time.monotonic() - started) / 60, 1)},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ data/support_result.json за {(time.monotonic() - started) / 60:.1f} мин")


if __name__ == "__main__":
    main()
