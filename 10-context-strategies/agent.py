"""Агент с тремя стратегиями управления контекстом (без суммаризации).

    window     — в модель уходят только последние keep_last сообщений;
    facts      — блок ключ-значение с важными данными плюс последние keep_last;
    branching  — цепочка активной ветки целиком, ветки независимы друг от друга.

Как и раньше, вся логика запроса-ответа заперта в агенте: web.py не собирает messages
и не знает про провайдеров. Состояние (реплики, ветки, факты, чекпоинты) лежит в SQLite
и поднимается при старте.
"""

import itertools
import json
import pathlib
import re

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
    # Стратегия управления контекстом: window | facts | branching.
    "strategy": "facts",
    # Размер окна для window и facts.
    "keep_last": 6,
    # Активная ветка для branching.
    "branch": storage.MAIN_BRANCH,
    "data": "",
    "data_format": "toon",
}

STRATEGIES = {
    "window": {"title": "Sliding Window",
               "note": "только последние N сообщений, остальное отбрасывается"},
    "facts": {"title": "Sticky Facts",
              "note": "блок ключ-значение + последние N сообщений"},
    "branching": {"title": "Branching",
                  "note": "чекпоинты и независимые ветки диалога"},
}

EMPTY_STATS = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0,
               # Обновление facts — отдельные вызовы LLM; считаем их отдельно,
               # чтобы сравнение стратегий по расходу было честным.
               "facts_calls": 0, "facts_tokens": 0}

FACTS_PROMPT = (
    "Ты ведёшь карточку фактов о проекте в виде пар ключ-значение.\n"
    "Ниже текущая карточка и новая реплика пользователя. Верни ОБНОВЛЁННУЮ карточку "
    "целиком — только JSON-объект, без markdown и пояснений.\n"
    "Ключи — короткие названия на русском: цель, сроки, бюджет, стек, ограничения, "
    "предпочтения, решения, договорённости. Значения — сжатые формулировки с числами "
    "и названиями. Сохраняй всё, что было, если реплика это не меняет. "
    "Ничего не выдумывай.\n\n"
    "КАРТОЧКА:\n{facts}\n\nРЕПЛИКА ПОЛЬЗОВАТЕЛЯ:\n{text}"
)

_counter = itertools.count(1)


