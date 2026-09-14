"""Модель памяти агента: три слоя и роутер, который предлагает, куда что положить.

    short    краткосрочная — реплики текущего диалога, окно последних N сообщений;
    working  рабочая       — данные текущей задачи, чистятся при смене задачи;
    long     долговременная — профиль, решения, знания; общая на весь стенд.

Роутер ничего не записывает сам: он возвращает предложения, а решение принимает
пользователь. Это требование задания «явно выбирать, что и куда сохраняется».
"""

import json
import re

import providers

LAYERS = {
    "short": {
        "title": "Краткосрочная",
        "holds": "реплики текущего диалога",
        "life": "до сброса диалога; в модель уходит окно последних N сообщений",
        "scope": "агент",
    },
    "working": {
        "title": "Рабочая",
        "holds": "данные текущей задачи: цель, цифры, ограничения, промежуточные результаты",
        "life": "до кнопки «Новая задача»",
        "scope": "агент",
    },
    "long": {
        "title": "Долговременная",
        "holds": "профиль пользователя, принятые решения, знания",
        "life": "бессрочно, переживает перезапуск и смену агента",
        "scope": "общая на все агенты",
    },
}

KINDS = ("профиль", "решение", "знание", "данные задачи")

ROUTER_PROMPT = (
    "Ты раскладываешь информацию из реплики пользователя по слоям памяти агента.\n\n"
    "СЛОИ:\n"
    "- working (рабочая) — то, что относится к ТЕКУЩЕЙ задаче и перестанет быть нужным, "
    "когда задача закончится: цель задачи, бюджет, сроки, объёмы, технические условия, "
    "промежуточные результаты.\n"
    "- long (долговременная) — то, что останется верным и после этой задачи: факты "
    "о самом пользователе (имя, город, роль, предпочтения), принятые решения, "
    "устойчивые знания о проекте или команде.\n\n"
    "Реплики без новых фактов (вопросы, уточнения, благодарности) не дают предложений — "
    "верни пустой список.\n"
    "Ничего не выдумывай: значение должно дословно следовать из реплики.\n\n"
    'Верни ТОЛЬКО JSON-массив без markdown, вида: [{"layer": "long", '
    '"kind": "профиль", "key": "имя", "value": "Алекс"}]\n'
    f'Поле kind — одно из: {", ".join(KINDS)}.\n'
    "Ключ — короткое существительное на русском (имя, город, бюджет, срок, стек).\n\n"
    "УЖЕ В РАБОЧЕЙ ПАМЯТИ:\n<<WORKING>>\n\n"
    "УЖЕ В ДОЛГОВРЕМЕННОЙ:\n<<LONG>>\n\n"
    "РЕПЛИКА ПОЛЬЗОВАТЕЛЯ:\n<<TEXT>>"
)


def route(model, text, working, long_memory):
    """Спрашивает у модели, что из реплики в какой слой положить.

    Возвращает (список предложений, расход токенов). Ничего не сохраняет.
    """
    result = providers.call(
        model,
        [{"role": "user", "content": (
            ROUTER_PROMPT
            .replace("<<WORKING>>", _as_lines(working) or "(пусто)")
            .replace("<<LONG>>", _as_lines({k: v["value"]
                                            for k, v in long_memory.items()}) or "(пусто)")
            .replace("<<TEXT>>", text))}],
        temperature=0.0,
        max_tokens=500,
    )
    tokens = result["prompt_tokens"] + result["completion_tokens"]

    raw = re.sub(r"^```(?:json)?|```$", "", result["text"].strip(), flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return [], tokens          # модель ответила не JSON — предложений просто нет
    if not isinstance(parsed, list):
        return [], tokens

    proposals = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        layer = item.get("layer")
        key = str(item.get("key", "")).strip()
        value = str(item.get("value", "")).strip()
        if layer in ("working", "long") and key and value:
            proposals.append({"layer": layer, "kind": item.get("kind", ""),
                              "key": key, "value": value})
    return proposals, tokens


def _as_lines(pairs):
    return "\n".join(f"{key}: {value}" for key, value in pairs.items())


def blocks(working, long_memory, use_working=True, use_long=True):
    """Текстовые блоки слоёв для промпта плюс их размеры для интерфейса.

    Размер в символах показывает вклад каждого слоя в запрос — по нему видно,
    во что обходится включённая память.
    """
    result = {"messages": [], "sizes": {"working": 0, "long": 0}}

    if use_long and long_memory:
        by_kind = {}
        for key, item in long_memory.items():
            by_kind.setdefault(item["kind"] or "знание", []).append(f"{key}: {item['value']}")
        text = "Долговременная память (профиль, решения, знания):\n" + "\n".join(
            f"[{kind}] " + "; ".join(values) for kind, values in by_kind.items())
        result["messages"].append({"role": "system", "content": text})
        result["sizes"]["long"] = len(text)

    if use_working and working:
        text = "Рабочая память (данные текущей задачи):\n" + _as_lines(working)
        result["messages"].append({"role": "system", "content": text})
        result["sizes"]["working"] = len(text)

    return result
