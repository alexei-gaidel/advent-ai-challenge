"""Мини-чат по Конституции РФ: история, RAG на каждом ходу, источники, память задачи.

Запуск:
    python3 web.py     → откроется http://localhost:8025

Слева — чаты (режим выбирается при создании), в центре — лента: под каждым ответом
поисковый запрос, источники-ссылки и цитаты; справа — память задачи: цель, уточнения,
ограничения, термины. Поля можно поправить и закрепить замком — модель их не тронет.

Сервер тонкий: всё делает Chat из chat.py.
"""

import json
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import chat
import storage
import taskmemory

PORT = 8025
DB = storage.connect()
LOCK = threading.Lock()


PAGE = '''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RAG-чат с памятью</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#6b7280; --card:#f4f4f5;
          --accent:#2563eb; --border:#e4e4e7; --ok:#16a34a; --bad:#dc2626; --warn:#d97706; --me:#dbeafe; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa; --card:#27272a; --accent:#3b82f6;
            --border:#3f3f46; --ok:#22c55e; --bad:#f87171; --warn:#fbbf24; --me:#1e3a5f; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:14.5px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  .app { display:grid; grid-template-columns:230px 1fr 320px; height:100vh; }
  @media (max-width: 900px) { .app { grid-template-columns:1fr; height:auto; } }
  aside, section { border-right:1px solid var(--border); overflow:auto; padding:14px; min-width:0; }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin:0 0 8px; }
  input, select, textarea { width:100%; padding:7px 9px; font:inherit; border:1px solid var(--border);
          border-radius:7px; background:var(--bg); color:var(--fg); }
  textarea { resize:vertical; min-height:54px; }
  button { padding:8px 14px; font:inherit; border:0; border-radius:7px; background:var(--accent); color:#fff; cursor:pointer; }
  button:disabled { opacity:.5; cursor:wait; }
  button.ghost { background:var(--card); color:var(--fg); border:1px solid var(--border); }
  .chatlist div { padding:7px 9px; border-radius:7px; cursor:pointer; margin-bottom:3px; font-size:13.5px; }
  .chatlist div.active { background:var(--card); font-weight:600; }
  .chatlist small { display:block; color:var(--muted); font-weight:400; }
  .new { display:flex; flex-direction:column; gap:6px; margin-bottom:14px; }
  .main { display:flex; flex-direction:column; height:100vh; padding:0; }
  .feed { flex:1; overflow:auto; padding:16px; }
  .msg { max-width:820px; margin:0 0 14px; }
  .msg.user { margin-left:auto; background:var(--me); padding:8px 12px; border-radius:10px; width:fit-content; max-width:80%; }
  .msg.assistant .text { white-space:pre-wrap; }
  .msg.unknown .text { border-left:3px solid var(--warn); padding-left:10px; }
  .meta { color:var(--muted); font-size:12px; margin-top:4px; }
  .src { font-size:12.5px; margin-top:4px; }
  .src a { color:var(--accent); margin-right:8px; }
  details { font-size:12.5px; margin-top:4px; }
  .quote { background:var(--card); border-radius:6px; padding:4px 8px; margin:3px 0; }
  .quote::before { content:"✓ "; color:var(--ok); font-weight:700; }
  .compose { display:flex; gap:8px; padding:12px 16px; border-top:1px solid var(--border); }
  .compose textarea { min-height:44px; }
  .field { margin-bottom:12px; }
  .field label { display:flex; justify-content:space-between; font-size:12.5px; color:var(--muted); margin-bottom:3px; }
  .hint { color:var(--muted); font-size:12px; }
  .err { color:var(--bad); }
</style>
</head>
<body>
<div class="app">
  <aside>
    <h2>Новый чат</h2>
    <div class="new">
      <input id="title" placeholder="Название">
      <select id="mode">__MODE_OPTIONS__</select>
      <button id="create">Создать</button>
    </div>
    <h2>Чаты</h2>
    <div class="chatlist" id="chats"></div>
  </aside>
  <section class="main">
    <div class="feed" id="feed"><div class="hint">Создайте чат или выберите слева. Окно истории в промпте — __WINDOW__ сообщений,
      поиск: bge-m3 → порог → реранкер, ответ — с дословными цитатами.</div></div>
    <form class="compose" id="f">
      <textarea id="msg" placeholder="Сообщение… (Enter — отправить, Shift+Enter — перенос)"></textarea>
      <button id="send">→</button>
    </form>
  </section>
  <aside>
    <h2>Память задачи</h2>
    <div id="memory" class="hint">—</div>
  </aside>
</div>
<script>
const LABELS = __LABELS__;
const FIELDS = {goal: "Цель", clarified: "Уточнено", constraints: "Ограничения", terms: "Термины"};
let current = null;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function api(path, body) {
  const res = await fetch(path, body ? {method: "POST", headers: {"Content-Type": "application/json"},
                                        body: JSON.stringify(body)} : {});
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data;
}

async function loadChats() {
  const {chats} = await api("/api/chats");
  $("chats").innerHTML = chats.map(c => `<div data-id="${c.id}" class="${c.id === current ? "active" : ""}">
      ${esc(c.title)}<small>${esc(LABELS[c.mode])} · ${c.turns} ход.</small></div>`).join("") || '<div class="hint">пусто</div>';
}

function renderMessage(m) {
  if (m.role === "user") return `<div class="msg user">${esc(m.content)}</div>`;
  const meta = m.meta || {};
  const sources = (meta.sources || []).map(s => `<a href="${esc(s.source)}" target="_blank" title="${esc(s.section)}">${esc(s.chunk_id)}</a>`).join("");
  const quotes = (meta.quotes || []).map(q => `<div class="quote">«${esc(q.text)}» <span class="hint">${esc(q.chunk_id)}</span></div>`).join("");
  return `<div class="msg assistant ${meta.unknown ? "unknown" : ""}"><div class="text">${esc(m.content)}</div>
    <div class="src"><b>Источники:</b> ${sources || "нет — контекст слабый, ответ «не знаю»"}</div>
    ${quotes ? `<details><summary>цитаты (${meta.quotes.length})</summary>${quotes}</details>` : ""}
    <div class="meta">поиск: «${esc(meta.query)}»${meta.side_question ? " · побочный вопрос" : ""} ·
      ${meta.llm_calls} вызова LLM · ${meta.prompt_tokens}+${meta.completion_tokens} ток. · ${meta.seconds} с</div></div>`;
}

function renderMemory(state, mode) {
  if (mode !== "memory") { $("memory").innerHTML = '<div class="hint">В этом чате память задачи выключена (абляция): только окно истории.</div>'; return; }
  const locked = state.locked || [];
  const value = (k) => k === "terms" ? Object.entries(state.terms || {}).map(([a, b]) => a + " = " + b).join("\\n")
                     : k === "goal" ? state.goal : (state[k] || []).join("\\n");
  $("memory").innerHTML = Object.entries(FIELDS).map(([k, label]) => `<div class="field">
      <label>${label}<span><input type="checkbox" data-lock="${k}" ${locked.includes(k) ? "checked" : ""} style="width:auto"> 🔒</span></label>
      <textarea data-field="${k}">${esc(value(k))}</textarea></div>`).join("")
    + '<button class="ghost" id="saveMem" type="button">Сохранить память</button><div class="hint" style="margin-top:6px">По строке на пункт; термины — «термин = смысл». 🔒 — модель не меняет поле.</div>';
  $("saveMem").onclick = saveMemory;
}

async function openChat(id) {
  current = id;
  const data = await api("/api/chat?id=" + id);
  $("feed").innerHTML = data.messages.map(renderMessage).join("") || '<div class="hint">Пустой чат — напишите первое сообщение.</div>';
  $("feed").scrollTop = $("feed").scrollHeight;
  renderMemory(data.state, data.chat.mode);
  loadChats();
}

async function saveMemory() {
  const fields = {};
  document.querySelectorAll("[data-field]").forEach(t => {
    const lines = t.value.split("\\n").map(s => s.trim()).filter(Boolean);
    const k = t.dataset.field;
    if (k === "goal") fields.goal = t.value.trim();
    else if (k === "terms") fields.terms = Object.fromEntries(lines.map(l => l.split("=").map(s => s.trim())).filter(p => p.length === 2));
    else fields[k] = lines;
  });
  const locked = [...document.querySelectorAll("[data-lock]:checked")].map(c => c.dataset.lock);
  const data = await api("/api/state", {chat_id: current, fields, locked});
  renderMemory(data.state, "memory");
}

$("chats").onclick = (e) => { const d = e.target.closest("[data-id]"); if (d) openChat(Number(d.dataset.id)); };
$("create").onclick = async () => {
  const data = await api("/api/chats", {title: $("title").value || "Новый чат", mode: $("mode").value});
  $("title").value = ""; openChat(data.id);
};
$("msg").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("f").requestSubmit(); } });
$("f").onsubmit = async (e) => {
  e.preventDefault();
  const message = $("msg").value.trim();
  if (!message || !current) return;
  $("send").disabled = true;
  $("feed").insertAdjacentHTML("beforeend", renderMessage({role: "user", content: message}) + '<div class="meta" id="wait">ищу в Конституции, обновляю память…</div>');
  $("feed").scrollTop = $("feed").scrollHeight;
  $("msg").value = "";
  try { await api("/api/send", {chat_id: current, message}); await openChat(current); }
  catch (err) { $("wait").outerHTML = `<div class="err">Ошибка: ${esc(err.message)}</div>`; }
  finally { $("send").disabled = false; }
};
loadChats();
</script>
</body>
</html>
'''


