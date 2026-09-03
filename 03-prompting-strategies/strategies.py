"""День 3 — четыре способа решить одну задачу через API.

Прямой ответ, «решай пошагово», промпт, составленный самой моделью, и консилиум
экспертов. Плюс пятый вызов — судья, который сравнивает ответы и выбирает лучший.
Интерфейс — в web.py.
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

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

# Аналитическая задача: требует декомпозиции, явных допущений и арифметики.
DEFAULT_QUESTION = (
    "Оцени, сколько чашек кофе в день продаёт средняя кофейня в центре крупного "
    "города и какова её месячная выручка. Приведи итоговые числа."
)


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


def ask(user_prompt, system=None):
    """Один вызов LLM. Возвращает текст, число токенов и время ответа."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_prompt})

    request = urllib.request.Request(
        API_URL,
        data=json.dumps({"model": MODEL, "messages": messages}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {get_api_key()}",
        },
    )

    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Ошибка API {error.code}: {error.read().decode()}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Сеть недоступна: {error.reason}") from error
    except TimeoutError as error:
        raise RuntimeError("Таймаут: модель не ответила вовремя") from error

    return {
        "text": data["choices"][0]["message"]["content"].strip(),
        "tokens": data["usage"]["completion_tokens"],
        "seconds": round(time.monotonic() - started, 1),
    }


# --- Четыре способа ------------------------------------------------------------
# У всех одинаковая сигнатура (question) -> dict, чтобы web.py вызывал их единообразно.


def solve_direct(question):
    """1. Прямой ответ: только вопрос, никаких дополнительных инструкций."""
    result = ask(question)
    return {**result, "calls": 1, "extra": {}}


def solve_step_by_step(question):
    """2. Та же задача плюс инструкция рассуждать пошагово."""
    prompt = (
        f"{question}\n\n"
        "Решай пошагово: разбей задачу на последовательные шаги, выполни каждый "
        "по порядку, показывай промежуточные расчёты. В конце дай итоговые числа."
    )
    result = ask(prompt)
    return {**result, "calls": 1, "extra": {}}


def solve_meta_prompt(question):
    """3. Модель сначала пишет промпт для решения, потом решает по нему.

    Два вызова API: сгенерированный промпт возвращаем в extra, он самое интересное
    в этом способе — видно, что модель считает хорошей постановкой задачи.
    """
    meta = ask(
        "Ты инженер промптов. Составь промпт, который заставит языковую модель решить "
        "задачу ниже максимально точно и обоснованно. Верни только текст промпта, "
        "без пояснений и без решения самой задачи.\n\n"
        f"Задача: {question}"
    )
    generated_prompt = meta["text"]

    solution = ask(generated_prompt)
    return {
        "text": solution["text"],
        "tokens": meta["tokens"] + solution["tokens"],
        "seconds": round(meta["seconds"] + solution["seconds"], 1),
        "calls": 2,
        "extra": {"prompt": generated_prompt},
    }


EXPERTS_SYSTEM = (
    "Ты ведёшь консилиум из трёх экспертов, каждый разбирает задачу со своей стороны.\n\n"
    "1. АНАЛИТИК — раскладывает задачу на составляющие и явно перечисляет допущения "
    "с числами (проходимость, средний чек, часы работы и прочее).\n"
    "2. ИНЖЕНЕР — делает расчёт по допущениям аналитика, показывает формулы и "
    "проверяет арифметику.\n"
    "3. КРИТИК — ищет слабые места: завышенные допущения, пропущенные факторы, "
    "ошибки в расчёте, и предлагает поправки.\n\n"
    "Выведи мнение каждого эксперта под его заголовком, а затем блок ИТОГ: "
    "согласованный ответ с итоговыми числами и диапазоном неопределённости."
)


def solve_experts(question):
    """4. Консилиум экспертов, заданный через system_prompt."""
    result = ask(question, system=EXPERTS_SYSTEM)
    return {**result, "calls": 1, "extra": {}}


STRATEGIES = {
    "direct": {"title": "1. Прямой ответ", "note": "вопрос без инструкций", "run": solve_direct},
    "steps": {"title": "2. Решай пошагово", "note": "инструкция рассуждать по шагам", "run": solve_step_by_step},
    "meta": {"title": "3. Промпт от модели", "note": "модель пишет промпт, потом решает по нему", "run": solve_meta_prompt},
    "experts": {"title": "4. Консилиум экспертов", "note": "аналитик, инженер, критик в system_prompt", "run": solve_experts},
}


# --- Судья ---------------------------------------------------------------------

JUDGE_FORMAT = (
    'Верни только JSON без markdown-обёртки, ровно в таком виде: '
    '{"winner": "B", "ranking": ["B", "D", "A", "C"], "comment": "одно-два предложения"}'
)


def judge(question, answers):
    """5-й вызов: сравнивает ответы и выбирает лучший.

    answers — список {"strategy": ключ, "text": ответ}. Ответы обезличиваются метками
    A/B/C/D и перемешиваются, чтобы судья не выбирал по названию способа или по позиции.
    """
    shuffled = list(answers)
    random.shuffle(shuffled)
    labels = {}
    blocks = []
    for index, answer in enumerate(shuffled):
        label = chr(ord("A") + index)
        labels[label] = answer["strategy"]
        blocks.append(f"=== Ответ {label} ===\n{answer['text']}")

    prompt = (
        "Ты строгий судья. Ниже задача и несколько ответов на неё от разных решателей.\n"
        "Оцени по критериям: явность допущений, арифметическая непротиворечивость, "
        "реалистичность итоговых чисел, полнота декомпозиции.\n"
        "Выбери лучший ответ и упорядочи все от лучшего к худшему.\n\n"
        f"ЗАДАЧА: {question}\n\n" + "\n\n".join(blocks) + "\n\n" + JUDGE_FORMAT
    )

    result = ask(prompt)
    verdict = {"tokens": result["tokens"], "seconds": result["seconds"], "raw": result["text"]}

    text = result["text"].strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError:
        return {**verdict, "winner": None, "ranking": [], "comment": result["text"]}

    # Возвращаем метки обратно в ключи способов, чтобы интерфейс знал, кого подсветить.
    return {
        **verdict,
        "winner": labels.get(parsed.get("winner")),
        "ranking": [labels[label] for label in parsed.get("ranking", []) if label in labels],
        "comment": deanonymize(parsed.get("comment", ""), labels),
    }


# Кириллические двойники латинских букв: судья пишет по-русски и легко их путает.
LOOKALIKE = {"A": "АA", "B": "ВB", "C": "СC", "D": "D"}


def deanonymize(comment, labels):
    """Меняет в комментарии судьи метки A/B/C/D на названия способов."""
    for label, strategy in labels.items():
        # Буква целым словом: «Ответ C лучший» заменяем, «Cost» и «Абрикос» — нет.
        pattern = r"(?<![^\W\d_])[" + LOOKALIKE[label] + r"](?![^\W\d_])"
        title = STRATEGIES[strategy]["title"]
        comment = re.sub(pattern, f"«{title}»", comment)
    return comment
