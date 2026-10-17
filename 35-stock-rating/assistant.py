"""Ассистент веб-чата: «стоит ли брать Лукойл?» → дашборд и рейтинг; дальше — вопросы по нему.

Конвейер одного анализа: найти тикер → срез данных (Мосбиржа + МСФО + ставка ЦБ) →
формула → AI-аналитик. Следующие сообщения без новой компании — вопросы к текущему
анализу: модель отвечает по тем же данным и своему же разбору.

Роутер — маленький вызов LLM: «лукойла», «сбер», «магнит» (≠ ММК) в тикер скриптом не
превращаются, а угаданный моделью тикер код проверяет на бирже.

    python3 assistant.py "стоит ли покупать лукойл?"
"""

import datetime as dt
import json
import pathlib
import re
import sys
import time

import analyst
import moex
import providers
import scoring
import snapshot

ROUTER_MODEL = "deepseek-chat"
RUNS = pathlib.Path(__file__).resolve().parent / "data" / "runs"

ROUTER = """Пользователь пишет в чат анализа акций Мосбиржи. Определи, о какой компании речь.
Если в сообщении названа компания или тикер — intent "analyze", company — название в
именительном падеже, ticker — тикер Мосбиржи, если знаешь (обыкновенные акции).
Если это вопрос к уже показанному анализу без новой компании — intent "question".
Сейчас на экране: <<CURRENT>>.
Ответ строго JSON: {"intent": "analyze" | "question", "company": "…", "ticker": "…"}"""

FOLLOWUP = """Ты — аналитик, который только что разобрал акцию. Ниже входные данные и твой разбор.
Отвечай на вопрос пользователя коротко (до 120 слов), по-русски, опираясь только на эти
данные. Если ответа в данных нет — так и скажи, не выдумывай новости и цифры.
Это не индивидуальная инвестиционная рекомендация — напоминать об этом не нужно, это
написано в интерфейсе.

ДАННЫЕ:
<<FACTS>>

РАЗБОР:
<<ANALYSIS>>"""


def chart_series(snap, days=365):
    """Год свечей для графика: цена, SMA50, SMA200 и IMOEX, приведённый к цене акции."""
    since = (dt.date.fromisoformat(snap["as_of"]) - dt.timedelta(days=days)).isoformat()
    closes = [c["close"] for c in snap["candles"]]
    rows, base = [], None
    for i, c in enumerate(snap["candles"]):
        if c["date"] < since:
            continue
        index = scoring.price_on(snap["index"], c["date"])
        base = base or (c["close"] / index if index else None)
        avg = lambda n: round(sum(closes[i - n + 1:i + 1]) / n, 2) if i + 1 >= n else None
        rows.append({"d": c["date"], "c": c["close"], "s50": avg(50), "s200": avg(200),
                     "i": round(index * base, 2) if index and base else None})
    return rows