def build_page():
    options = "".join(f'<option value="{m}">{chat.MODE_LABELS[m]}</option>' for m in chat.MODES)
    return (PAGE.replace("__MODE_OPTIONS__", options)
            .replace("__WINDOW__", str(chat.WINDOW))
            .replace("__LABELS__", json.dumps(chat.MODE_LABELS, ensure_ascii=False)))


def chat_payload(chat_id):
    c = chat.Chat(chat_id, DB)
    return {"chat": c.info, "messages": c.history(), "state": c.state}


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
            elif url.path == "/api/chats":
                self._json({"chats": storage.chats(DB)})
            elif url.path == "/api/chat":
                self._json(chat_payload(int(parse_qs(url.query).get("id", ["0"])[0])))
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
            if self.path == "/api/chats":
                c = chat.Chat.create(str(payload.get("title") or "Новый чат")[:80],
                                     payload.get("mode") or "memory", DB)
                self._json({"id": c.id})
            elif self.path == "/api/send":
                with LOCK:
                    result = chat.Chat(int(payload.get("chat_id") or 0), DB).send(
                        str(payload.get("message") or ""))
                self._json(result)
            elif self.path == "/api/state":
                fields = payload.get("fields") or {}
                if not isinstance(fields, dict):
                    raise ValueError("fields должен быть объектом")
                state = chat.Chat(int(payload.get("chat_id") or 0), DB).set_state(
                    {k: v for k, v in fields.items() if k in taskmemory.FIELDS},
                    payload.get("locked"))
                self._json({"state": state})
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
    print(f"RAG-чат с памятью: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
