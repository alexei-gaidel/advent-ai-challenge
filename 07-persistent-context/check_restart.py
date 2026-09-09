"""Проверка из задания: диалог → перезапуск приложения → диалог продолжается.

Скрипт сам поднимает сервер, ведёт диалог, убивает процесс, показывает содержимое
базы, поднимает сервер заново и проверяет, что агент помнит сказанное до перезапуска.

    python3 check_restart.py
"""

import json
import pathlib
import sqlite3
import subprocess
import sys
import time
import urllib.request

import storage

BASE = "http://127.0.0.1:8007"
HERE = pathlib.Path(__file__).parent


def api(path, body=None, method="POST"):
    data = json.dumps(body or {}).encode() if method == "POST" else None
    request = urllib.request.Request(BASE + path, data=data,
                                     headers={"Content-Type": "application/json"},
                                     method=method)
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def start_server():
    process = subprocess.Popen([sys.executable, "web.py"], cwd=HERE,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(40):
        time.sleep(0.25)
        try:
            urllib.request.urlopen(BASE + "/", timeout=2)
            return process
        except Exception:
            continue
    raise RuntimeError("сервер не поднялся")


def step(number, title):
    print(f"\n{'=' * 66}\n{number}. {title}\n{'=' * 66}")


def main():
    if storage.DB_PATH.exists():
        storage.DB_PATH.unlink()          # начинаем с чистой базы, чтобы проверка была честной
    for suffix in ("-wal", "-shm"):
        extra = storage.DB_PATH.with_name(storage.DB_PATH.name + suffix)
        extra.unlink(missing_ok=True)

    step(1, "Первый запуск: начинаем диалог")
    server = start_server()
    agent_id = api("/api/agents", {"settings": {
        "name": "Память", "max_tokens": 120,
        "system_prompt": "Ты ассистент. Отвечай коротко."}})["id"]
    print(f"   создан агент {agent_id}")

    reply = api(f"/api/agents/{agent_id}/ask",
                {"text": "Запомни: меня зовут Алекс, я пишу на Python."})
    print(f"   → Запомни: меня зовут Алекс, я пишу на Python.\n   ← {reply['text'][:110]}")

    reply = api(f"/api/agents/{agent_id}/ask", {"text": "Как меня зовут? Одним словом."})
    print(f"   → Как меня зовут?\n   ← {reply['text'][:110]}")

    step(2, "Выключаем приложение")
    server.terminate()
    server.wait(timeout=10)
    print("   процесс сервера остановлен")

    step(3, "Что осталось на диске")
    with sqlite3.connect(storage.DB_PATH) as connection:
        rows = connection.execute(
            "SELECT agent_id, role, substr(content, 1, 52) FROM messages ORDER BY id"
        ).fetchall()
    print(f"   файл {storage.DB_PATH.name}, {storage.DB_PATH.stat().st_size} байт")
    for agent, role, content in rows:
        print(f"   {agent}  {role:<9} {content}")

    step(4, "Запускаем заново и продолжаем тот же диалог")
    server = start_server()
    try:
        state = api("/api/state", method="GET")
        restored = state["agents"][0]
        print(f"   поднято агентов: {state['db']['agents']}, "
              f"сообщений: {state['db']['messages']}")
        print(f"   имя агента после перезапуска: {restored['settings']['name']}, "
              f"вызовов в счётчике: {restored['stats']['calls']}")

        # В этой сессии имя агенту не называли — он может знать его только из базы.
        reply = api(f"/api/agents/{agent_id}/ask",
                    {"text": "Как меня зовут и на чём я пишу? Одной строкой."})
        print(f"   → Как меня зовут и на чём я пишу?\n   ← {reply['text'][:140]}")

        remembered = "лекс" in reply["text"] and "ython" in reply["text"].lower()
        step(5, "Итог")
        print(f"   агент помнит имя и язык после перезапуска: "
              f"{'ДА' if remembered else 'НЕТ'}")
        print(f"   сообщений в базе сейчас: {storage.stats()['messages']}")
        return 0 if remembered else 1
    finally:
        server.terminate()


if __name__ == "__main__":
    sys.exit(main())