class StockAssistant:
    def __init__(self, model=analyst.DEFAULT_MODEL):
        self.model = model
        self.current = None         # последний анализ: snapshot, score, ai
        self.history = []           # вопросы-ответы по текущему анализу

    def reset(self):
        self.current, self.history = None, []

    def resolve(self, text):
        """Текст → (тикер, расход токенов). Тикер латиницей проверяется без LLM."""
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
        for word in re.findall(r"\b[A-Z]{1,5}P?\b", text):
            if moex.find_ticker(word) and moex.find_ticker(word)[0][0] == word:
                return "analyze", word, usage
        current = f"{self.current['snap']['name']} ({self.current['snap']['ticker']})" if self.current else "ничего"
        reply = providers.call(ROUTER_MODEL, [
            {"role": "system", "content": ROUTER.replace("<<CURRENT>>", current)},
            {"role": "user", "content": text}], temperature=0.0, max_tokens=120,
            response_format={"type": "json_object"})
        for k in usage:
            usage[k] += reply[k] or 0
        try:
            route = analyst.parse(reply["text"])
        except ValueError:
            route = {"intent": "question"}
        if route.get("intent") != "analyze":
            return "question", None, usage
        guess = str(route.get("ticker") or "").upper()
        if guess:
            hits = moex.find_ticker(guess)
            if hits and hits[0][0] == guess:
                return "analyze", guess, usage
        hits = moex.find_ticker(str(route.get("company") or ""))
        return ("analyze", hits[0][0], usage) if hits else ("unknown", route.get("company"), usage)

    def analyze(self, ticker, temperature=0.0):
        started = time.monotonic()
        steps = []
        t = time.monotonic()
        snap = snapshot.take(ticker)
        steps.append({"step": "данные", "seconds": round(time.monotonic() - t, 1)})
        result = scoring.score(snap)
        t = time.monotonic()
        ai = analyst.analyze(snap, result, self.model, temperature)
        steps.append({"step": f"AI {self.model}", "seconds": round(time.monotonic() - t, 1)})
        self.current = {"snap": snap, "score": result, "ai": ai}
        self.history = []
        out = {
            "kind": "analysis",
            "ticker": snap["ticker"], "name": snap["name"], "full_name": snap["full_name"],
            "quote": snap["quote"], "as_of": snap["as_of"],
            "period": snap["period"], "report_date": snap["report_date"],
            "periods": snap["report"]["periods"],
            "key_rate": snap["key_rate"],
            "chart": chart_series(snap),
            "metrics": result["metrics"], "factors": result["factors"],
            "factor_names": scoring.FACTOR_NAMES, "weights": scoring.WEIGHTS,
            "formula": result["formula"], "formula_recommendation": scoring.recommendation(result["formula"]),
            "ai": ai,
            "sources": {"prices": "iss.moex.com", "reports": snap["report"]["source"], "rate": "cbr.ru"},
            "steps": steps, "seconds": round(time.monotonic() - started, 1),
        }
        self.save(out)
        return out

    def answer(self, text):
        cur = self.current
        system = (FOLLOWUP.replace("<<FACTS>>", json.dumps(analyst.facts(cur["snap"], cur["score"]), ensure_ascii=False))
                          .replace("<<ANALYSIS>>", json.dumps({k: cur["ai"][k] for k in
                                   ("rating", "recommendation", "adjustments", "summary", "pros", "risks", "watch")},
                                   ensure_ascii=False)))
        messages = [{"role": "system", "content": system}] + self.history[-6:] + [{"role": "user", "content": text}]
        reply = providers.call(self.model, messages, temperature=0.3, max_tokens=1500)
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply["text"]}]
        return {"kind": "answer", "text": reply["text"], "prompt_tokens": reply["prompt_tokens"],
                "completion_tokens": reply["completion_tokens"], "cost": reply["cost"] or 0.0,
                "seconds": reply["seconds"]}

    def ask(self, text, temperature=0.0):
        text = text.strip()
        if not text:
            raise ValueError("пустое сообщение")
        intent, ticker, usage = self.resolve(text)
        if intent == "analyze":
            out = self.analyze(ticker, temperature)
        elif intent == "unknown":
            out = {"kind": "answer", "text": f"Не нашёл «{ticker}» среди акций Мосбиржи (режим TQBR). "
                                             "Напиши тикер, например SBER или LKOH."}
        elif self.current is None:
            out = {"kind": "answer", "text": "Назови компанию или тикер — например, «Сбербанк» или LKOH."}
        else:
            out = self.answer(text)
        out["router"] = usage
        return out

    def save(self, out):
        RUNS.mkdir(parents=True, exist_ok=True)
        name = f"{dt.datetime.now():%Y%m%d-%H%M%S}-{out['ticker']}.json"
        (RUNS / name).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    bot = StockAssistant()
    for text in sys.argv[1:] or ["стоит ли покупать лукойл?", "почему не покупать, если дивиденды 13%?"]:
        r = bot.ask(text)
        if r["kind"] == "analysis":
            print(f"{r['name']} ({r['ticker']}) {r['quote']['price']} ₽ · формула {r['formula']} · "
                  f"AI {r['ai']['rating']} {r['ai']['recommendation']} · {r['seconds']} с")
            print(r["ai"]["summary"])
        else:
            print("→", r["text"])
