"""День 5 — версии моделей.

Один и тот же запрос уходит в три модели разного размера через Groq API:
7B, 27B и 120B параметров. Замеряются время, токены и стоимость.
Качество оценивает независимый судья — DeepSeek, то есть провайдер, который сам
в сравнении не участвует. Интерфейс — в web.py.
"""

import json
import os
import pathlib
import random
import re
import sys
import time
import urllib.error
import urllib.request

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
JUDGE_MODEL = "deepseek-chat"

MAX_TOKENS = 900

# Слабая / средняя / сильная — начало, середина и конец списка Groq по размеру.
MODELS = [
    {
        "key": "weak",
        "id": "allam-2-7b",
        "level": "Слабая",
        "size": "7B",
        "owner": "SDAIA",
        "link": "https://console.groq.com/docs/model/allam-2-7b",
    },
    {
        "key": "medium",
        "id": "qwen/qwen3.8-27b",
        "level": "Средняя",
        "size": "27B",
        "owner": "Alibaba Cloud",
        "link": "https://console.groq.com/docs/model/qwen3.8-27b",
    },
    {
        "key": "strong",
        "id": "openai/gpt-oss-120b",
        "level": "Сильная",
        "size": "120B",
        "owner": "OpenAI",
        "link": "https://console.groq.com/docs/model/openai/gpt-oss-120b",
    },
]

# Цены в долларах за 1 млн токенов, из console.groq.com/docs/models.
# Через API тарифы не отдаются, так что таблица заполняется руками.
# Для модели без цены стоимость в интерфейсе показывается прочерком.
PRICES = {
    "openai/gpt-oss-120b": {"input": 0.15, "output": 0.60},
    "qwen/qwen3.8-27b": {"input": 0.80, "output": 4.00},
    # allam-2-7b в прайс-листе Groq не значится — стоимость показывается прочерком.
}

PRESETS = {
    "logic": {
        "title": "Логическая задача",
        "note": "есть проверяемый ответ — видно точность",
        "prompt": (
            "У Алисы четыре брата и три сестры. Сколько сестёр у брата Алисы? "
            "Объясни рассуждение и дай итоговое число."
        ),
        # У брата Алисы сёстры — три сестры Алисы плюс сама Алиса.
        "expect": ["4"],
    },
    "explain": {
        "title": "Объяснение",
        "note": "правильного ответа нет — видно качество текста",
        "prompt": (
            "Объясни, почему небо голубое, так чтобы понял восьмилетний ребёнок. "
            "Уложись в четыре предложения."
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


def get_api_key(name):
    """Берёт ключ по имени переменной: GROQ_API_KEY или DEEPSEEK_API_KEY."""
    key = os.environ.get(name)
    if key:
        return key

    env_file = find_env_file()
    if env_file:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")

    sys.exit(f"Ключ не найден: впиши {name} в .env в корне репозитория")


def post(url, api_key, body, timeout=180):
    """Общий POST к OpenAI-совместимому API. Возвращает разобранный JSON."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            # Без своего User-Agent Cloudflare перед Groq отдаёт 403 (код 1010):
            # стандартный «Python-urllib» у него в чёрном списке.
            "User-Agent": "advent-ai-challenge/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Ошибка API {error.code}: {error.read().decode()[:300]}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Сеть недоступна: {error.reason}") from error
    except TimeoutError as error:
        raise RuntimeError("Таймаут: модель не ответила вовремя") from error


def strip_thinking(text):
    """Убирает блок <think>…</think>: часть моделей печатает рассуждение прямо в ответ."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def price_of(model_id, usage):
    """Стоимость запроса по таблице PRICES. Без заполненных цен — None."""
    price = PRICES.get(model_id)
    if not price:
        return None
    return round(
        usage["prompt_tokens"] / 1e6 * price["input"]
        + usage["completion_tokens"] / 1e6 * price["output"],
        6,
    )


def run_model(model, prompt, expect=()):
    """Один запрос к одной модели с полным замером."""
    started = time.monotonic()
    data = post(
        GROQ_URL,
        get_api_key("GROQ_API_KEY"),
        {
            "model": model["id"],
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": MAX_TOKENS,
        },
    )
    wall = round(time.monotonic() - started, 2)

    message = data["choices"][0]["message"]
    usage = data["usage"]
    text = strip_thinking(message.get("content") or "")
    # Рассуждающие модели отдают ход мысли отдельным полем и тратят на него токены.
    reasoning = message.get("reasoning") or ""
    reasoning_tokens = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)

    return {
        **{field: model[field] for field in ("key", "id", "level", "size", "owner", "link")},
        "text": text or "(пустой ответ)",
        "reasoning": reasoning,
        "ok": all(fragment.lower() in text.lower() for fragment in expect) if expect else None,
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "reasoning_tokens": reasoning_tokens,
        "chars": len(text),
        # wall — полное время запроса, total_time — сколько считала сама модель,
        # queue_time — сколько запрос ждал свободного ускорителя.
        "wall": wall,
        "total_time": round(usage.get("total_time", 0), 2),
        "queue_time": round(usage.get("queue_time", 0), 2),
        "finish_reason": data["choices"][0]["finish_reason"],
        "cost": price_of(model["id"], usage),
    }


JUDGE_FORMAT = (
    'Верни только JSON без markdown-обёртки, ровно в таком виде: '
    '{"winner": "B", "ranking": ["B", "A", "C"], "comment": "одно-два предложения"}'
)


def judge(prompt, answers):
    """Оценивает качество ответов. Судья — DeepSeek, он в сравнении не участвует.

    Ответы обезличены и перемешаны: судья не должен знать, где сильная модель.
    """
    shuffled = list(answers)
    random.shuffle(shuffled)
    labels = {}
    blocks = []
    for index, answer in enumerate(shuffled):
        label = chr(ord("A") + index)
        labels[label] = answer["key"]
        blocks.append(f"=== Ответ {label} ===\n{answer['text']}")

    body = {
        "model": JUDGE_MODEL,
        "messages": [{"role": "user", "content": (
            "Ты строгий судья. Ниже задача и ответы разных моделей.\n"
            "Оцени по критериям: правильность, ясность объяснения, отсутствие лишнего.\n"
            "Выбери лучший ответ и упорядочи все от лучшего к худшему.\n\n"
            f"ЗАДАЧА: {prompt}\n\n" + "\n\n".join(blocks) + "\n\n" + JUDGE_FORMAT
        )}],
    }
    data = post(DEEPSEEK_URL, get_api_key("DEEPSEEK_API_KEY"), body)
    raw = data["choices"][0]["message"]["content"].strip()

    text = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"winner": None, "ranking": [], "comment": raw}

    levels = {model["key"]: model["level"] for model in MODELS}
    comment = parsed.get("comment", "")
    for label, key in labels.items():
        # Метки судьи меняем на уровни моделей, иначе вердикт читается как «лучший — C».
        comment = re.sub(rf"(?<![^\W\d_])[{label}](?![^\W\d_])", f"«{levels[key]}»", comment)

    return {
        "winner": labels.get(parsed.get("winner")),
        "ranking": [labels[label] for label in parsed.get("ranking", []) if label in labels],
        "comment": comment,
    }
