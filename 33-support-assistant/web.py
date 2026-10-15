"""Панель оператора поддержки: http://localhost:8033

Слева — тикеты из CRM, в центре — тикет, карточка клиента и журнал шлюза, справа —
черновик ассистента (диагноз + ответ клиенту, источники RAG, вызовы MCP). Кнопки
«в заметку» и «ответить и закрыть» пишут в CRM через ассистента — модель туда не пишет.

Тонкий сервер: ни messages, ни URL провайдеров здесь нет, только методы SupportAssistant.

    python3 web.py [--no-browser]
"""

import json
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import assistant as support

PORT = 8033
BOT = None
LOCK = threading.Lock()       # одно stdio-соединение с MCP на всё приложение


def bot():
    global BOT
    if BOT is None:
        BOT = support.SupportAssistant()
    return BOT


PAGE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Поддержка · ассистент</title>
<style>
:root { --bg:#0f1115; --panel:#171a21; --line:#262a33; --text:#e4e4e7; --dim:#8b8f98;
        --accent:#60a5fa; --good:#4ade80; --warn:#fbbf24; --bad:#f87171; --tool:#c4b5fd; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:14px/1.5 -apple-system, "Segoe UI", sans-serif; }
header { padding:10px 16px; border-bottom:1px solid var(--line); background:var(--panel);
         display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
header h1 { font-size:16px; margin:0; }
.badge { font:12px ui-monospace, Menlo, monospace; padding:2px 8px; border-radius:6px;
         border:1px solid var(--line); color:var(--dim); }
.badge.warn { color:var(--warn); border-color:#5c4813; }
main { display:grid; grid-template-columns:260px 1fr 1.1fr; gap:12px; padding:12px; }
@media (max-width:900px) { main { grid-template-columns:1fr; } }
section { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px; min-width:0; }
h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--dim); margin:0 0 8px; }
.ticket { padding:8px; border-radius:8px; cursor:pointer; border:1px solid transparent; }
.ticket:hover { border-color:var(--line); }
.ticket.active { border-color:var(--accent); background:#111827; }
.ticket .id { font:12px ui-monospace, Menlo, monospace; color:var(--dim); }
.st { font-size:11px; padding:1px 6px; border-radius:4px; border:1px solid var(--line); margin-left:4px; }
.st.новый { color:var(--warn); } .st.закрыт { color:var(--dim); } .st.в { color:var(--accent); }
pre { white-space:pre-wrap; font:12px/1.45 ui-monospace, Menlo, monospace; background:#0b0d11;
      padding:8px; border-radius:6px; margin:6px 0; max-height:260px; overflow:auto; }
table { width:100%; border-collapse:collapse; font:12px ui-monospace, Menlo, monospace; }
td, th { padding:3px 6px; border-bottom:1px solid var(--line); text-align:left; }
.s4 { color:var(--warn); } .s5 { color:var(--bad); } .s2 { color:var(--good); }
.key.отозван, .key.заменён { color:var(--bad); }
textarea, input { width:100%; background:#0b0d11; color:var(--text); border:1px solid var(--line);
         border-radius:8px; padding:8px; font:14px/1.5 -apple-system, sans-serif; }
textarea { min-height:170px; resize:vertical; }
button { background:var(--accent); color:#0b0d11; border:0; border-radius:8px; padding:8px 14px;
         font-weight:600; cursor:pointer; margin:6px 6px 0 0; }
button.ghost { background:transparent; color:var(--text); border:1px solid var(--line); }
button:disabled { opacity:.5; }
.diag { border-left:3px solid var(--warn); padding:6px 10px; background:#1c1a12; border-radius:4px; }
.chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
.chip { font:11px ui-monospace, Menlo, monospace; padding:2px 6px; border-radius:5px; border:1px solid var(--line); color:var(--dim); }
.chip.tool { color:var(--tool); border-color:#3b2f5e; } .chip.code { color:var(--good); border-color:#1f4d33; }
.muted { color:var(--dim); font-size:12px; }
.err { color:var(--bad); }
</style></head><body>
<header><h1>Поддержка · приватный LLM-сервис</h1>
  <span class="badge">RAG: __CHUNKS__ фрагментов (README шлюза, FAQ, ошибки)</span>
  <span class="badge">MCP CRM: __TOOLS__</span> __STALE__</header>
<main>
  <section><h2>Тикеты</h2><div id="list" class="muted">загрузка…</div></section>
  <section><h2>Тикет и клиент</h2><div id="card" class="muted">Выберите тикет слева.</div></section>
  <section><h2>Ассистент</h2>
    <div id="draft" class="muted">Нажмите «Подготовить ответ» или задайте вопрос о продукте без тикета.</div>
    <div style="margin-top:10px">
      <input id="q" placeholder="Свободный вопрос о продукте (без тикета) или уточнение к тикету">
      <button id="ask">Подготовить ответ</button>
    </div>
  </section>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let current = null, last = null;

async function api(path, body) {
  const res = await fetch(path, body ? {method:"POST", headers:{"Content-Type":"application/json"},
                                        body: JSON.stringify(body)} : {});
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data;
}

async function loadList() {
  const {tickets} = await api("/api/tickets");
  $("list").innerHTML = tickets.map(t => `<div class="ticket ${t.id === current ? "active" : ""}" data-id="${t.id}">
      <div class="id">${t.id} · ${esc(t.user_id)}<span class="st ${esc(t.status.split(" ")[0])}">${esc(t.status)}</span></div>
      <div>${esc(t.subject)}</div></div>`).join("");
  document.querySelectorAll(".ticket").forEach(el => el.onclick = () => openTicket(el.dataset.id));
}

async function openTicket(id) {
  current = id; last = null; loadList();
  $("draft").innerHTML = `<span class="muted">Нажмите «Подготовить ответ» — ассистент возьмёт данные тикета из CRM.</span>`;
  const {ticket: t, user: u, events: ev} = await api("/api/ticket?id=" + encodeURIComponent(id));
  $("card").innerHTML = `
    <div><b>${esc(t.id)}</b> · ${esc(t.created)} · <span class="st">${esc(t.status)}</span></div>
    <h3 style="margin:6px 0">${esc(t.subject)}</h3><div>${esc(t.text)}</div>
    ${t.attachment ? `<pre>${esc(t.attachment)}</pre>` : ""}
    ${t.notes.length ? `<div class="muted" style="margin-top:6px">Заметки:</div>` + t.notes.map(n =>
       `<pre>${esc(n.time)} · ${esc(n.author)}\n${esc(n.text)}</pre>`).join("") : ""}
    <h2 style="margin-top:12px">Клиент</h2>
    <div>${esc(u.name)} · ${esc(u.email)} · тариф <b>${esc(u.plan)}</b> · ${esc(u.network)}</div>
    <table><tr><th>ключ</th><th>маска</th><th>лимит</th><th>статус</th></tr>${u.keys.map(k =>
      `<tr><td>${esc(k.name)}</td><td>${esc(k.key)}</td><td>${k.rpm}/мин</td>
       <td class="key ${esc(k.status)}">${esc(k.status)}${k.reason ? " · " + esc(k.changed) + ": " + esc(k.reason) : ""}</td></tr>`).join("")}</table>
    <h2 style="margin-top:12px">Журнал шлюза (${ev.total})</h2>
    ${ev.events.length ? `<table><tr><th>время</th><th>ключ</th><th>адрес</th><th>статус</th><th>детали</th></tr>${ev.events.map(e =>
      `<tr><td>${esc(e.time)}</td><td>${esc(e.key)}</td><td>${esc(e.path)}</td>
       <td class="s${String(e.status)[0]}">${e.status}</td>
       <td>${esc([e.error, e.max_tokens && "max_tokens " + e.max_tokens, e.clipped && "обрезан",
                  e.estimate && "≈" + e.estimate + " ток", e.concurrent && e.concurrent + " одновременно"].filter(Boolean).join(" · "))}</td></tr>`).join("")}</table>`
      : `<div class="muted">${esc(ev.note)}</div>`}`;
}

function renderDraft(r) {
  last = r;
  const chips = (r.tools || []).map(t => `<span class="chip ${t.by === "модель" ? "tool" : "code"}">MCP ${esc(t.name)} · ${esc(t.by)}</span>`).join("")
    + (r.sources || []).map(s => `<span class="chip">[${s.n}] ${esc(s.source.split("/").pop())} › ${esc(s.section.split(" › ").pop())} · ${esc(s.score)}</span>`).join("");
  $("draft").innerHTML = `
    ${r.diagnosis ? `<div class="diag"><b>Диагноз.</b> ${esc(r.diagnosis)}</div>` : ""}
    <div class="muted" style="margin-top:8px">Ответ клиенту — можно править:</div>
    <textarea id="reply">${esc(r.reply)}</textarea>
    <div class="chips">${chips}</div>
    <div class="muted" style="margin-top:6px">${r.prompt_tokens}+${r.completion_tokens} токенов · $${r.cost} · ${r.seconds} с${r.stale ? " · индекс устарел" : ""}</div>
    ${r.ticket_id ? `<button id="note" class="ghost">В заметку тикета</button><button id="close">Ответить и закрыть</button>` : ""}
    <div id="saved" class="muted"></div>`;
  if (r.ticket_id) {
    $("note").onclick = () => save("в работе");
    $("close").onclick = () => save("закрыт");
  }
}

async function save(status) {
  const text = (last.diagnosis ? "Диагноз: " + last.diagnosis + "\n\n" : "") + "Ответ клиенту: " + $("reply").value;
  try {
    const r = await api("/api/note", {ticket_id: last.ticket_id, text, status});
    $("saved").textContent = `Сохранено в CRM через MCP: ${r.ticket_id}, статус «${r.status}», заметок ${r.notes}.`;
    openTicket(last.ticket_id).then(() => renderDraft(last));
  } catch (e) { $("saved").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
}

$("ask").onclick = async () => {
  const question = $("q").value.trim();
  if (!current && !question) { $("draft").innerHTML = `<span class="err">Выберите тикет или задайте вопрос.</span>`; return; }
  $("ask").disabled = true; $("draft").innerHTML = `<span class="badge">ассистент думает…</span>`;
  try { renderDraft(await api("/api/answer", {ticket_id: current, question})); }
  catch (e) { $("draft").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  $("ask").disabled = false;
};
loadList().catch(e => $("list").innerHTML = `<span class="err">${esc(e.message)}</span>`);
</script></body></html>"""


def build_page():
    b = bot()
    tools = ", ".join(t["name"] for t in b.bridge.info()["tools"]) or "нет соединения"
    stale = '<span class="badge warn">индекс устарел — python3 index.py</span>' if b.index.stale else ""
    return (PAGE.replace("__CHUNKS__", str(b.index.meta.get("chunks", 0)))
            .replace("__TOOLS__", tools).replace("__STALE__", stale))


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
            elif url.path == "/api/tickets":
                with LOCK:
                    self._json({"tickets": bot().tickets()})
            elif url.path == "/api/ticket":
                with LOCK:
                    self._json(bot().ticket_card(parse_qs(url.query).get("id", [""])[0]))
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
            if self.path == "/api/answer":
                with LOCK:
                    result = bot().answer(question=str(payload.get("question") or "") or None,
                                          ticket_id=payload.get("ticket_id") or None)
                self._json(result)
            elif self.path == "/api/note":
                with LOCK:
                    result = bot().save_note(str(payload.get("ticket_id") or ""),
                                             str(payload.get("text") or ""),
                                             payload.get("status") or None)
                self._json(result)
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
    print(f"Панель поддержки: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
