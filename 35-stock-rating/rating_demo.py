"""Замер: насколько рейтингу AI можно доверять.

Часть A — сейчас. 21 акция × deepseek-chat × 3 прогона при
temperature=0:
  - повторяемость: разброс рейтинга между прогонами и смена рекомендации;
  - насколько AI отходит от формулы и в какую сторону;
  - следует ли AI собственным поправкам: его рейтинг против средневзвешенного факторов
    после его же поправок.

Часть B — задним числом. Срезы на 2025-04-01 и 2025-10-01 (только опубликованные к той
дате отчёты, цены до неё, ставка ЦБ на тот день), потом — доходность акции за следующие
12 месяцев минус IMOEX. Название компании скрыто («Компания X»), чтобы модель не
вспоминала, чем всё кончилось; для DeepSeek тот же срез ещё и с названием — проверка утечки.
Метрики: ранговая корреляция рейтинга с будущей избыточной доходностью и разница между
верхней и нижней третью.

    python3 rating_demo.py [runs]   # по умолчанию 3 прогона в части A
"""

import concurrent.futures as cf
import datetime as dt
import json
import pathlib
import statistics
import re
import sys
import threading
import time

import analyst
import moex
import scoring
import snapshot

TICKERS = ["SBER", "VTBR", "T", "MOEX", "LKOH", "ROSN", "GAZP", "NVTK", "TATN", "GMKN", "PLZL",
           "ALRS", "CHMF", "NLMK", "PHOR", "MGNT", "X5", "MTSS", "YDEX", "OZON", "AFLT"]
MODELS = ["deepseek-chat"]
BACKTEST_DATES = ["2025-04-01", "2025-10-01"]
BACKTEST_SKIP = {"X5"}          # торгуется с января 2025 — года графика к апрелю нет
OUT = pathlib.Path(__file__).resolve().parent / "data" / "rating_result.json"


CACHE = pathlib.Path(__file__).resolve().parent / "data" / "demo_cache.json"
CACHE_LOCK = threading.Lock()
# У gpt-oss-120b на Groq лимит 8000 токенов в минуту, а один разбор ~3700 — запросы к
# Groq идут строго по одному, DeepSeek параллельно.
GROQ_LOCK = threading.Lock()


def cached():
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def call(snap, result, model, anonymous=False, run=0):
    """analyze с кэшем (перезапуск не повторяет готовое) и повтором при 429 и обрыве сети."""
    key = f"{snap['ticker']}|{snap['as_of'] if not snap['live'] else 'live'}|{model}|{anonymous}|{run}"
    with CACHE_LOCK:
        hit = cached().get(key)
    if hit:
        return hit
    for attempt in range(12):
        try:
            if model.startswith("openai/"):
                with GROQ_LOCK:
                    out = analyst.analyze(snap, result, model, 0.0, anonymous)
            else:
                out = analyst.analyze(snap, result, model, 0.0, anonymous)
            break
        except RuntimeError as error:
            text = str(error)
            if attempt == 11 or not any(s in text for s in ("429", "Сеть", "Таймаут", "не тот JSON", "503")):
                raise
            wait = re.search(r"try again in ([\d.]+)s", text)
            time.sleep(float(wait.group(1)) + 2 if wait else 20)
    with CACHE_LOCK:
        data = cached()
        data[key] = out
        CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return out


def adjusted(result, ai):
    """Средневзвешенное факторов после поправок самого AI — «куда он сам себя привёл»."""
    factors = dict(result["factors"])
    for a in ai["adjustments"]:
        if isinstance(a["to"], (int, float)):
            factors[a["factor"]] = a["to"]
    known = {k: v for k, v in factors.items() if v is not None}
    return sum(scoring.WEIGHTS[k] * v for k, v in known.items()) / sum(scoring.WEIGHTS[k] for k in known)


