"""Два длинных сценария по 13 реплик: чат с памятью задачи против чата только с историей.

В обоих режимах одинаковые окно истории (chat.WINDOW сообщений), поиск, реранкер и ответ с
цитатами. Разница одна — блок «Память задачи» (цель, уточнения, ограничения, термины).
Ограничения задаются в первой реплике и к пятому ходу выпадают из окна: держатся они
дальше только за счёт памяти — или не держатся.

Проверки на каждом ходу:
- кодом: есть ли источники, все ли цитаты дословны, есть ли нужная статья среди источников,
  соблюдено ли ограничение сценария (число предложений, номер статьи, латиница);
- судья deepseek-reasoner — один вызов на сценарий: видит настоящую цель и ограничения
  пользователя и ответы обоих режимов (обезличены, порядок случайный по ходам):
  goal 0–2 — ответ работает на цель и соблюдает ограничения/термины; answer 0–2 — по существу.

    python3 chat_demo.py               # ~20 минут
    python3 chat_demo.py --repeats 1
"""

import argparse
import json
import pathlib
import random
import re
import sys
import time

import chat
import embeddings
import providers
import storage

JUDGE = "deepseek-reasoner"
RESULTS = pathlib.Path(__file__).with_name("data") / "chat_result.json"

ARTICLE = re.compile(r"(?:ст\.\s*|стать\w*\s+)\d+", re.I)


def sentences(text):
    """Число предложений; «ст. 81», «ч. 2», «п. «а»» и «т. е.» точками не считаются."""
    clean = re.sub(r"\b(ст|ч|п|т|е|г|гг|им|рф)\.\s*", "", text, flags=re.I)
    clean = re.sub(r"\d+\.\d+", "0", clean)
    clean = re.sub(r"\[[^\]]*\]", "", clean)
    parts = [p for p in re.split(r"[.!?…]+(?:\s|$)", clean) if len(p.strip()) > 2]
    lines = [l for l in text.splitlines() if re.match(r"\s*(?:\d+[.)]|[-•*])\s", l)]
    return max(len(parts), len(lines))


def check_exam(answer):
    """Сценарий 1: не больше 3 предложений и номер статьи в ответе."""
    problems = []
    if sentences(answer) > 3:
        problems.append(f"{sentences(answer)} предложений")
    if not ARTICLE.search(answer):
        problems.append("нет номера статьи")
    return problems


def check_plain(answer):
    """Сценарий 2: без латыни."""
    latin = re.findall(r"\b[A-Za-z]{3,}\b", answer)
    return [f"латиница: {', '.join(latin[:3])}"] if latin else []


SCENARIOS = [
    {"key": "exam", "title": "Экзамен: Президент против Председателя Правительства",
     "goal": "подготовка к экзамену: сравнить статус и полномочия Президента и Председателя Правительства",
     "constraints": "отвечать кратко, не больше 3 предложений, всегда указывать номер статьи; "
                    "только действующая редакция (с 6-й реплики)",
     "check": check_exam,
     "turns": [
         ("Готовлюсь к экзамену по конституционному праву: хочу сравнить Президента и Председателя "
          "Правительства. Отвечай кратко — не больше 3 предложений — и всегда указывай номер статьи.", []),
         ("Как избирается Президент и на какой срок?", ["81"]),
         ("А как назначается Председатель Правительства?", ["111"]),
         ("Кто может отправить его в отставку?", ["83", "117"]),
         ("Какие требования к кандидату в Президенты?", ["81"]),
         ("Уточню: меня интересует только действующая редакция, без истории поправок.", []),
         ("Кстати, сколько депутатов в Госдуме?", ["95"]),
         ("Ладно, вернёмся к теме. Кто из них руководит работой Правительства?", ["83", "113"]),
         ("А кто определяет структуру федеральных органов исполнительной власти?", ["112"]),
         ("Может ли Президент распустить Госдуму из-за кандидатуры премьера?", ["111"]),
         ("Чем отличается досрочное прекращение полномочий у них?", ["92", "117"]),
         ("Что Президент может сделать с постановлением Правительства, если оно противоречит Конституции?", ["115"]),
         ("Собери итог по нашей цели: три главных различия между ними.", []),
     ]},
    {"key": "detention", "title": "Задержание: права гражданина",
     "goal": "человек без юридического образования разбирается в своих правах при задержании полицией",
     "constraints": "объяснять простым языком, без латыни и канцелярита; ВНЖ = вид на жительство "
                    "(с 5-й реплики), у пользователя ВНЖ в Казахстане",
     "check": check_plain,
     "turns": [
         ("Я не юрист. Хочу понять свои права, если меня задержит полиция. Объясняй простым языком, "
          "без латыни и канцелярита.", []),
         ("Сколько меня могут держать без решения суда?", ["22"]),
         ("А если задержали ночью — что-то меняется?", ["22"]),
         ("Обязан ли я что-то говорить полицейским?", ["51"]),
         ("Кстати, у меня ВНЖ в Казахстане — ВНЖ это вид на жительство. Это как-то влияет на мои права здесь?", ["62"]),
         ("А адвокат мне положен?", ["48"]),
         ("А бесплатно?", ["48"]),
         ("Кстати, а на какой срок избирается Президент?", ["81"]),
         ("Вернёмся к моей ситуации. Могут ли обыскать мою квартиру без суда?", ["25"]),
         ("А читать переписку в моём телефоне?", ["23"]),
         ("Могут ли использовать против меня показания, полученные с нарушением закона?", ["50"]),
         ("Если со мной обращались жестоко — это законно?", ["21"]),
         ("Подведи итог для моей ситуации: что мне важно помнить при задержании, по пунктам.", []),
     ]},
]

