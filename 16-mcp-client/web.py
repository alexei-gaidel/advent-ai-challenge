"""Страница-обозреватель MCP: подключается к серверам и показывает их инструменты.

Запуск:
    python3 web.py     → откроется http://localhost:8016
"""

import json
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mcp_client import LOCAL_SERVER, PUBLIC_SERVERS, MCPClient, MCPError

PORT = 8016

PAGE = '''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Обозреватель MCP</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#6b7280;
          --card:#f4f4f5; --accent:#2563eb; --border:#e4e4e7; --ok:#16a34a; --bad:#ef4444; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa;
            --card:#27272a; --accent:#3b82f6; --border:#3f3f46; --ok:#22c55e; --bad:#f87171; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 -apple-system,
         BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
  .wrap { max-width:1100px; margin:0 auto; padding:22px 16px 60px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:18px; }
  .bar { display:flex; gap:8px; margin-bottom:16px; }
  .bar input { flex:1; padding:10px 13px; font:inherit; border-radius:10px;
               border:1px solid var(--border); background:var(--card); color:var(--fg); }
  button { padding:9px 17px; font-size:14px; border:0; border-radius:9px;
           background:var(--accent); color:#fff; cursor:pointer; white-space:nowrap; }
  button.ghost { background:transparent; color:var(--muted); border:1px solid var(--border); }
  button:disabled { opacity:.5; cursor:default; }
  .servers { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:18px; }
  .card { border:1px solid var(--border); border-radius:12px; background:var(--card);
          padding:14px 16px; margin-bottom:14px; }
  .card h2 { font-size:16px; margin:0 0 3px; }
  .meta { color:var(--muted); font-size:12.5px; }
  .meta b { color:var(--fg); font-weight:600; }
  .tool { border-top:1px solid var(--border); padding:10px 0 4px; }
  .tool .name { font-weight:600; font-size:14px; }
  .tool .desc { color:var(--muted); font-size:13px; margin:3px 0 6px; }
  .args { font-size:12.5px; }
  .args div { padding:1px 0; }
  .args .req { color:var(--ok); }
  .args .opt { color:var(--muted); }
  .err { color:var(--bad); font-size:13px; }
  .spin { color:var(--muted); font-size:13px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Обозреватель MCP</h1>
  <div class="sub">Клиент на стандартной библиотеке: initialize → notifications/initialized → tools/list</div>

  <div class="bar">
    <input id="url" placeholder="URL любого MCP-сервера, например https://mcp.deepwiki.com/mcp">
    <button id="go">Подключиться</button>
  </div>
  <div class="servers" id="servers"></div>
  <div id="out"></div>
</div>

<script>
  const KNOWN = __SERVERS__;
  const out = document.getElementById("out");
  const servers = document.getElementById("servers");

  KNOWN.forEach((s) => {
    const button = document.createElement("button");
    button.className = "ghost";
    button.textContent = s.name + (s.transport === "stdio" ? " · stdio" : "");
    button.onclick = () => connect(s.transport === "stdio" ? { local: true } : { url: s.url },
                                   s.name);
    servers.append(button);
  });

  const esc = (text) => {
    const d = document.createElement("div");
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  };

  function argsHtml(schema) {
    const props = (schema && schema.properties) || {};
    const required = new Set((schema && schema.required) || []);
    const names = Object.keys(props);
    if (!names.length) return '<div class="args opt">аргументов нет</div>';
    return '<div class="args">' + names.map((name) => {
      const field = props[name];
      let kind = field.type;
      if (!kind && (field.anyOf || field.oneOf)) {
        kind = (field.anyOf || field.oneOf).map((x) => x.type || "?").join(" | ");
      }
      const enums = field.enum ? " · варианты: " + field.enum.join(", ") : "";
      return '<div><span class="' + (required.has(name) ? "req" : "opt") + '">' +
        (required.has(name) ? "обязательный" : "необязательный") + "</span> " +
        esc(name) + ": " + esc(kind || "не указан") + esc(enums) +
        (field.description ? '<div class="opt">' + esc(field.description) + "</div>" : "") +
        "</div>";
    }).join("") + "</div>";
  }

  async function connect(body, title) {
    out.innerHTML = '<div class="spin">подключаюсь…</div>';
    let data;
    try {
      const res = await fetch("/api/tools", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      data = await res.json();
    } catch (err) {
      out.innerHTML = '<div class="err">ошибка запроса: ' + esc(err) + "</div>";
      return;
    }

    if (data.error) {
      out.innerHTML = '<div class="card"><h2>' + esc(title || body.url) +
        '</h2><div class="err">не получилось: ' + esc(data.text) + "</div></div>";
      return;
    }

    out.innerHTML = '<div class="card"><h2>' + esc(data.server.name || title) + " " +
      esc(data.server.version || "") + '</h2><div class="meta">протокол <b>' +
      esc(data.protocol) + "</b> · транспорт <b>" + esc(data.transport) +
      "</b> · соединение за <b>" + esc(data.seconds) + " c</b> · инструментов <b>" +
      data.tools.length + "</b></div>" +
      data.tools.map((tool) =>
        '<div class="tool"><div class="name">' + esc(tool.name) + "</div>" +
        '<div class="desc">' + esc((tool.description || "").slice(0, 300)) + "</div>" +
        argsHtml(tool.inputSchema) + "</div>").join("") + "</div>";
  }

  document.getElementById("go").onclick = () => {
    const url = document.getElementById("url").value.trim();
    if (url) connect({ url }, url);
  };
  document.getElementById("url").onkeydown = (e) => {
    if (e.key === "Enter") document.getElementById("go").click();
  };

  connect({ local: true }, "Свой сервер");
</script>
</body>
</html>
'''


def build_page():
    known = [{"name": name, "url": url, "transport": "http"} for name, url in PUBLIC_SERVERS]
    known.append({"name": "Свой сервер", "url": "", "transport": "stdio"})
    return PAGE.replace("__SERVERS__", json.dumps(known, ensure_ascii=False)).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload):
        self._reply(200, "application/json; charset=utf-8",
                    json.dumps(payload, ensure_ascii=False).encode())

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._reply(200, "text/html; charset=utf-8", build_page())
        else:
            self._reply(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        # Разбор запроса тоже под try: битый payload не должен ронять поток обработчика.
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path != "/api/tools":
                self._reply(404, "text/plain; charset=utf-8", b"not found")
                return

            if payload.get("local"):
                client = MCPClient.stdio("Свой сервер", LOCAL_SERVER)
            else:
                client = MCPClient.http("Сервер", payload["url"])

            print(f"→ подключение: {payload.get('url') or 'stdio'}")
            try:
                client.connect()
                tools = client.list_tools()
                self._json({
                    "server": client.server_info,
                    "protocol": client.protocol_version,
                    "transport": client.transport.kind,
                    "seconds": client.connect_seconds,
                    "tools": tools,
                })
            finally:
                client.close()

        except MCPError as error:
            self._json({"error": True, "text": str(error)})
        except Exception as error:
            traceback.print_exc()
            self._json({"error": True, "text": f"Ошибка: {error}"})

    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Известных серверов: {len(PUBLIC_SERVERS) + 1}\nОткрой {url}  (Ctrl+C — остановить)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")


if __name__ == "__main__":
    main()
