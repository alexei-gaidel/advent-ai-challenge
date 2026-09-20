"""Агент с моделью памяти из трёх слоёв и профилем пользователя поверх неё.

Краткосрочная память — реплики диалога, рабочая — данные текущей задачи,
долговременная — решения и знания (общая на все агенты). Профиль — кто пользователь
и как ему отвечать; профилей несколько, агент привязан к одному (settings["profile"])
и подмешивает его в каждый запрос. Слои хранятся раздельно (storage.py), описаны
в memory.py и включаются независимо друг от друга.

Роутер после каждой реплики предлагает, что куда положить — в том числе в профиль,
если пользователь рассказал о себе или попросил отвечать иначе. Сам он ничего не пишет:
предложения ждут решения пользователя — принять, переложить или отклонить.
"""

import itertools
import json
import pathlib

import invariants as inv
import memory
import profiles
import providers
import storage
import taskstate
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
            "Ты критик. Находи слабые места, риски и необоснованные допущения. "
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
    # Профиль пользователя: чей подключён и подключён ли вообще.
    "profile": "",
    "use_profile": True,
    # Окно краткосрочной памяти.
    "keep_last": 6,
    # Название текущей задачи: меняется вместе с очисткой рабочей памяти.
    "task": "",
    # Роутер можно выключить и раскладывать память только руками.
    "router": True,
    # Состояние задачи: подмешивать ли его в запрос и предлагать ли переходы.
    "use_task_state": True,
    "task_advisor": True,
    # Дисциплина этапа: запрещает ассистенту делать работу другого этапа.
    "stage_discipline": True,
    # Инварианты: подмешивать ли их в запрос и проверять ли ответ аудитором.
    "use_invariants": True,
    "audit": True,
    "data": "",
    "data_format": "toon",
}

