"""Агент по Конституции РФ с двумя режимами: без RAG и с RAG.

Запуск:
    python3 web.py     → откроется http://localhost:8022

Режим «сравнить» задаёт один вопрос дважды и кладёт ответы рядом: слева модель по
памяти, справа — по найденным статьям, под ответом видно, какие чанки ушли в промпт.
Для контрольных вопросов показывается ожидание из rag_demo.py.

Сервер тонкий: сообщения собирает rag.py, отвечает Agent из agent.py.
"""

import json
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent
import rag_demo

PORT = 8022
BOT = agent.Agent()
LOCK = threading.Lock()


PAGE = '''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Конституция: RAG</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#6b7280;
          --card:#f4f4f5; --accent:#2563eb; --border:#e4e4e7; --ok:#16a34a; --bad:#dc2626;
          --plain:#d97706; --rag:#0891b2; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa; --card:#27272a; --accent:#3b82f6;
            --border:#3f3f46; --ok:#22c55e; --bad:#f87171; --plain:#fbbf24; --rag:#22d3ee; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  main { max-width:1180px; margin:0 auto; padding:20px 16px 60px; }
  h1 { font-size:22px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:16px; }
  form { display:flex; gap:8px; flex-wrap:wrap; }
  input[type=text] { flex:1 1 420px; padding:10px 12px; font:inherit; border:1px solid var(--border);
          border-radius:8px; background:var(--bg); color:var(--fg); }
  .modes { display:flex; border:1px solid var(--border); border-radius:8px; overflow:hidden; }
  .modes label { padding:9px 14px; cursor:pointer; font-size:14px; }
  .modes input { display:none; }
  .modes input:checked + span { font-weight:600; color:var(--accent); }
  button { padding:10px 18px; font:inherit; border:0; border-radius:8px; background:var(--accent);
           color:#fff; cursor:pointer; }
  button:disabled { opacity:.5; cursor:wait; }
  .examples { margin:10px 0 18px; display:flex; flex-wrap:wrap; gap:6px; }
  .examples button { background:var(--card); color:var(--fg); font-size:12.5px; padding:5px 10px;
                     border:1px solid var(--border); }
  .expect { background:var(--card); border-left:3px solid var(--ok); padding:8px 12px;
            border-radius:6px; font-size:13.5px; margin-bottom:14px; }
  .cols { display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:16px; }
  .col { border:1px solid var(--border); border-radius:10px; padding:14px; min-width:0; }
  .col h2 { font-size:15px; margin:0 0 8px; }
  .col.plain h2 { color:var(--plain); } .col.rag h2 { color:var(--rag); }
  .answer { white-space:pre-wrap; }
  .meta { color:var(--muted); font-size:12.5px; margin-top:10px; }
  .chunk { background:var(--card); border-radius:6px; padding:6px 10px; margin-top:6px; font-size:12.5px; }
  .chunk summary { cursor:pointer; }
  .chunk pre { white-space:pre-wrap; font:12px/1.45 ui-monospace, Menlo, monospace; margin:6px 0 0; }
  .tag { display:inline-block; font-size:11px; padding:0 6px; border-radius:4px;
         background:var(--border); margin-left:4px; }
  .err { color:var(--bad); }
</style>
</head>
<body>
<main>
  <h1>Конституция РФ: ответ без RAG и с RAG</h1>
  <div class="sub">Отвечает __MODEL__ (temperature=0). RAG: вопрос → bge-m3 → топ-__K__ из __CHUNKS__
    чанков → статьи + вопрос в промпт. Текст: kremlin.ru, с поправками 2020.</div>
  <form id="f">
    <input type="text" id="q" placeholder="Вопрос по Конституции" autocomplete="off">
    <div class="modes">
      <label><input type="radio" name="mode" value="plain"><span>без RAG</span></label>
      <label><input type="radio" name="mode" value="rag"><span>с RAG</span></label>
      <label><input type="radio" name="mode" value="both" checked><span>сравнить</span></label>
    </div>
    <button id="go">Спросить</button>
  </form>
  <div class="examples" id="ex"></div>
  <div id="out"></div>
</main>
<script>
const QUESTIONS = __QUESTIONS__;
const LABELS = {plain: "без RAG — по памяти модели", rag: "с RAG — по найденным статьям"};
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

QUESTIONS.forEach((item, i) => {
  const b = document.createElement("button");
  b.type = "button"; b.textContent = (i + 1) + ". " + item.q;
  b.onclick = () => { $("q").value = item.q; $("f").requestSubmit(); };
  $("ex").appendChild(b);
});

function column(mode, r) {
  const hits = r.hits.map(h => `<details class="chunk"><summary>${h.score.toFixed(3)} · ${esc(h.chunk_id)}
      ${h.amended ? '<span class="tag">поправка 2020</span>' : ''}
      ${r.cited.includes(h.article) ? '<span class="tag">процитирована</span>' : ''}</summary>
      <pre>${esc(h.text)}</pre></details>`).join("");
  return `<div class="col ${mode}"><h2>${LABELS[mode]}</h2>
    <div class="answer">${esc(r.answer)}</div>
    <div class="meta">статьи в ответе: ${r.cited.length ? r.cited.map(esc).join(", ") : "—"} ·
      промпт ${r.prompt_tokens} ток. (${r.prompt_chars} симв.) · ответ ${r.completion_tokens} ток. ·
      ${r.seconds} с${r.cost != null ? " · $" + r.cost.toFixed(5) : ""}</div>
    ${hits ? '<div class="meta">найденные чанки → в промпт:</div>' + hits : ""}</div>`;
}

$("f").onsubmit = async (e) => {
  e.preventDefault();
  const question = $("q").value.trim();
  if (!question) return;
  const mode = document.querySelector("input[name=mode]:checked").value;
  $("go").disabled = true;
  $("out").innerHTML = '<div class="meta">думаю…</div>';
  try {
    const res = await fetch("/api/ask", {method: "POST", headers: {"Content-Type": "application/json"},
                                         body: JSON.stringify({question, mode})});
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const known = QUESTIONS.find(item => item.q === question);
    const expect = known ? `<div class="expect"><b>Ожидание:</b> ${esc(known.expect)}
        <br><b>Источники:</b> ст. ${known.sources.map(esc).join(", ")}</div>` : "";
    $("out").innerHTML = expect + '<div class="cols">' +
      Object.entries(data.results).map(([m, r]) => column(m, r)).join("") + "</div>";
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
                 for x in rag_demo.QUESTIONS]
    try:
        chunks = str(len(BOT.index.rows))
    except RuntimeError:
        chunks = "?"
    return (PAGE
            .replace("__MODEL__", BOT.model)
            .replace("__K__", str(BOT.k))
            .replace("__CHUNKS__", chunks)
            .replace("__QUESTIONS__", json.dumps(questions, ensure_ascii=False)))


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
                mode = payload.get("mode") or "both"
                modes = agent.MODES if mode == "both" else [mode]
                with LOCK:
                    results = {m: BOT.ask(question, m) for m in modes}
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
    print(f"Конституция, RAG: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
