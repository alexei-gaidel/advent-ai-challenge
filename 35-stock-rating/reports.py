"""Отчётность МСФО: годовые таблицы smart-lab.ru/q/<TICKER>/f/y/MSFO/.

Первоисточник (e-disclosure.ru) скрипту отвечает 403, а отчёты там — PDF. Smart-lab
сводит те же отчёты в одну таблицу по годам плюс LTM (последние 12 месяцев) и даты
публикации. Это вторичный источник — в README это оговорено.

Строки у банков и у остальных разные (у Сбера нет «Выручки», есть «Чистый операц доход»),
поэтому разбирается вся таблица как есть, а нужные метрики достаются по меткам.

    python3 reports.py LKOH
"""

import datetime as dt
import html
import re
import sys

import moex

URL = "https://smart-lab.ru/q/{ticker}/f/y/MSFO/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"

# Метка в таблице → короткое имя. Ищем по началу строки: «Выручка , млрд руб».
METRICS = {
    "revenue": ["Выручка", "Чистый операц доход"],
    "net_income": ["Чистая прибыль"],
    "ebitda": ["EBITDA"],
    "ocf": ["Опер.денежный поток"],
    "capex": ["CAPEX"],
    "fcf": ["FCF"],
    "debt": ["Долг"],
    "net_debt": ["Чистый долг"],
    "equity": ["Капитал", "Баланс стоимость", "Чистые активы"],
    "dividend": ["Дивиденд"],
    "payout": ["Дивиденды/прибыль"],
    "shares": ["Число акций ао"],
    "roe": ["ROE"],
    "capital_adequacy": ["Дост.осн капитала"],
    "npl": ["Просроченные кредиты, NPL"],
}


def number(text):
    """«1 251» → 1251.0, «17.0%» → 17.0, «» → None."""
    text = text.replace("\xa0", "").replace(" ", "").replace("%", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def cells(row_html):
    return [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S)]


def parse(page):
    """HTML страницы → {periods, dates, rows: {метка: {период: число}}, units}."""
    start = page.find("<table")
    if start < 0:
        raise RuntimeError("на странице нет таблицы отчётности")
    rows = [cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>",
                                         page[start:page.find("</table>", start)], re.S)]
    header = next(r for r in rows if any(re.fullmatch(r"\d{4}", c) for c in r))
    # Колонки периодов: годы и LTM; пустые колонки-разделители пропускаем.
    columns = {i: ("LTM" if c.startswith("LTM") else c) for i, c in enumerate(header)
               if re.fullmatch(r"\d{4}", c) or c.startswith("LTM")}
    # В строках данных на одну ячейку больше (значок «?»), выравниваем по правому краю.
    shift = lambda r: len(r) - len(header)
    result = {"periods": list(columns.values()), "dates": {}, "rows": {}, "units": {}}
    for r in rows:
        if len(r) < len(header) or not r[0] or r is header:
            continue
        label, _, unit = r[0].partition(" , ")
        values = {p: r[i + shift(r)] for i, p in columns.items()}
        if label == "Дата отчета":
            result["dates"] = {p: v for p, v in values.items() if v}
            continue
        nums = {p: number(v) for p, v in values.items()}
        if any(v is not None for v in nums.values()):
            result["rows"][label.strip()] = nums
            result["units"][label.strip()] = unit.strip()
    return result


# Строки, которые есть в каждом отчёте. Дивиденды сюда не входят: их платят не каждый
# квартал, пустая клетка там нормальна.
CHECKED = {"Выручка", "Чистый операц доход", "Операционная прибыль", "EBITDA", "Чистая прибыль",
           "Опер.денежный поток", "CAPEX", "FCF", "Долг", "Наличность", "Чистый долг", "Капитал",
           "Баланс стоимость", "Чистые активы", "Дост.осн капитала", "Просроченные кредиты, NPL"}


