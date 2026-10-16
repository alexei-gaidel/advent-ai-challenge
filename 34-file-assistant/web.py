"""Файловый ассистент в браузере: http://localhost:8034

Слева — цель (свой текст или один из четырёх сценариев), в центре — трасса вызовов
MCP, которые ассистент сделал сам, и его отчёт, справа — diff черновика. Кнопки
«применить» и «выбросить» — действие человека: ассистент этих инструментов не получает.

Тонкий сервер: ни messages, ни URL провайдеров, ни работы с файлами здесь нет —
только методы FileAssistant.

    python3 web.py [--no-browser]
"""

import html
import json
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import assistant as fa

PORT = 8034
BOT = None
LOCK = threading.Lock()       # одно stdio-соединение с MCP и одна песочница на всё приложение


def bot():
    global BOT
    if BOT is None:
        BOT = fa.FileAssistant()
    return BOT


PAGE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Файловый ассистент</title>
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
main { display:grid; grid-template-columns:300px 1fr 1.1fr; gap:12px; padding:12px; }
@media (max-width:1000px) { main { grid-template-columns:1fr; } }
section { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px; min-width:0; }
h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--dim); margin:0 0 8px; }
textarea { width:100%; min-height:150px; background:#0b0d11; color:var(--text); border:1px solid var(--line);
           border-radius:8px; padding:8px; font:13px/1.5 -apple-system, sans-serif; resize:vertical; }
button { background:var(--accent); color:#0b0d11; border:0; border-radius:8px; padding:8px 14px;
         font-weight:600; cursor:pointer; margin:6px 6px 0 0; }
