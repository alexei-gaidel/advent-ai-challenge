"""Формула: метрики из среза и баллы по факторам 1–10. Всё считает код, без LLM.

Это опора для AI: модель не пересчитывает P/E в уме (там она и ошибается), а получает
готовые числа и баллы и решает, что в них важнее. Формульный рейтинг — средневзвешенное
факторов; AI-рейтинг сравнивается с ним в замере.

Шкалы — кусочно-линейные: точки (значение метрики → балл), между ними интерполяция.
Пороги подобраны под российский рынок 2024–2026 (ставка ЦБ 14–21%, P/E 3–8 — норма).

    python3 scoring.py LKOH [2025-04-01]
"""

import datetime as dt
import math
import sys

import reports

WEIGHTS = {
    "valuation": 0.20, "rate_premium": 0.10, "growth": 0.15, "profitability": 0.15,
    "debt": 0.10, "dividends": 0.10, "trend": 0.15, "risk": 0.05,
}
FACTOR_NAMES = {
    "valuation": "Оценка (P/E, P/BV, EV/EBITDA)",
    "rate_premium": "Доходность прибыли против ставки ЦБ",
    "growth": "Рост выручки и прибыли",
    "profitability": "Рентабельность (ROE, маржа)",
    "debt": "Долговая нагрузка / достаточность капитала",
    "dividends": "Дивиденды",
    "trend": "Тренд графика против IMOEX",
    "risk": "Риск (волатильность, просадка)",
}
SCALES = {
    "pe": [(0, 10), (3, 9.5), (5, 8.5), (8, 6.5), (12, 4.5), (20, 2), (35, 1)],
    "pbv": [(0.3, 9.5), (0.5, 9), (1, 7), (2, 4.5), (4, 2), (8, 1)],
    "ev_ebitda": [(1, 10), (2, 9.5), (4, 7), (6, 5), (10, 2.5), (15, 1)],
    "premium": [(-15, 1), (-10, 2), (-5, 3.5), (0, 5), (5, 7.5), (10, 9.5), (15, 10)],
    "revenue_growth": [(-30, 1), (-10, 3), (0, 4.5), (10, 6), (20, 8), (40, 10)],
    "income_growth": [(-60, 1), (-25, 3), (0, 4.5), (20, 7), (50, 9), (100, 10)],
    "roe": [(-10, 1), (0, 2), (5, 3), (15, 6), (25, 8.5), (35, 10)],
    "margin": [(-5, 1), (0, 2), (5, 4), (10, 6), (20, 8.5), (30, 10)],
    "nd_ebitda": [(-1, 10), (0, 9.5), (1, 8), (2, 6), (3, 4), (4, 2), (5, 1)],
    "capital": [(8, 2), (10, 5), (12, 7), (14, 9), (16, 10)],
    "div_yield": [(0, 2), (4, 4.5), (8, 6.5), (12, 8.5), (16, 10)],
    "rel_return": [(-40, 1), (-20, 3), (0, 5), (20, 7), (40, 9), (60, 10)],
    "vs_sma200": [(-30, 1), (-20, 2), (0, 5), (20, 8), (40, 9)],
    "volatility": [(15, 9), (20, 8), (30, 6), (45, 4), (60, 2), (80, 1)],
    "drawdown": [(-60, 1), (-40, 3), (-25, 6), (-10, 9), (0, 10)],
}


def scale(name, x):
    points = SCALES[name]
    if x is None:
        return None
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def pct(new, old):
    if new is None or old is None or old <= 0:
        return None
    return (new / old - 1) * 100


def price_on(series, date):
    """Цена закрытия на дату или ближайшую раньше (у индекса нет выходных свечей)."""
    before = [c["close"] for c in series if c["date"] <= date]
    return before[-1] if before else None


def sma(closes, n):
    return sum(closes[-n:]) / n if len(closes) >= n else None


def chart_metrics(candles, index, as_of):
    """Метрики графика за год: доходность, против IMOEX, SMA, волатильность, просадка."""
    year_ago = (dt.date.fromisoformat(as_of) - dt.timedelta(days=365)).isoformat()
    year = [c for c in candles if c["date"] >= year_ago]
    closes = [c["close"] for c in candles]
    last = closes[-1]
    ret = pct(last, year[0]["close"])
    ret_index = pct(price_on(index, as_of), price_on(index, year[0]["date"]))
    daily = [math.log(b["close"] / a["close"]) for a, b in zip(year, year[1:]) if a["close"] and b["close"]]
    avg = sum(daily) / len(daily)
    volatility = math.sqrt(sum((d - avg) ** 2 for d in daily) / (len(daily) - 1) * 252) * 100
    peak, drawdown = 0, 0
    for c in year:
        peak = max(peak, c["close"])
        drawdown = min(drawdown, (c["close"] / peak - 1) * 100)
    sma200 = sma(closes, 200)
    return {
        "return_1y": ret, "index_return_1y": ret_index,
        "rel_return_1y": None if ret is None or ret_index is None else ret - ret_index,
        "sma50": sma(closes, 50), "sma200": sma200,
        "vs_sma200": pct(last, sma200),
        "volatility": volatility, "max_drawdown": drawdown,
        "high_52w": max(c["high"] for c in year), "low_52w": min(c["low"] for c in year),
    }


