"""Простой веб-чат с локальной моделью.

    python3 web.py     → откроется http://localhost:8026

Слева выбор модели (всё, что скачано в Ollama) и что сейчас в памяти, справа — диалог;
под каждым ответом токены, время и скорость генерации. История живёт в странице.
Сервер тонкий: все обращения к модели — в local_llm.py.
"""

import json
import sys
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import local_llm

PORT = 8026

PAGE = '''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Локальная LLM</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#6b7280; --card:#f4f4f5;
          --accent:#2563eb; --border:#e4e4e7; --ok:#16a34a; --bad:#dc2626; --me:#dbeafe; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa; --card:#27272a; --accent:#3b82f6;
            --border:#3f3f46; --ok:#22c55e; --bad:#f87171; --me:#1e3a5f; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  .app { display:grid; grid-template-columns:250px 1fr; height:100vh; }
  @media (max-width: 760px) { .app { grid-template-columns:1fr; height:auto; } .main { height:80vh; } }
  aside { border-right:1px solid var(--border); padding:16px; overflow:auto; }
  h1 { font-size:17px; margin:0 0 4px; }
  h2 { font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); margin:18px 0 6px; }
  .muted { color:var(--muted); font-size:13px; }
  select, textarea { width:100%; padding:8px 10px; font:inherit; border:1px solid var(--border);
          border-radius:8px; background:var(--bg); color:var(--fg); }
  button { padding:9px 16px; font:inherit; border:0; border-radius:8px; background:var(--accent); color:#fff; cursor:pointer; }
  button:disabled { opacity:.5; cursor:wait; }
  button.ghost { background:var(--card); color:var(--fg); border:1px solid var(--border); width:100%; margin-top:8px; }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--bad); margin-right:6px; }
  .dot.on { background:var(--ok); }
  .main { display:flex; flex-direction:column; height:100vh; min-width:0; }
  .feed { flex:1; overflow:auto; padding:18px; }
  .msg { max-width:780px; margin:0 auto 14px; }
  .bubble { padding:10px 14px; border-radius:12px; white-space:pre-wrap; word-wrap:break-word; }
  .user .bubble { background:var(--me); margin-left:15%; }
  .bot .bubble { background:var(--card); margin-right:8%; }
  .meta { font-size:12px; color:var(--muted); margin-top:4px; }
  .err { color:var(--bad); }
  .examples button { display:block; width:100%; text-align:left; margin-bottom:6px; background:var(--card);
          color:var(--fg); border:1px solid var(--border); font-size:13px; padding:7px 10px; }
  form { display:flex; gap:8px; padding:12px 18px; border-top:1px solid var(--border); }
  form textarea { resize:none; height:52px; }
</style>
</head>
<body>
<div class="app">
  <aside>
    <h1>Локальная LLM</h1>
    <div class="muted"><span class="dot" id="dot"></span><span id="status">проверяю Ollama…</span></div>
    <h2>Модель</h2>
    <select id="model"></select>
    <div class="muted" id="ps" style="margin-top:6px"></div>
    <h2>Примеры</h2>
    <div class="examples">
      <button data-q="Столица Франции? Ответь одним словом.">1 · простой факт</button>
      <button data-q="В корзине 23 яблока. Съели 7, потом докупили втрое больше, чем съели. Сколько яблок стало? Реши по шагам.">2 · задача в несколько шагов</button>
      <button data-q="Напиши функцию на Python is_palindrome(s), которая игнорирует регистр, пробелы и знаки препинания. Только код.">3 · код</button>
    </div>
    <button class="ghost" id="clear">Новый диалог</button>
    <p class="muted">Запросы идут на localhost:11434 — без интернета и ключей.</p>
  </aside>
  <div class="main">
    <div class="feed" id="feed"></div>
    <form id="form">
      <textarea id="q" placeholder="Спросите локальную модель… (Enter — отправить, Shift+Enter — новая строка)"></textarea>
      <button id="send">Отправить</button>
    </form>
  </div>
</div>
<script>
const $ = id => document.getElementById(id);
let history = [];

function esc(s) { return s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function add(role, text, meta, isErr) {
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.innerHTML = '<div class="bubble' + (isErr ? ' err' : '') + '">' + esc(text) + '</div>' +
                  (meta ? '<div class="meta">' + esc(meta) + '</div>' : '');
  $('feed').appendChild(div);
  $('feed').scrollTop = $('feed').scrollHeight;
  return div;
}

async function status() {
  try {
    const r = await (await fetch('/api/status')).json();
    if (r.error) throw new Error(r.error);
    $('dot').className = 'dot on';
    $('status').textContent = 'Ollama ' + r.version;
    const cur = $('model').value || r.default;
    $('model').innerHTML = r.models.map(m =>
      '<option value="' + m.name + '">' + m.name + ' · ' + m.params + ' · ' + m.size_gb + ' ГБ</option>').join('');
    if (r.models.some(m => m.name === cur)) $('model').value = cur;
    $('ps').textContent = r.loaded.length
      ? 'в памяти: ' + r.loaded.map(m => m.name + ' (' + m.vram_gb + ' ГБ)').join(', ')
      : 'в памяти ничего — первый запрос загрузит модель';
  } catch (e) {
    $('dot').className = 'dot';
    $('status').textContent = e.message;
  }
}

async function send(text) {
  text = text.trim();
  if (!text) return;
  $('q').value = '';
  $('send').disabled = true;
  add('user', text);
  history.push({role: 'user', content: text});
  const wait = add('bot', 'думаю…');
  try {
    const r = await (await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({model: $('model').value, messages: history})})).json();
    wait.remove();
    if (r.error) { history.pop(); add('bot', r.error, null, true); }
    else {
      history.push({role: 'assistant', content: r.text});
      add('bot', r.text, r.prompt_tokens + ' → ' + r.output_tokens + ' токенов · ' + r.total_s +
          ' с (загрузка ' + r.load_s + ' с) · ' + r.tok_per_s + ' ток/с');
    }
  } catch (e) { wait.remove(); history.pop(); add('bot', String(e), null, true); }
  $('send').disabled = false;
  status();
}

$('form').onsubmit = e => { e.preventDefault(); send($('q').value); };
$('q').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send($('q').value); } };
$('clear').onclick = () => { history = []; $('feed').innerHTML = ''; };
document.querySelectorAll('.examples button').forEach(b => b.onclick = () => send(b.dataset.q));
status();
</script>
</body>
</html>
'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body, kind="application/json"):
        data = body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            if self.path == "/":
                return self.reply(200, PAGE, "text/html")
            if self.path == "/api/status":
                return self.reply(200, {"version": local_llm.version(), "default": local_llm.MODEL,
                                        "models": local_llm.models(), "loaded": local_llm.loaded()})
            self.reply(404, {"error": "нет такого адреса"})
        except Exception as error:
            traceback.print_exc()
            self.reply(200, {"error": str(error)})

    def do_POST(self):
        try:
            if self.path != "/api/chat":
                return self.reply(404, {"error": "нет такого адреса"})
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            messages = body.get("messages")
            if not isinstance(messages, list) or not messages:
                return self.reply(400, {"error": "нужен непустой список messages"})
            self.reply(200, local_llm.chat(messages, body.get("model") or local_llm.MODEL))
        except Exception as error:
            traceback.print_exc()
            self.reply(400 if isinstance(error, ValueError) else 500, {"error": str(error)})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Локальная LLM: {url}  (Ctrl+C — выход)")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
