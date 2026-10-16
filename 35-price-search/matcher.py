"""Сопоставление: какие из найденных предложений — тот же товар, и какое из них дешевле.

Наивный ответ «самое дешёвое в выдаче» почти всегда неверен: наверх по цене всплывают
чехлы, амбушюры и подделки, а на Яндекс.Маркете встречается «Беспроводные наушники Sony
WH-1000XM5, черные» за 557 ₽ — по адресу и описанию это силиконовый чехол.

Два шага:
1. Код: без цены — вон; слово из must_not в названии — «аксессуар». Остальное решает
   модель (жёсткий фильтр по артикулу выкидывал настоящие товары — см. README).
2. deepseek-chat смотрит оставшиеся: название, описание, адрес страницы и цену — и относит
   каждое к классу same / variant / accessory / other с короткой причиной.
Код выбирает самое дешёвое «same» в каждом магазине и в целом и помечает «подозрительно
дёшево» — меньше половины медианы «same».

    python3 matcher.py "sony wh-1000xm5"     # identify → markets → match
"""

import json
import re
import statistics
import time
import urllib.parse

import providers

MODEL = "deepseek-chat"
CLASSES = ("same", "variant", "accessory", "other")
LABELS = {"same": "тот же товар", "variant": "другой вариант", "accessory": "аксессуар",
          "other": "другой товар"}

PROMPT = """Человек ищет самый дешёвый ИМЕННО ЭТОТ товар:
<<CARD>>

Ниже предложения с маркетплейсов. Для каждого определи класс:
- same — тот же товар (та же модель; если в карточке указаны цвет, память или размер —
  они тоже совпадают). Новый, не б/у. Разная комплектация SIM, «Global» или страна
  выпуска — это same, но упомяни в note;
- variant — та же модель, но другой цвет, объём памяти или размер;
- accessory — чехол, плёнка, кабель, амбушюры, запчасть, подставка и т. п. ДЛЯ искомого
  товара. Если человек сам ищет аксессуар (например, чехол), то чужие чехлы — это other,
  а не accessory;
- other — другой товар (другая модель или поколение, другой бренд, подделка, «аналог»,
  б/у, уценка).
Смотри не только на название: продавцы пишут в названии чужую модель. Адрес страницы,
описание и цена тоже говорят, что это: «chekhol» в адресе, «Материал: силикон» или цена
в 30 раз ниже остальных — это не наушники.

<<OFFERS>>

Ответ JSON: {"items": [{"id": номер, "class": "same|variant|accessory|other",
"note": "коротко почему, до 12 слов"}]} — по одному элементу на КАЖДЫЙ номер."""


def squash(text):
    """«WH-1000XM5 128 ГБ» → «wh1000xm5128гб»: сравнение без пробелов, дефисов, регистра."""
    return re.sub(r"[\s\-_/.,()«»\"']+", "", (text or "").lower())


def slug(url):
    path = urllib.parse.urlparse(url or "").path
    return path.strip("/").split("/")[-2] if path.count("/") >= 2 else path


def prefilter(card, offers):
    """Код до модели: явные случаи. → (кандидаты для модели, уже размеченные)."""
    # Жёсткого фильтра по must больше нет: в первом замере (data/price_result_v1.json) он
    # дважды выкинул настоящие товары — «io9» не совпал с «iO Series 9», «11-го поколения»
    # с «Paperwhite 11». Названия на маркетплейсах слишком разные для подстрок: модель
    # получает must как подсказку и решает сама. Код отсекает только бесспорное.
    must_not = [m.lower() for m in card.get("must_not", []) if m.strip()]
    candidates, decided = [], []
    for offer in offers:
        title = offer.get("title", "")
        if not offer.get("price"):
            decided.append({**offer, "class": "other", "note": "нет цены", "by": "код"})
            continue
        hit = next((w for w in must_not if re.search(rf"(?<!\w){re.escape(w)}", title.lower())), None)
        if hit:
            decided.append({**offer, "class": "accessory", "note": f"в названии «{hit}»", "by": "код"})
            continue
        candidates.append(offer)
    return candidates, decided


