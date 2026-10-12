"""Приватный AI-сервис: локальная LLM (Ollama) за HTTP-шлюзом с ключами и лимитами.

Запуск:
    python3 server.py --init-keys   # один раз: два ключа в корневой .env
    python3 server.py               # слушает 0.0.0.0:8030, печатает адрес в локальной сети

API (OpenAI-совместимое — подходит любой OpenAI-клиент с base_url=http://<ip>:8030/v1):
    POST /v1/chat/completions   messages, max_tokens, temperature, stream
    GET  /v1/models
    GET  /stats                 счётчики, очередь, латентность (с ключом)
    GET  /health                без ключа
    GET  /                      страница чата

Ollama остаётся на 127.0.0.1:11434 — из сети до модели можно добраться только через шлюз.
Сервер тонкий: проверки и очередь — limits.py, поход в модель — local_llm.py.
"""

import json
import pathlib
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import limits
import local_llm

PORT = 8030
HOST = "0.0.0.0"
KEYS = {}
BUCKET = limits.TokenBucket()
GATE = limits.Gate()
STATS = limits.Stats()
LOG = pathlib.Path(__file__).with_name("data") / "requests.log"
LOG_LOCK = threading.Lock()


def lan_addresses():
    """IPv4 адреса локальной сети. UDP-трюк тут не годится: маршрут по умолчанию идёт в VPN (utun)."""
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found, iface = [], ""
    for line in out.splitlines():
        if line and not line[0].isspace():
            iface = line.split(":")[0]
        match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line)
        if match and not iface.startswith(("lo", "utun", "bridge")):
            ip = match.group(1)
            if ip.startswith(("192.168.", "10.")) or re.match(r"172\.(1[6-9]|2\d|3[01])\.", ip):
                found.append(ip)
    return found


def write_log(**fields):
    fields = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **fields}
    with LOG_LOCK:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fields, ensure_ascii=False) + "\n")


def completion(result, extra):
    """Ответ Ollama → формат OpenAI chat.completion (+ наш блок gateway)."""
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion",
        "created": int(time.time()), "model": result["model"],
        "choices": [{"index": 0, "message": {"role": "assistant", "content": result["text"]},
                     "finish_reason": "length" if result["done_reason"] == "length" else "stop"}],
        "usage": {"prompt_tokens": result["prompt_tokens"], "completion_tokens": result["output_tokens"],
                  "total_tokens": result["prompt_tokens"] + result["output_tokens"]},
        "gateway": extra,
    }


