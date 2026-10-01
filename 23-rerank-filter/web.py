"""RAG по Конституции РФ: четыре режима поиска, порог и топ-K настраиваются со страницы.

Запуск:
    python3 web.py     → откроется http://localhost:8023

Под каждым ответом — путь отбора: переписанный запрос, кандидаты с косинусом и оценкой
реранкера, на какой стадии каждый отсечён. Режим «сравнить» прогоняет все четыре.

Сервер тонкий: отбор — retrieval.py, ответ — Agent из agent.py.
"""

import json
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent
import rerank_demo
import retrieval

PORT = 8023
BOT = agent.Agent()
LOCK = threading.Lock()
LIMITS = {"threshold": (0.0, 1.0, float), "k_before": (1, 40, int), "k_after": (1, 10, int),
          "rerank_min": (0, 10, int)}


PAGE = '''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Реранкинг RAG</title>
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
  main { max-width:1280px; margin:0 auto; padding:20px 16px 60px; }
  h1 { font-size:22px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:14px; }
  form { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  input, select { padding:9px 10px; font:inherit; border:1px solid var(--border); border-radius:8px;
                  background:var(--bg); color:var(--fg); }
  #q { flex:1 1 420px; }
  .num { width:74px; }
  label.p { font-size:13px; color:var(--muted); display:flex; gap:6px; align-items:center; }
  button { padding:10px 18px; font:inherit; border:0; border-radius:8px; background:var(--accent);
           color:#fff; cursor:pointer; }
  button:disabled { opacity:.5; cursor:wait; }
  .examples { margin:10px 0 16px; display:flex; flex-wrap:wrap; gap:6px; }
  .examples button { background:var(--card); color:var(--fg); font-size:12.5px; padding:5px 10px;
                     border:1px solid var(--border); }
  .expect { background:var(--card); border-left:3px solid var(--ok); padding:8px 12px;
            border-radius:6px; font-size:13.5px; margin-bottom:14px; }
  .cols { display:grid; grid-template-columns:repeat(auto-fit, minmax(290px, 1fr)); gap:14px; }
  .col { border:1px solid var(--border); border-radius:10px; padding:12px; min-width:0; }
  .col h2 { font-size:14.5px; margin:0 0 6px; color:var(--accent); }
  .answer { white-space:pre-wrap; font-size:14px; }
  .meta { color:var(--muted); font-size:12px; margin-top:8px; }
  .query { font-size:12.5px; background:var(--card); padding:4px 8px; border-radius:6px; margin-bottom:8px; }
  table { width:100%; border-collapse:collapse; font:11.5px/1.35 ui-monospace, Menlo, monospace; margin-top:6px; }
  td, th { padding:2px 4px; text-align:left; border-bottom:1px solid var(--border); }
  th { color:var(--muted); font-weight:500; }
  tr.kept td { color:var(--ok); font-weight:600; }
  tr.below_threshold td, tr.rerank_low td, tr.cut_k td { color:var(--muted); }
  tr.gold td:first-child::after { content:" ★"; color:var(--warn); }
  .err { color:var(--bad); }
</style>
</head>
<body>
<main>
  <h1>RAG по Конституции: порог, реранкер, query rewrite</h1>
  <div class="sub">Отвечает и реранжирует __MODEL__ (temperature=0), эмбеддинги bge-m3, __CHUNKS__ чанков.
    Зелёным — чанки, ушедшие в промпт; ★ — статья из ожидания контрольного вопроса.</div>
  <form id="f">
    <input type="text" id="q" placeholder="Вопрос по Конституции" autocomplete="off">
    <select id="mode">__MODE_OPTIONS__<option value="all" selected>сравнить все</option></select>
    <button id="go">Спросить</button>
    <label class="p">порог <input class="num" id="threshold" type="number" step="0.01" value="__THRESHOLD__"></label>
    <label class="p">топ-K до <input class="num" id="k_before" type="number" value="__K_BEFORE__"></label>
    <label class="p">после <input class="num" id="k_after" type="number" value="__K_AFTER__"></label>
    <label class="p">реранкер ≥ <input class="num" id="rerank_min" type="number" value="__RERANK_MIN__"></label>
  </form>
  <div class="examples" id="ex"></div>
  <div id="out"></div>
</main>
<script>
const QUESTIONS = __QUESTIONS__;
const LABELS = __LABELS__;
const STATUS = {kept: "в промпт", below_threshold: "ниже порога", rerank_low: "реранкер", cut_k: "за топ-K"};
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

QUESTIONS.forEach((item, i) => {
  const b = document.createElement("button");
  b.type = "button"; b.textContent = (i + 1) + ". " + item.q;
  b.onclick = () => { $("q").value = item.q; $("f").requestSubmit(); };
  $("ex").appendChild(b);
});

function column(mode, r, gold) {
  const t = r.trace;
  const rows = t.candidates.map(c => `<tr class="${c.status}${gold.includes(c.article) ? " gold" : ""}">
      <td>${esc(c.chunk_id)}</td><td>${c.score.toFixed(3)}</td>
      <td>${c.rerank == null ? "—" : c.rerank}</td><td>${STATUS[c.status]}</td></tr>`).join("");
  const query = t.query !== r.question ? `<div class="query">запрос: ${esc(t.query)}</div>` : "";
  return `<div class="col"><h2>${esc(LABELS[mode])}</h2>${query}
    <div class="answer">${esc(r.answer)}</div>
    <div class="meta">в промпт ${r.hits.length} чанков (${r.context_chars} симв.) · токенов ${r.prompt_tokens}+${r.completion_tokens}
      · ${r.seconds} с · $${r.cost.toFixed(5)}</div>
    <table><tr><th>чанк</th><th>косинус</th><th>LLM</th><th>итог</th></tr>${rows}</table></div>`;
}

$("f").onsubmit = async (e) => {
  e.preventDefault();
  const question = $("q").value.trim();
  if (!question) return;
  const params = {};
  for (const key of ["threshold", "k_before", "k_after", "rerank_min"]) params[key] = Number($(key).value);
  $("go").disabled = true;
  $("out").innerHTML = '<div class="meta">ищу, реранжирую, отвечаю…</div>';
  try {
    const res = await fetch("/api/ask", {method: "POST", headers: {"Content-Type": "application/json"},
                                         body: JSON.stringify({question, mode: $("mode").value, params})});
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const known = QUESTIONS.find(item => item.q === question);
    const gold = known ? known.sources : [];
    const expect = known ? `<div class="expect"><b>Ожидание:</b> ${esc(known.expect)}
        <br><b>Источники:</b> ${gold.length ? "ст. " + gold.map(esc).join(", ") : "нет — вопрос вне базы"}</div>` : "";
    $("out").innerHTML = expect + '<div class="cols">' +
      Object.entries(data.results).map(([m, r]) => column(m, r, gold)).join("") + "</div>";
  } catch (err) {
    $("out").innerHTML = `<div class="err">Ошибка: ${esc(err.message)}</div>`;
  } finally {
    $("go").disabled = false;
  }
};
</script>
</body>
</html>
'''