JUDGE_PROMPT = """Ты проверяешь два чат-ассистента по Конституции РФ на одном и том же длинном диалоге.
Настоящая цель пользователя: <<GOAL>>
Его ограничения и термины: <<CONSTRAINTS>>

Ниже — ход за ходом реплика пользователя и ответы двух ассистентов (A и B; на каждом ходу
буквы назначены случайно). У каждого ответа — его цитаты из Конституции.

Для каждого хода и каждого ответа поставь:
  goal: 2 — ответ работает на цель диалога и соблюдает ограничения и термины пользователя
           (на побочный вопрос «кстати…» — коротко ответить и не потерять цель потом);
        1 — частично: нарушено ограничение или ответ уходит от цели;
        0 — цель или ограничения потеряны;
  answer: 2 — по существу отвечает на реплику, факты подтверждены цитатами; 1 — частично; 0 — нет.
Верни только JSON: {"1": {"A": {"goal": 0-2, "answer": 0-2}, "B": {...}}, "2": {...}, ...}

<<TURNS>>"""


JUDGE_BATCH = 4         # ходов на один вызов судьи


def judge(scenario, rows, seed):
    """Оценки судьи пишутся прямо в rows[...][mode]["goal" | "judged"]. → (токены, $)."""
    tokens, cost = 0, 0.0
    for start in range(0, len(rows), JUDGE_BATCH):
        t, c = _judge_batch(scenario, rows[start:start + JUDGE_BATCH], seed + start)
        tokens, cost = tokens + t, cost + c
    return tokens, cost


