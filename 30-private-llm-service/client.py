"""CLI-чат с приватным сервисом по сети: история, стриминг, понятные ошибки лимитов.

    python3 client.py                                   # localhost:8030, ключ owner из .env
    python3 client.py --url http://192.168.0.181:8030   # с другого компьютера в сети
    python3 client.py --key sk-local-… "Один вопрос"    # без диалога

Только stdlib: urllib + разбор SSE. Ключ — --key, LLM_SERVICE_KEY или первый из LLM_SERVICE_KEYS.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

import limits


def default_key():
    if os.environ.get("LLM_SERVICE_KEY"):
        return os.environ["LLM_SERVICE_KEY"]
    keys = limits.load_keys()
    return next(iter(keys), "")


def ask(url, key, messages, max_tokens=256):
    """Стримит ответ в stdout, возвращает (текст, финальный чанк с usage/gateway)."""
    body = json.dumps({"messages": messages, "stream": True, "max_tokens": max_tokens}).encode()
    request = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {key}"})
    text, final = "", {}
    with urllib.request.urlopen(request, timeout=300) as response:
        for raw in response:
            line = raw.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            if chunk.get("error"):
                raise RuntimeError(chunk["error"]["message"])
            piece = chunk["choices"][0]["delta"].get("content", "")
            if piece:
                text += piece
                print(piece, end="", flush=True)
            if "usage" in chunk:
                final = chunk
    print()
    return text, final


def turn(url, key, history):
    started = time.monotonic()
    try:
        text, final = ask(url, key, history)
    except urllib.error.HTTPError as error:
        info = json.loads(error.read() or b"{}").get("error", {})
        hint = f" (повторить через {error.headers['Retry-After']} с)" if error.headers.get("Retry-After") else ""
        print(f"  ✗ {error.code} {info.get('type')}: {info.get('message')}{hint}")
        history.pop()
        return
    except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
        print(f"  ✗ нет связи с сервисом: {error}")
        history.pop()
        return
    history.append({"role": "assistant", "content": text})
    u, g = final.get("usage", {}), final.get("gateway", {})
    print(f"  · {u.get('prompt_tokens')}→{u.get('completion_tokens')} ток · {time.monotonic() - started:.1f} с · "
          f"{g.get('tok_per_s')} ток/с · очередь {g.get('queue_wait_s')} с")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="*")
    parser.add_argument("--url", default=os.environ.get("LLM_SERVICE_URL", "http://localhost:8030"))
    parser.add_argument("--key", default=default_key())
    args = parser.parse_args()
    history = []
    if args.question:
        history.append({"role": "user", "content": " ".join(args.question)})
        turn(args.url, args.key, history)
        return 0
    print(f"Чат с {args.url}. Пустая строка — выход, /new — новый диалог.")
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line:
            break
        if line == "/new":
            history.clear()
            continue
        history.append({"role": "user", "content": line})
        turn(args.url, args.key, history)
    return 0


if __name__ == "__main__":
    sys.exit(main())
