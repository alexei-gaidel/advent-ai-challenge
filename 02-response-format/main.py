"""День 2 — контроль формата ответа.

Один и тот же вопрос отправляется в LLM три раза с разным уровнем контроля:
без ограничений, с описанием формата, и с форматом + лимитом длины + стоп-условием.
Ответы печатаются рядом с метриками, чтобы разницу было видно.

Запуск:
    python3 main.py
    python3 main.py "свой вопрос"
"""

import json
import os
import pathlib
import sys
import textwrap
import urllib.error
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

QUESTION = "Расскажи, чем интересно озеро Байкал."

# Явное описание формата + условие завершения ответа.
FORMAT_RULES = (
    "Отвечай строго списком ровно из трёх пунктов.\n"
    "Каждый пункт — с новой строки в виде «N. текст», не длиннее 12 слов.\n"
    "Никаких вступлений, выводов и пояснений вне списка.\n"
    "После третьего пункта напиши на отдельной строке КОНЕЦ и прекрати ответ."
)

# Ограничение длины на стороне API и стоп-последовательности:
# генерация обрывается, как только модель напечатает КОНЕЦ или начнёт четвёртый пункт.
MAX_TOKENS = 120
STOP = ["КОНЕЦ", "4."]


def find_env_file():
    """Ищет .env рядом со скриптом и выше по дереву — общий ключ лежит в корне репо."""
    for folder in pathlib.Path(__file__).resolve().parents:
        candidate = folder / ".env"
        if candidate.exists():
            return candidate
    return None


def get_api_key():
    """Берёт ключ из переменной окружения, иначе из файла .env."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key

    env_file = find_env_file()
    if env_file:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")

    sys.exit("Ключ не найден: скопируй .env.example в .env (в корне репо) и впиши DEEPSEEK_API_KEY")


def ask(messages, max_tokens=None, stop=None):
    """Запрос к LLM. Возвращает текст ответа, причину остановки и число токенов."""
    body = {"model": MODEL, "messages": messages}
    if max_tokens:
        body["max_tokens"] = max_tokens
    if stop:
        body["stop"] = stop

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {get_api_key()}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        sys.exit(f"Ошибка API {error.code}: {error.read().decode()}")
    except urllib.error.URLError as error:
        sys.exit(f"Сеть недоступна: {error.reason}")

    choice = data["choices"][0]
    return {
        "text": choice["message"]["content"].strip(),
        # stop — модель закончила сама или сработала стоп-последовательность,
        # length — ответ обрезан лимитом max_tokens
        "finish_reason": choice["finish_reason"],
        "tokens": data["usage"]["completion_tokens"],
    }


def build_runs(question):
    """Три уровня контроля над одним и тем же вопросом."""
    return [
        {
            "title": "1. Без ограничений",
            "note": "голый вопрос, модель сама решает формат и длину",
            "messages": [{"role": "user", "content": question}],
            "params": {},
        },
        {
            "title": "2. + описание формата",
            "note": "формат задан словами, лимитов на стороне API нет",
            "messages": [
                {"role": "system", "content": FORMAT_RULES},
                {"role": "user", "content": question},
            ],
            "params": {},
        },
        {
            "title": "3. + лимит длины и стоп-условие",
            "note": f"то же плюс max_tokens={MAX_TOKENS} и stop={STOP}",
            "messages": [
                {"role": "system", "content": FORMAT_RULES},
                {"role": "user", "content": question},
            ],
            "params": {"max_tokens": MAX_TOKENS, "stop": STOP},
        },
    ]


def show(run, result):
    """Печатает один прогон: условия, ответ, метрики."""
    print("=" * 72)
    print(run["title"])
    print(f"   ({run['note']})")
    print("-" * 72)
    for line in result["text"].splitlines():
        print(textwrap.fill(line, width=72, subsequent_indent="   ") if line else "")
    print("-" * 72)
    text = result["text"]
    print(
        f"символов: {len(text):<6} слов: {len(text.split()):<5} "
        f"строк: {len(text.splitlines()):<4} "
        f"токенов: {result['tokens']:<5} finish_reason: {result['finish_reason']}"
    )
    print()


def main():
    question = " ".join(sys.argv[1:]) or QUESTION
    print(f"\nВопрос (одинаковый во всех прогонах): {question}\n")

    results = []
    for run in build_runs(question):
        result = ask(run["messages"], **run["params"])
        results.append((run, result))
        show(run, result)

    print("=" * 72)
    print("ИТОГ")
    print("=" * 72)
    print(f"{'прогон':<34}{'символов':>10}{'слов':>8}{'токенов':>10}{'стоп':>10}")
    for run, result in results:
        text = result["text"]
        print(
            f"{run['title']:<34}{len(text):>10}{len(text.split()):>8}"
            f"{result['tokens']:>10}{result['finish_reason']:>10}"
        )
    print()


if __name__ == "__main__":
    main()
