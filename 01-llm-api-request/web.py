"""Простой веб-интерфейс к тому же LLM-клиенту.

Запуск:
    python3 web.py     → откроется http://localhost:8000
"""

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from main import MODEL, ask

PORT = 8000

PAGE = '''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LLM чат</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#6b7280;
          --card:#f4f4f5; --accent:#2563eb; --border:#e4e4e7; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa;
            --card:#27272a; --accent:#3b82f6; --border:#3f3f46; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:16px/1.55 -apple-system,
         BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
  .wrap { max-width:720px; margin:0 auto; padding:24px 16px 120px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:24px; }
  .msg { padding:12px 16px; border-radius:12px; margin-bottom:12px; white-space:pre-wrap;
         word-wrap:break-word; }
  .user { background:var(--accent); color:#fff; margin-left:15%; }
  .bot { background:var(--card); margin-right:15%; }
  .role { font-size:11px; text-transform:uppercase; letter-spacing:.06em;
          opacity:.65; margin-bottom:4px; }
  form { position:fixed; bottom:0; left:0; right:0; background:var(--bg);
         border-top:1px solid var(--border); padding:12px 16px; }
  .row { max-width:720px; margin:0 auto; display:flex; gap:8px; }
  input { flex:1; padding:12px 14px; font-size:16px; border-radius:10px;
          border:1px solid var(--border); background:var(--card); color:var(--fg); }
  input:focus { outline:2px solid var(--accent); outline-offset:-1px; }
  button { padding:12px 20px; font-size:16px; border:0; border-radius:10px;
           background:var(--accent); color:#fff; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Чат с LLM</h1>
  <div class="sub">DeepSeek API &middot; ответ приходит из модели, а не из шаблона</div>
  <div id="log"></div>
</div>
<form id="form">
  <div class="row">
    <input id="input" placeholder="Спроси что-нибудь…" autocomplete="off" autofocus>
    <button id="send">Отправить</button>
  </div>
</form>
<script>
  const history = [];
  const log = document.getElementById("log");
  const form = document.getElementById("form");
  const input = document.getElementById("input");
  const send = document.getElementById("send");

  function add(role, text) {
    const div = document.createElement("div");
    div.className = "msg " + (role === "user" ? "user" : "bot");
    const label = document.createElement("div");
    label.className = "role";
    label.textContent = role === "user" ? "вы" : "llm";
    const body = document.createElement("div");
    body.textContent = text;
    div.append(label, body);
    log.append(div);
    window.scrollTo(0, document.body.scrollHeight);
    return body;
  }

  form.onsubmit = async (e) => {
    e.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    add("user", question);
    history.push({ role: "user", content: question });
    input.value = "";
    send.disabled = true;
    const pending = add("bot", "…думаю");
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history }),
      });
      const data = await res.json();
      pending.textContent = data.answer;
      history.push({ role: "assistant", content: data.answer });
    } catch (err) {
      pending.textContent = "Ошибка запроса: " + err;
    }
    send.disabled = false;
    input.focus();
  };
</script>
</body>
</html>
'''.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._reply(200, "text/html; charset=utf-8", PAGE)
        else:
            self._reply(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        if self.path != "/api/chat":
            self._reply(404, "text/plain; charset=utf-8", b"not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        messages = json.loads(self.rfile.read(length))["messages"]

        try:
            answer = ask(messages)
        except SystemExit as error:          # ask() завершает CLI, в вебе — показываем текст
            answer = f"Ошибка: {error}"
        except Exception as error:
            answer = f"Ошибка: {error}"

        self._reply(200, "application/json; charset=utf-8",
                    json.dumps({"answer": answer}).encode())

    def log_message(self, *args):
        pass


def main():
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Модель: {MODEL}\nОткрой {url}  (Ctrl+C — остановить)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")


if __name__ == "__main__":
    main()