def _judge_batch(scenario, rows, seed):
    # Весь сценарий одним вызовом не влезает: reasoner сжигает 8000 токенов на скрытые
    # рассуждения и возвращает пустой ответ (finish_reason=length). Поэтому пачки по 4 хода
    # и запас max_tokens — цель и ограничения судья знает из промпта, а не из прошлых ходов.
    rng = random.Random(seed)
    order, blocks = {}, []
    for row in rows:
        modes = list(chat.MODES)
        rng.shuffle(modes)
        order[row["turn"]] = modes
        parts = [f"### Ход {row['turn']}\nПользователь: {row['message']}"]
        for letter, mode in zip("AB", modes):
            c = row[mode]
            quotes = "; ".join(f"[{q['chunk_id']}] «{q['text'][:200]}»" for q in c["quotes"]) or "нет"
            parts.append(f"[{letter}] {c['answer']}\n[{letter}] цитаты: {quotes}")
        blocks.append("\n".join(parts))
    prompt = (JUDGE_PROMPT.replace("<<GOAL>>", scenario["goal"])
              .replace("<<CONSTRAINTS>>", scenario["constraints"])
              .replace("<<TURNS>>", "\n\n".join(blocks)))
    tokens, cost, verdict = 0, 0.0, {}
    for _ in range(3):
        try:
            reply = providers.call(JUDGE, [{"role": "user", "content": prompt}],
                                   temperature=0, max_tokens=20000)
        except RuntimeError as error:          # обрыв или таймаут — ещё попытка
            print(f"     судья: {error}, повтор")
            continue
        tokens += reply["prompt_tokens"] + reply["completion_tokens"]
        cost += reply["cost"] or 0
        match = re.search(r"\{.*\}", reply["text"], flags=re.S)
        try:
            verdict = json.loads(match.group(0)) if match else {}
        except json.JSONDecodeError:
            verdict = {}
        if all(str(row["turn"]) in verdict for row in rows):
            break
    for row in rows:
        v = verdict.get(str(row["turn"])) or {}
        for letter, mode in zip("AB", order[row["turn"]]):
            cell = v.get(letter) or {}
            row[mode]["goal"] = cell.get("goal")
            row[mode]["judged"] = cell.get("answer")
    return tokens, cost


def run_scenario(scenario, attempt, log):
    db = storage.connect()
    chats = {mode: chat.Chat.create(f"демо {attempt + 1}: {scenario['title']}", mode, db)
             for mode in chat.MODES}
    rows = []
    for turn, (message, articles) in enumerate(scenario["turns"], 1):
        row = {"turn": turn, "message": message, "articles": articles}
        for mode, c in chats.items():
            r = c.send(message)
            found = {s["chunk_id"].split("#")[0].replace("ст.", "") for s in r["sources"]}
            row[mode] = {
                "answer": r["answer"], "query": r["query"], "side": r["side_question"],
                "unknown": r["unknown"], "sources": [s["chunk_id"] for s in r["sources"]],
                "quotes": r["quotes"], "rejected": len(r["rejected"]),
                "has_sources": bool(r["sources"]),
                "article_ok": (bool(found & set(articles)) if articles else None),
                "violations": [] if r["unknown"] else scenario["check"](r["answer"]),
                "state": r["state"] if mode == "memory" else None,
                "prompt_tokens": r["prompt_tokens"], "completion_tokens": r["completion_tokens"],
                "cost": r["cost"], "seconds": r["seconds"],
            }
        rows.append(row)
        m, h = row["memory"], row["history"]
        mark = lambda c: ("не знаю" if c["unknown"] else
                          f"ист {len(c['sources'])}{' ✓' if c['article_ok'] else (' ✗' if c['article_ok'] is False else '')}"
                          f"{' ⚠ ' + ', '.join(c['violations']) if c['violations'] else ''}")
        log(f"  {turn:>2}. память: {mark(m):28} | история: {mark(h):28} {message[:44]}")
    tokens, cost = judge(scenario, rows, seed=attempt * 10 + len(scenario["key"]))
    return {"rows": rows, "chat_ids": {m: c.id for m, c in chats.items()},
            "judge_tokens": tokens, "judge_cost": cost,
            "final_state": rows[-1]["memory"]["state"]}


def summarize(rows):
    out = {}
    for mode in chat.MODES:
        cells = [row[mode] for row in rows]
        answered = [c for c in cells if not c["unknown"]]
        with_articles = [(c, row) for c, row in zip(cells, rows) if row["articles"]]
        late = [c for c, row in zip(cells, rows) if row["turn"] >= 5 and not c["unknown"]]
        out[mode] = {
            "turns": len(cells), "answered": len(answered),
            "with_sources": sum(c["has_sources"] for c in answered),
            "unknown": len(cells) - len(answered),
            "article_ok": sum(bool(c["article_ok"]) for c, _ in with_articles),
            "article_total": len(with_articles),
            "rejected": sum(c["rejected"] for c in cells),
            "violations": sum(bool(c["violations"]) for c in answered),
            "violations_late": sum(bool(c["violations"]) for c in late),
            "late_total": len(late),
            "goal": sum(c.get("goal") or 0 for c in cells),
            "judged": sum(c.get("judged") or 0 for c in cells),
            "max": 2 * len(cells),
            "final_goal": cells[-1].get("goal"),
            "prompt_tokens": sum(c["prompt_tokens"] for c in cells),
            "completion_tokens": sum(c["completion_tokens"] for c in cells),
            "cost": round(sum(c["cost"] for c in cells), 5),
        }
    return out


