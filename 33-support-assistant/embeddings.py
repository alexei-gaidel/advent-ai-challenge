"""Эмбеддинги через локальную Ollama (модель bge-m3, 1024 измерения).

У DeepSeek API эндпоинта эмбеддингов нет — только chat/completions, поэтому векторы
считаются локально. Из Python это обычный HTTP к localhost, без пакетов.

    brew install ollama && ollama serve && ollama pull bge-m3
"""

import json
import math
import os
import time
import urllib.error
import urllib.request

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MODEL = "bge-m3"
BATCH = 16


def _post(path, body, timeout=300):
    request = urllib.request.Request(
        OLLAMA + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Ollama {error.code}: {error.read().decode()[:300]}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Ollama не отвечает на {OLLAMA} — запусти `ollama serve` "
                           f"({error.reason})") from error
    except TimeoutError as error:
        raise RuntimeError("Ollama не успела посчитать эмбеддинги") from error


def check(model=MODEL):
    """Ollama запущена и модель скачана — иначе понятная ошибка до начала работы."""
    request = urllib.request.Request(OLLAMA + "/api/tags")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            names = [m["name"] for m in json.load(response).get("models", [])]
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"Ollama не отвечает на {OLLAMA} — запусти `ollama serve`") from error
    if not any(name.split(":")[0] == model for name in names):
        raise RuntimeError(f"Модель {model} не скачана — `ollama pull {model}`")


def normalize(vector):
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def embed(texts, model=MODEL, on_batch=None):
    """Векторы для списка текстов, L2-нормированные: косинус = скалярное произведение."""
    vectors = []
    for start in range(0, len(texts), BATCH):
        batch = texts[start:start + BATCH]
        data = _post("/api/embed", {"model": model, "input": batch})
        vectors += [normalize(v) for v in data["embeddings"]]
        if on_batch:
            on_batch(len(vectors), len(texts))
    return vectors


def embed_one(text, model=MODEL):
    started = time.monotonic()
    vector = embed([text], model)[0]
    return vector, round(time.monotonic() - started, 3)
