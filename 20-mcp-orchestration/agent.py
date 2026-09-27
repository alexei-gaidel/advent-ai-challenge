"""Агент с инструментами нескольких MCP-серверов.

Из дней 11–19 взят только цикл tool calling: модель отвечает списком вызовов,
реестр выполняет их на нужных серверах, результаты возвращаются в диалог — и так
до обычного текстового ответа. Память, профили, инварианты и задача выброшены:
в этом дне они только закрывали бы суть — выбор инструмента и порядок вызовов.
"""

import datetime
import itertools
import re
import time

import providers
from registry import Registry, parse_arguments

DEFAULTS = {
    "model": providers.DEFAULT_MODEL,
    "temperature": 0.0,
    # gpt-oss тратит часть бюджета на скрытые рассуждения — нужен запас.
    "max_tokens": 1500,
    "max_tool_rounds": 12,
    "system_prompt": (
        "Ты помощник по поездкам. Тебе доступны инструменты трёх MCP-серверов: "
        "weather (города и прогноз), money (курсы ЦБ и бюджет), notes (заметки). "
        "Сам решай, какие инструменты и в каком порядке вызвать. Идентификаторы "
        "(place_id, forecast_id, rate_id, budget_id, note_id) бери только из ответов "
        "инструментов, никогда не придумывай. Если инструмент вернул ошибку — исправь "
        "вызов. В конце коротко ответь пользователю по-русски."
    ),
}

# Бесплатный Groq ограничивает токены в минуту, а длинный флоу каждый круг заново
# отправляет всю историю. На 429 ждём и повторяем тот же круг, а не весь флоу.
RATE_LIMIT_WAITS = (15, 30, 45, 60, 60)

_counter = itertools.count(1)


def call_with_retry(stats, *args, **kwargs):
    for wait in (*RATE_LIMIT_WAITS, None):
        try:
            return providers.call(*args, **kwargs)
        except RuntimeError as error:
            if "429" not in str(error) or wait is None:
                raise
            hint = re.search(r"try again in ([\d.]+)s", str(error))
            pause = max(wait, float(hint.group(1)) + 1) if hint else wait
            stats["rate_limit_waits"] += 1
            stats["waited"] = round(stats["waited"] + pause, 1)
            time.sleep(pause)


class Agent:
    def __init__(self, settings=None, registry=None):
        self.id = next(_counter)
        self.settings = {**DEFAULTS, **(settings or {})}
        self.registry = registry or Registry()
        self.history = []
        self.stats = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
                      "cost": 0.0, "tool_calls": 0, "tool_rounds": 0,
                      "rate_limit_waits": 0, "waited": 0.0}

    def system(self):
        today = datetime.date.today()
        return f"{self.settings['system_prompt']}\nСегодня {today:%Y-%m-%d}."

    def ask(self, text):
        """Один ход пользователя: сколько угодно кругов инструментов, затем ответ."""
        messages = [{"role": "system", "content": self.system()}, *self.history,
                    {"role": "user", "content": text}]
        before = dict(self.stats)
        result, calls = self._run_with_tools(messages)

        self.history += [{"role": "user", "content": text},
                         {"role": "assistant", "content": result["text"]}]
        self.stats["requests"] += 1
        spent = {key: round(self.stats[key] - before[key], 6)
                 for key in ("prompt_tokens", "completion_tokens", "cost",
                             "tool_calls", "tool_rounds", "rate_limit_waits", "waited")}
        return {"text": result["text"], "calls": calls, "turn": spent,
                "stats": dict(self.stats), "finish_reason": result["finish_reason"]}

    def _run_with_tools(self, messages):
        specs = self.registry.specs()
        conversation = list(messages)
        calls = []
        rounds = max(1, int(self.settings["max_tool_rounds"]))

        for round_number in range(1, rounds + 2):
            last_round = round_number == rounds + 1
            result = call_with_retry(
                self.stats,
                self.settings["model"],
                conversation,
                temperature=float(self.settings["temperature"]),
                max_tokens=int(self.settings["max_tokens"]),
                # На последнем круге инструменты убираем: нужен текстовый ответ.
                tools=None if last_round else specs,
            )
            self.stats["prompt_tokens"] += result["prompt_tokens"]
            self.stats["completion_tokens"] += result["completion_tokens"]
            self.stats["cost"] = round(self.stats["cost"] + (result["cost"] or 0), 6)

            if not result["tool_calls"]:
                return result, calls

            self.stats["tool_rounds"] += 1
            conversation.append(result["message"])
            for call in result["tool_calls"]:
                function = call.get("function") or {}
                arguments = parse_arguments(function.get("arguments"))
                output, entry = self.registry.call(function.get("name", ""), arguments,
                                                   round_number)
                self.stats["tool_calls"] += 1
                calls.append(entry)
                conversation.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                     "content": output})

        return result, calls

    def reset(self):
        self.history = []

    def info(self):
        return {"id": self.id, "settings": self.settings, "stats": self.stats,
                "history": self.history}