def gaps_in_ltm(ticker, data):
    """Строки, у которых LTM собран не из всех отчётов текущего года.

    Smart-lab складывает LTM из полугодий и кварталов; если в последнем отчёте строки нет
    (у Полюса в 2026Q2 нет EBITDA), LTM молча становится суммой за полгода. Проверка:
    строка должна быть заполнена во всех кварталах после последнего годового отчёта,
    опубликованных к дате LTM.
    """
    ltm_date = data["dates"].get("LTM")
    years = [p for p in data["periods"] if p != "LTM"]
    if not ltm_date or not years:
        return set()
    page = moex.fetch(URL.format(ticker=ticker).replace("/f/y/", "/f/q/"), ttl=86400, raw=True)
    start = page.find("<table")
    rows = [cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>",
                                         page[start:page.find("</table>", start)], re.S)]
    header = next((r for r in rows if any(re.fullmatch(r"\d{4}Q\d", c) for c in r)), None)
    dates = next((r for r in rows if r and r[0] == "Дата отчета"), None)
    if not header or not dates:
        return set()
    day = lambda s: dt.datetime.strptime(s, "%d.%m.%Y").date()
    shift = len(dates) - len(header)
    needed = [i for i, c in enumerate(header)
              if re.fullmatch(r"\d{4}Q\d", c) and c[:4] > years[-1]
              and dates[i + shift] and day(dates[i + shift]) <= day(ltm_date)]
    gaps = set()
    for r in rows:
        label = r[0].partition(" , ")[0].strip() if r else ""
        if label in CHECKED and label in data["rows"] and len(r) == len(dates):
            if any(number(r[i + shift]) is None for i in needed):
                gaps.add(label)
    return gaps


def load(ticker):
    """Таблица МСФО по тикеру (кэш на сутки). Неполные значения LTM заменяются на None."""
    page = moex.fetch(URL.format(ticker=ticker), ttl=86400, raw=True)
    if "Финансовая отчетность" not in page and "финансовая отчетность" not in page:
        raise RuntimeError(f"smart-lab не знает отчётности {ticker}")
    data = parse(page)
    data["ticker"] = ticker
    data["source"] = URL.format(ticker=ticker)
    # «0.00» у smart-lab — заглушка «нет данных»: чистый долг ноль при пустом долге,
    # нулевой капитал, ноль акций. Настоящих нулей в этих строках не бывает.
    debt = data["rows"].get("Долг", {})
    for label in ("Чистый долг", "Капитал", "Баланс стоимость", "Чистые активы", "Число акций ао"):
        for p, v in data["rows"].get(label, {}).items():
            if v == 0 and (label != "Чистый долг" or debt.get(p) is None):
                data["rows"][label][p] = None
    data["ltm_gaps"] = sorted(gaps_in_ltm(ticker, data))
    for label in data["ltm_gaps"]:
        data["rows"][label]["LTM"] = None
    return data


def metric(data, name, period):
    """Значение метрики за период по первой подходящей метке из METRICS.

    Если LTM у строки выброшен как неполный — берётся последний полный год.
    """
    for label in METRICS[name]:
        # «Долг» не должен совпасть с «Долг/EBITDA», поэтому сравниваем целиком.
        row = data["rows"].get(label)
        if row is None:
            continue
        if row.get(period) is not None:
            return row[period]
        if period == "LTM" and label in data.get("ltm_gaps", ()):
            years = [p for p in data["periods"] if p != "LTM" and row.get(p) is not None]
            if years:
                return row[years[-1]]
    return None


def as_of(data, date):
    """Срез «что было известно на дату»: только годы, отчёт по которым уже вышел, без LTM.

    Нужен для проверки задним числом: модель не должна видеть будущие отчёты.
    """
    known = []
    for p in data["periods"]:
        if p == "LTM":
            continue
        published = data["dates"].get(p)
        if published and dt.datetime.strptime(published, "%d.%m.%Y").date().isoformat() <= date:
            known.append(p)
    sliced = dict(data, periods=known, rows={k: {p: v[p] for p in known} for k, v in data["rows"].items()},
                  dates={p: data["dates"][p] for p in known})
    return sliced


def last_period(data):
    """Самый свежий период: LTM, если есть, иначе последний год с прибылью."""
    for p in reversed(data["periods"]):
        if metric(data, "net_income", p) is not None:
            return p
    return None


if __name__ == "__main__":
    data = load(sys.argv[1] if len(sys.argv) > 1 else "LKOH")
    print("периоды:", data["periods"], "даты:", data["dates"])
    for name in METRICS:
        print(f"{name:17}", [metric(data, name, p) for p in data["periods"]])
    print("строк всего:", len(data["rows"]))
