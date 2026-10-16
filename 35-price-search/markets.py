"""Поиск на маркетплейсах: Яндекс.Маркет, Wildberries, Ozon — с общим форматом ответа.

    {market, status: ok | blocked | captcha | error, query, url, offers: [...], seconds, note}
    offer: {market, title, description, price, old_price, url, rating, reviews, position}

Яндекс.Маркет отдаёт поиск обычному HTTP-запросу: в HTML лежит schema.org ItemList
(JSON-LD) с названием, ценой, описанием и ссылкой — разбирать вёрстку не нужно.

Ozon и WB скрипт не пускают (антибот, 403), поэтому их страницу поиска открывает настоящий
Safari через WebDriver (safari.py), а карточки достаются JS из самой страницы: ссылки на
товар + ближайшая карточка с ценой «… ₽». Капча — статус captcha, без попыток обхода.

Вежливость: один поиск на маркетплейс за запрос пользователя, без сортировки по цене (по
цене вверх наверх всплывают чехлы и запчасти), первые ~40 карточек.

    python3 markets.py "sony wh-1000xm5"            # все три
    python3 markets.py "sony wh-1000xm5" ym         # один
"""

import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import safari

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/26.0 Safari/605.1.15")
LIMIT = 40
MARKETS = {"ozon": "Ozon", "wb": "Wildberries", "ym": "Яндекс.Маркет"}


def _result(market, query, url, started, offers=None, status="ok", note=""):
    return {"market": market, "name": MARKETS[market], "status": status, "query": query,
            "url": url, "offers": (offers or [])[:LIMIT], "note": note,
            "seconds": round(time.monotonic() - started, 1)}


