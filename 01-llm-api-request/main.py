"""Минимальный клиент к LLM (DeepSeek) через HTTP API.

Запуск:
    ключ берётся из .env в корне репозитория (см. .env.example)
    python3 main.py "Привет, кто ты?"     # разовый вопрос
    python3 main.py                        # интерактивный чат
"""

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"


def find_env_file():
    """Ищет .env рядом со скриптом и выше по дереву — общий ключ лежит в корне репо."""
    for folder in pathlib.Path(__file__).resolve().parents:
        candidate = folder / ".env"
        if candidate.exists():
            return candidate
    return None


def get_api_key():
    """Берёт ключ из переменной окружения, иначе из локального файла .env."""
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


def ask(messages):
    """Отправляет историю сообщений в LLM и возвращает текст ответа."""
    api_key = get_api_key()

    payload = json.dumps({"model": MODEL, "messages": messages}).encode()
    request = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        sys.exit(f"Ошибка API {error.code}: {error.read().decode()}")
    except urllib.error.URLError as error:
        sys.exit(f"Сеть недоступна: {error.reason}")

    return data["choices"][0]["message"]["content"]


def main():
    if len(sys.argv) > 1:
        print(ask([{"role": "user", "content": " ".join(sys.argv[1:])}]))
        return

    messages = [{"role": "system", "content": "Ты полезный ассистент. Отвечай кратко."}]
    print("Чат с LLM. Пустая строка или Ctrl+C — выход.\n")
    while True:
        try:
            question = input("Вы: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        messages.append({"role": "user", "content": question})
        answer = ask(messages)
        messages.append({"role": "assistant", "content": answer})
        print(f"\nLLM: {answer}\n")


if __name__ == "__main__":
    main()
