"""Профили пользователей: кто спрашивает и как ему отвечать.

Профиль — слой поверх модели памяти из задания 11. Долговременная память хранит
решения и знания о проекте, профиль — предпочтения человека: стиль, формат,
ограничения и факты о нём. Профилей может быть несколько; агент привязан к одному
и подмешивает его в каждый запрос, поэтому пользователь ничего не повторяет.
"""

import json
import re

import providers
import storage

# Поля предпочтений. Порядок — порядок в промпте и в интерфейсе.
FIELDS = {
    "about": {"title": "Кто это", "hint": "роль и опыт — «тимлид, десять лет в Python»"},
    "style": {"title": "Стиль", "hint": "тон и обращение — «на ты, коротко, без воды»"},
    "format": {"title": "Формат", "hint": "как оформлять — «списки, код без пояснений»"},
    "limits": {"title": "Ограничения", "hint": "чего не делать — «без эмодзи, без англицизмов»"},
}

# Роутер может предложить дописать в профиль факт или одно из предпочтений.
KIND_FIELD = {"стиль": "style", "формат": "format", "ограничения": "limits"}
KINDS = ("факт",) + tuple(KIND_FIELD)

# Стартовые профили — чтобы стенд с первого запуска показывал разницу в ответах.
PRESETS = [
    {
        "name": "Алекс",
        "about": "тимлид, десять лет в Python, знает термины",
        "style": "на «ты», коротко и по делу, без вступлений и итогов",
        "format": "маркированные списки; если нужен код — только код, без пояснений",
        "limits": "без эмодзи, без англицизмов там, где есть русское слово",
        "facts": {"город": "Казань", "стек": "Python, PostgreSQL"},
    },
    {
        "name": "Маша",
        "about": "джун, три месяца в разработке, терминов боится",
        "style": "на «ты», дружелюбно и подробно, с бытовыми сравнениями",
        "format": "сплошной текст без списков, каждый термин — сразу с пояснением в скобках",
        "limits": "не предполагать, что что-то «очевидно»; не более одной новой идеи на абзац",
        "facts": {"учит": "Python по курсу", "цель": "первая работа"},
    },
    {
        "name": "Игорь Петрович",
        "about": "руководитель направления, не программист, принимает решения о бюджете",
        "style": "на «вы», деловой тон, сразу выводы и риски",
        "format": "сначала одна строка «итого», потом до трёх пунктов; цифры и сроки — обязательно",
        "limits": "без кода и технических терминов; без слов «просто» и «легко»",
        "facts": {"отвечает за": "продукт «Аренда велосипедов»", "горизонт": "квартал"},
    },
]


def empty(name=""):
    return {"name": name, "about": "", "style": "", "format": "", "limits": "", "facts": {}}


def block(profile):
    """System-сообщение с профилем. Именно оно делает персонализацию автоматической:
    пользователь ничего не пишет о себе в вопросе — агент и так знает."""
    if not profile:
        return ""
    lines = [f"Имя: {profile['name']}"] if profile.get("name") else []
    for key, field in FIELDS.items():
        if profile.get(key):
            lines.append(f"{field['title']}: {profile[key]}")
    if profile.get("facts"):
        lines.append("Факты: " + "; ".join(f"{k} — {v}" for k, v in profile["facts"].items()))
    if not lines:
        return ""
    return ("Профиль пользователя. Подстраивай под него каждый ответ, даже если в вопросе "
            "об этом не сказано: обращение, тон, форму, объём, лексику.\n" + "\n".join(lines))


# --- хранилище ------------------------------------------------------------------

def load_all():
    """Все профили, при первом запуске — заведём стартовые."""
    profiles = storage.load_profiles()
    if not profiles:
        for preset in PRESETS:
            create(preset)
        profiles = storage.load_profiles()
    return profiles


def get(profile_id):
    profile = storage.load_profiles().get(profile_id)
    if not profile:
        raise RuntimeError(f"Профиль {profile_id} не найден")
    return profile


def create(fields=None):
    profile = {**empty(), **(fields or {})}
    if not profile["name"].strip():
        raise RuntimeError("У профиля должно быть имя")
    profile["id"] = f"u{storage.max_profile_number() + 1}"
    storage.save_profile(profile["id"], profile)
    return profile


def update(profile_id, fields):
    profile = get(profile_id)
    for key, value in fields.items():
        if key in FIELDS or key == "name":
            profile[key] = str(value).strip()
        elif key == "facts" and isinstance(value, dict):
            profile["facts"] = {str(k).strip(): str(v).strip() for k, v in value.items()
                                if str(k).strip() and str(v).strip()}
    storage.save_profile(profile_id, profile)
    return profile


def delete(profile_id):
    storage.delete_profile(profile_id)


