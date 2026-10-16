"""Поиск самой низкой цены по фото или описанию: http://localhost:8035

Чат: прикрепи фото и/или опиши товар → карточка (что за товар) → поиск на Ozon, Wildberries
и Яндекс.Маркете → итог «дешевле всего здесь» и все найденные предложения с меткой «тот же
товар / другой вариант / аксессуар / другой товар» и причиной. Следующие сообщения уточняют
(«нужен чёрный», «это XM4»), «Новый товар» начинает сначала.

Тонкий сервер: ни messages, ни URL провайдеров, ни разбора маркетплейсов — только
методы PriceAssistant. Приложение локальное, на одного человека: одна карточка в памяти.

    python3 web.py [--no-browser]
"""

import base64
import json
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import assistant as pa
import markets
import safari

PORT = 8035
MAX_BODY = 12 * 1024 * 1024
BOT = pa.PriceAssistant()
LOCK = threading.Lock()       # одна карточка и одно окно Safari на приложение

PAGE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Где дешевле</title>
<style>
:root { --bg:#0f1115; --panel:#171a21; --line:#262a33; --text:#e4e4e7; --dim:#8b8f98;
        --accent:#60a5fa; --good:#4ade80; --warn:#fbbf24; --bad:#f87171; --tool:#c4b5fd; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:14px/1.5 -apple-system, "Segoe UI", sans-serif; }
header { padding:10px 16px; border-bottom:1px solid var(--line); background:var(--panel);
         display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
header h1 { font-size:16px; margin:0; }
.badge { font:12px ui-monospace, Menlo, monospace; padding:2px 8px; border-radius:6px; border:1px solid var(--line); color:var(--dim); }
main { max-width:980px; margin:0 auto; padding:12px 16px 160px; }
.msg { margin:10px 0; padding:10px 12px; border-radius:10px; border:1px solid var(--line); background:var(--panel); }
.msg.user { background:#111827; border-color:#1e3a5f; margin-left:15%; }
.msg.user img { max-width:180px; max-height:180px; border-radius:6px; display:block; margin-bottom:6px; }
.card { color:var(--dim); font-size:13px; }
.card b { color:var(--text); }
.best { border:1px solid #1f4d33; background:#0f1d16; border-radius:8px; padding:10px; margin:8px 0; }
.best .p { font-size:22px; font-weight:700; color:var(--good); }
.markets { display:flex; gap:8px; flex-wrap:wrap; margin:6px 0; }
.mk { border:1px solid var(--line); border-radius:8px; padding:6px 10px; font-size:13px; min-width:170px; }
.mk .s { font-size:12px; } .ok { color:var(--good); } .captcha, .error { color:var(--bad); }
table { width:100%; border-collapse:collapse; font-size:12px; margin-top:6px; }
td, th { padding:4px 6px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
th { color:var(--dim); font-weight:500; } td.r { text-align:right; white-space:nowrap; }
.c-same { color:var(--good); } .c-variant { color:var(--warn); } .c-accessory, .c-other { color:var(--dim); }
details summary { cursor:pointer; color:var(--accent); font-size:13px; }
.muted { color:var(--dim); font-size:12px; }
a { color:var(--accent); }
footer { position:fixed; bottom:0; left:0; right:0; background:var(--panel); border-top:1px solid var(--line); padding:10px 16px; }
.row { max-width:980px; margin:0 auto; display:flex; gap:8px; align-items:flex-end; flex-wrap:wrap; }
textarea { flex:1; min-width:240px; min-height:48px; background:#0b0d11; color:var(--text); border:1px solid var(--line);
           border-radius:8px; padding:8px; font:14px -apple-system, sans-serif; resize:vertical; }
button { background:var(--accent); color:#0b0d11; border:0; border-radius:8px; padding:10px 14px; font-weight:600; cursor:pointer; }
button.ghost { background:transparent; color:var(--text); border:1px solid var(--line); font-weight:400; }
button:disabled { opacity:.5; }
label.m { font-size:12px; color:var(--dim); }
#preview { max-height:48px; border-radius:6px; display:none; }
</style></head><body>
<header><h1>Где дешевле — тот же товар на трёх маркетплейсах</h1>
  <span class="badge">фото → __VISION__ · текст и сопоставление → __TEXT__</span>
  <span class="badge">Ozon и WB — через Safari, Маркет — HTTP</span>
</header>
<main id="chat">
  <div class="msg"><b>Пришли фото и/или описание товара.</b> Найду этот же товар на Ozon, Wildberries
  и Яндекс.Маркете и покажу, где дешевле. Чехлы, амбушюры, другие модели и цвета отсею — и покажу,
  почему. Ozon и WB открываются в окне Safari: если там появится проверка «я не робот», пройди её
  в окне сам — приложение капчу не обходит.</div>
</main>
<footer><div class="row">
  <label class="ghost" style="cursor:pointer"><input type="file" id="file" accept="image/*" hidden onchange="pick()">
    <span class="badge">📎 фото</span></label>
  <img id="preview">
  <textarea id="q" placeholder="Например: наушники сони с шумодавом пятое поколение, чёрные"></textarea>
  <div>
    <label class="m"><input type="checkbox" class="mkt" value="ozon" checked> Ozon</label>
    <label class="m"><input type="checkbox" class="mkt" value="wb" checked> WB</label>
    <label class="m"><input type="checkbox" class="mkt" value="ym" checked> Маркет</label><br>
    <button id="send" onclick="send()">Искать</button>
    <button class="ghost" onclick="reset()">Новый товар</button>
  </div>
</div></footer>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const rub = v => v == null ? "—" : Math.round(v).toLocaleString("ru-RU") + " ₽";
const NAMES = {ozon: "Ozon", wb: "Wildberries", ym: "Яндекс.Маркет"};
const LABELS = {same: "тот же товар", variant: "другой вариант", accessory: "аксессуар", other: "другой товар"};
let image = null;

function pick() {
  const f = $("file").files[0]; if (!f) return;
  if (f.size > 8 * 1024 * 1024) { alertMsg("фото больше 8 МБ"); return; }
  const r = new FileReader();
  r.onload = () => { image = r.result; $("preview").src = image; $("preview").style.display = "block"; };
  r.readAsDataURL(f);
}
function add(html, cls) { $("chat").insertAdjacentHTML("beforeend", `<div class="msg ${cls || ""}">${html}</div>`); window.scrollTo(0, 1e9); }
function alertMsg(t) { add(`<span class="error">${esc(t)}</span>`); }
async function api(path, body) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const d = await r.json(); if (d.error) throw new Error(d.error); return d;
}
async function reset() { await api("/api/reset", {}); add("Начинаем с нового товара.", ""); }
function cardHtml(c) {
  const v = Object.entries(c.variant).filter(([k, x]) => x).map(([k, x]) => x).join(", ");
  return `<div class="card">Ищу: <b>${esc(c.title || "?")}</b>${v ? " · " + esc(v) : ""} · уверенность ${c.confidence}
    ${c.queries.length ? `<br>запрос: «${esc(c.queries[0])}»` : ""}${c.must.length ? ` · обязательно: ${esc(c.must.join(", "))}` : ""}</div>`;
}
function resultHtml(r) {
  let h = cardHtml(r.card);
  if (r.need_input) return h + `<p><b>${esc(r.answer)}</b></p>`;
  const m = r.match, b = m.best;
  h += b ? `<div class="best"><div class="p">${rub(b.price)}</div> на <b>${NAMES[b.market]}</b> —
          <a href="${esc(b.url)}" target="_blank">${esc(b.title)}</a><div class="muted">${esc(b.note)}</div></div>`
       : `<div class="best" style="border-color:#5c4813;background:#1c1a12">Того же товара не нашлось — уточни модель или вариант.</div>`;
  h += `<div class="markets">` + Object.entries(m.per_market).map(([k, x]) =>
    `<div class="mk"><b>${NAMES[k]}</b> <span class="s ${x.status}">${x.status === "ok" ? "ок" : x.status}</span> · ${x.seconds} с<br>
     найдено ${x.found}, тот же товар ${x.same}${x.best ? `<br>лучшее: <a href="${esc(x.best.url)}" target="_blank">${rub(x.best.price)}</a>` : ""}
     ${x.note ? `<div class="muted">${esc(x.note)}</div>` : ""}</div>`).join("") + `</div>`;
  if (m.naive && (!b || m.naive.url !== b.url))
    h += `<p class="muted">Самое дешёвое в выдаче — ${rub(m.naive.price)}, но это «${esc(m.naive.title.slice(0, 80))}» —
          ${esc((m.offers.find(o => o.url === m.naive.url) || {}).note || "")}</p>`;
  h += `<details><summary>Все предложения (${m.offers.length}): тот же ${m.counts.same}, вариант ${m.counts.variant},
        аксессуары ${m.counts.accessory}, другое ${m.counts.other}</summary><table>
        <tr><th>класс</th><th>магазин</th><th>цена</th><th>название</th><th>почему</th></tr>` +
    m.offers.map(o => `<tr><td class="c-${o.class}">${LABELS[o.class]}${o.suspicious ? " ⚠" : ""}</td><td>${NAMES[o.market]}</td>
      <td class="r">${rub(o.price)}</td><td><a href="${esc(o.url)}" target="_blank">${esc(o.title.slice(0, 90))}</a></td>
      <td class="muted">${esc(o.note)} · ${o.by}${o.suspicious ? " · " + esc(o.suspicious) : ""}</td></tr>`).join("") + `</table></details>`;
  h += `<p class="muted">${r.steps.map(s => `${esc(s.step)}${s.status ? " " + s.status : ""} ${s.seconds} с`).join(" → ")}
        · ${r.prompt_tokens}+${r.completion_tokens} ток. · $${r.cost} · всего ${r.seconds} с</p>`;
  return h;
}
async function send() {
  const text = $("q").value.trim();
  if (!text && !image) return;
  const markets = [...document.querySelectorAll(".mkt:checked")].map(i => i.value);
  add((image ? `<img src="${image}">` : "") + esc(text), "user");
  $("q").value = ""; $("send").disabled = true;
  const body = {text, image, markets}; image = null; $("preview").style.display = "none"; $("file").value = "";
  add('<span class="muted" id="wait">опознаю товар и ищу на маркетплейсах — 20–60 с…</span>');
  try { const r = await api("/api/ask", body); $("wait").parentElement.remove(); add(resultHtml(r)); }
  catch (e) { $("wait").parentElement.remove(); alertMsg(e.message); }
  $("send").disabled = false;
}
$("q").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
</script>
</body></html>
"""


def build_page():
    return (PAGE.replace("__VISION__", pa.identify.VISION_MODEL)
                .replace("__TEXT__", pa.identify.TEXT_MODEL))


def decode_image(data_url):
    """data:image/...;base64,… → байты. Пусто → None."""
    if not data_url:
        return None
    if not isinstance(data_url, str) or not data_url.startswith("data:image/") or "," not in data_url:
        raise ValueError("фото должно прийти как data:image/…;base64")
    try:
        return base64.b64decode(data_url.split(",", 1)[1], validate=True)
    except ValueError as error:
        raise ValueError("фото повреждено (base64)") from error


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, content_type, body):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload, code=200):
        self._reply(code, "application/json", json.dumps(payload, ensure_ascii=False))

    def do_GET(self):
        if self.path == "/":
            self._reply(200, "text/html", build_page())
        else:
            self._json({"error": "не найдено"}, 404)

    def do_POST(self):
        # Весь разбор под try: битый payload должен вернуть JSON, а не оборвать соединение.
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                # Тело надо дочитать, иначе клиент получит обрыв соединения («Load failed»
                # в браузере), а не понятную ошибку. Совсем огромное — не читаем.
                if length <= 8 * MAX_BODY:
                    remaining = length
                    while remaining > 0:
                        remaining -= len(self.rfile.read(min(remaining, 1 << 20)) or b"x" * remaining)
                raise ValueError(f"запрос больше {MAX_BODY // 1024 // 1024} МБ — уменьши фото")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("ожидался JSON-объект")
            if self.path == "/api/ask":
                chosen = [m for m in payload.get("markets") or [] if m in markets.MARKETS]
                if not chosen:
                    raise ValueError("выбери хотя бы один маркетплейс")
                image = decode_image(payload.get("image"))
                with LOCK:
                    self._json(BOT.ask(str(payload.get("text") or ""), image, chosen))
            elif self.path == "/api/reset":
                with LOCK:
                    BOT.reset()
                self._json({"ok": True})
            else:
                self._json({"error": "не найдено"}, 404)
        except Exception as error:
            traceback.print_exc()
            self._json({"error": str(error)}, 400)

    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Где дешевле: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        safari.BROWSER.quit()


if __name__ == "__main__":
    main()
