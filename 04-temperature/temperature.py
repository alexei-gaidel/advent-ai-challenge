"""День 4 — температура.

Один и тот же запрос уходит с temperature 0, 0.7 и 1.2. Каждая температура
прогоняется несколько раз: разброс виден не внутри одного ответа, а между
повторами одного и того же запроса. Интерфейс — в web.py.
"""

import difflib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

TEMPERATURES = [0.0, 0.7, 1.2]
RUNS_PER_TEMPERATURE = 3

# Две задачи разного склада: на одной проверяется точность, на другой — креативность.
PRESETS = {
    "facts": {
        "title": "Фактический вопрос",
        "note": "есть один правильный ответ — видно точность",
        "prompt": (
            "В каком году был запущен первый искусственный спутник Земли и как он "
            "назывался? Ответь одной строкой."
        ),
        # Ответ считается верным, если в нём есть все эти подстроки.
        "expect": ["1957", "путник-1"],
    },
    "creative": {
        "title": "Творческая задача",
        "note": "правильного ответа нет — видно разнообразие",
        "prompt": (
            "Придумай название и слоган для маленькой кофейни у метро. "
            "Ответь одной строкой в формате «Название — слоган»."
        ),
        "expect": [],
    },
}


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


def ask(prompt, temperature):
    """Один вызов LLM с заданной температурой."""
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {get_api_key()}",
        },
    )

    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Ошибка API {error.code}: {error.read().decode()}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Сеть недоступна: {error.reason}") from error

    return {
        "text": data["choices"][0]["message"]["content"].strip(),
        "tokens": data["usage"]["completion_tokens"],
        "seconds": round(time.monotonic() - started, 1),
    }


def words(text):
    return re.findall(r"[^\W\d_]+", text.lower())


def similarity(texts):
    """Среднее попарное сходство повторов, %. 100 — все прогоны совпали дословно."""
    pairs = [
        difflib.SequenceMatcher(None, first, second).ratio()
        for index, first in enumerate(texts)
        for second in texts[index + 1:]
    ]
    return round(100 * sum(pairs) / len(pairs)) if pairs else 100


def measure(answers, expect):
    """Считает метрики по всем прогонам одной температуры."""
    texts = [answer["text"] for answer in answers]
    all_words = [word for text in texts for word in words(text)]

    return {
        # стабильность: насколько повторы похожи друг на друга
        "similarity": similarity(texts),
        "identical": len(set(texts)) == 1,
        # разнообразие: сколько разных слов модель использовала суммарно
        "unique_words": len(set(all_words)),
        "avg_chars": round(sum(len(text) for text in texts) / len(texts)),
        "avg_tokens": round(sum(answer["tokens"] for answer in answers) / len(answers)),
        # точность: сколько прогонов содержат все ожидаемые подстроки
        "correct": sum(answer["ok"] for answer in answers) if expect else None,
    }


def check(text, expect):
    """Проверяет ответ по эталонным подстрокам. Без эталона проверять нечего."""
    if not expect:
        return None
    return all(fragment.lower() in text.lower() for fragment in expect)


def run_temperature(prompt, temperature, runs=RUNS_PER_TEMPERATURE, expect=()):
    """Прогоняет один и тот же запрос несколько раз при одной температуре."""
    answers = []
    for _ in range(runs):
        result = ask(prompt, temperature)
        result["ok"] = check(result["text"], expect)
        answers.append(result)

    return {
        "temperature": temperature,
        "answers": answers,
        "metrics": measure(answers, expect),
    }
