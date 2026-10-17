"""Рейтинг акций Мосбиржи: http://localhost:8035

Чат слева, дашборд справа: график цены за год (SMA50, SMA200, IMOEX в масштабе акции),
основные показатели, баллы по факторам — формула против AI — и итог: рейтинг от 1 до 10
с десятичной запятой и рекомендация. Следующие сообщения — вопросы к показанному анализу
или новая компания.

Тонкий сервер: ни messages, ни URL провайдеров и бирж — только методы StockAssistant.
Приложение локальное, на одного человека: один текущий анализ в памяти.

    python3 web.py [--no-browser]
"""

import json
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import assistant as sa

PORT = 8035
MAX_BODY = 64 * 1024
BOT = sa.StockAssistant()
LOCK = threading.Lock()

PAGE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Рейтинг акций</title>
<style>
:root { color-scheme: light;
  --bg:#f4f4f2; --panel:#fcfcfb; --line:#e3e2de; --grid:#ecebe7; --text:#0b0b0b; --dim:#52514e; --muted:#8a8984;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#a8a7a1;
  --good:#008300; --warn:#b77d00; --bad:#d03b3a; --accent:#2a78d6; --user:#eaf1fb; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark;
  --bg:#111110; --panel:#1a1a19; --line:#2c2c2a; --grid:#242423; --text:#f0efec; --dim:#c3c2b7; --muted:#8f8e86;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#6d6c66;
  --good:#3fb950; --warn:#d6a01d; --bad:#e66767; --accent:#3987e5; --user:#17243a; } }
:root[data-theme="dark"] { color-scheme: dark;
  --bg:#111110; --panel:#1a1a19; --line:#2c2c2a; --grid:#242423; --text:#f0efec; --dim:#c3c2b7; --muted:#8f8e86;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#6d6c66;
  --good:#3fb950; --warn:#d6a01d; --bad:#e66767; --accent:#3987e5; --user:#17243a; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:14px/1.45 -apple-system, "Segoe UI", sans-serif; }
header { padding:10px 16px; border-bottom:1px solid var(--line); background:var(--panel); display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
header h1 { font-size:16px; margin:0; }
.badge { font:12px ui-monospace, Menlo, monospace; padding:2px 8px; border-radius:6px; border:1px solid var(--line); color:var(--dim); }
.wrap { display:grid; grid-template-columns: minmax(280px, 360px) 1fr; gap:16px; padding:16px; max-width:1400px; margin:0 auto; }
@media (max-width: 900px) { .wrap { grid-template-columns: 1fr; } }
.panel { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px; }
#chat { display:flex; flex-direction:column; height:calc(100vh - 90px); position:sticky; top:16px; }
@media (max-width: 900px) { #chat { height:auto; position:static; } }
#log { flex:1; overflow-y:auto; display:flex; flex-direction:column; gap:8px; min-height:200px; }
.msg { padding:8px 10px; border-radius:10px; border:1px solid var(--line); white-space:pre-wrap; }
.msg.user { background:var(--user); align-self:flex-end; max-width:90%; }
.msg .meta { color:var(--muted); font-size:12px; margin-top:4px; white-space:normal; }
.row { display:flex; gap:8px; margin-top:10px; }
textarea { flex:1; min-height:44px; background:var(--bg); color:var(--text); border:1px solid var(--line); border-radius:8px; padding:8px; font:14px -apple-system, sans-serif; resize:vertical; }
button { background:var(--accent); color:#fff; border:0; border-radius:8px; padding:8px 14px; font-weight:600; cursor:pointer; }
button:disabled { opacity:.5; }
.chips { display:flex; gap:6px; flex-wrap:wrap; margin-top:8px; }
.chip { font-size:12px; padding:3px 8px; border-radius:12px; border:1px solid var(--line); cursor:pointer; color:var(--dim); background:none; font-weight:400; }
#dash { display:flex; flex-direction:column; gap:16px; }
.empty { color:var(--dim); padding:40px; text-align:center; }
.top { display:grid; grid-template-columns: 1fr auto; gap:16px; align-items:start; }
@media (max-width: 640px) { .top { grid-template-columns: 1fr; } }
.title h2 { margin:0; font-size:20px; } .title .sub { color:var(--dim); font-size:13px; }
.price { font-size:28px; font-weight:700; margin-top:6px; font-variant-numeric: tabular-nums; }
.chg { font-size:14px; font-weight:500; margin-left:8px; }
.rating { text-align:center; min-width:200px; border:1px solid var(--line); border-radius:12px; padding:12px 18px; }
.rating .num { font-size:52px; font-weight:800; line-height:1; font-variant-numeric: tabular-nums; }
.rating .of { color:var(--muted); font-size:16px; }
.rating .rec { font-size:16px; font-weight:700; margin-top:6px; text-transform:uppercase; letter-spacing:.04em; }
.rating .f { color:var(--dim); font-size:12px; margin-top:6px; }
.r-buy { color:var(--good); } .r-hold { color:var(--warn); } .r-sell { color:var(--bad); }
.legend { display:flex; gap:14px; flex-wrap:wrap; font-size:12px; color:var(--dim); margin-bottom:6px; }
.legend i { display:inline-block; width:16px; height:0; border-top:2px solid; vertical-align:middle; margin-right:5px; }
.chart { position:relative; }
.chart svg { display:block; width:100%; height:auto; }
.tip { position:absolute; pointer-events:none; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:6px 8px; font-size:12px; display:none; box-shadow:0 2px 8px rgba(0,0,0,.12); white-space:nowrap; }
.tiles { display:grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap:8px; }
.tile { border:1px solid var(--line); border-radius:10px; padding:8px 10px; }
.tile .k { color:var(--dim); font-size:12px; } .tile .v { font-size:18px; font-weight:600; font-variant-numeric: tabular-nums; }
.tile .n { color:var(--muted); font-size:11px; }
h3 { margin:0 0 8px; font-size:14px; }
table.f { width:100%; border-collapse:collapse; font-size:13px; }
table.f td { padding:5px 4px; border-bottom:1px solid var(--grid); vertical-align:middle; }
table.f td.num { text-align:right; width:46px; font-variant-numeric: tabular-nums; }
.bar { position:relative; height:16px; }
.bar span { position:absolute; left:0; height:6px; border-radius:0 4px 4px 0; }
.bar .a { top:1px; background:var(--s4); } .bar .b { top:9px; background:var(--s1); }
.why { color:var(--dim); font-size:12px; }
ul { margin:4px 0; padding-left:18px; } li { margin:2px 0; }
.cols { display:grid; grid-template-columns: 1fr 1fr; gap:16px; } @media (max-width: 640px) { .cols { grid-template-columns: 1fr; } }
.note { color:var(--muted); font-size:12px; }
</style></head><body>
<header><h1>Рейтинг акций Мосбиржи</h1>
  <span class="badge">AI-аналитик: __MODEL__</span>
  <span class="badge">цены — ISS Мосбиржи · МСФО — smart-lab · ставка — ЦБ</span>
</header>
<div class="wrap">
  <section class="panel" id="chat">
    <div id="log"><div class="msg">Назови компанию или тикер — разберу отчётность, график и ставку ЦБ и поставлю рейтинг от 1 до 10. Потом можно спросить «почему?».
<span class="meta">Не индивидуальная инвестиционная рекомендация: оценка по открытым данным, без новостей.</span></div></div>
    <div class="chips">
      <button class="chip" onclick="quick('Сбербанк')">Сбербанк</button>
      <button class="chip" onclick="quick('стоит ли покупать Лукойл?')">Лукойл</button>
      <button class="chip" onclick="quick('Яндекс')">Яндекс</button>
      <button class="chip" onclick="quick('Магнит')">Магнит</button>
      <button class="chip" onclick="quick('почему такой рейтинг?')">почему?</button>
    </div>
    <div class="row"><textarea id="q" placeholder="Например: стоит ли брать Газпром?"></textarea>
      <button id="send" onclick="send()">→</button></div>
  </section>
  <section id="dash"><div class="panel empty">Здесь появится дашборд: график, показатели и рейтинг.</div></section>
</div>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const ru = (v, d = 1) => v == null ? "—" : Number(v).toLocaleString("ru-RU", {minimumFractionDigits: d, maximumFractionDigits: d});
const pct = (v, d = 1, sign = false) => v == null ? "—" : (sign && v > 0 ? "+" : "") + ru(v, d) + "%";
const recClass = r => r === "покупать" ? "r-buy" : r === "держать" ? "r-hold" : "r-sell";

function add(html, cls) { $("log").insertAdjacentHTML("beforeend", `<div class="msg ${cls || ""}">${html}</div>`); $("log").scrollTop = 1e9; }
async function api(path, body) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const d = await r.json(); if (d.error) throw new Error(d.error); return d;
}
function quick(t) { $("q").value = t; send(); }
async function send() {
  const text = $("q").value.trim(); if (!text) return;
  add(esc(text), "user"); $("q").value = ""; $("send").disabled = true;
  add('<span class="note" id="wait">собираю данные и спрашиваю аналитика…</span>');
  try {
    const r = await api("/api/ask", {text});
    $("wait").parentElement.remove();
    if (r.kind === "analysis") {
      render(r);
      add(`<b>${esc(r.name)} — ${ru(r.ai.rating)} из 10, ${esc(r.ai.recommendation)}.</b>\n${esc(r.ai.summary)}
<span class="meta">формула ${ru(r.formula)} · ${r.ai.prompt_tokens}+${r.ai.completion_tokens} ток. · $${r.ai.cost.toFixed(4)} · ${r.seconds} с</span>`);
    } else {
      add(esc(r.text) + (r.cost != null ? `<span class="meta">${r.prompt_tokens}+${r.completion_tokens} ток. · $${(r.cost || 0).toFixed(4)} · ${r.seconds} с</span>` : ""));
    }
  } catch (e) { $("wait").parentElement.remove(); add(`<span class="r-sell">${esc(e.message)}</span>`); }
  $("send").disabled = false;
}
$("q").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });

// ---- график цены: одна ось (рубли), IMOEX приведён к цене акции в первый день ----
const SERIES = [["c", "Цена", "--s1", ""], ["s50", "SMA 50", "--s2", ""], ["s200", "SMA 200", "--s3", ""], ["i", "IMOEX (в масштабе акции)", "--s4", "5 4"]];
function chart(rows) {
  const W = 860, H = 300, L = 8, R = 64, T = 10, B = 24;
  const vals = rows.flatMap(r => SERIES.map(s => r[s[0]]).filter(v => v != null));
  let lo = Math.min(...vals), hi = Math.max(...vals); const pad = (hi - lo) * 0.06; lo -= pad; hi += pad;
  const x = i => L + (W - L - R) * i / (rows.length - 1), y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  const step = Math.pow(10, Math.floor(Math.log10((hi - lo) / 4))); const tick = [1, 2, 2.5, 5, 10].map(m => m * step).find(t => (hi - lo) / t <= 5);
  let g = "";
  for (let v = Math.ceil(lo / tick) * tick; v <= hi; v += tick)
    g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)"/><text x="${W - R + 6}" y="${y(v) + 4}" font-size="11" fill="var(--muted)">${ru(v, tick < 1 ? 2 : 0)}</text>`;
  let month = "";
  rows.forEach((r, i) => { const m = r.d.slice(0, 7); if (m !== month) { month = m; if (i > 0) g += `<text x="${x(i)}" y="${H - 6}" font-size="11" fill="var(--muted)" text-anchor="middle">${["янв","фев","мар","апр","май","июн","июл","авг","сен","окт","ноя","дек"][+m.slice(5) - 1]}</text>`; } });
  const lines = SERIES.slice().reverse().map(([k, , color, dash]) => {
    let d = "", pen = false;
    rows.forEach((r, i) => { if (r[k] == null) { pen = false; return; } d += (pen ? "L" : "M") + x(i).toFixed(1) + " " + y(r[k]).toFixed(1); pen = true; });
    return `<path d="${d}" fill="none" stroke="var(${color})" stroke-width="${k === "c" ? 2 : 1.5}" stroke-dasharray="${dash}" stroke-linejoin="round"/>`;
  }).join("");
  const last = rows[rows.length - 1];
  const end = `<circle cx="${x(rows.length - 1)}" cy="${y(last.c)}" r="4" fill="var(--s1)" stroke="var(--panel)" stroke-width="2"/>`;
  return {svg: `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="График цены за год">${g}${lines}${end}
    <line id="cross" y1="${T}" y2="${H - B}" stroke="var(--muted)" stroke-width="1" visibility="hidden"/>
    <rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent" id="hit"/></svg>`, x, W, L, R};
}
function hover(rows, geo) {
  const box = document.querySelector(".chart"), svg = box.querySelector("svg"), tip = box.querySelector(".tip"), cross = svg.querySelector("#cross");
  svg.querySelector("#hit").addEventListener("mousemove", e => {
    const rect = svg.getBoundingClientRect(), px = (e.clientX - rect.left) / rect.width * geo.W;
    const i = Math.max(0, Math.min(rows.length - 1, Math.round((px - geo.L) / (geo.W - geo.L - geo.R) * (rows.length - 1))));
    const r = rows[i], cx = geo.x(i);
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
    tip.innerHTML = `<b>${r.d.split("-").reverse().join(".")}</b><br>` + SERIES.filter(s => r[s[0]] != null)
      .map(([k, name, color]) => `<span style="color:var(${color})">●</span> ${name}: ${ru(r[k], 2)}`).join("<br>");
    tip.style.display = "block";
    const left = cx / geo.W * rect.width; tip.style.left = (left > rect.width / 2 ? left - tip.offsetWidth - 12 : left + 12) + "px"; tip.style.top = "8px";
  });
  svg.querySelector("#hit").addEventListener("mouseleave", () => { tip.style.display = "none"; cross.setAttribute("visibility", "hidden"); });
}

function tile(k, v, n) { return `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div>${n ? `<div class="n">${n}</div>` : ""}</div>`; }
function render(r) {
  const m = r.metrics, q = r.quote, ai = r.ai;
  const adj = Object.fromEntries(ai.adjustments.map(a => [a.factor, a]));
  const chg = q.change_pct || 0;
  const tiles = [
    tile("Капитализация", ru(m.cap_bn, 0) + " млрд ₽"),
    tile("P/E", ru(m.pe, 1), m.loss ? "убыток" : `прибыль ${r.period}`),
    tile("P/BV", ru(m.pbv, 2)),
    m.is_bank ? tile("Достаточность капитала", pct(m.capital_adequacy), "Н1.1") : tile("EV/EBITDA", ru(m.ev_ebitda, 1)),
    m.is_bank ? tile("NPL", pct(m.npl)) : tile("Чистый долг/EBITDA", ru(m.nd_ebitda, 2)),
    tile("ROE", pct(m.roe)),
    tile("Дивдоходность", pct(m.div_yield), m.payout != null ? `выплата ${ru(m.payout, 0)}% прибыли` : ""),
    tile("Выручка", pct(m.revenue_growth, 1, true), m.growth_years),
    tile("Чистая прибыль", pct(m.income_growth, 1, true), m.growth_years),
    tile("За год", pct(m.return_1y, 1, true), `IMOEX ${pct(m.index_return_1y, 1, true)}`),
    tile("Волатильность", pct(m.volatility, 0), `просадка ${pct(m.max_drawdown, 0)}`),
    tile("Ставка ЦБ", pct(r.key_rate), `доходность прибыли ${pct(m.earnings_yield)}`),
  ].join("");
  const factors = Object.keys(r.weights).map(k => {
    const f = r.factors[k], a = adj[k], to = a && a.to != null ? a.to : f;
    return `<tr><td>${esc(r.factor_names[k])}<div class="note">вес ${Math.round(r.weights[k] * 100)}%</div>${a ? `<div class="why">AI: ${esc(a.why)}</div>` : ""}</td>
      <td style="width:34%"><div class="bar"><span class="a" style="width:${(f || 0) * 10}%"></span><span class="b" style="width:${(to || 0) * 10}%"></span></div></td>
      <td class="num">${f == null ? "—" : ru(f)}</td><td class="num"><b>${to == null ? "—" : ru(to)}</b></td></tr>`;
  }).join("");
  const c = chart(r.chart);
  $("dash").innerHTML = `
  <div class="panel top">
    <div class="title"><h2>${esc(r.name)} <span class="badge">${esc(r.ticker)}</span></h2>
      <div class="sub">${esc(r.full_name)} · отчёт МСФО: ${esc(r.period)}, опубликован ${esc(r.report_date)} · котировка ${esc(q.time)}</div>
      <div class="price">${ru(q.price, q.price < 10 ? 3 : 1)} ₽<span class="chg ${chg >= 0 ? "r-buy" : "r-sell"}">${pct(chg, 2, true)}</span></div></div>
    <div class="rating"><div><span class="num ${recClass(ai.recommendation)}">${ru(ai.rating)}</span> <span class="of">/ 10</span></div>
      <div class="rec ${recClass(ai.recommendation)}">${esc(ai.recommendation)}</div>
      <div class="f">формула: ${ru(r.formula)} · ${esc(r.formula_recommendation)}${ai.consistent ? "" : `<br>AI написал «${esc(ai.said)}»`}</div></div>
  </div>
  <div class="panel"><h3>Цена за год, ₽</h3>
    <div class="legend">${SERIES.map(([, n, color, dash]) => `<span><i style="border-top-color:var(${color});${dash ? "border-top-style:dashed" : ""}"></i>${n}</span>`).join("")}</div>
    <div class="chart">${c.svg}<div class="tip"></div></div></div>
  <div class="panel"><h3>Основные показатели</h3><div class="tiles">${tiles}</div>
    <div class="note" style="margin-top:8px">Мультипликаторы — по текущей цене и отчёту ${esc(r.period)}; рост — ${esc(m.growth_years)} (полные годы).</div></div>
  <div class="panel"><h3>Факторы: формула и AI</h3>
    <div class="legend"><span><i style="border-top:6px solid var(--s4)"></i>формула</span><span><i style="border-top:6px solid var(--s1)"></i>после поправки AI</span></div>
    <table class="f">${factors}</table></div>
  <div class="panel cols"><div><h3>Плюсы</h3><ul>${ai.pros.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div>
    <div><h3>Риски</h3><ul>${ai.risks.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div>
    <div style="grid-column:1/-1"><h3>Что отслеживать</h3><div>${esc(ai.watch)}</div>
    <div class="note" style="margin-top:8px">Источники: цены ${esc(r.sources.prices)}, отчётность <a href="${esc(r.sources.reports)}" target="_blank">${esc(r.sources.reports)}</a>, ставка ${esc(r.sources.rate)}. Не индивидуальная инвестиционная рекомендация.</div></div></div>`;
  hover(r.chart, c);
}
</script>
</body></html>
"""


def build_page():
    return PAGE.replace("__MODEL__", BOT.model)


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
                self.rfile.read(min(length, 1 << 20))
                raise ValueError("слишком длинное сообщение")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("ожидался JSON-объект")
            if self.path == "/api/ask":
                with LOCK:
                    self._json(BOT.ask(str(payload.get("text") or "")))
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
    print(f"Рейтинг акций: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
