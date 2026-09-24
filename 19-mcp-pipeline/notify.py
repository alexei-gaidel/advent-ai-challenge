"""Доставка сводки: Telegram через Bot API.

Один HTTP-запрос, без библиотек. Если токен не задан, сводка пишется в файл —
чтобы демонстрация и расписание работали у любого, кто склонировал репозиторий.
"""

import json
import pathlib
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
OUTBOX = HERE / "data" / "outbox.log"


def find_env_file():
    """Ищет .env рядом со скриптом и выше по дереву — общий ключ лежит в корне репо."""
    for folder in HERE.parents:
        candidate = folder / ".env"
        if candidate.exists():
            return candidate
    return None


def get_setting(name):
    """Читает переменную из окружения или из .env. В Actions придёт из окружения."""
    import os

    value = os.environ.get(name)
    if value:
        return value.strip()

    env_file = find_env_file()
    if env_file:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def send(text):
    """Отправляет текст. Возвращает описание того, что произошло."""
    token = get_setting("TELEGRAM_BOT_TOKEN")
    chat_id = get_setting("TELEGRAM_CHAT_ID")

    if not (token and chat_id):
        OUTBOX.parent.mkdir(parents=True, exist_ok=True)
        with OUTBOX.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n" + "-" * 40 + "\n")
        return f"telegram не настроен, сводка записана в {OUTBOX.name}"

    payload = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "advent-ai-challenge/1.0"},
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            answer = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:160]
        raise RuntimeError(f"telegram ответил {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"сеть недоступна: {error.reason}") from error
    except TimeoutError as error:
        raise RuntimeError("таймаут: telegram не ответил вовремя") from error

    if not answer.get("ok"):
        raise RuntimeError(f"telegram отказал: {answer.get('description')}")
    return f"отправлено в telegram, сообщение {answer['result']['message_id']}"


def configured():
    return bool(get_setting("TELEGRAM_BOT_TOKEN") and get_setting("TELEGRAM_CHAT_ID"))


if __name__ == "__main__":
    import sys

    if "--test" in sys.argv:
        # Проверка доставки: убеждаемся, что токен и chat_id рабочие,
        # не дожидаясь настоящих изменений в продаже билетов.
        print("telegram настроен:", configured())
        print(send("Проверка связи: мониторинг билетов на матч Россия — Намибия "
                   "настроен и работает."))