def report(runs):
    for attempt, run in enumerate(runs, 1):
        for scenario in SCENARIOS:
            result = run[scenario["key"]]
            print(f"=== проход {attempt}, сценарий «{scenario['title']}»")
            for mode in chat.MODES:
                s = result["summary"][mode]
                print(f"     {chat.MODE_LABELS[mode]:32} цель {s['goal']}/{s['max']}  по существу "
                      f"{s['judged']}/{s['max']}  источники {s['with_sources']}/{s['answered']}  "
                      f"нужная статья {s['article_ok']}/{s['article_total']}  нарушений "
                      f"{s['violations']} (после 5-го хода {s['violations_late']}/{s['late_total']})  "
                      f"${s['cost']:.4f}")
            print(f"     итоговая цель в памяти: {result['final_state']['goal']}")


def rejudge():
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    by_key = {s["key"]: s for s in SCENARIOS}
    for attempt, run in enumerate(data["runs"]):
        for key, result in run.items():
            if result.get("rejudged"):
                print(f"судья: проход {attempt + 1}, {key} — уже оценён")
                continue
            print(f"судья: проход {attempt + 1}, {key}")
            result["judge_tokens"], result["judge_cost"] = judge(
                by_key[key], result["rows"], seed=attempt * 10 + len(key))
            result["summary"] = summarize(result["rows"])
            result["rejudged"] = True
            # Прогресс сохраняется после каждого сценария: обрыв сети не съедает уже оплаченное.
            RESULTS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    report(data["runs"])
    finish(data["runs"], data)
    return 0


def finish(runs, data):
    judge_tokens = sum(r["judge_tokens"] for run in runs for r in run.values())
    judge_cost = sum(r["judge_cost"] for run in runs for r in run.values())
    missing = sum(row[m].get("goal") is None for run in runs for r in run.values()
                  for row in r["rows"] for m in chat.MODES)
    print(f"судья: {judge_tokens} токенов, ${judge_cost:.4f}, не разобрано оценок: {missing}")
    data.update({"runs": runs, "judge_tokens": judge_tokens, "judge_cost": round(judge_cost, 5)})
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {RESULTS.relative_to(pathlib.Path(__file__).parent)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--rejudge", action="store_true",
                        help="не гонять чаты заново: переоценить ответы из data/chat_result.json")
    args = parser.parse_args()
    if args.rejudge:
        return rejudge()

    embeddings.check()
    print(f"окно истории: {chat.WINDOW} сообщений; режимы: "
          + " / ".join(chat.MODE_LABELS[m] for m in chat.MODES) + f"; судья {JUDGE}\n")
    runs = []
    for attempt in range(args.repeats):
        run = {}
        for scenario in SCENARIOS:
            print(f"=== проход {attempt + 1}, сценарий «{scenario['title']}»")
            started = time.monotonic()
            result = run_scenario(scenario, attempt, print)
            result["summary"] = summarize(result["rows"])
            result["seconds"] = round(time.monotonic() - started, 1)
            run[scenario["key"]] = result
            for mode in chat.MODES:
                s = result["summary"][mode]
                print(f"     {chat.MODE_LABELS[mode]:32} цель {s['goal']}/{s['max']}  по существу "
                      f"{s['judged']}/{s['max']}  источники {s['with_sources']}/{s['answered']}  "
                      f"нужная статья {s['article_ok']}/{s['article_total']}  нарушений "
                      f"{s['violations']} (после 5-го хода {s['violations_late']}/{s['late_total']})  "
                      f"${s['cost']:.4f}")
            print(f"     итоговая цель в памяти: {result['final_state']['goal']}\n")
        runs.append(run)

    finish(runs, {"window": chat.WINDOW, "judge": JUDGE,
                  "scenarios": [{k: v for k, v in s.items() if k != "check"} for s in SCENARIOS]})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        print(f"ошибка: {error}", file=sys.stderr)
        sys.exit(1)
