"""Агент с явной моделью памяти из трёх слоёв.

Краткосрочная память — реплики диалога, рабочая — данные текущей задачи,
долговременная — профиль, решения и знания (общая на все агенты). Слои хранятся
раздельно (storage.py), описаны в memory.py и включаются независимо друг от друга.

Роутер после каждой реплики предлагает, что куда положить, но ничего не пишет сам:
предложения ждут решения пользователя — принять, переложить в другой слой или отклонить.
"""

import itertools
import json
import pathlib

import memory
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
    # Выключатели слоёв памяти — ими и проверяется влияние каждого на ответы.
    "use_short": True,
    "use_working": True,
    "use_long": True,
    # Окно краткосрочной памяти.
    "keep_last": 6,
    # Название текущей задачи: меняется вместе с очисткой рабочей памяти.
    "task": "",
    # Роутер можно выключить и раскладывать память только руками.
    "router": True,
    "data": "",
    "data_format": "toon",
}

EMPTY_STATS = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0,
               # Роутер — отдельные вызовы LLM, считаем их отдельно.
               "router_calls": 0, "router_tokens": 0}

_counter = itertools.count(1)


class Agent:
    """Один чат: диалог, три слоя памяти и очередь предложений роутера."""

    def __init__(self, agent_id, settings=None, restored=None):
        self.id = agent_id
        self.settings = dict(DEFAULTS)
        restored = restored or {}

        self.history = list(restored.get("history") or [])
        self.working = dict(restored.get("working") or {})
        self.proposals = list(restored.get("proposals") or [])
        self.stats = {**EMPTY_STATS, **(restored.get("stats") or {})}
        self._proposal_counter = itertools.count(len(self.proposals) + 1)

        if settings:
            self.settings.update({k: v for k, v in settings.items() if k in DEFAULTS})

    # --- слои ------------------------------------------------------------------

    @property
    def long(self):
        """Долговременная память общая, поэтому всегда читается из хранилища."""
        return storage.load_long()

    def remember(self, layer, key, value, kind="знание"):
        """Ручная запись в память, минуя роутер."""
        key, value = key.strip(), value.strip()
        if not key or not value:
            raise RuntimeError("Нужны и ключ, и значение")

        if layer == "working":
            self.working[key] = value
            storage.save_working(self.id, key, value)
        elif layer == "long":
            storage.save_long(key, value, kind or "знание", self.id)
        else:
            raise RuntimeError("Записать можно только в рабочую или долговременную память")
        return self.snapshot()

    def forget(self, layer, key):
        if layer == "working":
            self.working.pop(key, None)
            storage.delete_working(self.id, key)
        elif layer == "long":
            storage.delete_long(key)
        return self.snapshot()

    def decide(self, proposal_id, action, layer=None):
        """Решение пользователя по предложению роутера: принять, переложить, отклонить."""
        proposal = next((p for p in self.proposals if p["id"] == proposal_id), None)
        if not proposal:
            raise RuntimeError("Предложение не найдено")

        if action == "reject":
            storage.update_proposal(proposal_id, "rejected")
        else:
            target = layer or proposal["layer"]
            self.remember(target, proposal["key"], proposal["value"], proposal.get("kind"))
            storage.update_proposal(proposal_id, "accepted", target)

        self.proposals = [p for p in self.proposals if p["id"] != proposal_id]
        return self.snapshot()

    def new_task(self, title=""):
        """Смена задачи: рабочая память обнуляется, долговременная остаётся."""
        self.working = {}
        storage.clear_working(self.id)
        self.settings["task"] = title
        self.persist()
        return self.snapshot()

    # --- состояние -------------------------------------------------------------

    def update(self, settings):
        for key, value in settings.items():
            if key in DEFAULTS:
                self.settings[key] = value
        self.persist()
        return self.snapshot()

    def persist(self):
        storage.save_agent(self.id, self.settings, self.stats)

    def reset(self):
        """Сброс агента: диалог, рабочая память и предложения. Долгая память общая — жива."""
        self.history = []
        self.working = {}
        self.proposals = []
        self.stats = dict(EMPTY_STATS)
        storage.clear_history(self.id)
        self.persist()
        return self.snapshot()

    # --- запрос ----------------------------------------------------------------

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
        """Собирает запрос: system, включённые слои памяти, окно диалога и вопрос."""
        messages = []
        if self.settings["system_prompt"].strip():
            messages.append({"role": "system", "content": self.settings["system_prompt"]})

        layers = memory.blocks(self.working, self.long,
                               use_working=self.settings["use_working"],
                               use_long=self.settings["use_long"])
        messages.extend(layers["messages"])

        if self.settings["use_short"]:
            keep = max(0, int(self.settings["keep_last"]))
            messages.extend(self.history[-keep:] if keep else self.history)

        messages.append({"role": "user", "content": question})
        return messages, layers["sizes"]

    def ask(self, text):
        """Принять запрос, ответить, а затем предложить, что запомнить."""
        prefix, data_savings = self._data_block()
        question = prefix + text

        messages, sizes = self._messages(question)
        result = providers.call(
            self.settings["model"],
            messages,
            temperature=float(self.settings["temperature"]),
            max_tokens=int(self.settings["max_tokens"]),
            stop=[s for s in (self.settings["stop"] or "").split("|") if s.strip()],
        )

        self.history.append({"role": "user", "content": text})
        self.history.append({"role": "assistant", "content": result["text"]})
        storage.append_message(self.id, "user", text)
        storage.append_message(self.id, "assistant", result["text"])

        self.stats["calls"] += 1
        self.stats["prompt_tokens"] += result["prompt_tokens"]
        self.stats["completion_tokens"] += result["completion_tokens"]
        self.stats["cost"] = round(self.stats["cost"] + (result["cost"] or 0), 6)

        proposed = self._route(text) if self.settings["router"] else []
        self.persist()

        return {**result, "agent": self.snapshot(), "data_savings": data_savings,
                "layer_sizes": sizes, "proposed": proposed}

    def _route(self, text):
        """Роутер предлагает, что куда положить. Запись произойдёт только по решению."""
        proposals, tokens = memory.route(self.settings["model"], text, self.working, self.long)
        self.stats["router_calls"] += 1
        self.stats["router_tokens"] += tokens

        fresh = []
        for item in proposals:
            # Дубли не предлагаем: если такое значение уже лежит в слое, пропускаем.
            if item["layer"] == "working" and self.working.get(item["key"]) == item["value"]:
                continue
            if item["layer"] == "long":
                current = self.long.get(item["key"])
                if current and current["value"] == item["value"]:
                    continue

            proposal = {"id": f"{self.id}-p{next(self._proposal_counter)}",
                        "agent_id": self.id, "status": "pending", **item}
            self.proposals.append(proposal)
            storage.save_proposal(proposal["id"], self.id, item["layer"], item.get("kind"),
                                  item["key"], item["value"])
            fresh.append(proposal)
        return fresh

    def snapshot(self):
        """Состояние агента для интерфейса: три слоя и очередь предложений."""
        long_memory = self.long
        sizes = memory.blocks(self.working, long_memory,
                              self.settings["use_working"], self.settings["use_long"])["sizes"]
        return {
            "id": self.id,
            "settings": self.settings,
            "stats": self.stats,
            "history": self.history,
            "messages": len(self.history),
            "layers": {
                "short": {"count": len(self.history),
                          "in_context": min(len(self.history),
                                            int(self.settings["keep_last"]) or len(self.history))
                          if self.settings["use_short"] else 0},
                "working": {"items": self.working, "chars": sizes["working"]},
                "long": {"items": long_memory, "chars": sizes["long"]},
            },
            "proposals": self.proposals,
        }


