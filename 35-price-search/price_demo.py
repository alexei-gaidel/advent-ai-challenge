"""Замер на живых маркетплейсах: 10 товаров (6 по описанию, 4 по фото), temperature=0.

Что меряется — по каждому товару и проходу:
    1. опознание: бренд и модель в карточке совпали с меткой (регулярка, код);
    2. доступность: статус каждого маркетплейса (ok / blocked / captcha / error) и время;
    3. наивная цена: самое дешёвое в выдаче — это вообще тот товар? (судья);
    4. итог AI: выбранное предложение — тот товар? (судья); сколько «тот же» судья
       подтвердил; не пропустил ли AI более дешёвый тот же товар среди отброшенных (судья);
    5. токены, $, секунды.

Судья — openai/gpt-oss-120b на Groq: в конвейере он не участвует (там deepseek-chat и
qwen3.8-27b). Отвечает строго: тот же товар (модель, обязательные варианты, новый) или нет.

Цены живые, поэтому сырые выдачи каждого прохода лежат в data/runs/ (через assistant),
а итог — в data/price_result.json, лог — data/price_demo.log.

    python3 price_demo.py                 # 2 прохода, все маркетплейсы
    python3 price_demo.py 1 ym            # 1 проход, только Яндекс.Маркет
"""

import json
import pathlib
import re
import sys
import time

import assistant as pa
import markets
import matcher
import providers
import safari

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
TESTSET = DATA / "testset"
JUDGE = "openai/gpt-oss-120b"

ITEMS = [
    {"id": "sony-exact", "text": "sony wh-1000xm5 черные", "label": r"wh-?1000\s?xm5"},
    {"id": "sony-vague", "text": "наушники сони с шумодавом пятое поколение", "label": r"wh-?1000\s?xm5"},
    {"id": "airpods4-anc", "text": "apple airpods 4 с активным шумоподавлением", "label": r"airpods\s?4"},
    {"id": "iphone16", "text": "айфон 16 на 128 черный", "label": r"iphone\s?16(?!\s?(pro|plus|e))"},
    {"id": "oralb-io9", "text": "электрическая зубная щетка oral-b io 9 черная", "label": r"io\s?9"},
    {"id": "dyson-v15", "text": "пылесос дайсон в15 детект", "label": r"v15"},
    {"id": "photo-airpods-pro2", "image": "airpods-pro-2.jpg", "label": r"airpods\s?pro",
     "followup": "второе поколение, кейс USB-C"},
    {"id": "photo-switch-oled", "image": "switch-oled.png", "label": r"switch.*oled|oled.*switch"},
    {"id": "photo-kindle", "image": "kindle-paperwhite-2023.jpg", "label": r"paperwhite",
     "followup": "Paperwhite 11-го поколения, 16 ГБ"},
    {"id": "photo-dualsense", "image": "dualsense.png", "label": r"dualsense"},
]

JUDGE_PROMPT = """Ищем самый дешёвый ИМЕННО ЭТОТ товар: «<<TITLE>>»<<VARIANT>>.
Для каждого предложения с маркетплейса ответь, тот же ли это товар: та же модель и
поколение, указанные цвет/память/размер совпадают (если указаны), новый, не аксессуар
к нему и не подделка. Региональная версия (Global, SIM) — это тот же товар.

<<OFFERS>>

JSON: {"items": [{"id": номер, "same": true или false}]} — по одному на каждый номер."""


def judge(card, offers):
    """Судья: {url: True/False} по списку предложений."""
    if not offers:
        return {}, 0.0
    variant = ", ".join(v for v in card["variant"].values() if v)
    lines = "\n".join(f"[{n}] {o['price'] or 0:.0f} ₽ · {o['title'][:150]} · адрес: "
                      f"{matcher.slug(o['url'])[:70]}"
                      + (f" · {o['description'][:120]}" if o.get("description") else "")
                      for n, o in enumerate(offers))
    prompt = (JUDGE_PROMPT.replace("<<TITLE>>", card["title"] or f"{card['brand']} {card['model']}")
              .replace("<<VARIANT>>", f" ({variant})" if variant else "").replace("<<OFFERS>>", lines))
    reply = providers.call(JUDGE, [{"role": "user", "content": prompt}], temperature=0,
                           max_tokens=4000, response_format={"type": "json_object"})
    try:
        items = json.loads(reply["text"]).get("items", [])
        verdict = {int(i["id"]): bool(i.get("same")) for i in items if str(i.get("id", "")).isdigit()}
    except (json.JSONDecodeError, AttributeError, ValueError):
        verdict = {}
    return {offers[n]["url"]: v for n, v in verdict.items() if n < len(offers)}, reply["cost"] or 0.0