def spearman(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def forward_excess(ticker, date, days=365):
    """Доходность акции за days дней после date минус IMOEX, в п.п. (без дивидендов)."""
    end = (dt.date.fromisoformat(date) + dt.timedelta(days=days)).isoformat()
    stock = moex.candles(ticker, date, end)
    index = moex.candles(moex.INDEX, date, end, index=True)
    r = (stock[-1]["close"] / stock[0]["close"] - 1) * 100
    ri = (index[-1]["close"] / index[0]["close"] - 1) * 100
    return round(r, 1), round(ri, 1), round(r - ri, 1)


def part_a(runs, pool):
    print(f"\n=== Часть A: сейчас, {len(TICKERS)} акций × {runs} прогона, temperature=0 ===")
    snaps = {t: snapshot.take(t) for t in TICKERS}
    scores = {t: scoring.score(s) for t, s in snaps.items()}
    jobs = {(t, m, i): pool.submit(call, snaps[t], scores[t], m, False, i)
            for t in TICKERS for m in MODELS for i in range(runs)}
    rows = []
    for t in TICKERS:
        row = {"ticker": t, "name": snaps[t]["name"], "formula": scores[t]["formula"],
               "formula_rec": scoring.recommendation(scores[t]["formula"]), "models": {}}
        for m in MODELS:
            out = [jobs[(t, m, i)].result() for i in range(runs)]
            ratings = [o["rating"] for o in out]
            row["models"][m] = {
                "ratings": ratings, "mean": round(statistics.mean(ratings), 2),
                "spread": round(max(ratings) - min(ratings), 1),
                "recs": [o["recommendation"] for o in out],
                "said_mismatch": sum(not o["consistent"] for o in out),
                "self_gap": [round(o["rating"] - adjusted(scores[t], o), 2) for o in out],
                "adjustments": [len(o["adjustments"]) for o in out],
                "tokens": sum(o["prompt_tokens"] + o["completion_tokens"] for o in out),
                "cost": round(sum(o["cost"] for o in out), 6),
                "seconds": round(statistics.mean(o["seconds"] for o in out), 1),
                "sample": {k: out[0][k] for k in ("summary", "adjustments", "watch")},
            }
        rows.append(row)
        cells = " | ".join(f"{m.split('/')[-1]} {row['models'][m]['ratings']} {row['models'][m]['recs'][0]}"
                           for m in MODELS)
        print(f"{t:5} формула {row['formula']:4} {row['formula_rec']:9} | {cells}", flush=True)
    return rows


def part_b(pool):
    print(f"\n=== Часть B: задним числом {', '.join(BACKTEST_DATES)}, доходность за 12 мес. минус IMOEX ===")
    variants = [("deepseek-chat", True), ("deepseek-chat", False)]
    rows = []
    for date in BACKTEST_DATES:
        for t in TICKERS:
            if t in BACKTEST_SKIP:
                continue
            snap = snapshot.take(t, date)
            result = scoring.score(snap)
            ret, ret_index, excess = forward_excess(t, date)
            rows.append({"date": date, "ticker": t, "period": snap["period"], "formula": result["formula"],
                         "return": ret, "index_return": ret_index, "excess": excess,
                         "jobs": {f"{m}|{'anon' if a else 'named'}": pool.submit(call, snap, result, m, a)
                                  for m, a in variants}})
    for r in rows:
        r["ai"] = {}
        for key, job in r.pop("jobs").items():
            o = job.result()
            r["ai"][key] = {"rating": o["rating"], "rec": o["recommendation"], "cost": o["cost"],
                            "tokens": o["prompt_tokens"] + o["completion_tokens"]}
        print(f"{r['date']} {r['ticker']:5} отчёт {r['period']} формула {r['formula']:4} "
              + " ".join(f"{k.split('/')[-1]}={v['rating']}" for k, v in r["ai"].items())
              + f" | за 12 мес. {r['return']:+.1f}% (IMOEX {r['index_return']:+.1f}%) → {r['excess']:+.1f} п.п.", flush=True)
    return rows


def evaluate(rows, key):
    """Качество рейтинга key ('formula' или 'deepseek-chat|anon' …) против будущей доходности."""
    get = (lambda r: r["formula"]) if key == "formula" else (lambda r: r["ai"][key]["rating"])
    rec = (lambda r: scoring.recommendation(r["formula"])) if key == "formula" else (lambda r: r["ai"][key]["rec"])
    xs, ys = [get(r) for r in rows], [r["excess"] for r in rows]
    third = len(rows) // 3
    ordered = sorted(rows, key=get)
    buys = [r["excess"] for r in rows if rec(r) == "покупать"]
    sells = [r["excess"] for r in rows if rec(r) == "продавать"]
    return {
        "spearman": round(spearman(xs, ys), 3),
        "top_third": round(statistics.mean(r["excess"] for r in ordered[-third:]), 1),
        "bottom_third": round(statistics.mean(r["excess"] for r in ordered[:third]), 1),
        "buy_n": len(buys), "buy_beat": sum(x > 0 for x in buys),
        "buy_mean": round(statistics.mean(buys), 1) if buys else None,
        "sell_n": len(sells), "sell_lag": sum(x < 0 for x in sells),
        "sell_mean": round(statistics.mean(sells), 1) if sells else None,
    }


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    started = time.monotonic()
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        live = part_a(runs, pool)
        back = part_b(pool)

    print("\n=== Итоги A ===")
    summary_a = {}
    for m in MODELS:
        ms = [r["models"][m] for r in live]
        s = {
            "mean_spread": round(statistics.mean(x["spread"] for x in ms), 2),
            "max_spread": max(x["spread"] for x in ms),
            "unstable_rec": sum(len(set(x["recs"])) > 1 for x in ms),
            "mean_delta": round(statistics.mean(x["mean"] - r["formula"] for x, r in zip(ms, live)), 2),
            "mean_abs_delta": round(statistics.mean(abs(x["mean"] - r["formula"]) for x, r in zip(ms, live)), 2),
            "rec_vs_formula": sum(x["recs"][0] == r["formula_rec"] for x, r in zip(ms, live)),
            "said_mismatch": sum(x["said_mismatch"] for x in ms),
            "mean_abs_self_gap": round(statistics.mean(abs(g) for x in ms for g in x["self_gap"]), 2),
            "tokens_per_call": round(sum(x["tokens"] for x in ms) / (len(ms) * runs)),
            "cost_per_call": round(sum(x["cost"] for x in ms) / (len(ms) * runs), 5),
            "seconds_per_call": round(statistics.mean(x["seconds"] for x in ms), 1),
        }
        summary_a[m] = s
        print(m, s)

    print("\n=== Итоги B ===")
    summary_b = {}
    for key in ["formula", "deepseek-chat|anon", "deepseek-chat|named"]:
        summary_b[key] = {"all": evaluate(back, key)}
        for date in BACKTEST_DATES:
            summary_b[key][date] = evaluate([r for r in back if r["date"] == date], key)
        print(key, summary_b[key]["all"])
    leak = [r["ai"]["deepseek-chat|named"]["rating"] - r["ai"]["deepseek-chat|anon"]["rating"] for r in back]
    print(f"с названием минус без: среднее {statistics.mean(leak):+.2f}, |среднее| {statistics.mean(map(abs, leak)):.2f}")

    total_cost = (sum(x["cost"] for r in live for x in r["models"].values())
                  + sum(v["cost"] for r in back for v in r["ai"].values()))
    OUT.write_text(json.dumps({
        "date": dt.date.today().isoformat(), "runs": runs, "models": MODELS,
        "live": live, "summary_live": summary_a,
        "backtest": back, "summary_backtest": summary_b,
        "leak": {"mean": round(statistics.mean(leak), 2), "mean_abs": round(statistics.mean(map(abs, leak)), 2)},
        "total_cost": round(total_cost, 4), "seconds": round(time.monotonic() - started),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nвсего ${total_cost:.4f}, {time.monotonic() - started:.0f} с → {OUT}")


if __name__ == "__main__":
    main()