button.ghost { background:transparent; color:var(--text); border:1px solid var(--line); font-weight:500; }
button.preset { display:block; width:100%; text-align:left; margin:0 0 6px; }
button:disabled { opacity:.5; cursor:default; }
pre { white-space:pre-wrap; word-break:break-word; font:12px/1.45 ui-monospace, Menlo, monospace;
      background:#0b0d11; padding:8px; border-radius:6px; margin:6px 0; max-height:62vh; overflow:auto; }
.trace { font:12px ui-monospace, Menlo, monospace; max-height:30vh; overflow:auto; }
.trace div { padding:2px 0; border-bottom:1px solid var(--line); }
.trace .n { color:var(--dim); display:inline-block; width:28px; }
.trace .t { color:var(--tool); } .trace .w { color:var(--warn); } .trace .x { color:var(--bad); }
.add { color:var(--good); } .del { color:var(--bad); } .hunk { color:var(--accent); } .file { color:var(--warn); font-weight:600; }
.muted { color:var(--dim); font-size:12px; } .err { color:var(--bad); }
.stats { font:12px ui-monospace, Menlo, monospace; color:var(--dim); margin:4px 0 8px; }
</style></head><body>
<header><h1>Файловый ассистент</h1>
  <span class="badge">песочница: слепок __SNAPSHOT__</span>
  <span class="badge">MCP: __TOOLS__</span>
  <span class="badge">__MODEL__</span>
</header>
<main>
<section>
  <h2>Цель</h2>
  __PRESETS__
  <textarea id="goal" placeholder="Задача на уровне цели, например: найди, где используется find_env_file"></textarea>
  <button id="run" onclick="run()">Выполнить</button>
  <button class="ghost" onclick="post('/api/reset')">Сбросить песочницу</button>
  <p class="muted">Ассистент сам выбирает, какие файлы искать, читать и править. Правки
  попадают в черновик — на диск их переносит только кнопка «Применить».</p>
  <div id="ref"></div>
</section>
<section>
  <h2>Что делал ассистент</h2>
  <div class="stats" id="stats"></div>
  <div class="trace" id="trace"><span class="muted">трасса вызовов появится здесь</span></div>
  <h2 style="margin-top:12px">Отчёт</h2>
  <pre id="answer"></pre>
</section>
<section>
  <h2>Diff черновика</h2>
  <button onclick="post('/api/apply')">Применить</button>
  <button class="ghost" onclick="post('/api/discard')">Выбросить</button>
  <button class="ghost" onclick="loadDiff()">Обновить</button>
  <pre id="diff"><span class="muted">черновик пуст</span></pre>
  <div id="msg" class="muted"></div>
</section>
</main>
<script>
const PRESETS = __PRESETS_JSON__;
let current = null;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

function preset(key) {
  current = key;
  $("goal").value = PRESETS[key].goal;
  $("ref").innerHTML = "";
  if (key === "invariants") reference();
}
function renderDiff(text) {
  if (!text || text === "черновик пуст") { $("diff").innerHTML = '<span class="muted">черновик пуст</span>'; return; }
  $("diff").innerHTML = text.split("\n").map(l => {
    const cls = l.startsWith("+++") || l.startsWith("---") ? "file" : l.startsWith("@@") ? "hunk"
              : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "";
    return cls ? `<span class="${cls}">${esc(l)}</span>` : esc(l);
  }).join("\n");
}
function renderTrace(tools) {
  $("trace").innerHTML = tools.map(t => {
    const args = Object.entries(t.arguments).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(" ");
    const cls = !t.ok ? "x" : ["write_file", "edit_file"].includes(t.name) ? "w" : "t";
    return `<div><span class="n">${t.round}</span><span class="${cls}">${t.name}</span> ${esc(args)}` +
           (t.ok ? "" : ` <span class="x">✗ ${esc(t.result.slice(0, 140))}</span>`) + "</div>";
  }).join("") || '<span class="muted">инструменты не вызывались</span>';
}
async function call(path, body) {
  const r = await fetch(path, {method: body === undefined ? "GET" : "POST",
    headers: {"Content-Type": "application/json"}, body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await r.json();
  if (data.error) throw new Error(data.error);
  return data;
}
async function run() {
  const goal = $("goal").value.trim();
  if (!goal) return;
  $("run").disabled = true;
  $("answer").textContent = "ассистент работает — обычно 15–60 с…";
  $("trace").innerHTML = ""; $("stats").textContent = "";
  try {
    const r = await call("/api/run", {goal});
    renderTrace(r.tools);
    $("answer").textContent = r.answer;
    $("stats").textContent = `вызовов ${r.tools.length} · кругов ${r.rounds} · ошибок ${r.errors} · ` +
      `прочитано ${r.read.length} · изменено ${r.changed.length} · ` +
      `${r.prompt_tokens}+${r.completion_tokens} ток. · $${r.cost} · ${r.seconds} с`;
    renderDiff(r.diff);
  } catch (e) { $("answer").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  $("run").disabled = false;
}
async function post(path) {
  try { const r = await call(path, {}); $("msg").textContent = r.message; loadDiff(); }
  catch (e) { $("msg").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
}
async function loadDiff() {
  try { renderDiff((await call("/api/diff")).diff); } catch (e) { $("msg").textContent = e.message; }
}
async function reference() {
  const r = await call("/api/reference");
  $("ref").innerHTML = `<h2 style="margin-top:12px">Эталон кодом (invariants.py)</h2><div class="trace">` +
    r.violations.map(v => `<div><span class="t">${v.rule}</span> ${esc(v.path)}${v.line ? ":" + v.line : ""} — ${esc(v.detail)}</div>`).join("") + "</div>";
}
loadDiff();
</script>
</body></html>
"""


def build_page():
    info = bot().bridge.info()
    tools = [t["name"] for t in info["tools"]]
    human = [name for name in tools if name in ("apply_drafts", "discard_drafts")]
    presets = "".join(
        f'<button class="ghost preset" onclick="preset(\'{key}\')">{html.escape(p["title"])}</button>'
        for key, p in fa.PRESETS.items())
    return (PAGE.replace("__SNAPSHOT__", fa.workspace.SNAPSHOT)
                .replace("__TOOLS__", f"{len(tools) - len(human)} инструментов у модели, "
                                       f"{len(human)} у человека" if tools else "недоступен")
                .replace("__MODEL__", bot().model)
                .replace("__PRESETS_JSON__", json.dumps(fa.PRESETS, ensure_ascii=False))
                .replace("__PRESETS__", presets))


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
                with LOCK:
                    self._reply(200, "text/html", build_page())
            elif self.path == "/api/diff":
                with LOCK:
                    self._json({"diff": bot().diff()})
            elif self.path == "/api/reference":
                self._json({"violations": bot().reference()})
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
            actions = {"/api/apply": lambda: bot().apply(),
                       "/api/discard": lambda: bot().discard(),
                       "/api/reset": lambda: "песочница пересобрана: "
                                             f"{bot().reset().get('files')} файлов"}
            if self.path == "/api/run":
                with LOCK:
                    result = bot().run(str(payload.get("goal") or ""))
                self._json(result)
            elif self.path in actions:
                with LOCK:
                    self._json({"message": actions[self.path]()})
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
    print(f"Файловый ассистент: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
