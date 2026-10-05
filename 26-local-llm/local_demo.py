"""Доказательство дня: локальная модель запускается, отвечает по CLI и HTTP, решает запросы
разной сложности. Облачных моделей нет — ни как участника, ни как судьи.

    python3 local_demo.py            # ~2–4 минуты на M1 8 ГБ
    python3 local_demo.py --runs 1   # быстрее

1. Холодный старт: модель выгружается из памяти, первый запрос меряет загрузку.
2. Три способа обращения: `ollama run` (CLI), сырой HTTP /api/generate, local_llm.chat.
3. Четыре запроса нарастающей сложности, temperature=0, каждый --runs раз.
   Проверка автоматическая и без LLM-судьи: подстрока-эталон, а код — запуск тестов.

Результат: data/local_result.json — из него собирается demo.svg.
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.request

import local_llm

RESULT = pathlib.Path(__file__).with_name("data") / "local_result.json"

PALINDROME_TESTS = '''
assert is_palindrome("А роза упала на лапу Азора")
assert is_palindrome("Was it a car or a cat I saw?")
assert is_palindrome("")
assert not is_palindrome("hello, world")
assert not is_palindrome("Привет, мир")    # без этого «вырезать всю кириллицу» проходит: "" == ""
print("ok")
'''

TASKS = [
    {"level": 1, "name": "простой факт",
     "prompt": "Столица Австралии? Ответь одним словом.",
     "check": "substring", "expect": "Канберр"},
    {"level": 2, "name": "задача в 3 шага",
     "prompt": "В корзине 23 яблока. Съели 7, потом докупили втрое больше, чем съели. "
               "Сколько яблок стало? Реши по шагам, в конце напиши «Ответ: N».",
     "check": "answer", "expect": "37"},
    {"level": 3, "name": "код + тесты",
     "prompt": "Напиши функцию на Python is_palindrome(s) -> bool, которая игнорирует регистр, "
               "пробелы и знаки препинания и работает с русским текстом. Только код, без объяснений.",
     "check": "code", "expect": "5 assert-ов проходят"},
    {"level": 4, "name": "логическая ловушка",
     "prompt": "У Маши 3 брата. У каждого брата 2 сестры. Сколько сестёр у Маши? "
               "Подумай и в конце напиши «Ответ: N».",
     "check": "answer", "expect": "1"},
]


def verify(task, text):
    if task["check"] == "substring":
        return task["expect"].lower() in text.lower(), ""
    if task["check"] == "answer":
        found = re.findall(r"[Оо]твет\W*\**\s*(\d+)", text)
        got = found[-1] if found else None
        return got == task["expect"], f"ответ модели: {got}"
    match = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    code = match.group(1) if match else text
    try:
        run = subprocess.run([sys.executable, "-c", code + PALINDROME_TESTS],
                             capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return False, "тесты зависли"
    ok = run.returncode == 0 and "ok" in run.stdout
    if ok:
        return True, ""
    failed = re.findall(r'line (\d+).*\n\s*(assert .*)', run.stderr)
    return False, failed[-1][1].split("#")[0].strip()[:80] if failed else (run.stderr.strip().splitlines() or ["?"])[-1][:120]


def cold_start(model):
    local_llm.unload(model)
    time.sleep(1)
    before = local_llm.loaded()
    r = local_llm.ask("Привет! Ответь одним словом.", model, temperature=0)
    after = local_llm.loaded()
    print(f"холодный старт: в памяти до — {before or 'пусто'}, после — {after}")
    print(f"  загрузка {r['load_s']} с, весь запрос {r['total_s']} с, ответ: {r['text']!r}")
    warm = local_llm.ask("Привет! Ответь одним словом.", model, temperature=0)
    print(f"  тот же запрос «тёплым»: загрузка {warm['load_s']} с, весь {warm['total_s']} с")
    return {"loaded_before": before, "loaded_after": after, "cold": r, "warm": warm}


def access_paths(model):
    prompt = "Сколько будет 2+2? Ответь только числом."
    started = time.monotonic()
    cli = subprocess.run(["ollama", "run", model, prompt], stdin=subprocess.DEVNULL,  # без этого ждёт ввода
                         capture_output=True, text=True, timeout=120)
    cli_s = round(time.monotonic() - started, 2)
    print(f"CLI   $ ollama run {model} \"{prompt}\"  →  {cli.stdout.strip()!r}  ({cli_s} с)")

    request = urllib.request.Request(
        local_llm.OLLAMA + "/api/generate",
        data=json.dumps({"model": model, "prompt": prompt, "stream": False,
                         "options": {"temperature": 0}}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = json.load(response)
    print(f"HTTP  POST {local_llm.OLLAMA}/api/generate  →  {raw['response'].strip()!r}  "
          f"(eval_count={raw['eval_count']})")

    lib = local_llm.ask(prompt, model, temperature=0)
    print(f"PY    local_llm.ask(...)  →  {lib['text'].strip()!r}  ({local_llm.metrics_line(lib)})")
    return {"prompt": prompt,
            "cli": {"command": f'ollama run {model} "{prompt}"', "answer": cli.stdout.strip(), "seconds": cli_s},
            "http": {"url": local_llm.OLLAMA + "/api/generate", "answer": raw["response"].strip(),
                     "eval_count": raw["eval_count"]},
            "python": {"answer": lib["text"].strip(), **{k: lib[k] for k in ("total_s", "tok_per_s")}}}


def recheck(model, samples=10):
    """Провалы при temperature=0 — это детерминированная ошибка или невезение? Перепроверка:
    тот же факт по-английски и выборка с обычной температурой."""
    capital = {"ru": TASKS[0]["prompt"], "en": "What is the capital of Australia? One word."}
    lang = {k: local_llm.ask(v, model, temperature=0)["text"].strip() for k, v in capital.items()}
    print(f"  столица, temperature=0:  по-русски {lang['ru']!r}, по-английски {lang['en']!r}")
    hot = {}
    for task in (TASKS[0], TASKS[3]):
        got = []
        for _ in range(samples):
            text = local_llm.ask(task["prompt"], model, temperature=0.8)["text"]
            ok, note = verify(task, text)
            got.append({"ok": ok, "answer": note.replace("ответ модели: ", "") if note else text.strip()[:30]})
        hot[task["name"]] = got
        print(f"  {task['name']}, temperature=0.8 ×{samples}: верно {sum(g['ok'] for g in got)}/{samples} · "
              f"ответы: {', '.join(str(g['answer']) for g in got)}")
    return {"language": lang, "hot": hot}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=local_llm.MODEL)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    local_llm.check(args.model)
    info = next(m for m in local_llm.models() if m["name"] == args.model)
    print(f"Ollama {local_llm.version()} · {args.model} · {info['params']} · {info['size_gb']} ГБ на диске\n")

    print("=== 1. Холодный старт")
    cold = cold_start(args.model)
    print("\n=== 2. Три способа обращения")
    paths = access_paths(args.model)

    print(f"\n=== 3. Запросы разной сложности, temperature=0, {args.runs} прогона")
    rows = []
    for task in TASKS:
        runs = []
        for n in range(args.runs):
            r = local_llm.ask(task["prompt"], args.model, temperature=0)
            ok, note = verify(task, r["text"])
            runs.append({**r, "ok": ok, "note": note})
            print(f"  [{task['level']}] {task['name']:18} прогон {n + 1}: {'✓' if ok else '✗'} "
                  f"{r['output_tokens']:4} ток {r['total_s']:5} с {r['tok_per_s']:5} ток/с  {note}")
        same = len({run["text"] for run in runs}) == 1
        rows.append({**task, "runs": runs, "identical": same,
                     "passed": sum(run["ok"] for run in runs)})
        print(f"      ответ 1-го прогона: {' '.join(runs[0]['text'].split())[:300]}")
        print(f"      ответы {'совпали дословно' if same else 'РАЗНЫЕ'}\n")

    print(f"=== 4. Перепроверка провалов")
    extra = recheck(args.model)

    print("\n=== Итог")
    print(f"  {'запрос':24} {'верно':>6} {'вход':>5} {'выход':>6} {'время, с':>9} {'ток/с':>6}  повтор")
    for row in rows:
        avg = lambda key: sum(r[key] for r in row["runs"]) / len(row["runs"])
        print(f"  {row['level']}. {row['name']:21} {row['passed']:>3}/{len(row['runs']):<2} "
              f"{avg('prompt_tokens'):>5.0f} {avg('output_tokens'):>6.0f} {avg('total_s'):>9.2f} "
              f"{avg('tok_per_s'):>6.1f}  {'дословно' if row['identical'] else 'разные'}")
    print(f"  в памяти: {local_llm.loaded()}")

    RESULT.parent.mkdir(exist_ok=True)
    RESULT.write_text(json.dumps({"model": args.model, "info": info, "ollama": local_llm.version(),
                                  "date": time.strftime("%Y-%m-%d %H:%M"), "cold": cold,
                                  "paths": paths, "tasks": rows, "recheck": extra, "memory": local_llm.loaded()},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {RESULT.relative_to(pathlib.Path.cwd()) if RESULT.is_relative_to(pathlib.Path.cwd()) else RESULT}")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    main()
