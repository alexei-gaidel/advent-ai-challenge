"""Веб-чат ассистента разработчика: http://localhost:8031

Тонкий сервер: строку из поля ввода отдаёт DevAssistant.handle() и рисует результат —
ответ, источники RAG и вызовы MCP. Ни messages, ни URL провайдеров здесь нет.

    python3 web.py [--no-browser]
"""

import json
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import assistant as dev

PORT = 8031
BOT = None
LOCK = threading.Lock()      # одно stdio-соединение с MCP на всё приложение


def bot():
    global BOT
    if BOT is None:
        BOT = dev.DevAssistant()
    return BOT


PAGE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ассистент разработчика</title>
<style>
:root { --bg:#0f1115; --panel:#171a21; --line:#262a33; --text:#e4e4e7; --dim:#8b8f98;
        --accent:#60a5fa; --good:#4ade80; --warn:#fbbf24; --tool:#c4b5fd; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font:15px/1.5 -apple-system, "Segoe UI", sans-serif; }
header { display:flex; gap:12px; align-items:center; padding:12px 16px;
         border-bottom:1px solid var(--line); background:var(--panel); flex-wrap:wrap; }
header h1 { font-size:16px; margin:0; }
.badge { font:12px ui-monospace, Menlo, monospace; padding:3px 8px; border-radius:6px;
         border:1px solid var(--line); color:var(--dim); }
.badge.branch { color:var(--good); border-color:#1f4d33; }
.badge.warn { color:var(--warn); border-color:#5c4813; }
main { max-width:920px; margin:0 auto; padding:16px; }
#log { display:flex; flex-direction:column; gap:14px; padding-bottom:110px; }
.msg { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
.msg.user { border-color:#1e3a5f; color:var(--accent); font-family:ui-monospace, Menlo, monospace; }
.answer { white-space:pre-wrap; }
.answer.mono { font:13px/1.45 ui-monospace, Menlo, monospace; }
.meta { margin-top:10px; font-size:12px; color:var(--dim); display:flex; flex-direction:column; gap:6px; }
.chips { display:flex; flex-wrap:wrap; gap:6px; }
.chip { font:12px ui-monospace, Menlo, monospace; padding:2px 7px; border-radius:5px;
        border:1px solid var(--line); }
.chip.tool { color:var(--tool); border-color:#3b2f5e; }
.chip.code { color:var(--good); border-color:#1f4d33; }
details summary { cursor:pointer; }
details pre { white-space:pre-wrap; font-size:12px; max-height:220px; overflow:auto;
              background:#0b0d11; padding:8px; border-radius:6px; }
form { position:fixed; left:0; right:0; bottom:0; background:var(--panel);
       border-top:1px solid var(--line); padding:12px 16px; }
form div { max-width:920px; margin:0 auto; display:flex; gap:8px; }
input { flex:1; min-width:0; background:#0b0d11; color:var(--text); border:1px solid var(--line);
        border-radius:8px; padding:10px 12px; font:14px ui-monospace, Menlo, monospace; }
button { background:var(--accent); color:#0b0d11; border:0; border-radius:8px; padding:0 16px;
         font-weight:600; cursor:pointer; }
button:disabled { opacity:.5; }
.hints { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:12px; }
.hints button { background:transparent; color:var(--dim); border:1px solid var(--line);
                font:12px ui-monospace, Menlo, monospace; padding:4px 8px; font-weight:400; }
.err { color:#f87171; }
</style></head><body>
<header>
  <h1>Ассистент разработчика</h1>
  <span class="badge branch" id="branch">ветка: …</span>
  <span class="badge">RAG: __DOCS__ документов · __CHUNKS__ чанков</span>
  <span class="badge">MCP: __TOOLS__</span>
  __STALE__
</header>
<main>
  <div class="hints">
    <button data-q="/help Где лежит MCP-клиент и какие транспорты он поддерживает?">/help MCP-клиент</button>
    <button data-q="/help Что сейчас не закоммичено?">/help что не закоммичено</button>
    <button data-q="/help Какие колонки у таблицы chunks в индексе дня 25?">/help схема chunks</button>
    <button data-q="/branch">/branch</button>
    <button data-q="/status">/status</button>
    <button data-q="/files 31-dev-assistant">/files</button>
    <button data-q="/help">/help</button>
  </div>
  <div id="log"></div>
</main>
<form id="form"><div>
  <input id="q" autocomplete="off" placeholder="/help как устроен проект?  ·  /branch  ·  /diff  ·  /files 17-mcp-tool">
  <button id="send">→</button>
</div></form>
<script>
const log = document.getElementById("log"), q = document.getElementById("q"),
      send = document.getElementById("send");
const esc = s => String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

function add(cls, html) {
  const el = document.createElement("div");
  el.className = "msg " + cls; el.innerHTML = html; log.appendChild(el);
  el.scrollIntoView({behavior: "smooth", block: "end"}); return el;
}

function render(r) {
  if (r.error) return `<span class="err">Ошибка: ${esc(r.error)}</span>`;
  let html = `<div class="answer ${r.kind === "answer" ? "" : "mono"}">${esc(r.answer)}</div>`;
  const meta = [];
  if (r.tools && r.tools.length) {
    meta.push(`<div class="chips">MCP: ${r.tools.map(t =>
      `<span class="chip ${t.by === "модель" ? "tool" : "code"}" title="${esc(t.by)}">${esc(t.name)}${
        Object.keys(t.arguments || {}).length ? " " + esc(JSON.stringify(t.arguments)) : ""} · ${esc(t.by)}</span>`
    ).join("")}</div>`);
  }
  if (r.sources && r.sources.length) {
    meta.push(`<div class="chips">RAG: ${r.sources.map(s =>
      `<span class="chip">[${s.n}] ${esc(s.source)} › ${esc(s.section.slice(0, 40))} · ${s.score}</span>`
    ).join("")}</div>`);
  }
  if (r.kind === "answer") {
    meta.push(`<div>${r.prompt_tokens}+${r.completion_tokens} токенов · $${r.cost} · ${r.seconds} с` +
              `${r.rounds ? " · кругов инструментов: " + r.rounds : ""}${r.stale ? " · индекс устарел" : ""}</div>`);
    const traced = (r.tools || []).filter(t => t.result);
    if (traced.length) meta.push(`<details><summary>что вернул MCP</summary>${traced.map(t =>
      `<pre>${esc(t.name)}\n${esc(t.result)}</pre>`).join("")}</details>`);
  }
  if (meta.length) html += `<div class="meta">${meta.join("")}</div>`;
  return html;
}

async function ask(text) {
  if (!text.trim()) return;
  add("user", esc(text)); q.value = ""; send.disabled = true;
  const pending = add("bot", `<span class="badge">думаю…</span>`);
  try {
    const res = await fetch("/api/ask", {method: "POST", headers: {"Content-Type": "application/json"},
                                         body: JSON.stringify({text})});
    const r = await res.json();
    pending.innerHTML = render(r);
    if (r.branch) document.getElementById("branch").textContent = "ветка: " + r.branch;
  } catch (e) { pending.innerHTML = `<span class="err">${esc(e)}</span>`; }
  send.disabled = false; q.focus();
}

document.getElementById("form").onsubmit = e => { e.preventDefault(); ask(q.value); };
document.querySelectorAll(".hints button").forEach(b => b.onclick = () => ask(b.dataset.q));
fetch("/api/branch").then(r => r.json()).then(r => {
  if (r.branch) document.getElementById("branch").textContent = "ветка: " + r.branch; });
q.focus();
</script></body></html>"""


def build_page():
    b = bot()
    meta = b.index.meta
    tools = ", ".join(t["name"] for t in b.bridge.info()["tools"]) or "нет соединения"
    stale = ('<span class="badge warn">индекс устарел — python3 index.py</span>'
             if b.index.stale else "")
    return (PAGE.replace("__DOCS__", str(meta.get("documents", 0)))
            .replace("__CHUNKS__", str(meta.get("chunks", 0)))
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
            if self.path == "/":
                self._reply(200, "text/html", build_page())
            elif self.path == "/api/branch":
                with LOCK:
                    text = bot().bridge.call("git_branch", {})
                self._json({"branch": text.splitlines()[0].replace("Ветка: ", "")})
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
            if self.path != "/api/ask":
                self._json({"error": "не найдено"}, 404)
                return
            with LOCK:
                result = bot().handle(str(payload.get("text") or ""))
            self._json(result)
        except Exception as error:
            traceback.print_exc()
            self._json({"error": str(error)}, 400)

    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Ассистент разработчика: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