def full_years(data):
    return [p for p in data["periods"] if p != "LTM"]


def report_metrics(snap):
    """Мультипликаторы считаются по текущей цене, а не по цене на дату отчёта (как у smart-lab)."""
    data, period, price = snap["report"], snap["period"], snap["quote"]["price"]
    m = lambda name, p=period: reports.metric(data, name, p)
    is_bank = "Чистый операц доход" in data["rows"]
    # Прибыль делится и на обыкновенные, и на привилегированные акции.
    shares = (m("shares") or 0) + ((data["rows"].get("Число акций ап") or {}).get(period) or 0)
    shares = shares * 1e6 if shares else snap["quote"]["shares"]
    cap = price * shares / 1e9                      # млрд руб, как в отчётности
    net, revenue, ebitda = m("net_income"), m("revenue"), m("ebitda")
    equity = m("equity") or None                    # 0.00 у smart-lab значит «нет данных»
    net_debt = m("net_debt")
    years = [p for p in full_years(data) if m("revenue", p) and m("net_income", p) is not None]
    y1, y0 = (years[-1], years[-2]) if len(years) >= 2 else (None, None)
    dividend = m("dividend")
    return {
        "is_bank": is_bank, "cap_bn": cap, "price": price,
        "pe": cap / net if net and net > 0 else None, "loss": net is not None and net <= 0,
        "pbv": cap / equity if equity and equity > 0 else None,
        "ev_ebitda": (cap + net_debt) / ebitda if ebitda and ebitda > 0 and net_debt is not None and not is_bank else None,
        "nd_ebitda": net_debt / ebitda if ebitda and ebitda > 0 and net_debt is not None and not is_bank else None,
        "roe": m("roe"), "margin": None if is_bank or not revenue or net is None else net / revenue * 100,
        "capital_adequacy": m("capital_adequacy") if is_bank else None,
        "npl": m("npl") if is_bank else None,
        "revenue_growth": pct(m("revenue", y1), m("revenue", y0)) if y1 else None,
        "income_growth": pct(m("net_income", y1), m("net_income", y0)) if y1 else None,
        "growth_years": f"{y0}→{y1}" if y1 else "",
        "div_yield": dividend / price * 100 if dividend is not None and price else None,
        "payout": m("payout"),
        "net_income_bn": net, "revenue_bn": revenue, "ebitda_bn": ebitda,
    }


def score(snap):
    """Срез → {metrics, factors: {имя: {score, inputs}}, formula}."""
    r = report_metrics(snap)
    c = chart_metrics(snap["candles"], snap["index"], snap["as_of"])
    earnings_yield = 100 / r["pe"] if r["pe"] else (-5 if r["loss"] else None)
    premium = None if earnings_yield is None or snap["key_rate"] is None else earnings_yield - snap["key_rate"]
    pe_score = 1.0 if r["loss"] else scale("pe", r["pe"])
    factors = {
        "valuation": mean([pe_score, scale("pbv", r["pbv"]), scale("ev_ebitda", r["ev_ebitda"])]),
        "rate_premium": scale("premium", premium),
        "growth": mean([scale("revenue_growth", r["revenue_growth"]),
                        scale("income_growth", r["income_growth"])]),
        "profitability": mean([scale("roe", r["roe"]), scale("margin", r["margin"])]),
        "debt": scale("capital", r["capital_adequacy"]) if r["is_bank"] else scale("nd_ebitda", r["nd_ebitda"]),
        "dividends": scale("div_yield", r["div_yield"] or 0.0),
        "trend": mean([scale("rel_return", c["rel_return_1y"]), scale("vs_sma200", c["vs_sma200"])]),
        "risk": mean([scale("volatility", c["volatility"]), scale("drawdown", c["max_drawdown"])]),
    }
    # Фактор без данных выпадает, его вес делится между остальными.
    known = {k: v for k, v in factors.items() if v is not None}
    total = sum(WEIGHTS[k] for k in known)
    formula = sum(WEIGHTS[k] * v for k, v in known.items()) / total
    metrics = dict(r, **c, earnings_yield=earnings_yield, rate_premium=premium, key_rate=snap["key_rate"])
    return {
        "metrics": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in metrics.items()},
        "factors": {k: (round(v, 1) if v is not None else None) for k, v in factors.items()},
        "formula": round(min(10.0, max(1.0, formula)), 1),
    }


def recommendation(rating):
    """Порог один для формулы и AI — чтобы рекомендации были сравнимы."""
    if rating >= 7.0:
        return "покупать"
    if rating >= 4.5:
        return "держать"
    return "продавать"


if __name__ == "__main__":
    import json
    import snapshot
    snap = snapshot.take(sys.argv[1] if len(sys.argv) > 1 else "LKOH", sys.argv[2] if len(sys.argv) > 2 else None)
    result = score(snap)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print("формула:", result["formula"], recommendation(result["formula"]))
