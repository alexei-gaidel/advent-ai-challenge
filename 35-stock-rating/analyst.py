"""AI-аналитик: читает срез и формулу, ставит рейтинг 1–10 и объясняет.

Роль AI здесь — не арифметика (её сделал scoring.py), а суждение:
- какие факторы в этой компании важнее (у банка не бывает EBITDA, у экспортёра важен рубль);
- что формула видит неверно (рост посчитан по двум годам, а LTM уже другой; разовый убыток;
  выплата дивидендов больше прибыли — значит, долго так не будет);
- поправки к баллам с причиной, итоговый рейтинг, плюсы, риски, что отслеживать.

Рекомендация выводится кодом из рейтинга по тем же порогам, что и у формулы: модель
не может написать «покупать» при рейтинге 4,1. Если её словесная рекомендация расходится
с порогом — это сохраняется и попадает в замер.

    python3 analyst.py LKOH [deepseek-chat]
"""

import json
import sys

import providers
import reports
import scoring

DEFAULT_MODEL = "deepseek-chat"

# Строки отчётности, которые идут модели как таблица по годам (если есть у компании).
TABLE_ROWS = ["Выручка", "Чистый операц доход", "EBITDA", "Чистая прибыль", "Опер.денежный поток",
              "CAPEX", "FCF", "Чистый долг", "Капитал", "Дивиденд", "Дивиденды/прибыль", "ROE",
              "Дост.осн капитала", "Просроченные кредиты, NPL", "Стоимость риска (CoR)", "Число акций ао"]

SYSTEM = """Ты — аналитик российского фондового рынка. Тебе дают данные по одной акции
Мосбиржи: таблицу отчётности МСФО по годам (LTM — последние 12 месяцев), метрики графика
за год, ключевую ставку ЦБ и баллы формулы по факторам (1–10, посчитаны кодом, числа
верные — не пересчитывай их). Строки из ltm_incomplete_rows в LTM пустые: в последнем
отчёте их не было, и для метрик по ним взят последний полный год — не делай выводов о
динамике этих строк за LTM.

Твоя задача — суждение, которого нет в формуле:
1. Проверь, не вводят ли баллы в заблуждение: рост посчитан по двум последним полным
   годам, а LTM может показывать другое; разовые убытки или прибыли; дивиденды больше
   прибыли; особенности отрасли (банк, экспортёр, рост за счёт долга).
2. Поправь баллы факторов, где это оправдано данными, — с причиной. Не поправляй без причины.
3. Поставь итоговый рейтинг от 1.0 до 10.0 с одним знаком после точки
   (≥7.0 — покупать, 4.5–6.9 — держать, <4.5 — продавать).

Опирайся только на переданные данные и общеизвестную специфику отрасли. Не выдумывай
новостей, прогнозов менеджмента и цифр, которых нет во входе.

Ответ — строго JSON:
{"rating": 6.3,
 "recommendation": "держать",
 "adjustments": [{"factor": "growth", "to": 4.5, "why": "…"}],
 "summary": "2–3 предложения: главный вывод",
 "pros": ["…", "…"],
 "risks": ["…", "…"],
 "watch": "какой показатель в следующем отчёте изменит оценку"}
Ключи factor — только из списка: <<FACTORS>>."""


def fmt(v):
    return None if v is None else round(v, 2)


def facts(snap, result, anonymous=False):
    """Вход для модели — компактный JSON. anonymous=True прячет название и тикер."""
    data = snap["report"]
    table = {}
    for label in TABLE_ROWS:
        row = data["rows"].get(label)
        if row and any(v is not None for v in row.values()):
            unit = data["units"].get(label, "")
            table[f"{label}, {unit}" if unit else label] = {p: row[p] for p in data["periods"]}
    m = result["metrics"]
    return {
        "company": "Компания X" if anonymous else f"{snap['name']} ({snap['ticker']}), {snap['full_name']}",
        "type": "банк" if m["is_bank"] else "небанковская компания",
        "date": snap["as_of"],
        "price": m["price"], "cap_bn_rub": fmt(m["cap_bn"]),
        "last_report": {"period": snap["period"], "published": snap["report_date"]},
        # LTM этих строк неполный (в последнем отчёте их нет) — в метриках вместо LTM взят последний год.
        "ltm_incomplete_rows": [r for r in data.get("ltm_gaps", []) if r in TABLE_ROWS] if snap["period"] == "LTM" else [],
        "key_rate_cbr": snap["key_rate"],
        "report_table": table,
        "metrics": {k: m[k] for k in ("pe", "pbv", "ev_ebitda", "nd_ebitda", "roe", "margin",
                                      "capital_adequacy", "revenue_growth", "income_growth",
                                      "growth_years", "div_yield", "payout", "earnings_yield",
                                      "rate_premium") if m.get(k) is not None},
        "chart_1y": {k: m[k] for k in ("return_1y", "index_return_1y", "rel_return_1y", "vs_sma200",
                                       "volatility", "max_drawdown", "high_52w", "low_52w")},
        "formula_factors": result["factors"],
        "formula_weights": scoring.WEIGHTS,
        "formula_rating": result["formula"],
    }


def parse(text):
    """JSON из ответа; модели иногда оборачивают его в ```json."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def analyze(snap, result, model=DEFAULT_MODEL, temperature=0.0, anonymous=False):
    """Срез + формула → оценка AI. Возвращает словарь с рейтингом, текстом и расходом."""
    system = SYSTEM.replace("<<FACTORS>>", ", ".join(scoring.WEIGHTS))
    payload = json.dumps(facts(snap, result, anonymous), ensure_ascii=False)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": payload}]
    reply = providers.call(model, messages, temperature=temperature, max_tokens=3000,
                           response_format={"type": "json_object"})
    try:
        answer = parse(reply["text"])
        rating = round(min(10.0, max(1.0, float(answer["rating"]))), 1)
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError(f"модель {model} вернула не тот JSON: {reply['text'][:200]}") from error
    adjustments = [a for a in answer.get("adjustments") or []
                   if isinstance(a, dict) and a.get("factor") in scoring.WEIGHTS]
    said = str(answer.get("recommendation", "")).strip().lower()
    return {
        "model": model, "rating": rating,
        "recommendation": scoring.recommendation(rating),
        "said": said, "consistent": said == scoring.recommendation(rating),
        "adjustments": [{"factor": a["factor"], "from": result["factors"].get(a["factor"]),
                         "to": a.get("to"), "why": str(a.get("why", ""))} for a in adjustments],
        "summary": str(answer.get("summary", "")),
        "pros": [str(x) for x in answer.get("pros") or []],
        "risks": [str(x) for x in answer.get("risks") or []],
        "watch": str(answer.get("watch", "")),
        "prompt_tokens": reply["prompt_tokens"], "completion_tokens": reply["completion_tokens"],
        "cost": reply["cost"] or 0.0, "seconds": reply["seconds"],
    }


if __name__ == "__main__":
    import snapshot
    snap = snapshot.take(sys.argv[1] if len(sys.argv) > 1 else "LKOH")
    result = scoring.score(snap)
    ai = analyze(snap, result, sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODEL)
    print("формула", result["formula"], result["factors"])
    print(json.dumps(ai, ensure_ascii=False, indent=1))
