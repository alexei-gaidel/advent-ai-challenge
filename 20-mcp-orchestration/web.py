"""Панель оркестрации: зарегистрированные MCP-серверы, чат с агентом и лента вызовов.

Запуск:
    python3 web.py     → откроется http://localhost:8020

Слева — три сервера и их инструменты под полными именами. Справа — чат: каждый
вызов в ленте окрашен цветом сервера, который его выполнил, а под ответом на
сценарий «поездка на матч» стоит проверка порядка из orchestration.py.

Сервер тонкий: сообщения для модели собирает агент, маршрутизирует реестр,
проверяет orchestration. Здесь только передача и отрисовка.
"""

import json
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent as agents
import orchestration
from providers import CATALOG
from registry import Registry

PORT = 8020

REGISTRY = Registry()
AGENT = agents.Agent(registry=REGISTRY)
LOCK = threading.Lock()

PAGE = '''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Оркестрация MCP</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#6b7280;
          --card:#f4f4f5; --accent:#2563eb; --border:#e4e4e7; --ok:#16a34a; --bad:#dc2626;
          --weather:#0284c7; --money:#16a34a; --notes:#9333ea; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa; --card:#27272a; --accent:#3b82f6;
            --border:#3f3f46; --ok:#22c55e; --bad:#f87171;
            --weather:#38bdf8; --money:#4ade80; --notes:#c084fc; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 -apple-system,
         BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
  .wrap { max-width:1300px; margin:0 auto; padding:20px 16px 40px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:18px; }
  .grid { display:grid; grid-template-columns:340px 1fr; gap:18px; }
  @media (max-width:860px) { .grid { grid-template-columns:1fr; } }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px;
          padding:14px; margin-bottom:12px; }
  .srv h3 { margin:0 0 6px; font-size:14px; display:flex; gap:8px; align-items:center; }
  .dot { width:10px; height:10px; border-radius:50%; display:inline-block; flex:none; }
  .tool { font:12.5px ui-monospace, Menlo, monospace; margin:4px 0; }
  .tool small { display:block; color:var(--muted); font:12px/1.4 system-ui, sans-serif; }
  .coll { font-size:12.5px; color:var(--muted); }
  .bar { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
  textarea { width:100%; min-height:92px; padding:10px 12px; font:inherit; border-radius:10px;
             border:1px solid var(--border); background:var(--card); color:var(--fg); resize:vertical; }
  select, button { font:inherit; font-size:14px; padding:8px 14px; border-radius:9px;
                   border:1px solid var(--border); background:var(--card); color:var(--fg); }
  button.main { background:var(--accent); color:#fff; border:0; }
  button:disabled { opacity:.5; }
  .turn { border-top:1px solid var(--border); padding-top:12px; margin-top:14px; }
  .q { color:var(--accent); font-weight:600; margin-bottom:8px; white-space:pre-wrap; }
  .call { display:grid; grid-template-columns:54px 76px 1fr; gap:8px; align-items:start;
          font:12.5px/1.45 ui-monospace, Menlo, monospace; padding:5px 8px;
          border-left:3px solid var(--c); background:var(--card); border-radius:6px; margin:4px 0; }
  .call .srvname { color:var(--c); font-weight:600; }
  .call details summary { cursor:pointer; overflow-wrap:anywhere; }
  .call pre { white-space:pre-wrap; margin:6px 0 0; color:var(--muted); }
  .err { color:var(--bad); }
  .answer { white-space:pre-wrap; margin:10px 0; }
  .check { font-size:13px; padding:10px 12px; border-radius:10px; border:1px solid var(--border); }
  .check .pass { color:var(--ok); font-weight:600; } .check .fail { color:var(--bad); font-weight:600; }
  .meta { color:var(--muted); font-size:12.5px; }
  .steps { display:flex; flex-wrap:wrap; gap:6px; margin:6px 0; }
  .step { font:12px ui-monospace, Menlo, monospace; padding:2px 7px; border-radius:6px;
          border:1px solid var(--border); }
  .step.ok { border-color:var(--ok); } .step.bad { border-color:var(--bad); color:var(--bad); }
</style>
</head>
<body>
<div class="wrap">
  <h1>Оркестрация MCP: три сервера, один агент</h1>
  <div class="sub">Агент видит общий каталог инструментов, реестр отправляет каждый вызов
    на свой сервер. Цвет строки в ленте показывает, какой сервер выполнил вызов.</div>
  <div class="grid">
    <div id="servers"></div>
    <div>
      <div class="bar">
        <select id="model"></select>
        <button id="scenario">Сценарий «поездка на матч»</button>
        <button id="reset">Новый диалог</button>
      </div>
      <textarea id="text" placeholder="Например: какая погода будет в Казани послезавтра?"></textarea>
      <div class="bar" style="margin-top:8px"><button class="main" id="send">Отправить</button>
        <span class="meta" id="status"></span></div>
      <div id="log"></div>
    </div>
  </div>
</div>
<script>
const MODELS = __MODELS__;
const TASK = __TASK__;
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const color = s => `var(--${s || "bad"})`;

for (const m of MODELS) $("model").add(new Option(m.label, m.id));

async function api(path, body) {
  const r = await fetch(path, body ? {method:"POST", headers:{"Content-Type":"application/json"},
                                      body:JSON.stringify(body)} : {});
  return r.json();
}

async function loadServers() {
  const info = await api("/api/info");
  $("model").value = info.model;
  $("servers").innerHTML = info.servers.map(s => `
    <div class="card srv"><h3><span class="dot" style="background:${color(s.name)}"></span>
      ${esc(s.name)} <span class="meta">${esc(s.server.name || s.error)}</span></h3>
      ${s.tools.map(t => `<div class="tool">${esc(t.name)}(${esc(t.required.join(", "))})
        <small>${esc(t.description)}</small></div>`).join("")}
    </div>`).join("") +
    `<div class="card coll">Инструментов: ${info.tools}. Коллизии имён: ` +
    (Object.entries(info.collisions).map(([k, v]) => `<b>${esc(k)}</b> на ${esc(v.join(" и "))}`)
      .join("; ") || "нет") + ` — поэтому модель видит полные имена «сервер__инструмент».</div>`;
}

function renderTurn(q, r) {
  const calls = r.calls.map(c => `
    <div class="call" style="--c:${color(c.server)}">
      <span class="meta">круг ${c.round}</span>
      <span class="srvname">${esc(c.server || "?")}</span>
      <details><summary class="${c.ok ? "" : "err"}">${esc(c.name)} ${esc(JSON.stringify(c.arguments))}</summary>
        <pre>${esc(c.result)}</pre></details>
    </div>`).join("");
  let check = "";
  if (r.check) {
    const k = r.check;
    check = `<div class="check">
      <span class="${k.passed ? "pass" : "fail"}">${k.passed ? "Сценарий пройден" : "Есть нарушения"}</span>
      · шагов ${k.steps_ok}/7 · вызовов ${k.calls} · ошибок ${k.errors} · кругов ${k.rounds}
      · серверы: ${esc(k.servers.join(", "))}
      <div class="steps">${k.steps.map(s => `<span class="step ${s.ok && s.order_ok ? "ok" : "bad"}">${esc(s.tool)}</span>`).join("")}</div>
      ${Object.entries(k.facts).map(([f, v]) => `${v ? "✓" : v === false ? "✗" : "—"} ${esc(f)}`).join(" · ")}
      ${k.violations.map(v => `<div class="err">! ${esc(v)}</div>`).join("")}
    </div>`;
  }
  const t = r.turn;
  const el = document.createElement("div");
  el.className = "turn";
  el.innerHTML = `<div class="q">${esc(q)}</div>${calls}
    <div class="answer">${esc(r.text)}</div>${check}
    <div class="meta">${t.tool_calls} вызовов за ${t.tool_rounds} кругов ·
      ${t.prompt_tokens + t.completion_tokens} токенов · $${t.cost} · ${r.seconds} с</div>`;
  $("log").prepend(el);
}

async function send() {
  const text = $("text").value.trim();
  if (!text) return;
  $("send").disabled = true;
  $("status").textContent = "агент работает…";
  try {
    const r = await api("/api/ask", {text, model: $("model").value, scenario: text === TASK});
    if (r.error) { $("status").textContent = "ошибка: " + r.error; return; }
    renderTurn(text, r);
    $("status").textContent = "";
    $("text").value = "";
  } catch (e) {
    $("status").textContent = "ошибка: " + e;
  } finally {
    $("send").disabled = false;
  }
}

$("send").onclick = send;
$("text").addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); });
$("scenario").onclick = () => { $("text").value = TASK; $("text").focus(); };
$("reset").onclick = async () => { await api("/api/reset", {}); $("log").innerHTML = ""; };
loadServers();
</script>
</body>
</html>
'''