PAGE = r'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Приватный AI</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#6b7280; --card:#f4f4f5;
          --accent:#2563eb; --border:#e4e4e7; --ok:#16a34a; --bad:#dc2626; --warn:#d97706; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa; --card:#27272a; --accent:#3b82f6;
            --border:#3f3f46; --ok:#22c55e; --bad:#f87171; --warn:#fbbf24; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  main { max-width:860px; margin:0 auto; padding:16px 16px 40px; display:flex; flex-direction:column;
         min-height:100vh; }
  h1 { font-size:20px; margin:0 0 2px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:10px; }
  .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  input, textarea { padding:9px 10px; font:inherit; border:1px solid var(--border); border-radius:8px;
                    background:var(--bg); color:var(--fg); }
  #key { flex:1 1 220px; font-family:ui-monospace, monospace; font-size:13px; }
  textarea { flex:1 1 300px; resize:vertical; min-height:44px; }
  button { padding:10px 16px; font:inherit; border:0; border-radius:8px; background:var(--accent);
           color:#fff; cursor:pointer; }
  button.ghost { background:var(--card); color:var(--fg); border:1px solid var(--border); }
  button:disabled { opacity:.5; cursor:wait; }
  #stats { font-size:12px; color:var(--muted); background:var(--card); border-radius:8px;
           padding:6px 10px; margin:10px 0; overflow-wrap:anywhere; }
  #log { flex:1; display:flex; flex-direction:column; gap:10px; margin:6px 0 12px; }
  .msg { padding:9px 12px; border-radius:10px; max-width:88%; white-space:pre-wrap; overflow-wrap:anywhere; }
  .user { align-self:flex-end; background:var(--accent); color:#fff; }
  .assistant { align-self:flex-start; background:var(--card); }
  .meta { font-size:11.5px; color:var(--muted); margin-top:4px; white-space:normal; }
  .err { align-self:stretch; border-left:3px solid var(--bad); background:var(--card); }
  .err b { color:var(--bad); }
  .ctx { font-size:12px; color:var(--muted); }
  .ctx.hot { color:var(--warn); }
</style>
</head>
<body>
<main>
  <h1>Приватный AI-сервис</h1>
  <div class="sub">__MODEL__ в Ollama на домашнем Mac. Окно __CTX__ токенов, ответ до __OUT__,
    в модели одновременно __INFLIGHT__, очередь __QUEUE__. Запросы не покидают локальную сеть.</div>
  <div class="row">
    <input id="key" type="password" placeholder="API-ключ (sk-local-…)" autocomplete="off">
    <button class="ghost" id="clear" type="button">Новый чат</button>
  </div>
  <div id="stats">статистика появится после ввода ключа</div>
  <div id="log"></div>
  <form id="f" class="row">
    <textarea id="q" placeholder="Сообщение (Enter — отправить, Shift+Enter — перенос)"></textarea>
    <button id="send">Отправить</button>
  </form>
  <div class="ctx" id="ctx"></div>
</main>
<script>
const CTX = __CTX__, OUT = 256;
const $ = id => document.getElementById(id);
let history = [];
try { $('key').value = localStorage.getItem('llmKey') || ''; } catch (e) {}
$('key').addEventListener('change', () => { try { localStorage.setItem('llmKey', $('key').value.trim()); } catch (e) {} stats(); });

function estimate(msgs) {   // та же формула, что limits.estimate_tokens
  const sys = msgs.some(m => m.role === 'system');
  const chars = msgs.reduce((n, m) => n + m.content.length, 0);
  return (sys ? 9 : 30) + 6 * msgs.length + Math.floor(chars / 3.3) + 1;
}
function showCtx() {
  const e = estimate(history);
  $('ctx').textContent = `контекст ≈${e} + ответ ${OUT} из ${CTX} токенов · сообщений ${history.length}`;
  $('ctx').className = 'ctx' + (e + OUT > CTX * 0.8 ? ' hot' : '');
}
function bubble(cls, text) {
  const d = document.createElement('div'); d.className = 'msg ' + cls; d.textContent = text;
  $('log').appendChild(d); d.scrollIntoView({block: 'end'}); return d;
}
function errorBubble(status, e) {
  const d = bubble('err', '');
  const human = {401: 'Неверный ключ', 413: 'Слишком длинно', 429: 'Слишком часто', 503: 'Сервер занят'}[status] || 'Ошибка';
  d.innerHTML = `<b>${status} · ${human}</b><br>`; d.append(e.message || JSON.stringify(e));
}
async function stats() {
  const key = $('key').value.trim(); if (!key) return;
  try {
    const r = await fetch('/stats', {headers: {Authorization: 'Bearer ' + key}});
    const s = await r.json();
    if (!r.ok) { $('stats').textContent = s.error.message; return; }
    const req = Object.entries(s.requests).map(([k, v]) => `${k}: ${v}`).join(' · ') || '—';
    $('stats').textContent = `в работе ${s.running}/${s.limits.inflight}, в очереди ${s.waiting}/${s.limits.queue} · ` +
      `ответы ${req} · p50 ${s.latency_p50 ?? '—'} с, p95 ${s.latency_p95 ?? '—'} с · ` +
      `оценка/реальные токены ${s.estimate_vs_real ?? '—'}`;
  } catch (e) {}
}
setInterval(stats, 3000); stats(); showCtx();

$('clear').onclick = () => { history = []; $('log').innerHTML = ''; showCtx(); };
$('q').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('f').requestSubmit(); } });
$('f').onsubmit = async ev => {
  ev.preventDefault();
  const text = $('q').value.trim(); if (!text) return;
  $('send').disabled = true; $('q').value = '';
  history.push({role: 'user', content: text}); bubble('user', text); showCtx();
  const out = bubble('assistant', '…');
  const started = performance.now();
  try {
    const r = await fetch('/v1/chat/completions', {method: 'POST',
      headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + $('key').value.trim()},
      body: JSON.stringify({messages: history, stream: true, max_tokens: OUT})});
    if (!r.ok) {
      out.remove(); history.pop();
      const e = await r.json().catch(() => ({error: {message: r.statusText}}));
      errorBubble(r.status, e.error); return;
    }
    const left = r.headers.get('X-RateLimit-Remaining');
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = '', answer = '', first = null, gw = null;
    out.textContent = '';
    for (;;) {
      const {value, done} = await reader.read(); if (done) break;
      buf += dec.decode(value, {stream: true});
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, i).replace(/^data: /, ''); buf = buf.slice(i + 2);
        if (line === '[DONE]') continue;
        const chunk = JSON.parse(line);
        if (chunk.error) throw new Error(chunk.error.message);
        const piece = chunk.choices?.[0]?.delta?.content;
        if (piece) { if (first === null) first = performance.now(); answer += piece; out.textContent = answer; }
        if (chunk.gateway) gw = chunk;
      }
    }
    history.push({role: 'assistant', content: answer});
    const m = document.createElement('div'); m.className = 'meta';
    const g = gw ? gw.gateway : {};
    m.textContent = `${gw ? gw.usage.prompt_tokens : '?'}→${gw ? gw.usage.completion_tokens : '?'} ток · ` +
      `первый токен ${first ? ((first - started) / 1000).toFixed(1) : '?'} с · всего ${((performance.now() - started) / 1000).toFixed(1)} с · ` +
      `${g.tok_per_s ?? '?'} ток/с · очередь ${g.queue_wait_s ?? 0} с · оценка ${g.estimate ?? '?'} · осталось запросов ${left}`;
    out.appendChild(m);
  } catch (e) { out.textContent += '\n[обрыв: ' + e.message + ']'; }
  finally { $('send').disabled = false; showCtx(); stats(); $('q').focus(); }
};
</script>
</body>
</html>
'''


class Handler(BaseHTTPRequestHandler):
    server_version = "PrivateLLM/1.0"

    def _send(self, status, body, ctype="application/json; charset=utf-8", headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, str(v))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, error, who="-"):
        headers = {"Retry-After": error.retry_after} if error.retry_after else {}
        self._send(error.status, {"error": {"type": error.kind, "message": str(error)}}, headers=headers)
        STATS.record(error.status)
        write_log(key=who, path=self.path, status=error.status, error=error.kind)

    def _auth(self):
        return limits.authenticate(self.headers.get("Authorization"), KEYS)

    def do_GET(self):
        try:
            if self.path == "/":
                page = (PAGE.replace("__MODEL__", local_llm.MODEL).replace("__CTX__", str(limits.MAX_CONTEXT))
                        .replace("__OUT__", str(limits.MAX_OUTPUT)).replace("__INFLIGHT__", str(GATE.inflight_max))
                        .replace("__QUEUE__", str(GATE.queue_max)))
                self._send(200, page.encode(), "text/html; charset=utf-8")
            elif self.path == "/health":
                self._send(200, {"status": "ok", "model": local_llm.MODEL, "ollama": local_llm.version()})
            elif self.path == "/stats":
                self._auth()
                self._send(200, STATS.snapshot(GATE))
            elif self.path == "/v1/models":
                self._auth()
                self._send(200, {"object": "list", "data": [{"id": local_llm.MODEL, "object": "model",
                                                             "owned_by": "local"}]})
            else:
                self._send(404, {"error": {"type": "not_found", "message": "нет такого адреса"}})
        except limits.LimitError as error:
            self._error(error)
        except Exception as error:
            traceback.print_exc()
            self._send(500, {"error": {"type": "server_error", "message": str(error)}})

    def do_POST(self):
        # Весь разбор под try: битый payload должен вернуть JSON, а не оборвать соединение.
        who = "-"
        try:
            if self.path != "/v1/chat/completions":
                raise limits.LimitError(404, "not_found", "нет такого адреса")
            info = self._auth()
            who = info["name"]
            length = int(self.headers.get("Content-Length") or 0)
            if length > limits.MAX_BODY:
                self.rfile.read(min(length, 1024 * 1024))   # дочитать, иначе клиент получит обрыв, а не 413
                raise limits.LimitError(413, "body_too_large", f"тело {length} байт, максимум {limits.MAX_BODY}")
            left = BUCKET.take(who, info["rpm"])
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError as error:
                raise limits.LimitError(400, "invalid_json", f"тело не JSON: {error}") from error
            if not isinstance(body, dict):
                raise limits.LimitError(400, "invalid_json", "ожидался JSON-объект")
            req = limits.check_request(body)
        except limits.LimitError as error:
            self._error(error, who)
            return
        except Exception as error:
            traceback.print_exc()
            self._send(400, {"error": {"type": "invalid_request", "message": str(error)}})
            return

        try:
            wait = GATE.enter()
        except limits.LimitError as error:
            self._error(error, who)
            return
        started = time.monotonic()
        extra = {"key": who, "queue_wait_s": wait, "estimate": req["estimate"],
                 "max_tokens": req["max_tokens"], "max_tokens_clipped": req["clipped"],
                 "num_ctx": limits.MAX_CONTEXT}
        try:
            if req["stream"]:
                result = self._stream(req, extra, left)
            else:
                result = local_llm.chat(req["messages"], options=req["options"])
                extra["tok_per_s"] = result["tok_per_s"]
                self._send(200, completion(result, extra), headers={"X-RateLimit-Remaining": left})
            seconds = round(time.monotonic() - started + wait, 2)
            STATS.record(200, seconds, wait, result, req["estimate"])
            write_log(key=who, status=200, seconds=seconds, queue=wait, estimate=req["estimate"],
                      prompt=result["prompt_tokens"], output=result["output_tokens"], stream=req["stream"])
        except (BrokenPipeError, ConnectionResetError):
            STATS.record(499)
            write_log(key=who, status=499, error="client_closed")
        except Exception as error:
            traceback.print_exc()
            STATS.record(502)
            write_log(key=who, status=502, error=str(error)[:200])
            if not req["stream"]:
                self._send(502, {"error": {"type": "upstream_error", "message": str(error)}})
        finally:
            GATE.leave()

    def _stream(self, req, extra, left):
        """SSE в формате OpenAI: data: {choices:[{delta:{content}}]} … data: [DONE]."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-RateLimit-Remaining", str(left))
        self.end_headers()
        base = {"id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion.chunk",
                "model": local_llm.MODEL}

        def emit(obj):
            self.wfile.write(b"data: " + json.dumps(obj, ensure_ascii=False).encode() + b"\n\n")
            self.wfile.flush()

        parts = local_llm.chat_stream(req["messages"], options=req["options"])
        try:
            for kind, value in parts:
                if kind == "delta":
                    emit({**base, "choices": [{"index": 0, "delta": {"content": value}}]})
                else:
                    extra["tok_per_s"] = value["tok_per_s"]
                    emit({**base, "choices": [{"index": 0, "delta": {},
                                               "finish_reason": "length" if value["done_reason"] == "length" else "stop"}],
                          "usage": {"prompt_tokens": value["prompt_tokens"],
                                    "completion_tokens": value["output_tokens"]},
                          "gateway": extra})
                    self.wfile.write(b"data: [DONE]\n\n")
                    return {**value, "text": ""}
        except (BrokenPipeError, ConnectionResetError):
            raise
        except Exception as error:
            emit({"error": {"type": "upstream_error", "message": str(error)}})
            raise
        finally:
            parts.close()       # клиент ушёл — закрываем поток к Ollama, генерация останавливается
        raise RuntimeError("Ollama оборвала поток без done")

    def log_message(self, *args):
        pass


def main():
    if "--init-keys" in sys.argv:
        path = limits.init_keys()
        print(f"ключи дописаны в {path}" if path else "LLM_SERVICE_KEYS уже есть в .env")
        return 0
    KEYS.update(limits.load_keys())
    if not KEYS:
        print("Нет ключей. Один раз: python3 server.py --init-keys")
        return 1
    local_llm.check()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f"Приватный AI-сервис · {local_llm.MODEL} · Ollama {local_llm.version()} (только 127.0.0.1)")
    print(f"  этот компьютер:  http://localhost:{PORT}")
    for ip in lan_addresses():
        print(f"  локальная сеть:  http://{ip}:{PORT}")
    print(f"  ключи: " + ", ".join(f"{v['name']} {limits.mask(k)} {v['rpm']}/мин" for k, v in KEYS.items()))
    print(f"  лимиты: окно {limits.MAX_CONTEXT}, ответ ≤{limits.MAX_OUTPUT}, тело ≤{limits.MAX_BODY // 1024} КБ, "
          f"в модели {GATE.inflight_max}, очередь {GATE.queue_max}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