def classify(card, candidates, temperature=0.0):
    """Модель размечает кандидатов. → (размеченные, usage)."""
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
    if not candidates:
        return [], usage
    lines = []
    for n, offer in enumerate(candidates):
        lines.append(f"[{n}] {offer['market']} · {offer['price']:.0f} ₽ · {offer['title'][:160]}"
                     f" · адрес: {slug(offer.get('url', ''))[:80]}"
                     + (f" · описание: {offer['description'][:160]}" if offer.get("description")
                        and offer["description"] != offer["title"] else ""))
    card_text = json.dumps({k: card[k] for k in ("brand", "model", "variant", "category", "title", "must")},
                           ensure_ascii=False)
    prompt = PROMPT.replace("<<CARD>>", card_text).replace("<<OFFERS>>", "\n".join(lines))
    reply = providers.call(MODEL, [{"role": "user", "content": prompt}], temperature=temperature,
                           max_tokens=3000, response_format={"type": "json_object"})
    for key in usage:
        usage[key] = reply[key] or 0
    try:
        items = {int(i["id"]): i for i in json.loads(reply["text"]).get("items", [])
                 if isinstance(i, dict) and str(i.get("id", "")).isdigit()}
    except (json.JSONDecodeError, AttributeError, ValueError):
        items = {}
    labeled = []
    for n, offer in enumerate(candidates):
        item = items.get(n)
        if not item or item.get("class") not in CLASSES:
            labeled.append({**offer, "class": "other", "note": "модель не разметила — не берём",
                            "by": "модель"})
        else:
            labeled.append({**offer, "class": item["class"], "note": str(item.get("note", ""))[:120],
                            "by": "модель"})
    return labeled, usage


def match(card, results, temperature=0.0):
    """results — ответы markets.search_all → итог: лучшие предложения и разметка всех."""
    started = time.monotonic()
    offers = [o for r in results for o in r["offers"]]
    candidates, decided = prefilter(card, offers)
    labeled, usage = classify(card, candidates, temperature)
    everything = decided + labeled
    same = [o for o in everything if o["class"] == "same"]
    if len(same) >= 3:
        median = statistics.median(o["price"] for o in same)
        for offer in same:
            if offer["price"] < 0.5 * median:
                offer["suspicious"] = f"дешевле половины медианы ({median:,.0f} ₽)".replace(",", " ")
    trusted = [o for o in same if not o.get("suspicious")]
    best = min(trusted, key=lambda o: o["price"]) if trusted else None
    per_market = {}
    for result in results:
        mine = [o for o in trusted if o["market"] == result["market"]]
        per_market[result["market"]] = {
            "status": result["status"], "found": len(result["offers"]),
            "same": len([o for o in same if o["market"] == result["market"]]),
            "best": min(mine, key=lambda o: o["price"]) if mine else None,
            "note": result.get("note", ""), "seconds": result.get("seconds")}
    naive = min((o for o in offers if o.get("price")), key=lambda o: o["price"], default=None)
    everything.sort(key=lambda o: (CLASSES.index(o["class"]), o["price"] or 1e12))
    return {"best": best, "per_market": per_market, "offers": everything, "naive": naive,
            "counts": {c: sum(o["class"] == c for o in everything) for c in CLASSES},
            "seconds": round(time.monotonic() - started, 2), **usage}


if __name__ == "__main__":
    import sys
    import identify
    import markets
    import safari
    text = " ".join(sys.argv[1:]) or "sony wh-1000xm5"
    chosen = [m for m in ("ozon", "wb", "ym") if m in sys.argv] or ["ym"]
    card = identify.identify(text)["card"]
    print("карточка:", json.dumps(card, ensure_ascii=False))
    try:
        results = [markets.search(m, card["queries"][0]) for m in chosen]
    finally:
        safari.BROWSER.quit()
    out = match(card, results)
    for offer in out["offers"]:
        flag = f" ⚠ {offer['suspicious']}" if offer.get("suspicious") else ""
        print(f"  {LABELS[offer['class']]:15} {offer['price'] or 0:>9,.0f} ₽ [{offer['by']}] "
              f"{offer['title'][:60]} — {offer['note']}{flag}".replace(",", " "))
    naive = out["naive"]
    print(f"\nнаивно дешевле всего: {naive['price']:.0f} ₽ {naive['title'][:70]}" if naive else "")
    best = out["best"]
    print(f"ИТОГ: {best['price']:.0f} ₽ на {best['market']} — {best['title'][:70]}\n{best['url']}" if best
          else "ИТОГ: того же товара не найдено")
    print(f"{out['prompt_tokens']}+{out['completion_tokens']} ток. · ${out['cost']}")
