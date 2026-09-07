"""Агент — отдельная сущность со своими настройками, памятью и счётчиками.

Вся логика запроса и ответа заперта здесь: web.py не собирает messages, не знает
про провайдеров и не разбирает их ответы. Он умеет только одно — попросить агента
ответить на текст и показать, что тот вернул.
"""

import itertools
import json

import providers
import toon

ROLES = {
    "assistant": {
        "title": "Ассистент",
        "prompt": "Ты полезный ассистент. Отвечай по существу и без воды.",
    },
    "editor": {
        "title": "Строгий редактор",
        "prompt": (
            "Ты строгий редактор. Возвращай текст короче и яснее, убирай канцелярит "
            "и повторы. Не добавляй ничего от себя."
        ),
    },
    "translator": {
        "title": "Переводчик",
        "prompt": (
            "Ты переводчик на английский. Возвращай только перевод, без пояснений "
            "и без исходного текста."
        ),
    },
    "critic": {
        "title": "Критик",
        "prompt": (
            "Ты критик. Находи слабые места,riски и необоснованные допущения. "
            "Отвечай списком замечаний, каждое — одной строкой."
        ),
    },
    "child": {
        "title": "Объясняю ребёнку",
        "prompt": (
            "Объясняй так, чтобы понял восьмилетний ребёнок: простые слова, "
            "короткие предложения, бытовые сравнения."
        ),
    },
    "analyst": {
        "title": "Аналитик данных",
        "prompt": (
            "Ты аналитик. Отвечай на вопросы по приложенным данным, опирайся только "
            "на них, приводи конкретные числа."
        ),
    },
}

DEFAULTS = {
    "name": "Агент",
    "model": providers.DEFAULT_MODEL,
    "role": "assistant",
    "system_prompt": ROLES["assistant"]["prompt"],
    "temperature": 0.7,
    "max_tokens": 800,
    "stop": "",
    "memory": True,
    "memory_depth": 10,
    "data": "",
    "data_format": "toon",
}

_counter = itertools.count(1)


class Agent:
    """Один чат: настройки, история диалога и накопленная статистика."""

    def __init__(self, agent_id, settings=None):
        self.id = agent_id
        self.settings = dict(DEFAULTS)
        self.history = []
        self.stats = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
        if settings:
            self.update(settings)

    def update(self, settings):
        """Меняет настройки на лету. Незнакомые ключи игнорируются."""
        for key, value in settings.items():
            if key in DEFAULTS:
                self.settings[key] = value
        return self.snapshot()

    def reset(self):
        """Забыть диалог и обнулить счётчики. Настройки остаются."""
        self.history = []
        self.stats = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
        return self.snapshot()

    def _data_block(self):
        """Данные из настроек в выбранном формате — как приставка к вопросу."""
        raw = (self.settings["data"] or "").strip()
        if not raw:
            return "", None

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Поле данных: это не JSON ({error.msg}, позиция {error.pos})")

        if self.settings["data_format"] == "toon":
            block = toon.encode(parsed)
            label = "Данные в формате TOON (заголовок вида поле[N]{колонки}, дальше строки значений)"
        else:
            block = json.dumps(parsed, ensure_ascii=False, indent=2)
            label = "Данные в формате JSON"

        return f"{label}:\n{block}\n\n", toon.savings(parsed)

    def _messages(self, question):
        """Собирает запрос: system, память нужной глубины и текущий вопрос."""
        messages = []
        if self.settings["system_prompt"].strip():
            messages.append({"role": "system", "content": self.settings["system_prompt"]})

        if self.settings["memory"]:
            depth = max(0, int(self.settings["memory_depth"]))
            messages.extend(self.history[-depth:] if depth else self.history)

        messages.append({"role": "user", "content": question})
        return messages

    def ask(self, text):
        """Принять запрос пользователя, сходить в LLM, вернуть ответ с метриками."""
        prefix, data_savings = self._data_block()
        question = prefix + text

        result = providers.call(
            self.settings["model"],
            self._messages(question),
            temperature=float(self.settings["temperature"]),
            max_tokens=int(self.settings["max_tokens"]),
            stop=[s for s in (self.settings["stop"] or "").split("|") if s.strip()],
        )

        # В историю кладём вопрос без блока данных: данные подставляются заново
        # при каждом запросе и не должны копиться в памяти диалога.
        self.history.append({"role": "user", "content": text})
        self.history.append({"role": "assistant", "content": result["text"]})

        self.stats["calls"] += 1
        self.stats["prompt_tokens"] += result["prompt_tokens"]
        self.stats["completion_tokens"] += result["completion_tokens"]
        self.stats["cost"] = round(self.stats["cost"] + (result["cost"] or 0), 6)

        return {**result, "agent": self.snapshot(), "data_savings": data_savings}

    def snapshot(self):
        """Состояние агента для интерфейса."""
        return {
            "id": self.id,
            "settings": self.settings,
            "stats": self.stats,
            "messages": len(self.history),
        }


REGISTRY = {}


def create(settings=None):
    agent = Agent(f"a{next(_counter)}", settings)
    if not settings or "name" not in settings:
        agent.settings["name"] = f"Агент {agent.id[1:]}"
    REGISTRY[agent.id] = agent
    return agent


def clone(agent_id):
    """Копия агента с теми же настройками, но с чистой историей."""
    source = get(agent_id)
    copy = create(dict(source.settings))
    copy.settings["name"] = f"{source.settings['name']} · копия"
    return copy


def get(agent_id):
    agent = REGISTRY.get(agent_id)
    if not agent:
        raise RuntimeError(f"Агент {agent_id} не найден")
    return agent


def close(agent_id):
    REGISTRY.pop(agent_id, None)