EMPTY_STATS = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0,
               # Роутер и советчик по состоянию — отдельные вызовы LLM,
               # считаем их отдельно, чтобы видеть цену обслуживания.
               "router_calls": 0, "router_tokens": 0,
               "task_calls": 0, "task_tokens": 0,
               # Аудит инвариантов: сколько раз проверяли, сколько поймали и переписали.
               "audit_calls": 0, "audit_tokens": 0,
               "violations": 0, "rewrites": 0}

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
        # Карточка задачи поднимается из базы вместе с агентом: этап, шаги,
        # ожидаемое действие и флаг паузы переживают перезапуск.
        self.task = restored.get("task")
        if self.task:
            for event in self.task["events"]:
                event["saved"] = True      # поднято из базы, повторно писать не нужно
        self.task_proposal = None
        self._proposal_counter = itertools.count(len(self.proposals) + 1)

        if settings:
            self.settings.update({k: v for k, v in settings.items() if k in DEFAULTS})

    # --- слои ------------------------------------------------------------------

    @property
    def invariants(self):
        """Инварианты общие на стенд, поэтому всегда читаются из хранилища."""
        return storage.load_invariants()

    def add_invariant(self, text, kind="архитектура", rationale="", source="manual"):
        storage.save_invariant(inv.new_invariant(text, kind, rationale, source))
        return self.snapshot()

    def drop_invariant(self, invariant_id):
        storage.delete_invariant(invariant_id)
        return self.snapshot()

    def promote_decision(self, key):
        """Повышает принятое решение из долговременной памяти до инварианта."""
        item = self.long.get(key)
        if not item:
            raise RuntimeError(f"В долговременной памяти нет записи «{key}»")
        storage.save_invariant(inv.new_invariant(
            f"{key}: {item['value']}", "архитектура",
            f"решение зафиксировано в долговременной памяти ({item['kind']})", "promoted"))
        return self.snapshot()

    @property
    def long(self):
        """Долговременная память общая, поэтому всегда читается из хранилища."""
        return storage.load_long()

    @property
    def profile(self):
        """Профиль общий для всех агентов с тем же id — тоже читается из хранилища."""
        profile_id = self.settings["profile"]
        return storage.load_profiles().get(profile_id) if profile_id else None

    def set_profile(self, profile_id):
        """Смена пользователя: следующий же ответ пойдёт под новый профиль."""
        if profile_id:
            profiles.get(profile_id)          # проверка, что такой есть
        self.settings["profile"] = profile_id
        self.persist()
        return self.snapshot()

    def remember(self, layer, key, value, kind=""):
        """Ручная запись в память, минуя роутер."""
        key, value = key.strip(), value.strip()
        if layer == "profile":
            if not self.settings["profile"]:
                raise RuntimeError("Профиль не подключён — некуда записывать")
            profiles.apply(self.settings["profile"], kind or "факт", key, value,
                           model=self.settings["model"])
            return self.snapshot()

        if not key or not value:
            raise RuntimeError("Нужны и ключ, и значение")
        if layer == "working":
            self.working[key] = value
            storage.save_working(self.id, key, value)
        elif layer == "long":
            storage.save_long(key, value, kind or "знание", self.id)
        else:
            raise RuntimeError("Записать можно в рабочую, долговременную память или профиль")
        return self.snapshot()

    def forget(self, layer, key):
        if layer == "working":
            self.working.pop(key, None)
            storage.delete_working(self.id, key)
        elif layer == "long":
            storage.delete_long(key)
        elif layer == "profile" and self.settings["profile"]:
            profiles.forget(self.settings["profile"], key)
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
            kind = proposal.get("kind") or ""
            if kind not in memory.VALID_KINDS.get(target, ()):
                kind = memory.VALID_KINDS[target][0]    # переложили — тип подгоняем под слой
            self.remember(target, proposal["key"], proposal["value"], kind)
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

    # --- состояние задачи ------------------------------------------------------

    def start_task(self, title, stages=None):
        """Заводит новую задачу. Старая карточка заменяется целиком."""
        if self.task:
            storage.delete_task(self.task["id"])
        self.task = taskstate.new_task(self.id, title, stages)
        self.task_proposal = None
        self.settings["task"] = title
        self._save_task()
        return self.snapshot()

    def drop_task(self):
        if self.task:
            storage.delete_task(self.task["id"])
        self.task = None
        self.task_proposal = None
        return self.snapshot()

    def plan_task(self, context=""):
        """Этап планирования: агент составляет чек-лист шагов."""
        self._require_task()
        steps, tokens = taskstate.plan(self.settings["model"], self.task, context)
        self.stats["task_calls"] += 1
        self.stats["task_tokens"] += tokens
        if steps:
            taskstate.set_steps(self.task, steps)
            taskstate.set_expected(self.task, "user", "подтвердить план и перейти к выполнению")
            self._save_task()
        self.persist()
        return self.snapshot()

    def move(self, target):
        """Переход только по правилам: принудительного варианта больше нет."""
        self._require_task()
        taskstate.transition(self.task, target)
        self.task_proposal = None
        self._save_task()
        return self.snapshot()

    def pause_task(self, note=""):
        self._require_task()
        taskstate.pause(self.task, note)
        self._save_task()
        return self.snapshot()

    def resume_task(self):
        self._require_task()
        taskstate.resume(self.task)
        self._save_task()
        return self.snapshot()

    def mark_step(self, text, status="done"):
        self._require_task()
        taskstate.mark_step(self.task, text, status)
        self._save_task()
        return self.snapshot()

    def approve_plan(self):
        """Утверждение плана — осознанное действие человека, а не побочный эффект."""
        self._require_task()
        taskstate.approve_plan(self.task)
        self._save_task()
        return self.snapshot()

    def pass_validation(self):
        self._require_task()
        taskstate.pass_validation(self.task)
        self._save_task()
        return self.snapshot()

    def add_question(self, text):
        self._require_task()
        taskstate.add_question(self.task, text)
        self._save_task()
        return self.snapshot()

    def resolve_question(self, text):
        self._require_task()
        taskstate.resolve_question(self.task, text)
        self._save_task()
        return self.snapshot()

    def set_expected(self, actor, action):
        self._require_task()
        taskstate.set_expected(self.task, actor, action)
        self._save_task()
        return self.snapshot()

    def set_steps(self, steps):
        """Ручная правка плана: шаги можно переписать после агента."""
        self._require_task()
        taskstate.set_steps(self.task, steps)
        self._save_task()
        return self.snapshot()

    def set_stages(self, stages):
        """Набор этапов настраивается под задачу, пока с неё не начали сходить."""
        self._require_task()
        keys = {stage["key"] for stage in stages}
        if self.task["stage"] not in keys:
            raise RuntimeError("Текущего этапа нет в новом наборе")
        self.task["stages"] = stages
        self._save_task()
        return self.snapshot()

    def apply_advice(self, accept_step=True, accept_stage=True, accept_expected=True):
        """Принять предложение агента по состоянию — целиком или частями."""
        advice = self.task_proposal
        if not advice:
            raise RuntimeError("Нет предложения по состоянию")

        if accept_step and advice["step_done"] and advice["step"]:
            taskstate.mark_step(self.task, advice["step"])
        if accept_expected and advice["expected_action"]:
            taskstate.set_expected(self.task, advice["expected_actor"],
                                   advice["expected_action"])
        if accept_stage and advice["next_stage"]:
            taskstate.transition(self.task, advice["next_stage"])

        self.task_proposal = None
        self._save_task()
        return self.snapshot()

    def _require_task(self):
        if not self.task:
            raise RuntimeError("У агента нет активной задачи")

    def _save_task(self):
        storage.save_task(self.task)
        storage.save_steps(self.task["id"], self.task["steps"])
        for event in self.task["events"]:
            if not event.get("saved"):
                storage.add_event(self.task["id"], event["kind"], event["payload"])
                event["saved"] = True

    def update(self, settings):
        for key, value in settings.items():
            if key in DEFAULTS:
                self.settings[key] = value
        self.persist()
        return self.snapshot()

    def persist(self):
        storage.save_agent(self.id, self.settings, self.stats)

    def reset_task_only(self):
        """Сброс состояния задачи без потери диалога и памяти."""
        return self.drop_task()

    def reset(self):
        """Сброс агента: диалог, рабочая память и предложения. Долгая память общая — жива."""
        self.history = []
        self.working = {}
        self.proposals = []
        self.stats = dict(EMPTY_STATS)
        if self.task:
            storage.delete_task(self.task["id"])
        self.task = None
        self.task_proposal = None
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

        layers = memory.blocks(self.working, self.long, self.profile,
                               use_working=self.settings["use_working"],
                               use_long=self.settings["use_long"],
                               use_profile=self.settings["use_profile"])
        messages.extend(layers["messages"])

        rules = ""
        if self.settings["use_invariants"]:
            rules = inv.block(self.invariants)
            if rules:
                # Инварианты идут раньше памяти и состояния: это рамка,
                # внутри которой всё остальное имеет смысл.
                messages.append({"role": "system", "content": rules})

        state = ""
        if self.task and self.settings["use_task_state"]:
            state = taskstate.block(self.task)
            messages.append({"role": "system", "content": state})
            if self.settings["stage_discipline"]:
                # Без этого блока автомат сторожит только кнопки: в диалоге
                # ассистент спокойно сделает работу чужого этапа, если попросить.
                rules = taskstate.discipline(self.task)
                messages.append({"role": "system", "content": rules})
                state += "\n" + rules

        if self.settings["use_short"]:
            keep = max(0, int(self.settings["keep_last"]))
            messages.extend(self.history[-keep:] if keep else self.history)

        messages.append({"role": "user", "content": question})
        return messages, {**layers["sizes"], "task": len(state), "invariants": len(rules)}

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

        audit = self._audit(messages, question, result)

        self.history.append({"role": "user", "content": text})
        self.history.append({"role": "assistant", "content": result["text"]})
        storage.append_message(self.id, "user", text)
        storage.append_message(self.id, "assistant", result["text"])

        self.stats["calls"] += 1
        self.stats["prompt_tokens"] += result["prompt_tokens"]
        self.stats["completion_tokens"] += result["completion_tokens"]
        self.stats["cost"] = round(self.stats["cost"] + (result["cost"] or 0), 6)

        proposed = self._route(text) if self.settings["router"] else []
        advice = self._advise(text, result["text"])
        self.persist()

        return {**result, "agent": self.snapshot(), "data_savings": data_savings,
                "layer_sizes": sizes, "proposed": proposed, "task_proposal": advice,
                "audit": audit}

    def _audit(self, messages, question, result):
        """Проверяет ответ аудитором и при нарушении переписывает его в отказ.

        Меняет result на месте: пользователю уходит уже исправленный ответ,
        а в audit остаётся, что именно было нарушено и что забраковано.
        """
        rules = self.invariants
        if not (self.settings["audit"] and self.settings["use_invariants"] and rules):
            return None

        violations, tokens = inv.audit(self.settings["model"], rules, question,
                                       result["text"])
        self.stats["audit_calls"] += 1
        self.stats["audit_tokens"] += tokens
        if not violations:
            return {"violations": [], "rewritten": False, "tokens": tokens}

        self.stats["violations"] += len(violations)
        rejected = result["text"]

        retry = providers.call(
            self.settings["model"],
            messages + [{"role": "assistant", "content": rejected},
                        {"role": "user", "content": inv.rewrite_instruction(violations)}],
            temperature=0.0,
            max_tokens=int(self.settings["max_tokens"]),
        )
        self.stats["rewrites"] += 1
        self.stats["audit_tokens"] += retry["prompt_tokens"] + retry["completion_tokens"]
        self.stats["cost"] = round(self.stats["cost"] + (retry["cost"] or 0), 6)

        result["text"] = retry["text"]
        return {"violations": violations, "rewritten": True, "rejected": rejected,
                "tokens": tokens + retry["prompt_tokens"] + retry["completion_tokens"]}

    def _advise(self, text, answer):
        """Агент предлагает, что сделать с состоянием задачи. Решает пользователь."""
        if not (self.task and self.settings["task_advisor"]) or self.task["paused"]:
            return None

        advice, tokens = taskstate.propose(self.settings["model"], self.task, text, answer)
        self.stats["task_calls"] += 1
        self.stats["task_tokens"] += tokens
        if advice and not (advice["step_done"] or advice["next_stage"]
                           or advice["expected_action"]):
            advice = None
        self.task_proposal = advice
        return advice

    def _route(self, text):
        """Роутер предлагает, что куда положить. Запись произойдёт только по решению."""
        profile = self.profile
        proposals, tokens = memory.route(self.settings["model"], text, self.working,
                                         self.long, profile)
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
            if item["layer"] == "profile":
                if not profile:
                    continue              # некуда: профиль не подключён
                if item["kind"] == "факт" and profile["facts"].get(item["key"]) == item["value"]:
                    continue
                if (item["kind"] != "факт"
                        and item["value"].lower() in profile[profiles.KIND_FIELD[item["kind"]]].lower()):
                    continue

            proposal = {"id": f"{self.id}-p{next(self._proposal_counter)}",
                        "agent_id": self.id, "status": "pending", **item}
            self.proposals.append(proposal)
            storage.save_proposal(proposal["id"], self.id, item["layer"], item.get("kind"),
                                  item["key"], item["value"])
            fresh.append(proposal)
        return fresh

    def _task_snapshot(self):
        """Состояние задачи для интерфейса: этап, шаги, ожидание, журнал переходов."""
        task = self.task
        if not task:
            return None
        step = taskstate.current_step(task)
        return {
            "id": task["id"],
            "title": task["title"],
            "stages": task["stages"],
            "stage": task["stage"],
            "stage_index": taskstate.stage_index(task),
            "steps": task["steps"],
            "current_step": step["text"] if step else "",
            "expected": task["expected"],
            "paused": task["paused"],
            "pause_note": task["pause_note"],
            "allowed": [stage["key"] for stage in task["stages"]
                        if taskstate.can(task, stage["key"])[0]],
            "gates": {stage["key"]: taskstate.gate_report(task, stage["key"])
                      for stage in task["stages"]},
            "blocked": {stage["key"]: taskstate.can(task, stage["key"])[1]
                        for stage in task["stages"]
                        if not taskstate.can(task, stage["key"])[0]},
            "plan_approved": task["plan_approved"],
            "validation_passed": task["validation_passed"],
            "open_questions": task["open_questions"],
            "events": task["events"][-12:],
            "block_chars": len(taskstate.block(task)),
        }

    def snapshot(self):
        """Состояние агента для интерфейса: три слоя и очередь предложений."""
        long_memory = self.long
        profile = self.profile
        sizes = memory.blocks(self.working, long_memory, profile,
                              self.settings["use_working"], self.settings["use_long"],
                              self.settings["use_profile"])["sizes"]
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
                "profile": {"item": profile, "chars": sizes["profile"]},
            },
            "proposals": self.proposals,
            "invariants": self.invariants,
            "task_state": self._task_snapshot(),
            "task_proposal": self.task_proposal,
        }


REGISTRY = {}


def restore():
    global _counter
    storage.init()
    rows = storage.load_all()
    for row in rows:
        row["task"] = storage.load_task(row["id"])
        REGISTRY[row["id"]] = Agent(row["id"], row["settings"], row)

    _counter = itertools.count(storage.max_agent_number() + 1)
    return {"agents": len(rows),
            "messages": sum(len(row["history"]) for row in rows),
            "long": len(storage.load_long()),
            "profiles": len(profiles.load_all())}


def create(settings=None):
    agent = Agent(f"a{next(_counter)}", settings)
    if not settings or "name" not in settings:
        agent.settings["name"] = f"Агент {agent.id[1:]}"
    if not settings or "profile" not in settings:
        # Без явного выбора — первый профиль: стенд должен персонализировать сразу.
        known = profiles.load_all()
        agent.settings["profile"] = next(iter(known), "")
    agent.persist()
    REGISTRY[agent.id] = agent
    return agent


def clone(agent_id):
    """Копия агента: настройки и профиль те же, диалог и рабочая память чистые.

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