def _price(text):
    """«12 345 ₽» · «12 345,50» · 12345 → 12345.0; иначе None."""
    if isinstance(text, (int, float)):
        return float(text)
    digits = re.sub(r"[^\d,.]", "", str(text or "")).replace(",", ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


# --- Яндекс.Маркет: HTTP + JSON-LD ---------------------------------------------------------

def _ym_page(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept-Language": "ru-RU,ru;q=0.9"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.geturl(), response.read().decode("utf-8", "replace")


def search_ym(query):
    """HTTP; капча → пауза и второй запрос. Капчу Маркет показывает скриптам не всегда, а
    случайным образом. Через Safari не пробуем: автоматизированный Safari Маркет тоже
    распознаёт («requests sent from your device are automated»)."""
    started = time.monotonic()
    url = "https://market.yandex.ru/search?" + urllib.parse.urlencode({"text": query})
    final, page = url, ""
    try:
        for attempt in range(2):
            final, page = _ym_page(url)
            if "showcaptcha" not in final:
                break
            time.sleep(3)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        return _result("ym", query, url, started, status="error", note=str(error)[:200])
    if "showcaptcha" in final:
        return _result("ym", query, url, started, status="captcha",
                       note="Маркет дважды показал капчу — повтори позже")
    offers = []
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            data = json.loads(html.unescape(block))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or data.get("@type") != "ItemList":
            continue
        for element in data.get("itemListElement", []):
            item = element.get("item") or {}
            offer = item.get("offers") or {}
            rating = item.get("aggregateRating") or {}
            offers.append({
                "market": "ym", "position": element.get("position"),
                "title": item.get("name", ""), "description": item.get("description", ""),
                "price": _price(offer.get("price")), "old_price": None,
                "url": item.get("url") or offer.get("url", ""),
                "rating": rating.get("ratingValue"), "reviews": rating.get("ratingCount"),
            })
    if not offers:
        status = "captcha" if "captcha" in final else "error"
        return _result("ym", query, url, started, status=status,
                       note="в странице нет списка товаров (JSON-LD) — разметка изменилась или капча")
    return _result("ym", query, url, started, offers)


# --- Ozon и WB: Safari + разбор карточек из страницы -----------------------------------------

# Антибот-страницы площадок: их текст уходит в note, статус — blocked. Обходить не пытаемся.
BLOCKED = re.compile(r"подозрительная активность|проверяем браузер|похоже, нет соединения|"
                     r"выключите vpn|доступ ограничен|подтвердите, что вы не робот|captcha|"
                     r"fab_chlg|showcaptcha", re.I)

# Общий сбор карточек: ссылка на товар → поднимаемся к предкам, пока не встретится цена «… ₽».
# Предок, где уже больше одной ссылки на РАЗНЫЕ товары, — это сетка, а не карточка: берём
# предыдущий уровень, иначе цены соседних товаров смешаются. «… ₽/мес» (рассрочка) не цена.
# Текущая цена — наименьшая в карточке (рядом обычно зачёркнутая старая), старая — наибольшая.
EXTRACT = r"""
const pattern = new RegExp(arguments[0]);
const productHref = a => a.href.split("?")[0].split("#")[0];
const productsIn = el => new Set([...el.querySelectorAll("a[href]")].map(productHref).filter(h => pattern.test(h))).size;
const seen = new Set(), cards = [];
for (const a of document.querySelectorAll("a[href]")) {
  const href = productHref(a);
  if (!pattern.test(href) || seen.has(href)) continue;
  let card = a, best = null;
  for (let i = 0; i < 10 && card && card !== document.body; i++, card = card.parentElement) {
    if (i > 0 && productsIn(card) > 1) break;
    best = card;
    if (/\d[\d\s  ]*\s?₽/.test(card.innerText || "")) break;
  }
  const text = (best && best.innerText) || "";
  if (!/₽/.test(text)) continue;
  seen.add(href);
  const prices = [];
  const re = /(\d[\d\s  ]{0,12})\s?₽(\s*\/\s*мес)?/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m[2]) continue;
    const value = parseInt(m[1].replace(/[^\d]/g, ""), 10);
    if (value > 0) prices.push(value);
  }
  const lines = text.split("\n").map(s => s.trim()).filter(Boolean);
  // У ссылки-картинки текста нет: название — самый длинный текст среди ВСЕХ ссылок карточки
  // на этот же товар (aria-label, title, текст), а не первая попавшаяся строка.
  const names = [...best.querySelectorAll("a[href]")].filter(x => productHref(x) === href)
    .map(x => (x.getAttribute("aria-label") || x.getAttribute("title") || x.innerText || "").trim())
    .filter(t => t.length > 10 && !/₽/.test(t));
  const longest = lines.filter(l => !/₽|%|отзыв|оценк|рейтинг|особенност|осталось|доставк|купили|завтра|сегодня/i.test(l))
                       .sort((x, y) => y.length - x.length)[0] || "";
  const title = names.sort((x, y) => y.length - x.length)[0] || longest;
  cards.push({url: href, title: title.slice(0, 300), text: lines.slice(0, 14).join(" | ").slice(0, 600),
              prices: prices.slice(0, 6)});
  if (cards.length >= arguments[1]) break;
}
const body = (document.body && document.body.innerText || "").slice(0, 1500);
return {title: document.title, href: location.href, cards: cards, body: body};
"""

# Ленивые сетки дорисовывают карточки при прокрутке.
SCROLL = "window.scrollBy(0, Math.round(window.innerHeight * 1.5)); return document.body.scrollHeight;"

# WB: сайт сам берёт выдачу из поискового JSON (search.wb.ru). Находим этот запрос среди
# ресурсов страницы и перечитываем его из той же страницы — с её куками. Это надёжнее
# вёрстки; не вышло — разбор карточек. Цены в JSON — в копейках.
WB_JSON = r"""
const done = arguments[arguments.length - 1];
const urls = performance.getEntriesByType("resource").map(e => e.name)
  .filter(u => /search\.wb\.ru\/.*\/search\?/.test(u) && /resultset=catalog/.test(u));
if (!urls.length) { done({error: "поисковый запрос сайта не найден"}); return; }
fetch(urls[urls.length - 1], {credentials: "include"})
  .then(r => r.json()).then(d => {
    const products = (d.data && d.data.products) || d.products || [];
    done({url: urls[urls.length - 1], products: products.slice(0, arguments[0]).map(p => ({
      id: p.id, name: p.name, brand: p.brand, rating: p.reviewRating || p.rating, reviews: p.feedbacks,
      price: (p.sizes && p.sizes[0] && p.sizes[0].price) ? p.sizes[0].price.product / 100 : (p.salePriceU || 0) / 100,
      old: (p.sizes && p.sizes[0] && p.sizes[0].price) ? p.sizes[0].price.basic / 100 : (p.priceU || 0) / 100}))});
  }).catch(e => done({error: String(e)}));
"""

SAFARI_MARKETS = {
    "wb": {"search": "https://www.wildberries.ru/catalog/0/search.aspx?search={q}",
           "product": r"wildberries\.ru/catalog/\d+/detail\.aspx"},
    "ozon": {"search": "https://www.ozon.ru/search/?text={q}&from_global=true",
             "product": r"ozon\.ru/product/[\w-]+-\d+/?$"},
    "ym": {"search": "https://market.yandex.ru/search?text={q}",
           "product": r"market\.yandex\.ru/(card/|product--)[\w-]+/\d+"},
}


def _wb_from_json(browser):
    data = browser.run_async(WB_JSON, LIMIT, timeout=40)
    if not isinstance(data, dict) or not data.get("products"):
        return None
    offers = []
    for position, p in enumerate(data["products"], 1):
        if not p.get("id"):
            continue
        offers.append({"market": "wb", "position": position,
                       "title": f"{p.get('brand') or ''} {p.get('name') or ''}".strip(),
                       "description": "", "price": p.get("price") or None,
                       "old_price": p.get("old") if p.get("old") and p.get("old") != p.get("price") else None,
                       "url": f"https://www.wildberries.ru/catalog/{p['id']}/detail.aspx",
                       "rating": p.get("rating"), "reviews": p.get("reviews")})
    return offers


def search_safari(market, query, browser=None):
    started = time.monotonic()
    browser = browser or safari.BROWSER
    spec = SAFARI_MARKETS[market]
    url = spec["search"].format(q=urllib.parse.quote(query))
    page = {"cards": [], "title": "", "body": ""}
    try:
        browser.open(url, settle=2.5)
        for attempt in range(8):             # антибот-проверка или карточки дорисовываются
            page = browser.run(EXTRACT, spec["product"], LIMIT)
            if len(page["cards"]) >= 12 or (BLOCKED.search(page["body"]) and attempt >= 3):
                break
            browser.run(SCROLL)
            time.sleep(1.2)
        if market == "wb":
            from_json = _wb_from_json(browser)
            if from_json:
                return _result(market, query, url, started, from_json, note="из поискового JSON страницы")
    except safari.SafariError as error:
        return _result(market, query, url, started, status="error", note=str(error)[:300])
    if not page["cards"]:
        blocked = BLOCKED.search(page["body"])
        return _result(market, query, url, started, status="blocked" if blocked else "error",
                       note=(" ".join(page["body"].split())[:160] if blocked else
                             f"карточек не найдено, страница «{page['title'][:80]}»"))
    offers = []
    for position, card in enumerate(page["cards"], 1):
        # Баллы, кешбэк и платёж рассрочки тоже пишутся «… ₽»: суммы меньше 15% от самой
        # большой в карточке — не цена товара (на вёрстке Маркета так попадало «1 333 ₽»).
        top = max(card["prices"], default=0)
        prices = [p for p in card["prices"] if p >= 0.15 * top]
        offers.append({"market": market, "position": position, "title": card["title"],
                       "description": card["text"], "price": float(min(prices)) if prices else None,
                       "old_price": float(max(prices)) if len(prices) > 1 else None,
                       "url": card["url"], "rating": None, "reviews": None})
    return _result(market, query, url, started, offers, note="разбор карточек страницы")


def search(market, query):
    if market == "ym":
        return search_ym(query)
    return search_safari(market, query)


def search_all(query, markets=("ozon", "wb", "ym")):
    return [search(market, query) for market in markets]


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "sony wh-1000xm5"
    chosen = [m for m in sys.argv[2:] if m in MARKETS] or list(MARKETS)
    try:
        for result in search_all(query, chosen):
            print(f"\n■ {result['name']}: {result['status']} · {len(result['offers'])} карточек · "
                  f"{result['seconds']} с {result['note']}")
            for offer in result["offers"][:8]:
                price = f"{offer['price']:,.0f} ₽".replace(",", " ") if offer["price"] else "—"
                print(f"  {offer['position']:>2}. {price:>10}  {offer['title'][:80]}")
    finally:
        safari.BROWSER.quit()