class Agent:
    """Один чат: настройки, дерево веток, факты и счётчики."""

    def __init__(self, agent_id, settings=None, restored=None):
        self.id = agent_id
        self.settings = dict(DEFAULTS)
        restored = restored or {}

        self.messages = {storage.MAIN_BRANCH: []}
        self.messages.update({key: list(value)
                              for key, value in (restored.get("messages") or {}).items()})
        self.branches = {storage.MAIN_BRANCH:
                         {"parent_id": None, "fork_at": 0, "title": "главная"}}
        self.branches.update(restored.get("branches") or {})
        self.facts = dict(restored.get("facts") or {})
        self.checkpoints = list(restored.get("checkpoints") or [])
        self.stats = {**EMPTY_STATS, **(restored.get("stats") or {})}

        if settings:
            self.settings.update({k: v for k, v in settings.items() if k in DEFAULTS})

    # --- состояние ------------------------------------------------------------

    def update(self, settings):
        """Меняет настройки на лету. Незнакомые ключи игнорируются."""
        for key, value in settings.items():
            if key in DEFAULTS:
                self.settings[key] = value
        self.persist()
        return self.snapshot()

    def persist(self):
        storage.save_agent(self.id, self.settings, self.stats)

    def reset(self):
        """Забыть всё: реплики, ветки, факты, чекпоинты и счётчики."""
        self.messages = {storage.MAIN_BRANCH: []}
        self.branches = {storage.MAIN_BRANCH:
                         {"parent_id": None, "fork_at": 0, "title": "главная"}}
        self.facts = {}
        self.checkpoints = []
        self.stats = dict(EMPTY_STATS)
        self.settings["branch"] = storage.MAIN_BRANCH
        storage.clear_history(self.id)
        self.persist()
        return self.snapshot()

    # --- ветки ----------------------------------------------------------------

    def chain(self, branch_id=None):
        """Полный путь ветки: унаследованный префикс родителя плюс свои реплики."""
        branch_id = branch_id or self.settings["branch"]
        branch = self.branches.get(branch_id) or self.branches[storage.MAIN_BRANCH]
        own = list(self.messages.get(branch_id, []))
        if branch["parent_id"] is None:
            return own
        return self.chain(branch["parent_id"])[:branch["fork_at"]] + own

    def checkpoint(self, title=""):
        """Помечает текущую точку диалога, от неё потом создаются ветки."""
        branch = self.settings["branch"]
        at = len(self.chain(branch))
        checkpoint = {
            "id": f"{self.id}-cp{len(self.checkpoints) + 1}",
            "branch_id": branch,
            "at": at,
            "title": title or f"после {at} сообщ.",
        }
        self.checkpoints.append(checkpoint)
        storage.save_checkpoint(checkpoint["id"], self.id, branch, at, checkpoint["title"])
        return self.snapshot()

    def fork(self, checkpoint_id=None, title=""):
        """Создаёт ветку от чекпоинта (по умолчанию — от последнего)."""
        if not self.checkpoints:
            self.checkpoint()
        source = next((c for c in self.checkpoints if c["id"] == checkpoint_id),
                      self.checkpoints[-1])

        branch_id = f"{self.id}-b{len(self.branches)}"
        self.branches[branch_id] = {
            "parent_id": source["branch_id"],
            "fork_at": source["at"],
            "title": title or f"ветка {len(self.branches)}",
        }
        self.messages[branch_id] = []
        storage.save_branch(branch_id, self.id, source["branch_id"], source["at"],
                            self.branches[branch_id]["title"])
        self.settings["branch"] = branch_id
        self.persist()
        return self.snapshot()

    def switch(self, branch_id):
        """Переключение между ветками: контекст меняется целиком."""
        if branch_id not in self.branches:
            raise RuntimeError(f"Нет ветки {branch_id}")
        self.settings["branch"] = branch_id
        self.persist()
        return self.snapshot()

    # --- факты ----------------------------------------------------------------

    def _update_facts(self, text):
        """Отдельный вызов LLM: обновляет карточку фактов после реплики пользователя."""
        result = providers.call(
            self.settings["model"],
            [{"role": "user", "content": FACTS_PROMPT.format(
                facts=json.dumps(self.facts, ensure_ascii=False, indent=2) or "{}",
                text=text)}],
            temperature=0.0,
            max_tokens=500,
        )

        self.stats["facts_calls"] += 1
        self.stats["facts_tokens"] += result["prompt_tokens"] + result["completion_tokens"]
        self.stats["cost"] = round(self.stats["cost"] + (result["cost"] or 0), 6)

        raw = result["text"].strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None          # модель ответила не JSON — карточку не портим
        if not isinstance(parsed, dict):
            return None

        # Модель возвращает карточку целиком: ключи, которых больше нет, удаляем.
        removed = {key: "" for key in self.facts if key not in parsed}
        self.facts = {key: str(value) for key, value in parsed.items() if str(value).strip()}
        storage.save_facts(self.id, {**removed, **self.facts})
        return {"keys": len(self.facts),
                "tokens": result["prompt_tokens"] + result["completion_tokens"]}

    def facts_block(self):
        return "\n".join(f"{key}: {value}" for key, value in self.facts.items())

    # --- запрос ---------------------------------------------------------------

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
        """Собирает запрос по выбранной стратегии."""
        messages = []
        if self.settings["system_prompt"].strip():
            messages.append({"role": "system", "content": self.settings["system_prompt"]})

        if self.settings["memory"]:
            strategy = self.settings["strategy"]
            history = self.chain()
            keep = max(0, int(self.settings["keep_last"]))

            if strategy == "branching":
                # Ветвление — про изоляцию контекста, а не про экономию:
                # цепочка активной ветки уходит целиком.
                messages.extend(history)
            else:
                if strategy == "facts" and self.facts:
                    messages.append({"role": "system", "content":
                                     "Факты о проекте (актуальны всегда):\n" + self.facts_block()})
                messages.extend(history[-keep:] if keep else history)

        messages.append({"role": "user", "content": question})
        return messages

    def ask(self, text):
        """Принять запрос пользователя, сходить в LLM, вернуть ответ с метриками."""
        prefix, data_savings = self._data_block()
        question = prefix + text

        # Карточку фактов обновляем до сборки запроса, чтобы свежий факт
        # попал в контекст сразу же.
        facts_update = None
        if self.settings["memory"] and self.settings["strategy"] == "facts":
            facts_update = self._update_facts(text)

        result = providers.call(
            self.settings["model"],
            self._messages(question),
            temperature=float(self.settings["temperature"]),
            max_tokens=int(self.settings["max_tokens"]),
            stop=[s for s in (self.settings["stop"] or "").split("|") if s.strip()],
        )

        branch = self.settings["branch"]
        self.messages.setdefault(branch, []).append({"role": "user", "content": text})
        self.messages[branch].append({"role": "assistant", "content": result["text"]})

        self.stats["calls"] += 1
        self.stats["prompt_tokens"] += result["prompt_tokens"]
        self.stats["completion_tokens"] += result["completion_tokens"]
        self.stats["cost"] = round(self.stats["cost"] + (result["cost"] or 0), 6)

        storage.append_message(self.id, branch, "user", text)
        storage.append_message(self.id, branch, "assistant", result["text"])
        self.persist()

        return {**result, "agent": self.snapshot(), "data_savings": data_savings,
                "facts_update": facts_update}

    def snapshot(self):
        """Состояние агента для интерфейса."""
        chain = self.chain()
        keep = max(0, int(self.settings["keep_last"]))
        in_context = len(chain) if self.settings["strategy"] == "branching" else min(
            len(chain), keep or len(chain))

        return {
            "id": self.id,
            "settings": self.settings,
            "stats": self.stats,
            "facts": self.facts,
            "checkpoints": self.checkpoints,
            "branches": [
                {"id": key, "title": value["title"], "parent_id": value["parent_id"],
                 "fork_at": value["fork_at"], "messages": len(self.messages.get(key, []))}
                for key, value in self.branches.items()
            ],
            "branch": self.settings["branch"],
            "history": chain,
            "messages": len(chain),
            "in_context": in_context,
        }


