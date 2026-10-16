"""Файловый ассистент: цель на входе — чтение, поиск, анализ и правки файлов на выходе.

Задача ставится на уровне цели («проверь проект на правила CLAUDE.md»), а не «открой
файл X»: какие файлы смотреть, что искать и что менять, модель решает сама через
инструменты MCP-сервера file_server.py. Правки уходят в черновик; итоговый diff
забирает КОД (не модель), а применяет его только человек — apply()/discard().

    python3 assistant.py "цель"
"""

import json
import re
import time

import invariants
import providers
import tools as mcp_tools
import workspace

MODEL = "deepseek-chat"
MAX_TOOL_ROUNDS = 20
# За сколько кругов до конца напомнить о бюджете: в первом прогоне проверки правил
# модель все 16 кругов (тогда лимит был 16) искала и до записи отчёта не дошла (data/invariants_v1.json).
BUDGET_WARNING = 4
TOOL_RESULT_CHARS = 20000

SYSTEM = """Ты — ассистент, который работает с файлами проекта Advent AI Challenge
(ежедневные задания по LLM на Python, одна папка NN-название на день, правила проекта —
в CLAUDE.md в корне). Тебе ставят задачу на уровне цели. Какие файлы смотреть, решаешь сам:

- начинай с list_files или search, читай только то, что нужно для цели;
- опирайся только на то, что прочитал, — не придумывай пути, строки и содержимое;
- создавать и менять файлы — только через write_file и edit_file. Правки попадают в
  черновик, человек увидит diff и сам решит, применять ли. Меняй только то, что
  требует цель; для существующих файлов предпочитай edit_file;
- если edit_file вернул ошибку — перечитай нужное место и повтори точнее.

Когда закончишь — ответь текстом по-русски: что сделано (коротко), какие файлы
прочитаны и какие изменены. Отвечай без вступлений."""

LAST_ROUND = ("Лимит вызовов инструментов исчерпан, новых вызовов не будет. Ответь обычным "
              "текстом по уже сделанному; чего не успел — так и скажи.")
BUDGET = ("Осталось {left} кругов вызовов инструментов. Если цель требует создать или "
          "изменить файл — сделай это сейчас, по уже собранным данным; добирать данные "
          "будешь, только если останется запас.")
LEAK = re.compile(r"DSML|<\s*\|?\s*(tool_call|invoke)", re.I)

# Четыре сценария дня. Цели сформулированы как задачи, а не как список файлов.
PRESETS = {
    "usages": {
        "title": "Где используется API",
        "goal": ("Найди все места в коде проекта, где используется класс MCPClient: в каких "
                 "днях и файлах, и как он подключается — по stdio или по HTTP. Ответ — "
                 "таблица «файл → как используется». Файлы не меняй."),
    },
    "invariants": {
        "title": "Проверка правил CLAUDE.md",
        "goal": ("Проверь проект на соответствие жёстким правилам из CLAUDE.md, которые можно "
                 "проверить по файлам: порт web.py, записи в .gitignore, только стандартная "
                 "библиотека, граница web.py. Сохрани отчёт в INVARIANTS_REPORT.md — таблица "
                 "| файл | строка | правило | что не так | как исправить |, одна строка на "
                 "нарушение. Сам код не исправляй."),
    },
    "docs": {
        "title": "Обновить документацию по коду",
        "goal": ("В коде дня 31 (31-dev-assistant) поменялся набор инструментов MCP-сервера. "
                 "Приведи документацию этого дня в соответствие с кодом. Раздел README о баге "
                 "первого прогона — это история, его не переписывай."),
    },
    "changelog": {
        "title": "Сгенерировать CHANGELOG",
        "goal": ("Сгенерируй CHANGELOG.md в корне проекта: раздел на каждое задание челленджа, "
                 "от нового к старому, в заголовке номер, название и дата, под ним 1–2 строки "
                 "сути. Источники — история коммитов и README."),
    },
}


class FileAssistant:
    def __init__(self, bridge=None, model=MODEL):
        self.bridge = bridge or mcp_tools.ToolBridge()
        self.model = model

    # --- действия человека ----------------------------------------------------

    def diff(self):
        return self.bridge.call("draft_diff", {})

    def apply(self):
        return self.bridge.call("apply_drafts", {})

    def discard(self):
        return self.bridge.call("discard_drafts", {})

    @staticmethod
    def reset():
        return workspace.build(reset=True)

    @staticmethod
    def reference():
        """Эталон проверки правил — кодом, для сравнения с отчётом модели."""
        return invariants.check()

    # --- цель -----------------------------------------------------------------

    def run(self, goal, temperature=0.2):
        goal = (goal or "").strip()
        if goal in PRESETS:
            goal = PRESETS[goal]["goal"]
        if not goal:
            raise ValueError("пустая цель")
        started = time.monotonic()
        result = {"goal": goal, "tools": [], "rounds": 0, "errors": 0,
                  "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
        specs = self.bridge.specs(exclude=("apply_drafts", "discard_drafts"))
        if not specs:
            raise RuntimeError(f"MCP-сервер файлов недоступен: {self.bridge.error}")
        conversation = [{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": goal}]

        for round_number in range(MAX_TOOL_ROUNDS + 1):
            last = round_number == MAX_TOOL_ROUNDS
            if last:
                conversation.append({"role": "user", "content": LAST_ROUND})
            elif round_number == MAX_TOOL_ROUNDS - BUDGET_WARNING:
                conversation.append({"role": "user",
                                     "content": BUDGET.format(left=BUDGET_WARNING)})
                result["budget_warning"] = True
            reply = providers.call(self.model, conversation, temperature=temperature,
                                   max_tokens=6000, tools=None if last else specs)
            result["prompt_tokens"] += reply["prompt_tokens"]
            result["completion_tokens"] += reply["completion_tokens"]
            result["cost"] = round(result["cost"] + (reply["cost"] or 0), 6)
            if not reply["tool_calls"]:
                break
            result["rounds"] += 1
            conversation.append(reply["message"])
            for call in reply["tool_calls"]:
                function = call.get("function") or {}
                name = function.get("name", "")
                arguments = mcp_tools.parse_arguments(function.get("arguments"))
                output = self.bridge.call(name, arguments)
                failed = output.startswith(("инструмент вернул ошибку", "ошибка вызова"))
                result["errors"] += failed
                result["tools"].append({"round": result["rounds"], "name": name,
                                        "arguments": _short(arguments), "ok": not failed,
                                        "result": output[:600]})
                conversation.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                     "content": output[:TOOL_RESULT_CHARS]})

        result["answer"] = reply["text"]
        if LEAK.search(result["answer"] or ""):
            result["answer"] = "(модель вместо ответа выдала разметку вызова инструмента)"
            result["leak"] = True
        # Diff забирает код, а не модель: человек видит ровно то, что лежит в черновике.
        result["diff"] = self.diff()
        result["changed"] = re.findall(r"^\+\+\+ b/(.+)$", result["diff"], re.M)
        result["read"] = sorted({t["arguments"].get("path", "") for t in result["tools"]
                                 if t["name"] == "read_file"})
        result["seconds"] = round(time.monotonic() - started, 2)
        return result


def _short(arguments):
    """Аргументы для трассы: длинный текст файла не тащим в ленту целиком."""
    short = {}
    for key, value in arguments.items():
        if isinstance(value, str) and len(value) > 160:
            value = value[:160] + f"… ({len(value)} симв.)"
        short[key] = value
    return short


if __name__ == "__main__":
    import sys
    print(json.dumps(FileAssistant().run(" ".join(sys.argv[1:]) or "usages"),
                     ensure_ascii=False, indent=2))
