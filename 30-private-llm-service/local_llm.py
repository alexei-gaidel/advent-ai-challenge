"""Клиент локальной LLM через Ollama (дни 26–29), урезанный под сервис дня 30.

Шлюз server.py ходит сюда и только сюда. Ollama слушает 127.0.0.1:11434 и наружу не
открыта: из сети модель доступна лишь через шлюз с ключом и лимитами.

    brew install ollama
    ollama serve &
    ollama pull qwen2.5:3b
"""

import json
import os
import urllib.error
import urllib.request

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
if not OLLAMA.startswith("http"):
    OLLAMA = "http://" + OLLAMA
MODEL = os.environ.get("LLM_SERVICE_MODEL", "qwen2.5:3b")
EMBED_ONLY = ("bge-m3", "nomic-embed-text", "mxbai-embed-large")   # чатиться не умеют


def _open(path, body=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(OLLAMA + path, data=data,
                                     headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Ollama {error.code}: {error.read().decode()[:300]}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Ollama не отвечает на {OLLAMA} — запусти `ollama serve` "
                           f"({error.reason})") from error
    except TimeoutError as error:
        raise RuntimeError(f"Ollama не ответила за {timeout} с") from error


def _request(path, body=None, timeout=300):
    with _open(path, body, timeout) as response:
        return json.load(response)


def version():
    return _request("/api/version", timeout=5)["version"]


def models():
    """Скачанные чат-модели: имя и размер на диске."""
    found = _request("/api/tags", timeout=5).get("models", [])
    return [{"name": m["name"], "size_gb": round(m["size"] / 1e9, 2),
             "params": m.get("details", {}).get("parameter_size", "")}
            for m in found if m["name"].split(":")[0] not in EMBED_ONLY]


def check(model=MODEL):
    names = [m["name"] for m in models()]
    if model not in names and f"{model}:latest" not in names:
        raise RuntimeError(f"Модель {model} не скачана — `ollama pull {model}`")


def _body(messages, model, options, stream):
    return {"model": model, "messages": messages, "stream": stream, "options": options,
            "keep_alive": "30m"}


def _metrics(data):
    ns = 1e9
    eval_s = data.get("eval_duration", 0) / ns
    return {
        "model": data.get("model", ""),
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "output_tokens": data.get("eval_count", 0),
        "load_s": round(data.get("load_duration", 0) / ns, 2),
        "total_s": round(data.get("total_duration", 0) / ns, 2),
        "tok_per_s": round(data.get("eval_count", 0) / eval_s, 1) if eval_s else 0.0,
        "done_reason": data.get("done_reason", ""),
    }


def chat(messages, model=MODEL, options=None):
    """Один запрос к /api/chat целиком. options (num_ctx, num_predict, temperature) задаёт шлюз."""
    data = _request("/api/chat", _body(messages, model, options or {}, False))
    return {"text": data["message"]["content"], **_metrics(data)}


def chat_stream(messages, model=MODEL, options=None):
    """Стрим: отдаёт ("delta", текст) по мере генерации и в конце ("done", метрики)."""
    with _open("/api/chat", _body(messages, model, options or {}, True)) as response:
        for line in response:
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("error"):
                raise RuntimeError(f"Ollama: {data['error']}")
            piece = data.get("message", {}).get("content", "")
            if piece:
                yield "delta", piece
            if data.get("done"):
                yield "done", _metrics(data)
                return


if __name__ == "__main__":
    print("Ollama", version(), "· модели:", ", ".join(m["name"] for m in models()) or "нет")
    result = chat([{"role": "user", "content": "Ответь одним словом: столица Франции?"}],
                  options={"temperature": 0, "num_ctx": 4096})
    print(result["text"], result)
