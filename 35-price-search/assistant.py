"""Конвейер «опознать → найти на маркетплейсах → сопоставить → ответить» и чат-уточнения.

Состояние разговора — карточка товара. Первое сообщение (фото и/или текст) её создаёт,
следующие правят: «мне нужен чёрный», «256 ГБ», «это не та модель, это XM4». Правку
делает модель (короткий JSON поверх старой карточки), после неё поиск идёт заново.
Если модель по фото не уверена — поиска нет, в ответе вопрос человеку.

Сырые выдачи маркетплейсов каждого поиска сохраняются в data/runs/: цены живые, а
результат должен быть проверяемым и после того, как цены изменятся.

    python3 assistant.py "sony wh-1000xm5 черные" [ozon wb ym]
"""

import json
import pathlib
import time

import identify
import markets
import matcher
import providers
import safari

HERE = pathlib.Path(__file__).resolve().parent
RUNS = HERE / "data" / "runs"
MODEL = "deepseek-chat"

REFINE = """Текущая карточка товара, который ищет человек:
<<CARD>>
Человек уточняет: «<<TEXT>>»
Верни исправленную карточку целиком в том же JSON-формате (те же поля). Если уточнение
меняет модель — поправь queries и must. Если это вопрос, а не уточнение товара, верни
карточку без изменений."""


def _add(total, part):
    for key in ("prompt_tokens", "completion_tokens", "cost"):
        total[key] = round(total.get(key, 0) + (part.get(key) or 0), 6)


class PriceAssistant:
    def __init__(self, markets_on=("ozon", "wb", "ym")):
        self.markets_on = tuple(markets_on)
        self.card = None

    def reset(self):
        self.card = None

    def refine(self, text):
        prompt = (REFINE.replace("<<CARD>>", json.dumps(self.card, ensure_ascii=False))
                  .replace("<<TEXT>>", text))
        reply = providers.call(MODEL, [{"role": "user", "content": prompt}], temperature=0,
                               max_tokens=1200, response_format={"type": "json_object"})
        try:
            card = identify.normalize(json.loads(reply["text"]))
        except json.JSONDecodeError:
            card = self.card
        return card, reply

    def ask(self, text="", image=None, markets_on=None, temperature=0.0):
        """Одно сообщение пользователя → ответ с карточкой, статусами магазинов и итогом."""
        started = time.monotonic()
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
        steps = []
        if image or self.card is None:
            found = identify.identify(text, image, temperature)
            self.card = found["card"]
            _add(usage, found)
            steps.append({"step": "опознание", "model": found["model"], "seconds": found["seconds"]})
        else:
            self.card, reply = self.refine(text)
            _add(usage, reply)
            steps.append({"step": "уточнение карточки", "model": MODEL, "seconds": reply["seconds"]})

        result = {"card": self.card, "steps": steps}
        if self.card["confidence"] == "low" or not self.card["queries"]:
            result.update(answer=self.card.get("question") or "Не понял, что за товар — уточни модель.",
                          need_input=True, **usage,
                          seconds=round(time.monotonic() - started, 2))
            return result

        query = self.card["queries"][0]
        searched = []
        for market in markets_on or self.markets_on:
            searched.append(markets.search(market, query))
            steps.append({"step": f"поиск: {markets.MARKETS[market]}", "status": searched[-1]["status"],
                          "seconds": searched[-1]["seconds"]})
        matched = matcher.match(self.card, searched, temperature)
        _add(usage, matched)
        steps.append({"step": "сопоставление", "model": matcher.MODEL, "seconds": matched["seconds"]})
        result.update(query=query, match=matched, answer=self.summary(matched),
                      need_input=False, **usage, seconds=round(time.monotonic() - started, 2))
        self.save(result, searched)
        return result

    @staticmethod
    def summary(matched):
        best = matched["best"]
        down = [m for m, info in matched["per_market"].items() if info["status"] != "ok"]
        lines = []
        if best:
            lines.append(f"Дешевле всего: {best['price']:,.0f} ₽ на {markets.MARKETS[best['market']]} — "
                         f"{best['title'][:90]}".replace(",", " "))
        else:
            lines.append("Того же товара не нашлось ни в одном магазине — уточни модель или вариант.")
        naive = matched["naive"]
        if naive and best and naive["url"] != best["url"]:
            lines.append(f"Самое дешёвое в выдаче ({naive['price']:,.0f} ₽) — не то: "
                         f"«{naive['title'][:60]}».".replace(",", " "))
        if down:
            lines.append("Не ответили: " + ", ".join(
                f"{markets.MARKETS[m]} ({matched['per_market'][m]['status']})" for m in down) + ".")
        return " ".join(lines)

    @staticmethod
    def save(result, searched):
        RUNS.mkdir(parents=True, exist_ok=True)
        path = RUNS / time.strftime("%Y%m%d-%H%M%S.json")
        path.write_text(json.dumps({"card": result["card"], "query": result["query"],
                                    "results": searched, "best": result["match"]["best"],
                                    "offers": result["match"]["offers"]},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        result["saved"] = str(path.relative_to(HERE))


if __name__ == "__main__":
    import sys
    words = [a for a in sys.argv[1:] if a not in markets.MARKETS]
    chosen = [a for a in sys.argv[1:] if a in markets.MARKETS] or list(markets.MARKETS)
    bot = PriceAssistant(chosen)
    try:
        out = bot.ask(" ".join(words) or "sony wh-1000xm5 черные")
    finally:
        safari.BROWSER.quit()
    print(out["answer"])
    for step in out["steps"]:
        print("  ·", step)
    print(f"  {out['prompt_tokens']}+{out['completion_tokens']} ток. · ${out['cost']} · {out['seconds']} с · {out.get('saved', '')}")
