"""Агент — отдельная сущность со своими настройками, памятью и счётчиками.

Вся логика запроса и ответа заперта здесь: web.py не собирает messages, не знает
про провайдеров и не разбирает их ответы. Он умеет только одно — попросить агента
ответить на текст и показать, что тот вернул.

В отличие от дня 6, состояние переживает перезапуск: настройки, история и счётчики
пишутся в SQLite (storage.py) и поднимаются обратно при старте через restore().
"""

import itertools
import json
import pathlib

import providers
import storage
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
    "programmer": {
        "title": "Программист",
        "prompt": (
            "Ты опытный Python-разработчик. Пиши рабочий код без лишних зависимостей, "
            "в стиле окружающего кода. Сначала код, потом короткое пояснение, что он "
            "делает и какие есть ограничения. Не объясняй очевидное."
        ),
    },
    "tester": {
        "title": "Тестировщик",
        "prompt": (
            "Ты тестировщик. По присланному коду или описанию находи, чем его можно "
            "сломать: граничные значения, пустой ввод, юникод, отрицательные числа, "
            "конкурентный доступ, отказ сети. Выдавай список тест-кейсов: вход → "
            "ожидаемое поведение. Начинай с самых вероятных отказов."
        ),
    },
    "architect": {
        "title": "Архитектор",
        "prompt": (
            "Ты архитектор ПО. Отвечай на уровне решений, а не строк: границы модулей, "
            "потоки данных, где хранится состояние, что будет узким местом при росте. "
            "Предлагай простейшее решение, которое закрывает задачу, и честно называй "
            "его недостатки. Избегай преждевременных абстракций."
        ),
    },
    "reviewer": {
        "title": "Ревьюер кода",
        "prompt": (
            "Ты делаешь код-ревью. Ищи настоящие дефекты: ошибки логики, необработанные "
            "исключения, утечки ресурсов, гонки, небезопасную работу с вводом. Для "
            "каждого замечания — где, чем грозит и как починить. Стилистические придирки "
            "не пиши. Если код в порядке, так и скажи."
        ),
    },
    "debugger": {
        "title": "Отладчик",
        "prompt": (
            "Ты помогаешь чинить баги. По traceback и описанию симптома называй наиболее "
            "вероятную причину, объясняй механизм отказа и давай минимальную правку. "
            "Если данных не хватает — скажи, какой ровно эксперимент или лог нужен."
        ),
    },
    "docs": {
        "title": "Технический писатель",
        "prompt": (
            "Ты пишешь техническую документацию. По коду делай короткое описание: что "
            "делает, как запустить, какие параметры, что вернёт, чего не умеет. Без "
            "маркетинга и воды, примеры — рабочие."
        ),
    },
}

# Свои роли пользователь заводит прямо в интерфейсе; храним рядом со скриптом,
# чтобы они пережили перезапуск сервера.
CUSTOM_ROLES_PATH = pathlib.Path(__file__).with_name("custom_roles.json")


def custom_roles():
    """Роли, созданные пользователем. Битый файл не должен ронять сервер."""
    if not CUSTOM_ROLES_PATH.exists():
        return {}
    try:
        return json.loads(CUSTOM_ROLES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def all_roles():
    """Встроенные плюс пользовательские, с пометкой происхождения для интерфейса."""
    roles = {key: {**value, "custom": False} for key, value in ROLES.items()}
    roles.update({key: {**value, "custom": True} for key, value in custom_roles().items()})
    return roles


def add_role(title, prompt):
    """Заводит новую роль и возвращает обновлённый список всех ролей."""
    title, prompt = title.strip(), prompt.strip()
    if not title or not prompt:
        raise RuntimeError("У роли должны быть и название, и описание")

    custom = custom_roles()
    key = f"custom{len(custom) + 1}"
    while key in custom or key in ROLES:
        key = f"custom{int(key[6:]) + 1}"

    custom[key] = {"title": title, "prompt": prompt}
    CUSTOM_ROLES_PATH.write_text(json.dumps(custom, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    return {"key": key, "roles": all_roles()}


def delete_role(key):
    """Удаляет пользовательскую роль. Встроенные не трогаем."""
    custom = custom_roles()
    if key not in custom:
        raise RuntimeError("Удалять можно только свои роли")
    custom.pop(key)
    CUSTOM_ROLES_PATH.write_text(json.dumps(custom, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    return {"roles": all_roles()}

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

EMPTY_STATS = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}

_counter = itertools.count(1)


class Agent:
    """Один чат: настройки, история диалога и накопленная статистика."""

    def __init__(self, agent_id, settings=None, history=None, stats=None):
        self.id = agent_id
        self.settings = dict(DEFAULTS)
        # history и stats заполнены — значит агента поднимают из базы, а не создают.
        self.history = list(history or [])
        self.stats = dict(stats or EMPTY_STATS)
        if settings:
            self.settings.update({k: v for k, v in settings.items() if k in DEFAULTS})

    def update(self, settings):
        """Меняет настройки на лету. Незнакомые ключи игнорируются."""
        for key, value in settings.items():
            if key in DEFAULTS:
                self.settings[key] = value
        self.persist()
        return self.snapshot()

    def persist(self):
        """Сохраняет настройки и счётчики. История пишется отдельно, по реплике."""
        storage.save_agent(self.id, self.settings, self.stats)

    def reset(self):
        """Забыть диалог и обнулить счётчики — и в памяти, и в базе."""
        self.history = []
        self.stats = dict(EMPTY_STATS)
        storage.clear_history(self.id)
        self.persist()
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

        # Пишем сразу после успешного ответа: падение процесса не должно
        # потерять последнюю пару реплик.
        storage.append_message(self.id, "user", text)
        storage.append_message(self.id, "assistant", result["text"])
        self.persist()

        return {**result, "agent": self.snapshot(), "data_savings": data_savings}

    def snapshot(self):
        """Состояние агента для интерфейса."""
        return {
            "id": self.id,
            "settings": self.settings,
            "stats": self.stats,
            "messages": len(self.history),
            "history": self.history,
        }


REGISTRY = {}


def restore():
    """Поднимает агентов из базы при старте сервера. Возвращает сводку для лога."""
    global _counter
    storage.init()
    restored = storage.load_all()
    for row in restored:
        REGISTRY[row["id"]] = Agent(row["id"], row["settings"], row["history"], row["stats"])

    # Счётчик продолжается с максимума в базе, иначе новый агент получил бы id
    # уже восстановленного и затёр бы его историю.
    _counter = itertools.count(storage.max_agent_number() + 1)
    return {"agents": len(restored),
            "messages": sum(len(row["history"]) for row in restored)}


def create(settings=None):
    agent = Agent(f"a{next(_counter)}", settings)
    if not settings or "name" not in settings:
        agent.settings["name"] = f"Агент {agent.id[1:]}"
    REGISTRY[agent.id] = agent
    agent.persist()
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
    """Закрыл панель — агент удаляется вместе с перепиской, чтобы не вернуться."""
    REGISTRY.pop(agent_id, None)
    storage.delete_agent(agent_id)