def build_page():
    questions = [{"q": x["q"], "expect": x["expect"], "sources": x["sources"]}
                 for x in rerank_demo.QUESTIONS]
    try:
        chunks = str(len(BOT.index.rows))
    except RuntimeError:
        chunks = "?"
    options = "".join(f'<option value="{m}">{retrieval.MODE_LABELS[m]}</option>' for m in agent.MODES)
    d = retrieval.DEFAULTS
    return (PAGE
            .replace("__MODEL__", BOT.model)
            .replace("__CHUNKS__", chunks)
            .replace("__MODE_OPTIONS__", options)
            .replace("__THRESHOLD__", str(d["threshold"]))
            .replace("__K_BEFORE__", str(d["k_before"]))
            .replace("__K_AFTER__", str(d["k_after"]))
            .replace("__RERANK_MIN__", str(d["rerank_min"]))
            .replace("__LABELS__", json.dumps(retrieval.MODE_LABELS, ensure_ascii=False))
            .replace("__QUESTIONS__", json.dumps(questions, ensure_ascii=False)))


def parse_params(raw):
    """Параметры со страницы: только известные ключи, в разумных границах."""
    if not isinstance(raw, dict):
        raise ValueError("params должен быть объектом")
    params = {}
    for key, (low, high, cast) in LIMITS.items():
        if raw.get(key) is not None:
            params[key] = min(max(cast(raw[key]), low), high)
    return params


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
                question = str(payload.get("question") or "").strip()
                if not question:
                    raise ValueError("пустой вопрос")
                mode = payload.get("mode") or "all"
                modes = agent.MODES if mode == "all" else [mode]
                params = parse_params(payload.get("params") or {})
                with LOCK:
                    results = {m: BOT.ask(question, m, **params) for m in modes}
                self._json({"results": results})
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
    print(f"Реранкинг RAG: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
