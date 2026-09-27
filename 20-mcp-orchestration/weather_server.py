"""MCP-сервер «погода»: поиск города и прогноз на дату. Данные — open-meteo, без ключа.

    search(query)               → список городов с place_id
    forecast(place_id, date)    → прогноз на день и forecast_id

Прогноз принимает только place_id, выданный поиском в этом же сервере: координат
модель не видит и придумать их не может. Поэтому «сначала search, потом forecast» —
не пожелание из промпта, а условие, без которого второй вызов получает отказ.

    python3 weather_server.py              # ждёт JSON-RPC на stdin
    python3 weather_server.py --selftest   # прогон инструментов без клиента
"""

import datetime
import json
import urllib.error
import urllib.parse
import urllib.request

import mcp_server
from mcp_server import require

SERVER_INFO = {"name": "advent-weather-server", "version": "1.0.0"}

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HORIZON_DAYS = 15

TOOLS = [
    {
        "name": "search",
        "description": ("Ищет город по названию и возвращает кандидатов с place_id, "
                        "регионом и страной. place_id нужен инструменту forecast."),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "название города"}},
            "required": ["query"],
        },
    },
    {
        "name": "forecast",
        "description": ("Прогноз погоды на один день: температура, осадки, ветер. "
                        "Принимает place_id из search этого сервера и дату YYYY-MM-DD "
                        f"(не дальше {HORIZON_DAYS} дней вперёд). Возвращает forecast_id."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "place_id": {"type": "string", "description": "place_id из search"},
                "date": {"type": "string", "description": "дата YYYY-MM-DD"},
            },
            "required": ["place_id", "date"],
        },
    },
]

# Коды погоды WMO → человеческие слова (сокращённая таблица open-meteo).
WEATHER_CODES = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "изморозь", 51: "лёгкая морось", 53: "морось", 55: "сильная морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь",
    67: "сильный ледяной дождь", 71: "небольшой снег", 73: "снег", 75: "сильный снег",
    77: "снежная крупа", 80: "ливень", 81: "ливни", 82: "сильные ливни",
    85: "снегопад", 86: "сильный снегопад", 95: "гроза", 96: "гроза с градом",
    99: "сильная гроза с градом",
}

# Сервер живёт, пока открыт клиент: выданные идентификаторы хранятся в памяти процесса.
PLACES = {}
FORECASTS = {}


def fetch_json(url, params):
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        # Без своего User-Agent геокодер open-meteo отвечает 403.
        headers={"User-Agent": "advent-ai-challenge/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"open-meteo ответил {error.code}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"сеть недоступна: {error.reason}") from error
    except TimeoutError as error:
        raise RuntimeError("open-meteo не ответил вовремя") from error


def search(arguments):
    query = require(arguments, "query")
    data = fetch_json(GEO_URL, {"name": query, "count": 3, "language": "ru"})
    results = data.get("results") or []
    if not results:
        raise ValueError(f"город «{query}» не найден")

    lines = []
    for place in results:
        place_id = f"place-{place['id']}"
        PLACES[place_id] = place
        region = ", ".join(x for x in (place.get("admin1"), place.get("country")) if x)
        people = place.get("population")
        people = f", население {people:,}".replace(",", " ").replace(" ", ",", 1) if people else ""
        lines.append(f"place_id: {place_id} — {place['name']} ({region}){people}")
    return "\n".join(lines)


def forecast(arguments):
    place_id = require(arguments, "place_id")
    date_text = require(arguments, "date")

    place = PLACES.get(place_id)
    if not place:
        raise ValueError(f"place_id {place_id} не выдавался — сначала вызови search")

    try:
        day = datetime.date.fromisoformat(date_text)
    except ValueError as error:
        raise ValueError(f"дата {date_text} не в формате YYYY-MM-DD") from error
    today = datetime.date.today()
    if not today <= day <= today + datetime.timedelta(days=HORIZON_DAYS):
        raise ValueError(f"прогноз есть только на {today} … "
                         f"{today + datetime.timedelta(days=HORIZON_DAYS)}")

    data = fetch_json(FORECAST_URL, {
        "latitude": place["latitude"], "longitude": place["longitude"],
        "daily": ("temperature_2m_max,temperature_2m_min,precipitation_sum,"
                  "precipitation_probability_max,wind_speed_10m_max,weather_code"),
        "timezone": "auto", "start_date": day.isoformat(), "end_date": day.isoformat(),
    })
    daily = data["daily"]
    forecast_id = f"fc-{place['id']}-{day:%Y%m%d}"
    summary = {
        "place": place["name"],
        "date": day.isoformat(),
        "t_min": daily["temperature_2m_min"][0],
        "t_max": daily["temperature_2m_max"][0],
        "precipitation_mm": daily["precipitation_sum"][0],
        "precipitation_chance": daily["precipitation_probability_max"][0],
        "wind_kmh": daily["wind_speed_10m_max"][0],
        "sky": WEATHER_CODES.get(daily["weather_code"][0], f"код {daily['weather_code'][0]}"),
    }
    FORECASTS[forecast_id] = summary
    return (f"forecast_id: {forecast_id}\n"
            f"{summary['place']}, {summary['date']}: {summary['sky']}, "
            f"от {summary['t_min']} до {summary['t_max']} °C, осадки "
            f"{summary['precipitation_mm']} мм (вероятность {summary['precipitation_chance']}%), "
            f"ветер до {summary['wind_kmh']} км/ч")


HANDLERS = {"search": search, "forecast": forecast}


def run_tool(name, arguments):
    handler = HANDLERS.get(name)
    if not handler:
        raise ValueError(f"нет такого инструмента: {name}")
    return handler(arguments)


def selftest():
    print("→ search Екатеринбург")
    found = run_tool("search", {"query": "Екатеринбург"})
    print("  " + found.replace("\n", "\n  "))
    place_id = found.split("place_id: ")[1].split(" ")[0]

    date = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    print(f"\n→ forecast {place_id} {date}")
    print("  " + run_tool("forecast", {"place_id": place_id, "date": date}).replace("\n", "\n  "))

    print("\n=== защита от выдуманных входов ===")
    mcp_server.refuse("чужой place_id", run_tool, "forecast",
                      {"place_id": "place-1", "date": date})
    mcp_server.refuse("дата за горизонтом", run_tool, "forecast",
                      {"place_id": place_id, "date": "2030-01-01"})
    mcp_server.refuse("без даты", run_tool, "forecast", {"place_id": place_id})


if __name__ == "__main__":
    mcp_server.main(SERVER_INFO, TOOLS, run_tool, selftest)
