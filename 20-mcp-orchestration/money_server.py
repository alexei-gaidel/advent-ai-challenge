"""MCP-сервер «деньги»: курс ЦБ и расчёт бюджета. Данные — cbr-xml-daily.ru, без ключа.

    rates(currency)            → курс валюты к рублю и rate_id
    budget(items, rate_id)     → сумма в рублях и в валюте, budget_id

Бюджет пересчитывается только по rate_id, который выдал этот сервер: курс нельзя
передать числом «из головы», его надо сначала получить.

    python3 money_server.py              # ждёт JSON-RPC на stdin
    python3 money_server.py --selftest   # прогон инструментов без клиента
"""

import json
import urllib.error
import urllib.request

import mcp_server
from mcp_server import require

SERVER_INFO = {"name": "advent-money-server", "version": "1.0.0"}

CBR_URL = "https://www.cbr-xml-daily.ru/daily_json.js"

TOOLS = [
    {
        "name": "rates",
        "description": ("Официальный курс ЦБ РФ для валюты (EUR, USD, CNY, KZT …) "
                        "к рублю. Возвращает rate_id, который нужен инструменту budget."),
        "inputSchema": {
            "type": "object",
            "properties": {"currency": {"type": "string", "description": "код валюты, например EUR"}},
            "required": ["currency"],
        },
    },
    {
        "name": "budget",
        "description": ("Складывает статьи расходов в рублях и пересчитывает итог в валюту "
                        "по rate_id из rates этого сервера. Возвращает budget_id."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "статьи расходов",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"},
                                       "rub": {"type": "number", "description": "сумма в рублях"}},
                        "required": ["name", "rub"],
                    },
                },
                "rate_id": {"type": "string", "description": "rate_id из rates"},
            },
            "required": ["items", "rate_id"],
        },
    },
]

RATES = {}
BUDGETS = {}


def fetch_rates():
    request = urllib.request.Request(CBR_URL, headers={"User-Agent": "advent-ai-challenge/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            # Файл отдаётся как javascript, но внутри чистый JSON.
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"cbr-xml-daily ответил {error.code}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"сеть недоступна: {error.reason}") from error
    except TimeoutError as error:
        raise RuntimeError("cbr-xml-daily не ответил вовремя") from error


def rates(arguments):
    code = require(arguments, "currency").upper()
    data = fetch_rates()
    valute = data["Valute"].get(code)
    if not valute:
        raise ValueError(f"валюты {code} нет в курсах ЦБ")

    per_unit = valute["Value"] / valute["Nominal"]
    date = data["Date"][:10]
    rate_id = f"rate-{code}-{date.replace('-', '')}"
    RATES[rate_id] = {"code": code, "rub": per_unit, "date": date, "name": valute["Name"]}
    return (f"rate_id: {rate_id}\n"
            f"1 {code} ({valute['Name']}) = {per_unit:.4f} ₽ по курсу ЦБ на {date}")


def budget(arguments):
    rate_id = require(arguments, "rate_id")
    rate = RATES.get(rate_id)
    if not rate:
        raise ValueError(f"rate_id {rate_id} не выдавался — сначала вызови rates")

    items = arguments.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("items должен быть непустым списком {name, rub}")

    lines, total = [], 0.0
    for item in items:
        try:
            rub = float(item["rub"])
            name = str(item["name"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"статья расходов без name или rub: {item}") from error
        if rub < 0:
            raise ValueError(f"отрицательная сумма у «{name}»")
        total += rub
        lines.append(f"  {name}: {rub:,.0f} ₽".replace(",", " "))

    converted = total / rate["rub"]
    budget_id = f"bud-{len(BUDGETS) + 1}-{rate['code']}"
    BUDGETS[budget_id] = {"total_rub": total, "converted": converted, "code": rate["code"]}
    return (f"budget_id: {budget_id}\n" + "\n".join(lines)
            + f"\nитого: {total:,.0f} ₽ = {converted:,.2f} {rate['code']}".replace(",", " ")
            + f" (курс {rate['rub']:.4f} на {rate['date']})")


HANDLERS = {"rates": rates, "budget": budget}


def run_tool(name, arguments):
    handler = HANDLERS.get(name)
    if not handler:
        raise ValueError(f"нет такого инструмента: {name}")
    return handler(arguments)


def selftest():
    print("→ rates EUR")
    got = run_tool("rates", {"currency": "eur"})
    print("  " + got.replace("\n", "\n  "))
    rate_id = got.split("rate_id: ")[1].split("\n")[0]

    print("\n→ budget")
    items = [{"name": "билет", "rub": 15000}, {"name": "поезд", "rub": 12400}]
    print("  " + run_tool("budget", {"items": items, "rate_id": rate_id}).replace("\n", "\n  "))

    print("\n=== защита от выдуманных входов ===")
    mcp_server.refuse("выдуманный rate_id", run_tool, "budget",
                      {"items": items, "rate_id": "rate-EUR-20200101"})
    mcp_server.refuse("пустые статьи", run_tool, "budget", {"items": [], "rate_id": rate_id})
    mcp_server.refuse("неизвестная валюта", run_tool, "rates", {"currency": "XXX"})


if __name__ == "__main__":
    mcp_server.main(SERVER_INFO, TOOLS, run_tool, selftest)
