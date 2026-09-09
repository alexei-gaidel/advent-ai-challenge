"""Провайдеры LLM: DeepSeek и Groq за одним вызовом.

Разница между провайдерами (адрес, ключ, формат ответа, скрытые reasoning-токены)
заперта здесь. Агент про неё не знает и работает с единым словарём результата.
"""

import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

ENDPOINTS = {
    "deepseek": {"url": "https://api.deepseek.com/chat/completions", "key": "DEEPSEEK_API_KEY"},
    "groq": {"url": "https://api.groq.com/openai/v1/chat/completions", "key": "GROQ_API_KEY"},
}

# Модели для выпадающего списка. Цены — доллары за 1 млн токенов, из прайс-листов
# platform.deepseek.com и console.groq.com/docs/models.
CATALOG = [
    {"id": "deepseek-chat", "label": "DeepSeek Chat", "provider": "deepseek", "size": "—",
     "price": {"input": 0.27, "output": 1.10}},
    {"id": "allam-2-7b", "label": "Allam 7B", "provider": "groq", "size": "7B",
     "price": None},
    {"id": "openai/gpt-oss-20b", "label": "GPT-OSS 20B", "provider": "groq", "size": "20B",
     "price": {"input": 0.10, "output": 0.50}},
    {"id": "qwen/qwen3.8-27b", "label": "Qwen3.8 27B", "provider": "groq", "size": "27B",
     "price": {"input": 0.80, "output": 4.00}},
    {"id": "openai/gpt-oss-120b", "label": "GPT-OSS 120B", "provider": "groq", "size": "120B",
     "price": {"input": 0.15, "output": 0.60}},
]

BY_ID = {model["id"]: model for model in CATALOG}
DEFAULT_MODEL = "deepseek-chat"


def find_env_file():
    """Ищет .env рядом со скриптом и выше по дереву — общий ключ лежит в корне репо."""
    for folder in pathlib.Path(__file__).resolve().parents:
        candidate = folder / ".env"
        if candidate.exists():
            return candidate
    return None


def get_api_key(name):
    """Берёт ключ по имени переменной: DEEPSEEK_API_KEY или GROQ_API_KEY."""
    key = os.environ.get(name)
    if key:
        return key

    env_file = find_env_file()
    if env_file:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")

    raise RuntimeError(f"Ключ не найден: впиши {name} в .env в корне репозитория")


def post(url, api_key, body, timeout=180):
    """POST к OpenAI-совместимому API."""
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


def price_of(model_id, prompt_tokens, completion_tokens):
    """Стоимость запроса по тарифу модели. Без тарифа — None."""
    price = BY_ID.get(model_id, {}).get("price")
    if not price:
        return None
    return round(prompt_tokens / 1e6 * price["input"]
                 + completion_tokens / 1e6 * price["output"], 6)


def call(model_id, messages, temperature=0.7, max_tokens=800, stop=None):
    """Единый вызов модели. Возвращает одинаковый словарь для обоих провайдеров."""
    model = BY_ID.get(model_id)
    if not model:
        raise RuntimeError(f"Неизвестная модель: {model_id}")

    endpoint = ENDPOINTS[model["provider"]]
    body = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if stop:
        body["stop"] = stop

    started = time.monotonic()
    data = post(endpoint["url"], get_api_key(endpoint["key"]), body)
    seconds = round(time.monotonic() - started, 2)

    choice = data["choices"][0]
    usage = data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    return {
        "text": strip_thinking(choice["message"].get("content") or "") or "(пустой ответ)",
        # Рассуждающие модели Groq прячут ход мысли в отдельное поле и тратят на него токены.
        "reasoning": choice["message"].get("reasoning") or "",
        "reasoning_tokens": usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "finish_reason": choice.get("finish_reason", ""),
        "seconds": seconds,
        "cost": price_of(model_id, prompt_tokens, completion_tokens),
    }