REGISTRY = {}


def restore():
    global _counter
    storage.init()
    rows = storage.load_all()
    for row in rows:
        REGISTRY[row["id"]] = Agent(row["id"], row["settings"], row)

    _counter = itertools.count(storage.max_agent_number() + 1)
    return {"agents": len(rows),
            "messages": sum(len(row["history"]) for row in rows),
            "long": len(storage.load_long())}


def create(settings=None):
    agent = Agent(f"a{next(_counter)}", settings)
    if not settings or "name" not in settings:
        agent.settings["name"] = f"Агент {agent.id[1:]}"
    agent.persist()
    REGISTRY[agent.id] = agent
    return agent


def clone(agent_id):
    """Копия агента: настройки те же, диалог и рабочая память чистые.

    Долговременная память общая, поэтому копия сразу знает всё, что знал оригинал.
    """
    source = get(agent_id)
    copy = create(dict(source.settings))
    copy.settings["name"] = f"{source.settings['name']} · копия"
    copy.persist()
    return copy


def get(agent_id):
    agent = REGISTRY.get(agent_id)
    if not agent:
        raise RuntimeError(f"Агент {agent_id} не найден")
    return agent


def close(agent_id):
    REGISTRY.pop(agent_id, None)
    storage.delete_agent(agent_id)