def build_page():
    models = [{"id": m["id"], "label": m["label"]} for m in CATALOG if m["price"]]
    return (PAGE.replace("__MODELS__", json.dumps(models, ensure_ascii=False))
                .replace("__TASK__", json.dumps(orchestration.TASK, ensure_ascii=False)))


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
            elif self.path == "/api/info":
                self._json({**REGISTRY.info(), "model": AGENT.settings["model"]})
            else:
                self._json({"error": "не найдено"}, 404)
        except Exception as error:
            traceback.print_exc()
            self._json({"error": str(error)}, 500)

    def do_POST(self):
        # Весь разбор под try: битый payload должен вернуть JSON, а не оборвать соединение.
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("ожидался JSON-объект")

            if self.path == "/api/ask":
                text = str(payload.get("text") or "").strip()
                if not text:
                    raise ValueError("пустой запрос")
                with LOCK:
                    if payload.get("model"):
                        AGENT.settings["model"] = payload["model"]
                    started = time.monotonic()
                    reply = AGENT.ask(text)
                    reply["seconds"] = round(time.monotonic() - started, 1)
                reply["check"] = (orchestration.check(reply["calls"])
                                  if payload.get("scenario") else None)
                self._json(reply)
            elif self.path == "/api/reset":
                with LOCK:
                    AGENT.reset()
                self._json({"ok": True})
            else:
                self._json({"error": "не найдено"}, 404)
        except Exception as error:
            traceback.print_exc()
            self._json({"error": str(error)}, 400)

    def log_message(self, *args):
        pass


def main():
    REGISTRY.connect()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Оркестрация MCP: {url}  (серверов: {len(REGISTRY.clients)}, "
          f"инструментов: {len(REGISTRY.catalog)})")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        REGISTRY.close()


if __name__ == "__main__":
    main()