def run_item(item, chosen, log):
    bot = pa.PriceAssistant(chosen)
    image = (TESTSET / item["image"]).read_bytes() if item.get("image") else None
    try:
        result = bot.ask(item.get("text", ""), image, chosen, temperature=0)
    except (RuntimeError, ValueError) as error:
        log(f"  {item['id']}: ОШИБКА {error}")
        return {"id": item["id"], "error": str(error)}
    card = result["card"]
    row = {"id": item["id"], "by_photo": bool(image), "card": card,
           "need_input": result["need_input"], "cost": result["cost"],
           "tokens": result["prompt_tokens"] + result["completion_tokens"],
           "seconds": result["seconds"], "saved": result.get("saved")}
    if result["need_input"] and item.get("followup"):
        # Сценарный ответ человека на вопрос модели: меряем диалог, а не только первый шаг.
        log(f"  {item['id']}: модель спросила «{result['answer'][:80]}» → «{item['followup']}»")
        row["asked"] = result["answer"]
        result = bot.ask(item["followup"], None, chosen, temperature=0)
        card = result["card"]
        row.update(card=card, need_input=result["need_input"],
                   cost=row["cost"] + result["cost"], seconds=row["seconds"] + result["seconds"],
                   tokens=row["tokens"] + result["prompt_tokens"] + result["completion_tokens"],
                   saved=result.get("saved"))
    # Опознание — по карточке ПОСЛЕ ответа на вопрос модели (в v1 бралась первая карточка).
    id_text = f"{card['brand'] or ''} {card['model'] or ''} {card['title']}".lower()
    row["identified"] = bool(re.search(item["label"], id_text, re.I))
    if result["need_input"]:
        log(f"  {item['id']}: модель просит уточнить — {result['answer'][:100]}")
        return row
    match = result["match"]
    row["markets"] = {m: {"status": info["status"], "found": info["found"], "same": info["same"],
                          "seconds": info["seconds"], "note": info["note"][:160]}
                      for m, info in match["per_market"].items()}
    best, naive = match["best"], match["naive"]
    same = [o for o in match["offers"] if o["class"] == "same"]
    cheaper_rejected = [o for o in match["offers"] if o["class"] != "same" and o.get("price")
                        and best and o["price"] < best["price"]]
    to_judge = {o["url"]: o for o in (same[:12] + cheaper_rejected[:12] + ([naive] if naive else [])
                                     + ([best] if best else []))}
    verdict, judge_cost = judge(card, list(to_judge.values()))
    row.update({
        "best": best and {k: best[k] for k in ("market", "price", "title", "url")},
        "best_ok": verdict.get(best["url"]) if best else None,
        "naive": naive and {k: naive[k] for k in ("market", "price", "title", "url")},
        "naive_ok": verdict.get(naive["url"]) if naive else None,
        "same": len(same), "same_confirmed": sum(bool(verdict.get(o["url"])) for o in same[:12]),
        "same_judged": len(same[:12]),
        "missed_cheaper": [{"price": o["price"], "title": o["title"][:90], "class": o["class"],
                            "note": o["note"]} for o in cheaper_rejected[:12] if verdict.get(o["url"])],
        "counts": match["counts"], "judge_cost": judge_cost,
    })
    statuses = " ".join(f"{m}:{v['status']}" for m, v in row["markets"].items())
    log(f"  {item['id']}: опознан {'✓' if row['identified'] else '✗'} «{card['title'][:40]}» · {statuses} · "
        f"наивно {naive['price'] if naive else '—'} ₽ {'✓' if row['naive_ok'] else '✗'} · "
        f"AI {best['price'] if best else '—'} ₽ {'✓' if row['best_ok'] else ('—' if not best else '✗')} · "
        f"тот же {row['same_confirmed']}/{row['same_judged']} · пропущено дешевле {len(row['missed_cheaper'])} · "
        f"${round(row['cost'] + judge_cost, 4)} · {row['seconds']} с")
    for miss in row["missed_cheaper"]:
        log(f"      пропущено: {miss['price']:.0f} ₽ {miss['title'][:70]} [{miss['class']}: {miss['note']}]")
    return row