REGISTRY = {}


def restore():
    """Поднимает агентов из базы при старте сервера."""
    global _counter
    storage.init()
    rows = storage.load_all()
    for row in rows:
        REGISTRY[row["id"]] = Agent(row["id"], row["settings"], row)

    _counter = itertools.count(storage.max_agent_number() + 1)
    return {"agents": len(rows),
            "messages": sum(len(m) for row in rows for m in row["messages"].values())}


def create(settings=None):
    agent = Agent(f"a{next(_counter)}", settings)
    if not settings or "name" not in settings:
        agent.settings["name"] = f"Агент {agent.id[1:]}"
    agent.persist()
    # Главная ветка не хранится в таблице: Agent создаёт её сам при инициализации.
    REGISTRY[agent.id] = agent
    return agent


def clone(agent_id):
    """Копия агента с теми же настройками, но с чистой историей."""
    source = get(agent_id)
    copy = create({**source.settings, "branch": storage.MAIN_BRANCH})
    copy.settings["name"] = f"{source.settings['name']} · копия"
    copy.persist()
    return copy


def get(agent_id):
    agent = REGISTRY.get(agent_id)
    if not agent:
        raise RuntimeError(f"Агент {agent_id} не найден")
    return agent


def close(agent_id):
    """Закрыл панель — агент удаляется вместе с ветками и фактами."""
    REGISTRY.pop(agent_id, None)
    storage.delete_agent(agent_id)
