"""Дашборды по открытым данным ХМАО — Югры: http://localhost:8035

Слева — 20 категорий портала, под выбранной — темы, которые сформировала модель. В центре —
сводный дашборд темы по муниципалитетам (цифры считает код), справа — чат, который меняет
вид дашборда и коротко объясняет цифры.

Тонкий сервер: ни messages, ни URL провайдеров, ни разбора CSV здесь нет — только методы
OpenDataAssistant.

    python3 web.py [--no-browser]
"""

import json
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import assistant as od

PORT = 8035
BOT = od.OpenDataAssistant()
LOCK = threading.Lock()       # кэш и вежливый темп к порталу — общие на всё приложение

PAGE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Открытые данные Югры</title>
<style>
:root { --bg:#0f1115; --panel:#171a21; --line:#262a33; --text:#e4e4e7; --dim:#8b8f98;
        --accent:#60a5fa; --good:#4ade80; --warn:#fbbf24; --bad:#f87171; --bar:#3b82f6; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:14px/1.5 -apple-system, "Segoe UI", sans-serif; }
header { padding:10px 16px; border-bottom:1px solid var(--line); background:var(--panel);
         display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
header h1 { font-size:16px; margin:0; }
.badge { font:12px ui-monospace, Menlo, monospace; padding:2px 8px; border-radius:6px;
         border:1px solid var(--line); color:var(--dim); }
main { display:grid; grid-template-columns:260px 1fr 340px; gap:12px; padding:12px; }
@media (max-width:1100px) { main { grid-template-columns:1fr; } }
section { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px; min-width:0; }
h2 { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--dim); margin:0 0 8px; }
.cat, .topic { padding:5px 8px; border-radius:7px; cursor:pointer; border:1px solid transparent; }
.cat:hover, .topic:hover { border-color:var(--line); }
.cat.active, .topic.active { border-color:var(--accent); background:#111827; }
.cat .n, .topic .n { color:var(--dim); font-size:12px; float:right; }
.topic { margin-left:10px; font-size:13px; }
.topic .d { color:var(--dim); font-size:12px; display:block; }
.kpis { display:grid; grid-template-columns:repeat(auto-fill, minmax(170px, 1fr)); gap:8px; margin-bottom:10px; }
.kpi { border:1px solid var(--line); border-radius:8px; padding:8px 10px; }
.kpi .v { font-size:20px; font-weight:600; }
.kpi .t { color:var(--dim); font-size:12px; }
.controls { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin:8px 0; }
select, input[type=text], textarea { background:#0b0d11; color:var(--text); border:1px solid var(--line);
         border-radius:7px; padding:5px 8px; font:13px -apple-system, sans-serif; }
label.mo { font-size:12px; color:var(--dim); border:1px solid var(--line); border-radius:6px; padding:2px 6px; cursor:pointer; }
label.mo input { vertical-align:middle; }
table { width:100%; border-collapse:collapse; font-size:12px; }
td, th { padding:4px 6px; border-bottom:1px solid var(--line); text-align:right; }
td:first-child, th:first-child { text-align:left; }
th { color:var(--dim); font-weight:500; }
.warn { color:var(--warn); } .bad { color:var(--bad); } .muted { color:var(--dim); font-size:12px; }
.chat { display:flex; flex-direction:column; gap:8px; max-height:58vh; overflow:auto; margin-bottom:8px; }
.msg { padding:8px 10px; border-radius:8px; white-space:pre-wrap; }
.msg.user { background:#111827; border:1px solid #1e3a5f; align-self:flex-end; }
.msg.bot { background:#0b0d11; border:1px solid var(--line); }
.msg mark { background:#7f1d1d; color:#fecaca; padding:0 2px; border-radius:3px; }
textarea { width:100%; min-height:64px; resize:vertical; }
button { background:var(--accent); color:#0b0d11; border:0; border-radius:7px; padding:7px 12px;
         font-weight:600; cursor:pointer; margin-top:6px; }
button.chip { background:transparent; color:var(--text); border:1px solid var(--line); font-weight:400;
              padding:3px 8px; margin:0 4px 4px 0; font-size:12px; }
button:disabled { opacity:.5; }
svg text { fill:var(--text); font:12px -apple-system, sans-serif; }
svg .dim { fill:var(--dim); }
a { color:var(--accent); }
</style></head><body>
<header><h1>Открытые данные Югры → дашборд</h1>
  <span class="badge">data.admhmao.ru · __DATASETS__ наборов · __CATEGORIES__ категорий</span>
  <span class="badge">темы и рецепты сведения — __MODEL__, цифры — код</span>
</header>
<main>
<section><h2>Категории и темы</h2><div id="cats"></div></section>
<section>
  <div id="dash"><p class="muted">Выбери категорию слева, затем тему. Темы внутри категории
  формирует модель: на портале есть только издатели, а не подкатегории. Первое открытие темы
  качает наборы с портала (не чаще раза в секунду) — это 10–40 секунд.</p></div>
</section>
<section>
  <h2>Чат по дашборду</h2>
  <div id="chips"></div>
  <div class="chat" id="chat"></div>
  <textarea id="q" placeholder="Например: где хуже всего исполняется план? покажи только Сургут и Урай"></textarea>
  <button id="send" onclick="send()">Спросить</button>
</section>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
let state = {slug: null, n: null, view: null, dash: null};

function money(v, unit) {
  if (v === null || v === undefined) return "—";
  if (unit === "%") return v.toFixed(1).replace(".", ",") + "%";
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(1).replace(".", ",") + " млрд";
  if (a >= 1e6) return (v / 1e6).toFixed(1).replace(".", ",") + " млн";
  return Math.round(v).toLocaleString("ru-RU");
}
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const data = await r.json();
  if (data.error) throw new Error(data.error);
  return data;
}
async function loadCats() {
  const r = await api("/api/categories");
  $("cats").innerHTML = r.categories.map(c =>
    `<div class="cat" id="c-${c.slug}" onclick="openCat('${c.slug}')">${esc(c.title)}<span class="n">${c.count ?? ""}</span></div>
     <div id="t-${c.slug}"></div>`).join("");
}
async function openCat(slug) {
  document.querySelectorAll(".cat").forEach(e => e.classList.remove("active"));
  $("c-" + slug).classList.add("active");
  const box = $("t-" + slug);
  box.innerHTML = '<div class="topic muted">модель группирует наборы в темы…</div>';
  try {
    const r = await api("/api/topics?slug=" + slug);
    box.innerHTML = r.topics.map(t =>
      `<div class="topic" id="tp-${slug}-${t.n}" onclick="openTopic('${slug}', ${t.n})">${esc(t.title)}
        <span class="n">${t.count}</span><span class="d">${esc(t.publishers.slice(0, 5).join(", "))}${t.publishers.length > 5 ? "…" : ""}${t.comparable ? "" : " · не сводится"}</span></div>`).join("");
  } catch (e) { box.innerHTML = `<div class="topic bad">${esc(e.message)}</div>`; }
}
async function openTopic(slug, n, view) {
  document.querySelectorAll(".topic").forEach(e => e.classList.remove("active"));
  const el = $(`tp-${slug}-${n}`); if (el) el.classList.add("active");
  if (state.slug !== slug || state.n !== n) $("chat").innerHTML = "";
  state.slug = slug; state.n = n;
  $("dash").innerHTML = '<p class="muted">качаю наборы темы с портала и применяю рецепты сведения…</p>';
  try { render(await api("/api/open", {slug, n, view: view || null})); }
  catch (e) { $("dash").innerHTML = `<p class="bad">${esc(e.message)}</p>`; }
}
function bars(chart) {
  const items = chart.series, W = 640, row = 26, H = items.length * row + 10, left = 150;
  const max = Math.max(...items.map(s => s.value || 0), 1);
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img">` + items.map((s, i) => {
    const y = i * row + 4, w = s.value ? Math.max(2, (W - left - 110) * s.value / max) : 0;
    return `<text x="${left - 8}" y="${y + 15}" text-anchor="end">${esc(s.mo)}</text>` +
      (s.value === null ? `<text x="${left}" y="${y + 15}" class="dim">нет данных</text>` :
      `<rect x="${left}" y="${y + 3}" width="${w}" height="16" rx="3" fill="var(--bar)"/>` +
      `<text x="${left + w + 6}" y="${y + 15}">${money(s.value, chart.unit)}</text>`);
  }).join("") + "</svg>";
}
function render(d) {
  state.view = d.view; state.dash = d;
  const fresh = Object.fromEntries(d.freshness.map(f => [f.mo, f]));
  const opts = d.table.columns.map(c => `<option value="${c.key}" ${c.key === d.view.indicator ? "selected" : ""}>${esc(c.title)}</option>`).join("");
  const mos = d.mo_all.map(m => `<label class="mo"><input type="checkbox" value="${esc(m)}" ${d.view.mo.includes(m) ? "checked" : ""} onchange="applyControls()"> ${esc(m)}</label>`).join(" ");
  const b = d.build || {};
  $("dash").innerHTML = `
    <h2>${esc(d.topic)}</h2><p class="muted">${esc(d.description)}</p>
    <div class="kpis">${d.kpis.map(k => `<div class="kpi"><div class="v">${money(k.value, k.unit)}</div>
      <div class="t">${esc(k.title)}<br>${k.count} из ${k.of} МО</div></div>`).join("")}</div>
    <div class="controls">
      <select id="ind" onchange="applyControls()">${opts}</select>
      <select id="mode" onchange="applyControls()"><option value="absolute">значения</option>
        <option value="execution" ${d.view.mode === "execution" ? "selected" : ""}>% исполнения плана</option></select>
      <select id="sort" onchange="applyControls()"><option value="value_desc">по убыванию</option>
        <option value="value_asc" ${d.view.sort === "value_asc" ? "selected" : ""}>по возрастанию</option>
        <option value="name" ${d.view.sort === "name" ? "selected" : ""}>по алфавиту</option></select>
    </div>
    <div class="controls">${mos}</div>
    <h2>${esc(d.chart.title)}${d.chart.unit && d.chart.unit !== "%" ? ", " + esc(d.chart.unit) : ""}</h2>
    ${bars(d.chart)}
    <h2 style="margin-top:12px">Таблица и свежесть</h2>
    <table><tr><th>МО</th>${d.table.columns.map(c => `<th>${esc(c.title)}</th>`).join("")}<th>отчёт на</th></tr>
    ${d.table.rows.map(r => { const f = fresh[r.mo] || {};
      const date = f.from_version ? `<span class="warn" title="в данных нет отчётной даты — это дата выкладки файла">${r.date}*</span>`
                 : f.stale ? `<span class="warn" title="отстаёт от самой свежей даты на ${f.lag_days} дн.">${r.date}!</span>` : r.date;
      return `<tr><td><a href="${r.source}" target="_blank" title="${esc(r.title)}">${esc(r.mo)}</a>${r.problems.length ? ` <span class="warn" title="${esc(r.problems.join("; "))}">⚠</span>` : ""}</td>` +
        d.table.columns.map(c => `<td>${money(r.values[c.key], c.unit)}</td>`).join("") + `<td>${date}</td></tr>`; }).join("")}
    </table>
    <p class="muted">* дата выкладки файла: в самих данных отчётной даты нет. ! — отстаёт от самой свежей больше чем на 120 дней.</p>
    ${d.skipped.length ? `<h2 style="margin-top:12px">Не сведено</h2>` + d.skipped.map(s => `<div class="muted">✗ ${esc(s.mo)} — ${esc(s.title || "")}: ${esc(s.reason)}</div>`).join("") : ""}
    ${d.notes.length ? `<p class="warn">${esc(d.notes.join("; "))}</p>` : ""}
    <p class="muted">рецепты сведения: ${b.cached ? "из кэша" : `${b.prompt_tokens}+${b.completion_tokens} ток., $${b.cost}`} · ${b.seconds} с</p>`;
  $("chips").innerHTML = ["Где хуже всего исполняется план?", "Сравни только города", "Кто лидирует по объёму?", "Можно ли этим цифрам доверять?"]
    .map(t => `<button class="chip" onclick="ask('${t}')">${t}</button>`).join("");
}
function applyControls() {
  const view = {indicator: $("ind").value, mode: $("mode").value, sort: $("sort").value,
    mo: [...document.querySelectorAll("label.mo input:checked")].map(i => i.value)};
  openTopic(state.slug, state.n, view);
}
function ask(t) { $("q").value = t; send(); }
async function send() {
  const q = $("q").value.trim();
  if (!q || state.slug === null) return;
  $("q").value = ""; $("send").disabled = true;
  $("chat").insertAdjacentHTML("beforeend", `<div class="msg user">${esc(q)}</div>`);
  try {
    const r = await api("/api/chat", {slug: state.slug, n: state.n, view: state.view, message: q});
    let text = esc(r.answer);
    for (const u of r.unknown_numbers) text = text.split(esc(u)).join(`<mark title="этого числа нет на экране">${esc(u)}</mark>`);
    const calls = r.calls.map(c => `set_view ${esc(JSON.stringify(c.arguments))}`).join("<br>");
    $("chat").insertAdjacentHTML("beforeend", `<div class="msg bot">${text}` +
      (calls ? `<div class="muted">${calls}</div>` : "") +
      (r.unknown_numbers.length ? `<div class="bad">чисел нет на экране: ${r.unknown_numbers.length}</div>` : "") +
      `<div class="muted">${r.prompt_tokens}+${r.completion_tokens} ток. · $${r.cost} · ${r.seconds} с</div></div>`);
    render(r.dashboard);
  } catch (e) { $("chat").insertAdjacentHTML("beforeend", `<div class="msg bot bad">${esc(e.message)}</div>`); }
  $("send").disabled = false; $("chat").scrollTop = 1e9;
}
loadCats();
</script>
</body></html>
"""


def build_page():
    with LOCK:
        cats = BOT.categories()
        registry = od.portal.registry()
    return (PAGE.replace("__DATASETS__", str(len(registry)))
                .replace("__CATEGORIES__", str(len(cats)))
                .replace("__MODEL__", BOT.model))


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
        try:
            url = urlparse(self.path)
            if url.path == "/":
                self._reply(200, "text/html", build_page())
            elif url.path == "/api/categories":
                with LOCK:
                    self._json({"categories": BOT.categories()})
            elif url.path == "/api/topics":
                slug = parse_qs(url.query).get("slug", [""])[0]
                with LOCK:
                    self._json(BOT.topics(slug))
            else:
                self._json({"error": "не найдено"}, 404)
        except Exception as error:
            traceback.print_exc()
            self._json({"error": str(error)}, 400)

    def do_POST(self):
        # Весь разбор под try: битый payload должен вернуть JSON, а не оборвать соединение.
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("ожидался JSON-объект")
            slug, n = str(payload.get("slug") or ""), payload.get("n")
            if not isinstance(n, int):
                raise ValueError("нужен номер темы n")
            view = payload.get("view") if isinstance(payload.get("view"), dict) else None
            if self.path == "/api/open":
                with LOCK:
                    self._json(BOT.open(slug, n, view))
            elif self.path == "/api/chat":
                with LOCK:
                    self._json(BOT.chat(slug, n, view, str(payload.get("message") or "")))
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
    print(f"Открытые данные Югры: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