def main():
    args = sys.argv[1:]
    passes = next((int(a) for a in args if a.isdigit()), 2)
    chosen = [a for a in args if a in markets.MARKETS] or list(markets.MARKETS)
    DATA.mkdir(exist_ok=True)
    log_file = (DATA / "price_demo.log").open("w", encoding="utf-8")

    def log(text):
        print(text, flush=True)
        log_file.write(text + "\n")
        log_file.flush()

    started = time.monotonic()
    log(f"маркетплейсы: {', '.join(markets.MARKETS[m] for m in chosen)} · проходов {passes} · "
        f"temperature=0 · судья {JUDGE} · {time.strftime('%Y-%m-%d %H:%M')}")
    rows = []
    try:
        for attempt in range(1, passes + 1):
            log(f"\nПроход {attempt}")
            for item in ITEMS:
                row = run_item(item, chosen, log)
                row["attempt"] = attempt
                rows.append(row)
    finally:
        safari.BROWSER.quit()

    done = [r for r in rows if "error" not in r and not r.get("need_input")]
    found = [r for r in done if r.get("best")]
    summary = {
        "runs": len(rows), "errors": sum("error" in r for r in rows),
        "need_input": sum(bool(r.get("need_input")) for r in rows),
        "identified": sum(bool(r.get("identified")) for r in rows if "error" not in r),
        "identified_text": sum(r["identified"] for r in rows if "error" not in r and not r["by_photo"]),
        "text_runs": sum(1 for r in rows if "error" not in r and not r["by_photo"]),
        "identified_photo": sum(r["identified"] for r in rows if "error" not in r and r["by_photo"]),
        "photo_runs": sum(1 for r in rows if "error" not in r and r["by_photo"]),
        "markets": {m: {s: sum(1 for r in done if r["markets"][m]["status"] == s)
                        for s in ("ok", "blocked", "captcha", "error")} for m in chosen},
        "naive_ok": sum(bool(r["naive_ok"]) for r in done if r.get("naive")),
        "naive_total": sum(1 for r in done if r.get("naive")),
        "best_found": len(found), "best_ok": sum(bool(r["best_ok"]) for r in found),
        "same_confirmed": sum(r["same_confirmed"] for r in done),
        "same_judged": sum(r["same_judged"] for r in done),
        "missed_cheaper": sum(len(r["missed_cheaper"]) for r in done),
        "cost": round(sum(r.get("cost", 0) for r in rows), 4),
        "judge_cost": round(sum(r.get("judge_cost", 0) for r in rows), 4),
        "seconds": round(time.monotonic() - started),
    }
    s = summary
    log(f"\nИтог: опознано {s['identified']}/{s['runs'] - s['errors']} (текст {s['identified_text']}/{s['text_runs']}, "
        f"фото {s['identified_photo']}/{s['photo_runs']}) · уточнить просили {s['need_input']}")
    for m, st in s["markets"].items():
        log(f"  {markets.MARKETS[m]}: " + ", ".join(f"{k} {v}" for k, v in st.items() if v))
    log(f"  наивно самое дешёвое — тот товар: {s['naive_ok']}/{s['naive_total']}")
    log(f"  итог AI — тот товар: {s['best_ok']}/{s['best_found']} · «тот же» подтверждено судьёй "
        f"{s['same_confirmed']}/{s['same_judged']} · пропущено более дешёвых тех же {s['missed_cheaper']}")
    log(f"  конвейер ${s['cost']} · судья ${s['judge_cost']} · {s['seconds']} с")
    (DATA / "price_result.json").write_text(json.dumps(
        {"markets": chosen, "passes": passes, "judge": JUDGE, "summary": summary, "rows": rows},
        ensure_ascii=False, indent=2), encoding="utf-8")
    log("→ data/price_result.json")


if __name__ == "__main__":
    main()
