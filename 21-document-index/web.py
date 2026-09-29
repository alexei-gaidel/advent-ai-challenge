"""Поиск по индексу документов: один вопрос — две стратегии chunking рядом.

Запуск:
    python3 web.py     → откроется http://localhost:8021

Слева чанки индекса «fixed», справа «structure»: score, метаданные (source, section,
chunk_id, длина) и сам текст. Сверху — статистика обоих индексов из index.db.

Сервер тонкий: вектор запроса считает embeddings, поиск — index_store.
"""

import json
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chunking
import compare_chunking
import embeddings
import index_store

PORT = 8021
LOCK = threading.Lock()
INDEXES = {}


def indexes():
    """Индексы поднимаются из базы при первом запросе — сервер стартует и без них."""
    if not INDEXES:
        db = index_store.connect()
        for name in chunking.STRATEGIES:
            INDEXES[name] = index_store.Index(db, name)
    return INDEXES


def builds():
    try:
        rows = index_store.builds(index_store.connect())
    except Exception:
        rows = []
    return [{**row, "stats": json.loads(row["stats"])} for row in rows]


PAGE = '''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Индекс документов</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#6b7280;
          --card:#f4f4f5; --accent:#2563eb; --border:#e4e4e7; --ok:#16a34a; --bad:#dc2626;
          --fixed:#d97706; --structure:#0891b2; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa; --card:#27272a; --accent:#3b82f6;
            --border:#3f3f46; --ok:#22c55e; --bad:#f87171; --fixed:#fbbf24; --structure:#22d3ee; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 -apple-system,
         BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
  .wrap { max-width:1300px; margin:0 auto; padding:20px 16px 40px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:16px; }
  .bar { display:flex; gap:8px; margin-bottom:10px; }
  input { flex:1; min-width:0; padding:10px 12px; font:inherit; border-radius:10px;
          border:1px solid var(--border); background:var(--card); color:var(--fg); }
  button { font:inherit; font-size:14px; padding:8px 14px; border-radius:9px;
           border:1px solid var(--border); background:var(--card); color:var(--fg); cursor:pointer; }
  button.main { background:var(--accent); color:#fff; border:0; }
  button:disabled { opacity:.5; }
  .chips { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:16px; }
  .chips button { font-size:12.5px; padding:4px 10px; border-radius:20px; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:18px; }
  @media (max-width:860px) { .grid { grid-template-columns:1fr; } }
  .col h2 { font-size:15px; margin:0 0 8px; display:flex; gap:8px; align-items:center; }
  .dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
  .stat { font-size:12.5px; color:var(--muted); margin-bottom:10px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px;
          padding:12px 14px; margin-bottom:10px; }
  .meta { font:12px/1.5 ui-monospace, Menlo, monospace; color:var(--muted);
          overflow-wrap:anywhere; }
  .meta b { color:var(--fg); font-weight:600; }
  .score { float:right; font:600 13px ui-monospace, Menlo, monospace; }
  .sec { font-size:13.5px; font-weight:600; margin:4px 0 6px; }
  pre { white-space:pre-wrap; overflow-wrap:anywhere; font:12.5px/1.5 ui-monospace, Menlo,
        monospace; margin:0; max-height:220px; overflow:auto; }
  .err { color:var(--bad); }
</style>
</head>
<body>
<div class="wrap">
  <h1>Индекс документов · день 21</h1>
  <div class="sub">Корпус: README дней 01–20 и CLAUDE.md. Эмбеддинги <b>__MODEL__</b> через
    Ollama, индекс — SQLite. Один вопрос ищется сразу в двух индексах.</div>
  <div class="bar">
    <input id="q" placeholder="Вопрос по документации челленджа…" autofocus>
    <button class="main" id="go">Найти</button>
  </div>
  <div class="chips" id="chips"></div>
  <div class="sub" id="status"></div>
  <div class="grid">
    <div class="col"><h2><span class="dot" style="background:var(--fixed)"></span>fixed — окно
      __FIXED_SIZE__ симв., перекрытие __FIXED_OVERLAP__</h2><div class="stat" id="st-fixed"></div>
      <div id="r-fixed"></div></div>
    <div class="col"><h2><span class="dot" style="background:var(--structure)"></span>structure —
      по заголовкам</h2><div class="stat" id="st-structure"></div><div id="r-structure"></div></div>
  </div>
</div>
<script>
const EXAMPLES = __EXAMPLES__;
const BUILDS = __BUILDS__;
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

for (const b of BUILDS) {
  const s = b.stats;
  $("st-" + b.strategy).textContent = `${s.chunks} чанков · средний ${s.avg} симв. · ` +
    `${s.min}–${s.max} · разрезано таблиц/кода: ${s.broken} (${s.broken_pct}%) · ` +
    `эмбеддинг ${s.embed_seconds} с`;
}
if (!BUILDS.length) $("status").innerHTML =
  '<span class="err">index.db пуст — запусти python3 build_index.py</span>';

for (const q of EXAMPLES) {
  const b = document.createElement("button");
  b.textContent = q;
  b.onclick = () => { $("q").value = q; search(); };
  $("chips").append(b);
}

function card(hit) {
  return `<div class="card"><span class="score">${hit.score.toFixed(3)}</span>
    <div class="meta"><b>${esc(hit.source)}</b> · ${hit.n_chars} симв.<br>${esc(hit.chunk_id)}</div>
    <div class="sec">${esc(hit.section)}</div><pre>${esc(hit.text)}</pre></div>`;
}

async function search() {
  const query = $("q").value.trim();
  if (!query) return;
  $("go").disabled = true;
  $("status").textContent = "считаю вектор запроса…";
  try {
    const res = await fetch("/api/search", {method:"POST", body: JSON.stringify({query, k: 4})});
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    $("status").textContent = `вектор ${data.dim} измерений за ${data.seconds} с`;
    for (const [name, hits] of Object.entries(data.results))
      $("r-" + name).innerHTML = hits.map(card).join("");
  } catch (e) {
    $("status").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
  $("go").disabled = false;
}
$("go").onclick = search;
$("q").addEventListener("keydown", e => { if (e.key === "Enter") search(); });
</script>
</body>
</html>
'''


def build_page():
    examples = [q for q, _, _ in compare_chunking.QUESTIONS[:6]]
    return (PAGE
            .replace("__MODEL__", embeddings.MODEL)
            .replace("__FIXED_SIZE__", str(chunking.FIXED_SIZE))
            .replace("__FIXED_OVERLAP__", str(chunking.FIXED_OVERLAP))
            .replace("__EXAMPLES__", json.dumps(examples, ensure_ascii=False))
            .replace("__BUILDS__", json.dumps(builds(), ensure_ascii=False)))


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
            elif self.path == "/api/builds":
                self._json(builds())
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

            if self.path == "/api/search":
                query = str(payload.get("query") or "").strip()
                if not query:
                    raise ValueError("пустой запрос")
                k = max(1, min(int(payload.get("k") or 4), 10))
                with LOCK:
                    vector, seconds = embeddings.embed_one(query)
                    results = {name: index.search(vector, k)
                               for name, index in indexes().items()}
                self._json({"dim": len(vector), "seconds": seconds, "results": results})
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
    print(f"Индекс документов: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
