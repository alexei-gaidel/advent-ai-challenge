"""Клиент локальной LLM через Ollama: обычный HTTP к localhost, без пакетов и без ключей (копия дня 26).

    brew install ollama
    ollama serve &
    ollama pull qwen2.5:3b

Облако не участвует: запрос уходит на http://localhost:11434, модель крутится на этом Mac.
"""

import json
import os
import urllib.error
import urllib.request

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MODEL = "qwen2.5:3b"
EMBED_ONLY = ("bge-m3", "nomic-embed-text", "mxbai-embed-large")   # чатиться не умеют
# При num_ctx=4096 Ollama молча режет промпт до 2050 токенов (WARN «truncating input prompt»
# только в логе сервера), и инструкция реранкера пропадает. Промпт на 20 статей — ~5300 токенов.
NUM_CTX = 8192


def _request(path, body=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(OLLAMA + path, data=data,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Ollama {error.code}: {error.read().decode()[:300]}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Ollama не отвечает на {OLLAMA} — запусти `ollama serve` "
                           f"({error.reason})") from error
    except TimeoutError as error:
        raise RuntimeError(f"Ollama не ответила за {timeout} с") from error


def version():
    return _request("/api/version", timeout=5)["version"]


def models():
    """Скачанные чат-модели: имя и размер на диске."""
    found = _request("/api/tags", timeout=5).get("models", [])
    return [{"name": m["name"], "size_gb": round(m["size"] / 1e9, 2),
             "params": m.get("details", {}).get("parameter_size", "")}
            for m in found if m["name"].split(":")[0] not in EMBED_ONLY]


def loaded():
    """Что сейчас в памяти (аналог `ollama ps`)."""
    return [{"name": m["name"], "vram_gb": round(m.get("size_vram", 0) / 1e9, 2)}
            for m in _request("/api/ps", timeout=5).get("models", [])]


def check(model=MODEL):
    names = [m["name"] for m in models()]
    if model not in names:
        raise RuntimeError(f"Модель {model} не скачана — `ollama pull {model}`")


def unload(model=MODEL):
    """Выгрузить модель из памяти — чтобы замерить холодный старт."""
    _request("/api/generate", {"model": model, "keep_alive": 0})


def chat(messages, model=MODEL, temperature=None, max_tokens=None, json_mode=False,
         num_ctx=NUM_CTX):
    """Один запрос к /api/chat. Метрики — из ответа Ollama, а не секундомером снаружи.

    json_mode — аналог response_format=json_object: Ollama ограничивает выдачу грамматикой JSON.
    """
    options = {"num_ctx": num_ctx}
    if temperature is not None:
        options["temperature"] = temperature
    if max_tokens:
        options["num_predict"] = max_tokens
    body = {"model": model, "messages": messages, "stream": False, "options": options}
    if json_mode:
        body["format"] = "json"
    data = _request("/api/chat", body)
    ns = 1e9
    eval_s = data.get("eval_duration", 0) / ns
    return {
        "text": data["message"]["content"],
        "model": data["model"],
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "output_tokens": data.get("eval_count", 0),
        "load_s": round(data.get("load_duration", 0) / ns, 2),
        "prompt_s": round(data.get("prompt_eval_duration", 0) / ns, 2),
        "eval_s": round(eval_s, 2),
        "total_s": round(data.get("total_duration", 0) / ns, 2),
        "tok_per_s": round(data.get("eval_count", 0) / eval_s, 1) if eval_s else 0.0,
        "done_reason": data.get("done_reason", ""),
    }


def ask(question, model=MODEL, **kwargs):
    return chat([{"role": "user", "content": question}], model, **kwargs)


def metrics_line(r):
    return (f"{r['model']} · {r['prompt_tokens']}→{r['output_tokens']} ток · "
            f"{r['total_s']} с (загрузка {r['load_s']} с) · {r['tok_per_s']} ток/с")


if __name__ == "__main__":
    print("Ollama", version(), "· модели:", ", ".join(m["name"] for m in models()) or "нет")
    result = ask("Ответь одним словом: столица Франции?", temperature=0)
    print(result["text"])
    print(metrics_line(result))
