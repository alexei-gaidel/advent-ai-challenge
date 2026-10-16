"""Чат над дашбордом: человек пишет словами, модель меняет вид, код пересчитывает.

Модель не видит сырых данных и не считает. У неё один инструмент — set_view: какой
показатель, какие МО, сортировка, абсолютные числа или процент исполнения. Допустимые
значения подставляются в схему инструмента из текущей темы (enum), поэтому показать
несуществующий показатель или город она не может. После пересчёта модель получает
готовые цифры и пишет 1–3 предложения вывода.

Числовая страховка: каждое число из её текста ищется среди чисел на экране (с учётом
«млрд», «млн», «%» и округления). Не нашлось — число подсвечивается, а ответ помечается.

Плюс обёртки для web.py: категории, темы, открыть тему.
"""

import json
import re
import time

import catalog
import dashboard
import portal
import providers
import recipes
import topics as topic_index

MODEL = "deepseek-chat"

SYSTEM = """Ты — аналитик открытых данных ХМАО — Югры. Пользователь смотрит дашборд по теме
«<<TOPIC>>». Сейчас на экране:
<<SCREEN>>

Если просьба меняет то, ЧТО показать (показатель, муниципалитеты, сортировку, процент
исполнения), вызови set_view. Потом ответь 1–3 предложениями по-русски по цифрам с
экрана: только числа, которые есть на экране, округляй до млрд/млн с одним знаком.
Если данных для ответа нет — так и скажи, не придумывай. Помни про даты: если у МО
разные отчётные даты или дата взята из версии файла, предупреди об этом."""


def _money(value, unit):
    if value is None:
        return "—"
    if unit == "%":
        return f"{value:.1f}%"
    if abs(value) >= 1e9:
        return f"{value / 1e9:.1f} млрд руб."
    if abs(value) >= 1e6:
        return f"{value / 1e6:.1f} млн руб."
    return f"{value:,.0f}".replace(",", " ")


def screen_text(dash):
    """То, что видит пользователь, — текстом для модели."""
    lines = [f"вид: {json.dumps(dash['view'], ensure_ascii=False)}"]
    lines.append("итоги: " + "; ".join(
        f"{k['title']} = {_money(k['value'], k['unit'])} ({k['count']} из {k['of']} МО)"
        for k in dash["kpis"]))
    lines.append(f"график «{dash['chart']['title']}»: " + "; ".join(
        f"{s['mo']} {_money(s['value'], dash['chart']['unit'])}" for s in dash["chart"]["series"]))
    for f in dash["freshness"]:
        note = "дата версии файла, в данных её нет" if f["from_version"] else f"отчёт на {f['date']}"
        if f["stale"]:
            note += f", отстаёт на {f['lag_days']} дн."
        lines.append(f"  {f['mo']}: {note}")
    if dash["skipped"]:
        lines.append("не сведено: " + "; ".join(f"{s['mo']} — {s['reason'][:80]}"
                                                for s in dash["skipped"]))
    return "\n".join(lines)


def view_tool(table):
    keys = [i["key"] for i in table["indicators"]]
    mos = sorted({r["mo"] for r in table["rows"]})
    titles = "; ".join(f"{i['key']} — {i.get('title', '')}" for i in table["indicators"])
    return [{"type": "function", "function": {
        "name": "set_view",
        "description": "Изменить, что показывает дашборд. Показатели: " + titles,
        "parameters": {"type": "object", "properties": {
            "indicator": {"type": "string", "enum": keys},
            "mo": {"type": "array", "items": {"type": "string", "enum": mos},
                   "description": "какие МО показать; пустой список — все"},
            "sort": {"type": "string", "enum": list(dashboard.SORTS)},
            "mode": {"type": "string", "enum": list(dashboard.MODES),
                     "description": "absolute — значения показателя, execution — % исполнения плана"},
        }, "required": []}}}]


# --- числовая страховка -------------------------------------------------------------------

NUMBER = re.compile(r"(?<![\d.,])(\d{1,3}(?:[  ]\d{3})+|\d+)([.,]\d+)?\s*(млрд|млн|тыс|%)?",
                    re.I)
DATE_LIKE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2}")
SCALE = {"млрд": 1e9, "млн": 1e6, "тыс": 1e3}


