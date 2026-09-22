"""Инварианты проекта: то, что ассистент не имеет права нарушать.

Инварианты хранятся отдельно от диалога (таблица `invariants`, общая на весь стенд),
подмешиваются в каждый запрос отдельным системным блоком и проверяются после ответа
независимым вызовом LLM — аудитором. Если аудитор нашёл нарушение, ответ переписывается
в отказ: назвать инвариант и объяснить, почему предложенное невозможно.

Все инварианты жёсткие: обходных путей ассистент не предлагает. Снять инвариант можно
только явно, в панели управления, — это действие человека, а не выход из спора.
"""

import itertools
import json
import re

import providers

KINDS = ("архитектура", "стек", "бизнес-правило", "процесс")

_counter = itertools.count(1)


def new_invariant(text, kind="архитектура", rationale="", source="manual"):
    text = text.strip()
    if not text:
        raise RuntimeError("Инвариант не может быть пустым")
    return {
        "id": f"inv{next(_counter)}",
        "text": text,
        "kind": kind if kind in KINDS else "архитектура",
        "rationale": rationale.strip(),
        "source": source,
    }


def numbered(invariants):
    return [(index, item) for index, item in enumerate(invariants, 1)]


def block(invariants):
    """Системный блок с инвариантами — уходит в каждый запрос.

    Формулировки намеренно обязывающие: модель должна не просто «учитывать»
    ограничения, а отказывать, когда запрос им противоречит.
    """
    if not invariants:
        return ""

    lines = [
        "ИНВАРИАНТЫ ПРОЕКТА — решения, которые уже приняты и не обсуждаются.",
        "Правила работы с ними:",
        "1. Любое твоё предложение обязано им соответствовать.",
        "2. Если запрос пользователя требует нарушить инвариант — откажись. "
        "Назови номер и формулировку инварианта и объясни, почему предложенное "
        "невозможно именно из-за него.",
        "3. Не предлагай обходных путей, частичных нарушений и вариантов «если очень "
        "нужно». Настойчивость пользователя инвариант не отменяет.",
        "4. Прямо ссылайся на инварианты в рассуждении, когда они касаются вопроса.",
        "5. Отказ — это отказ: назови инвариант, объясни причину и остановись. "
        "Не предлагай альтернативных решений, если пользователь о них не просил.",
        "",
        "Список:",
    ]
    for number, item in numbered(invariants):
        rationale = f" — почему: {item['rationale']}" if item["rationale"] else ""
        lines.append(f"{number}. [{item['kind']}] {item['text']}{rationale}")
    return "\n".join(lines)


AUDIT_PROMPT = (
    "Ты аудитор. Проверяешь, не нарушает ли ответ ассистента инварианты проекта.\n\n"
    "ИНВАРИАНТЫ:\n<<LIST>>\n\n"
    "ВОПРОС ПОЛЬЗОВАТЕЛЯ:\n<<QUESTION>>\n\n"
    "ОТВЕТ АССИСТЕНТА:\n<<ANSWER>>\n\n"
    "Нарушение — это когда ответ предлагает, одобряет или описывает как приемлемое то, "
    "что инвариант запрещает. Прямой отказ со ссылкой на инвариант нарушением НЕ "
    "является. Упоминание запрещённой технологии ради объяснения, почему её нельзя, "
    "тоже не нарушение.\n"
    'Верни ТОЛЬКО JSON без markdown: {"violations": [{"number": 2, '
    '"quote": "цитата из ответа", "why": "коротко почему это нарушение"}]}\n'
    "Пустой список означает, что нарушений нет."
)

REWRITE_PROMPT = (
    "Твой предыдущий ответ нарушает инварианты проекта.\n\n"
    "НАРУШЕНО:\n<<VIOLATIONS>>\n\n"
    "Перепиши ответ так, чтобы он был отказом: назови номер и формулировку нарушенного "
    "инварианта, объясни, почему предложенное невозможно именно из-за него, и на этом "
    "остановись. Не предлагай обходных путей, альтернативных технологий и компромиссов. "
    "Две-четыре фразы, по делу."
)


def _json_from(text):
    raw = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def audit(model, invariants, question, answer):
    """Независимая проверка готового ответа. Возвращает (нарушения, токены)."""
    if not invariants:
        return [], 0

    listing = "\n".join(f"{number}. [{item['kind']}] {item['text']}"
                        for number, item in numbered(invariants))
    result = providers.call(
        model,
        [{"role": "user", "content": AUDIT_PROMPT
          .replace("<<LIST>>", listing)
          .replace("<<QUESTION>>", question)
          .replace("<<ANSWER>>", answer[:2000])}],
        temperature=0.0,
        max_tokens=400,
    )
    tokens = result["prompt_tokens"] + result["completion_tokens"]

    parsed = _json_from(result["text"])
    if not isinstance(parsed, dict):
        return [], tokens

    found = []
    for item in parsed.get("violations", []) or []:
        if not isinstance(item, dict):
            continue
        number = item.get("number")
        if not isinstance(number, int) or not 1 <= number <= len(invariants):
            continue
        found.append({
            "number": number,
            "invariant": invariants[number - 1]["text"],
            "kind": invariants[number - 1]["kind"],
            "quote": str(item.get("quote", "")).strip()[:200],
            "why": str(item.get("why", "")).strip(),
        })
    return found, tokens


def rewrite_instruction(violations):
    """Сообщение для перезапроса: что именно нарушено."""
    listing = "\n".join(
        f"- инвариант {v['number']} [{v['kind']}]: {v['invariant']}\n"
        f"  нарушение: {v['why']}" + (f"\n  цитата: «{v['quote']}»" if v["quote"] else "")
        for v in violations)
    return REWRITE_PROMPT.replace("<<VIOLATIONS>>", listing)
