"""TOON — Token-Oriented Object Notation.

Компактная запись тех же данных, что и JSON, но без повторяющихся ключей, скобок
и кавычек. Экономит токены на однородных массивах записей: ключи объявляются один раз
в заголовке, дальше идут только значения.

    users[2]{id,name}:
      1,Алиса
      2,Боб

Спецификация: https://github.com/toon-format/toon
Здесь реализовано подмножество, покрывающее случаи, где формат реально выигрывает:
табличные массивы, инлайн-массивы примитивов и вложенные объекты.
"""

import json

INDENT = "  "
NEEDS_QUOTES = (",", ":", '"', "\n")


def is_scalar(value):
    return value is None or isinstance(value, (str, int, float, bool))


def scalar(value):
    """Значение в строку. Кавычки — только если без них сломается разбор."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)

    text = str(value)
    if any(ch in text for ch in NEEDS_QUOTES) or text.strip() != text:
        return '"' + text.replace('"', '\\"').replace("\n", "\\n") + '"'
    return text


def is_table(value):
    """Массив однородных плоских объектов — единственный случай табличной формы."""
    if not isinstance(value, list) or len(value) < 2:
        return False
    if not all(isinstance(item, dict) for item in value):
        return False

    fields = list(value[0].keys())
    if not fields:
        return False
    return all(
        list(item.keys()) == fields and all(is_scalar(v) for v in item.values())
        for item in value
    )


def encode(data, level=0):
    """Кодирует объект в TOON. На вход — то же, что принял бы json.dumps."""
    pad = INDENT * level
    lines = []

    if isinstance(data, list):
        return encode({"items": data}, level)

    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.append(encode(value, level + 1))

        elif is_table(value):
            fields = list(value[0].keys())
            lines.append(f"{pad}{key}[{len(value)}]{{{','.join(fields)}}}:")
            for item in value:
                row = ",".join(scalar(item[field]) for field in fields)
                lines.append(f"{pad}{INDENT}{row}")

        elif isinstance(value, list) and all(is_scalar(item) for item in value):
            inline = ",".join(scalar(item) for item in value)
            lines.append(f"{pad}{key}[{len(value)}]: {inline}")

        elif isinstance(value, list):
            # Неоднородный массив: таблицей не выразить, разворачиваем по элементам.
            lines.append(f"{pad}{key}[{len(value)}]:")
            for item in value:
                if isinstance(item, dict):
                    lines.append(encode(item, level + 1))
                else:
                    lines.append(f"{pad}{INDENT}{scalar(item)}")

        else:
            lines.append(f"{pad}{key}: {scalar(value)}")

    return "\n".join(lines)


def savings(data):
    """Насколько TOON короче JSON на этих данных. Для отображения в интерфейсе.

    Считаем против обоих вариантов JSON: с отступами (как обычно кладут в промпт)
    и компактного — по нему сравнение строже и честнее.
    """
    pretty = json.dumps(data, ensure_ascii=False, indent=2)
    compact = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    as_toon = encode(data)
    return {
        "json_chars": len(pretty),
        "json_compact_chars": len(compact),
        "toon_chars": len(as_toon),
        "percent": round(100 * (1 - len(as_toon) / len(pretty))) if pretty else 0,
        "percent_compact": round(100 * (1 - len(as_toon) / len(compact))) if compact else 0,
    }


# Набор для кнопки «пример»: однородные записи, где формат выигрывает сильнее всего.
SAMPLE = {
    "shop": "Кофейня у метро",
    "week": 36,
    "sales": [
        {"day": "пн", "cups": 142, "revenue": 32660, "weather": "дождь"},
        {"day": "вт", "cups": 158, "revenue": 36340, "weather": "облачно"},
        {"day": "ср", "cups": 151, "revenue": 34730, "weather": "облачно"},
        {"day": "чт", "cups": 169, "revenue": 38870, "weather": "ясно"},
        {"day": "пт", "cups": 187, "revenue": 43010, "weather": "ясно"},
        {"day": "сб", "cups": 96, "revenue": 22080, "weather": "дождь"},
        {"day": "вс", "cups": 88, "revenue": 20240, "weather": "дождь"},
    ],
    "staff": ["Алиса", "Боб", "Витя"],
}