def check_numbers(text, dash):
    """Числа из текста, которых нет на экране. Годы, даты и счётчики «4 из 5» не проверяются."""
    screen = dashboard.numbers_on_screen(dash)
    screen += [k["count"] for k in dash["kpis"]] + [k["of"] for k in dash["kpis"]]
    screen.append(len(dash["table"]["rows"]))
    clean = DATE_LIKE.sub(" ", text or "")
    unknown = []
    for match in NUMBER.finditer(clean):
        whole = match.group(1).replace(" ", "").replace(" ", "")
        value = float(whole + (match.group(2) or "").replace(",", "."))
        suffix = (match.group(3) or "").lower()
        if not suffix and (1900 <= value <= 2100 and not match.group(2)):
            continue                                    # год
        scale = SCALE.get(suffix, 1)
        value *= scale
        # Допуск — от точности записи: «5.1 млрд» значит 5,05–5,15 млрд, «65,8%» — 65,75–65,85.
        # Первая версия брала 0,6% от значения и ругалась на честно округлённые числа.
        decimals = len(match.group(2)) - 1 if match.group(2) else 0
        tolerance = 0.5 * 10 ** -decimals * scale + 1e-9
        if not any(abs(value - x) <= tolerance for x in screen):
            unknown.append(match.group(0).strip())
    return unknown


# --- приложение ---------------------------------------------------------------------------

class OpenDataAssistant:
    def __init__(self, model=MODEL):
        self.model = model
        self.tables = {}          # (slug, номер темы) → сведённая таблица

    @staticmethod
    def categories():
        return portal.categories()

    @staticmethod
    def topics(slug):
        index = topic_index.build(slug)
        return {"slug": slug, "category": index["category"], "count": index["count"],
                "stats": index.get("stats", {}), "cached": index.get("cached"),
                "topics": [{"n": n, "title": t["title"], "description": t["description"],
                            "comparable": t["comparable"], "count": len(t["datasets"]),
                            "publishers": t["publishers"]}
                           for n, t in enumerate(index["topics"])]}

    def table(self, slug, n):
        key = (slug, int(n))
        if key not in self.tables:
            index = topic_index.build(slug)
            if not 0 <= int(n) < len(index["topics"]):
                raise ValueError("нет такой темы")
            self.tables[key] = recipes.build(index["topics"][int(n)])
        return self.tables[key]

    def open(self, slug, n, view=None):
        table = self.table(slug, n)
        dash = dashboard.build(table, view)
        dash["build"] = {k: table[k] for k in ("prompt_tokens", "completion_tokens", "cost",
                                               "seconds", "cached")}
        return dash

    def chat(self, slug, n, view, message, temperature=0.3):
        message = (message or "").strip()
        if not message:
            raise ValueError("пустое сообщение")
        started = time.monotonic()
        table = self.table(slug, n)
        dash = dashboard.build(table, view)
        tools = view_tool(table)
        messages = [{"role": "system", "content": SYSTEM.replace("<<TOPIC>>", table["topic"])
                     .replace("<<SCREEN>>", screen_text(dash))},
                    {"role": "user", "content": message}]
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
        calls = []
        for round_number in range(3):
            reply = providers.call(self.model, messages, temperature=temperature, max_tokens=700,
                                   tools=tools if round_number < 2 else None)
            for key in usage:
                usage[key] = round(usage[key] + (reply[key] or 0), 6)
            if not reply["tool_calls"]:
                break
            messages.append(reply["message"])
            for call in reply["tool_calls"]:
                try:
                    wanted = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    wanted = {}
                merged = {**dash["view"], **{k: v for k, v in wanted.items()
                                             if k in ("indicator", "mo", "sort", "mode")}}
                dash = dashboard.build(table, merged)
                calls.append({"arguments": wanted, "view": dash["view"], "notes": dash["notes"]})
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                 "content": "Пересчитано. Теперь на экране:\n" + screen_text(dash)})
        text = reply["text"]
        unknown = check_numbers(text, dash)
        dash["build"] = {k: table[k] for k in ("prompt_tokens", "completion_tokens", "cost",
                                               "seconds", "cached")}
        return {"answer": text, "unknown_numbers": unknown, "calls": calls, "dashboard": dash,
                "seconds": round(time.monotonic() - started, 2), **usage}


if __name__ == "__main__":
    import sys
    bot = OpenDataAssistant()
    result = bot.chat("finansy", int(sys.argv[1]) if len(sys.argv) > 1 else 0, None,
                      " ".join(sys.argv[2:]) or "Где хуже всего исполняется план?")
    print(result["answer"])
    print("вызовы:", json.dumps(result["calls"], ensure_ascii=False))
    print("чисел нет на экране:", result["unknown_numbers"])
    print(screen_text(result["dashboard"]))
