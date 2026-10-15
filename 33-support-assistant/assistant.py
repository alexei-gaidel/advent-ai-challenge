"""Ассистент поддержки: RAG по документации продукта + CRM через MCP → черновик ответа.

Как собирается ответ по тикету (режим full):
    1. Код всегда загружает через MCP тикет, карточку пользователя и его журнал шлюза —
       модель не выбирает, смотреть ли данные клиента, и не может их не увидеть;
    2. топ-5 фрагментов документации по вопросу (bge-m3);
    3. DeepSeek пишет диагноз для оператора и ответ клиенту; при нужде сама зовёт
       list_tickets (история обращений) — инструменты только на чтение.
Запись в CRM (add_note) модели не передаётся: заметку сохраняет оператор кнопкой.

Режимы для сравнения в support_demo.py: llm — только вопрос; rag — вопрос + документация;
full — вопрос + документация + данные клиента из CRM.
"""

import json
import re
import time

import index as rag
import providers
import tools as mcp_tools

MODEL = "deepseek-chat"
MAX_TOOL_ROUNDS = 3
MODES = {"llm": "только модель", "rag": "RAG без CRM", "full": "RAG + CRM"}
# Раздел документации, который объясняет сами данные CRM. Вопрос клиента («почему 401?»)
# на него не похож, и поиск его не находит — без него модель читала key: "-" как
# «запрос без ключа». Поэтому он едет вместе с данными, без поиска (урок дня 32).
PINNED_WITH_CRM = "Как читать журнал шлюза"
# Модели — только чтение. add_note и get_* с данными тикета код вызывает сам.
MODEL_TOOLS_EXCLUDE = ("add_note",)

SYSTEM = """Ты — ассистент службы поддержки приватного LLM-сервиса (OpenAI-совместимый шлюз
к локальной модели qwen2.5:3b). Ты готовишь черновик для оператора поддержки.

Правила:
- Факты о продукте — только из фрагментов документации [1]…[N], ссылайся на них номерами.
- Факты о клиенте — только из данных CRM (тикет, карточка, журнал шлюза), если они даны.
  Журнал шлюза — главный источник: по нему видно, что на самом деле произошло.
- Не выдумывай причину. Если данных не хватает — так и напиши, что нужно уточнить у клиента.
- Ключи клиента никогда не пиши целиком; данные других клиентов не упоминай.

Ответ строго в двух частях:
ДИАГНОЗ: 1–3 предложения для оператора — причина и на чём основан вывод.
ОТВЕТ КЛИЕНТУ: вежливо, по-русски, конкретные шаги, 3–7 предложений."""


class SupportAssistant:
    def __init__(self, index=None, bridge=None, model=MODEL):
        self.index = index or rag.Index()
        self.bridge = bridge or mcp_tools.ToolBridge()
        self.model = model

    # --- данные CRM через MCP (для веба и для ответа) ------------------------------

    def crm(self, name, **arguments):
        text = self.bridge.call(name, arguments)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise RuntimeError(text)

    def tickets(self):
        return self.crm("list_tickets")

    def ticket_card(self, ticket_id):
        ticket = self.crm("get_ticket", ticket_id=ticket_id)
        user = self.crm("get_user", user_id=ticket["user_id"])
        events = self.crm("get_events", user_id=ticket["user_id"])
        return {"ticket": ticket, "user": user, "events": events}

    def save_note(self, ticket_id, text, status=None):
        """Оператор нажал «сохранить»: запись в CRM — только отсюда, не из модели."""
        arguments = {"ticket_id": ticket_id, "text": text, "author": "оператор + ассистент"}
        if status:
            arguments["status"] = status
        return self.crm("add_note", **arguments)

    # --- ответ ------------------------------------------------------------------

    def answer(self, question=None, ticket_id=None, mode="full", temperature=0.2):
        started = time.monotonic()
        result = {"mode": mode, "ticket_id": ticket_id, "sources": [], "tools": [],
                  "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
        card = None
        if ticket_id:
            card = self.ticket_card(ticket_id)
            result["tools"] += [{"name": n, "by": "код"} for n in ("get_ticket", "get_user", "get_events")]
            question = question or card["ticket"]["text"]
        if not question:
            raise ValueError("нужен вопрос или тикет")
        result["question"] = question

        parts, specs = [], []
        if mode in ("rag", "full"):
            hits = self.index.search(question)
            if mode == "full" and card:
                pinned = self.index.section(PINNED_WITH_CRM)
                if pinned and all(h["section"] != pinned["section"] for h in hits):
                    hits.append(pinned)
            result["stale"] = self.index.stale
            result["sources"] = [{"n": n, "source": h["source"], "section": h["section"],
                                  "score": h["score"]} for n, h in enumerate(hits, 1)]
            parts.append("Фрагменты документации:\n\n" + "\n\n".join(
                f"[{n}] {h['source']} › {h['section']}\n{h['text']}" for n, h in enumerate(hits, 1)))
        if mode == "full" and card:
            parts.append("Данные CRM (получены через MCP):\n" + json.dumps(card, ensure_ascii=False, indent=1))
            specs = self.bridge.specs(exclude=MODEL_TOOLS_EXCLUDE + ("get_ticket", "get_user", "get_events"))
        parts.append(f"Вопрос клиента: {question}")
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": "\n\n".join(parts)}]
        text = self._loop(messages, specs, temperature, result)
        result.update(split(text))
        result["seconds"] = round(time.monotonic() - started, 2)
        return result

    def _loop(self, messages, specs, temperature, result):
        conversation = list(messages)
        for round_number in range(MAX_TOOL_ROUNDS + 1):
            last = round_number == MAX_TOOL_ROUNDS
            if last and specs:
                conversation.append({"role": "user", "content":
                                     "Инструментов больше не будет — ответь по имеющимся данным."})
            reply = providers.call(self.model, conversation, temperature=temperature,
                                   max_tokens=900, tools=None if (last or not specs) else specs)
            result["prompt_tokens"] += reply["prompt_tokens"]
            result["completion_tokens"] += reply["completion_tokens"]
            result["cost"] = round(result["cost"] + (reply["cost"] or 0), 6)
            if not reply["tool_calls"]:
                return reply["text"]
            conversation.append(reply["message"])
            for call in reply["tool_calls"]:
                function = call.get("function") or {}
                name = function.get("name", "")
                arguments = mcp_tools.parse_arguments(function.get("arguments"))
                if name in MODEL_TOOLS_EXCLUDE:          # страховка: схему ей не давали
                    output = "инструмент недоступен ассистенту"
                else:
                    output = self.bridge.call(name, arguments)
                result["tools"].append({"name": name, "arguments": arguments, "by": "модель"})
                conversation.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                     "content": output[:6000]})
        return reply["text"]


def split(text):
    """«ДИАГНОЗ: … ОТВЕТ КЛИЕНТУ: …» → два поля. Не разобралось — всё в ответ."""
    match = re.search(r"ДИАГНОЗ\W*(.*?)\s*ОТВЕТ КЛИЕНТУ\W*(.*)", text, flags=re.S | re.I)
    if not match:
        return {"diagnosis": "", "reply": text.strip(), "raw": text}
    return {"diagnosis": match.group(1).strip(" *\n"), "reply": match.group(2).strip(" *\n"),
            "raw": text}


if __name__ == "__main__":
    import sys
    r = SupportAssistant().answer(ticket_id=sys.argv[1] if len(sys.argv) > 1 else "T-101")
    print(json.dumps(r, ensure_ascii=False, indent=1))
