"""Модель памяти агента (задание 11) плюс профиль пользователя поверх неё.

    short    краткосрочная — реплики текущего диалога, окно последних N сообщений;
    working  рабочая       — данные текущей задачи, чистятся при смене задачи;
    long     долговременная — решения и знания о проекте; общая на весь стенд;
    profile  профиль       — кто пользователь и как ему отвечать; профилей несколько.

Факты о пользователе и его предпочтения раньше лежали в долговременной памяти
с пометкой «профиль». Теперь у них своё место: роутер предлагает положить их
в активный профиль. Записи по-прежнему только по решению пользователя.
"""

import json
import re

import profiles
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
        "holds": "принятые решения, знания о проекте и команде",
        "life": "бессрочно, переживает перезапуск и смену агента",
        "scope": "общая на все агенты",
    },
    "profile": {
        "title": "Профиль",
        "holds": "кто пользователь, стиль, формат, ограничения, факты о нём",
        "life": "бессрочно; агент выбирает, чей профиль подключить",
        "scope": "общий для всех агентов с этим профилем",
    },
}

LONG_KINDS = ("решение", "знание")

ROUTER_PROMPT = (
    "Ты раскладываешь информацию из реплики пользователя по памяти агента.\n\n"
    "КУДА:\n"
    "- working (рабочая) — то, что относится к ТЕКУЩЕЙ задаче и перестанет быть нужным, "
    "когда задача закончится: цель, бюджет, сроки, объёмы, технические условия. "
    "kind: «данные задачи».\n"
    "- long (долговременная) — принятые решения и устойчивые знания о проекте, "
    "команде, системе. kind: «решение» или «знание».\n"
    "- profile (профиль пользователя) — всё о САМОМ пользователе: факты о нём "
    "(kind «факт»: роль, город, стек, опыт) и пожелания, КАК ему отвечать — "
    "kind «стиль» (тон, обращение, объём), «формат» (списки, код, структура), "
    "«ограничения» (чего не делать). Просьба вроде «пиши короче» или «без эмодзи» — "
    "это профиль, а не задача.\n\n"
    "Реплики без новых фактов и пожеланий (вопросы, благодарности) не дают предложений — "
    "верни пустой список.\nНичего не выдумывай: значение должно дословно следовать из реплики.\n\n"
    'Верни ТОЛЬКО JSON-массив без markdown, вида: [{"layer": "profile", "kind": "факт", '
    '"key": "город", "value": "Казань"}, {"layer": "profile", "kind": "ограничения", '
    '"key": "эмодзи", "value": "без эмодзи"}]\n'
    "Ключ — короткое существительное на русском.\n\n"
    "УЖЕ В РАБОЧЕЙ ПАМЯТИ:\n<<WORKING>>\n\n"
    "УЖЕ В ДОЛГОВРЕМЕННОЙ:\n<<LONG>>\n\n"
    "УЖЕ В ПРОФИЛЕ:\n<<PROFILE>>\n\n"
    "РЕПЛИКА ПОЛЬЗОВАТЕЛЯ:\n<<TEXT>>"
)

VALID_KINDS = {
    "working": ("данные задачи",),
    "long": LONG_KINDS,
    "profile": profiles.KINDS,
}


def route(model, text, working, long_memory, profile):
    """Спрашивает у модели, что из реплики куда положить.

    Возвращает (список предложений, расход токенов). Ничего не сохраняет.
    """
    result = providers.call(
        model,
        [{"role": "user", "content": (
            ROUTER_PROMPT
            .replace("<<WORKING>>", _as_lines(working) or "(пусто)")
            .replace("<<LONG>>", _as_lines({k: v["value"]
                                            for k, v in long_memory.items()}) or "(пусто)")
            .replace("<<PROFILE>>", profiles.block(profile) or "(не подключён)")
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
        kind = str(item.get("kind", "")).strip()
        key = str(item.get("key", "")).strip()
        value = str(item.get("value", "")).strip()
        if layer not in VALID_KINDS or not key or not value:
            continue
        if kind not in VALID_KINDS[layer]:
            kind = VALID_KINDS[layer][0]
        proposals.append({"layer": layer, "kind": kind, "key": key, "value": value})
    return proposals, tokens


def _as_lines(pairs):
    return "\n".join(f"{key}: {value}" for key, value in pairs.items())


def blocks(working, long_memory, profile, use_working=True, use_long=True, use_profile=True):
    """Текстовые блоки слоёв для промпта плюс их размеры для интерфейса.

    Профиль идёт первым: это «как отвечать», и модель лучше держит такие
    инструкции, когда они стоят рядом с системным промптом.
    """
    result = {"messages": [], "sizes": {"working": 0, "long": 0, "profile": 0}}

    if use_profile:
        text = profiles.block(profile)
        if text:
            result["messages"].append({"role": "system", "content": text})
            result["sizes"]["profile"] = len(text)

    if use_long and long_memory:
        by_kind = {}
        for key, item in long_memory.items():
            by_kind.setdefault(item["kind"] or "знание", []).append(f"{key}: {item['value']}")
        text = "Долговременная память (решения, знания):\n" + "\n".join(
            f"[{kind}] " + "; ".join(values) for kind, values in by_kind.items())
        result["messages"].append({"role": "system", "content": text})
        result["sizes"]["long"] = len(text)

    if use_working and working:
        text = "Рабочая память (данные текущей задачи):\n" + _as_lines(working)
        result["messages"].append({"role": "system", "content": text})
        result["sizes"]["working"] = len(text)

    return result