MERGE_PROMPT = (
    "Это поле «<<FIELD>>» из профиля пользователя — как ему отвечать:\n<<CURRENT>>\n\n"
    "Пользователь добавил новое пожелание:\n<<NEW>>\n\n"
    "Перепиши поле так, чтобы новое пожелание было учтено, а противоречащие ему старые — "
    "убраны. Остальное сохрани дословно. Верни только текст поля, одной строкой, "
    "без пояснений и кавычек."
)


def merge(model, field, current, value):
    """Новое предпочтение не дописывается, а сливается со старыми.

    Иначе «без списков» встанет рядом с «маркированные списки», и модель выберет
    любое из двух. Без модели (model=None) — просто дописываем.
    """
    if not current:
        return value
    if value.lower() in current.lower():
        return current
    if not model:
        return f"{current}; {value}"
    result = providers.call(
        model,
        [{"role": "user", "content": MERGE_PROMPT
          .replace("<<FIELD>>", FIELDS[field]["title"])
          .replace("<<CURRENT>>", current)
          .replace("<<NEW>>", value)}],
        temperature=0.0,
        max_tokens=200,
    )
    text = result["text"].strip().strip('"«»').replace("\n", " ")
    return text or f"{current}; {value}"


def apply(profile_id, kind, key, value, model=None):
    """Запись из роутера или руками: факт — в facts, предпочтение — в своё поле."""
    profile = get(profile_id)
    key, value = key.strip(), value.strip()
    if kind == "факт":
        if not key or not value:
            raise RuntimeError("Факту нужны ключ и значение")
        profile["facts"][key] = value
    elif kind in KIND_FIELD:
        if not value:
            raise RuntimeError("Предпочтению нужно значение")
        field = KIND_FIELD[kind]
        profile[field] = merge(model, field, profile[field], value)
    else:
        raise RuntimeError(f"В профиль можно записать: {', '.join(KINDS)}")
    storage.save_profile(profile_id, profile)
    return profile


def forget(profile_id, key):
    profile = get(profile_id)
    profile["facts"].pop(key, None)
    storage.save_profile(profile_id, profile)
    return profile


# --- проверка: что из профиля ассистент учёл сам ---------------------------------

CHECK_PROMPT = (
    "Ты проверяешь, учёл ли ассистент профиль пользователя. Пользователь задал нейтральный "
    "вопрос и ничего не просил о форме ответа — всё, что совпало с профилем, ассистент "
    "сделал сам.\n\n"
    "Разбей предпочтения из профиля на отдельные требования (каждое проверяемое: "
    "обращение, тон, объём, списки, эмодзи, термины, код, структура и т. п.) и для каждого "
    "скажи, выполнено ли оно в ответе. Факты о пользователе тоже требование: "
    "использованы ли они, когда уместно (если неуместно — считай выполненным).\n\n"
    'Верни ТОЛЬКО JSON-массив без markdown: [{"requirement": "на ты", "ok": true, '
    '"evidence": "«посмотри», «тебе»"}]\n\n'
    "ПРОФИЛЬ:\n<<PROFILE>>\n\nВОПРОС:\n<<QUESTION>>\n\nОТВЕТ АССИСТЕНТА:\n<<ANSWER>>"
)


def check(model, profile, question, answer):
    """Судья: список требований профиля с пометкой «выполнено / нет»."""
    result = providers.call(
        model,
        [{"role": "user", "content": CHECK_PROMPT
          .replace("<<PROFILE>>", block(profile) or "(пусто)")
          .replace("<<QUESTION>>", question)
          .replace("<<ANSWER>>", answer)}],
        temperature=0.0,
        max_tokens=800,
    )
    raw = re.sub(r"^```(?:json)?|```$", "", result["text"].strip(), flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = []
    items = [{"requirement": str(i.get("requirement", "")), "ok": bool(i.get("ok")),
              "evidence": str(i.get("evidence", ""))}
             for i in parsed if isinstance(i, dict) and i.get("requirement")]
    return items, result["prompt_tokens"] + result["completion_tokens"]


EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def measure(text):
    """Дешёвые метрики без LLM: по ним разница между профилями видна и без судьи."""
    words = re.findall(r"[A-Za-zА-Яа-яЁё]+", text)
    latin = sum(1 for w in words if re.match(r"[A-Za-z]", w))
    lowered = f" {text.lower()} "
    you = "ты" if re.search(r"\b(ты|тебе|тебя|твой|твоя|твои|посмотри|попробуй)\b", lowered) else ""
    if re.search(r"\b(вы|вам|вас|ваш|ваша|ваши|посмотрите|попробуйте)\b", lowered):
        you = "вы" if not you else "ты/вы"
    return {
        "chars": len(text),
        "bullets": len(re.findall(r"^\s*(?:[-*•]|\d+[.)])\s", text, flags=re.MULTILINE)),
        "emoji": len(EMOJI.findall(text)),
        "latin": round(100 * latin / len(words)) if words else 0,
        "code": "```" in text,
        "address": you or "—",
    }
