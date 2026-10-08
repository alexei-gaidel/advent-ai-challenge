"""Оптимизация локальной LLM: тот же RAG по Конституции на разных пресетах модели.

Запуск:
    python3 web.py     → откроется http://localhost:8029

Выбор: один пресет, «до / после» (base и оптимизированный рядом) или все пять по очереди.
Под ответом — цитаты (✓/✗), время по этапам, сколько токенов ушло реранкеру, сколько модель
занимает в памяти и какая доля на GPU, перезагружалась ли она.

Сервер тонкий: настройки — presets.py, отбор — retrieval.py, ответ — citations.py через Agent.
"""

import json
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent
import citations
import local_llm
import optimize_demo
import presets
import retrieval

PORT = 8029
BOTS = {name: agent.Agent(name) for name in presets.ORDER}
GROUPS = {"before_after": [presets.BEFORE, presets.AFTER], "all": presets.ORDER}
LOCK = threading.Lock()
LIMITS = {"threshold": (0.0, 1.0, float), "rerank_min": (0, 10, int), "gate_min": (0, 10, int)}


PAGE = '''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Оптимизация LLM</title>
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
  main { max-width:1200px; margin:0 auto; padding:20px 16px 60px; }
  h1 { font-size:22px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:14px; }
  form { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  input, select { padding:9px 10px; font:inherit; border:1px solid var(--border); border-radius:8px;
                  background:var(--bg); color:var(--fg); }
  #q { flex:1 1 420px; }
  .num { width:70px; }
  label.p { font-size:13px; color:var(--muted); display:flex; gap:6px; align-items:center; }
  button { padding:10px 18px; font:inherit; border:0; border-radius:8px; background:var(--accent);
           color:#fff; cursor:pointer; }
  button:disabled { opacity:.5; cursor:wait; }
  .examples { margin:10px 0 16px; display:flex; flex-wrap:wrap; gap:6px; }
  .examples button, .clar button { background:var(--card); color:var(--fg); font-size:12.5px;
           padding:5px 10px; border:1px solid var(--border); }
  .expect { background:var(--card); border-left:3px solid var(--ok); padding:8px 12px;
            border-radius:6px; font-size:13.5px; margin-bottom:14px; }
  .cols { display:grid; grid-template-columns:repeat(auto-fit, minmax(340px, 1fr)); gap:14px; }
  .col { border:1px solid var(--border); border-radius:10px; padding:14px; min-width:0; }
  .col h2 { font-size:14.5px; margin:0 0 8px; color:var(--accent); }
  .answer { white-space:pre-wrap; }
  .unknown { border-left:3px solid var(--warn); padding-left:10px; }
  h3 { font-size:12.5px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin:12px 0 4px; }
  .src, .quote { font-size:13px; margin:3px 0; overflow-wrap:anywhere; }
  .src a { color:var(--accent); }
  .quote { background:var(--card); border-radius:6px; padding:5px 9px; }
  .quote.ok::before { content:"✓ "; color:var(--ok); font-weight:700; }
  .quote.bad { text-decoration:line-through; color:var(--muted); }
  .quote.bad::before { content:"✗ "; color:var(--bad); font-weight:700; text-decoration:none; display:inline-block; }
  .tag { font-size:11px; color:var(--muted); }
  .meta { color:var(--muted); font-size:12px; margin-top:10px; }
  .net { font-size:11.5px; padding:1px 7px; border-radius:10px; margin-left:6px; font-weight:400; }
  .net.off { background:color-mix(in srgb, var(--ok) 18%, transparent); color:var(--ok); }
  .net.on { background:color-mix(in srgb, var(--warn) 18%, transparent); color:var(--warn); }
  .clar { display:flex; flex-direction:column; gap:5px; margin-top:8px; }
  .clar button { text-align:left; }
  .err { color:var(--bad); }
</style>
</head>
<body>
<main>
  <h1>Локальная LLM под задачу: до и после оптимизации</h1>
  <div class="sub">RAG по Конституции РФ, всё в Ollama __OLLAMA__ на этом компьютере. Пресет меняет квантование,
    окно контекста, сколько фрагментов видит реранкер и промпт ответа. temperature=0.</div>
  <form id="f">
    <input type="text" id="q" placeholder="Вопрос по Конституции" autocomplete="off">
    <select id="stack"><option value="before_after" selected>до / после</option>__STACK_OPTIONS__<option value="all">все пять по очереди</option></select>
    <button id="go">Спросить</button>
    <label class="p">порог косинуса <input class="num" id="threshold" type="number" step="0.01" value="__THRESHOLD__"></label>
    <label class="p">«не знаю», если реранк &lt; <input class="num" id="gate_min" type="number" value="__GATE_MIN__"></label>
  </form>
  <div class="examples" id="ex"></div>
  <div id="out"></div>
</main>
<script>
const QUESTIONS = __QUESTIONS__;
const LABELS = __LABELS__;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const STATUS = {not_found: "нет в чанке дословно", wrong_chunk: "есть, но в другом чанке", empty: "пустая"};

function ask(text) { $("q").value = text; $("f").requestSubmit(); }
QUESTIONS.forEach((item, i) => {
  const b = document.createElement("button");
  b.type = "button"; b.textContent = (i + 1) + ". " + item.q;
  b.onclick = () => ask(item.q);
  $("ex").appendChild(b);
});

function column(stack, r) {
  const sources = r.sources.map(s => `<div class="src"><a href="${esc(s.source)}" target="_blank">${esc(s.chunk_id)}</a>
      <span class="tag">${esc(s.section)}</span></div>`).join("") || '<div class="tag">— нет</div>';
  const quotes = r.quotes.map(q => `<div class="quote ok">«${esc(q.text)}» <span class="tag">${esc(q.chunk_id)}</span></div>`).join("")
    + r.rejected.map(q => `<div class="quote bad">«${esc(q.text)}» <span class="tag">${esc(q.chunk_id)} — ${STATUS[q.status] || q.status}${q.attempt ? ", попытка " + q.attempt : ""}</span></div>`).join("");
  const clar = r.clarify.length ? '<div class="clar">' + r.clarify.map(o =>
      `<button type="button" data-q="${esc("Что говорит статья " + o.article + " Конституции: " + o.hint)}">ст. ${esc(o.article)} — ${esc(o.hint)}</button>`).join("") + "</div>" : "";
  const t = r.timing;
  const ps = (r.memory.ps || []).filter(m => !m.name.startsWith("bge-m3"))[0] || {};
  const mem = ps.size_gb ? `<span class="net off">${ps.size_gb} ГБ · ${ps.gpu_pct}% GPU</span>` : "";
  return `<div class="col"><h2>${esc(LABELS[stack])}${mem}</h2>
    <div class="tag">${esc(r.model)}</div>
    <div class="answer ${r.unknown ? "unknown" : ""}">${esc(r.answer)}</div>${clar}
    <h3>Источники</h3>${sources}
    <h3>Цитаты</h3>${quotes || '<div class="tag">— нет</div>'}
    <div class="meta">лучший реранк ${r.best_rerank ?? "—"} · вызовов LLM ${r.llm_calls} ·
      токенов ${r.prompt_tokens}+${r.completion_tokens} (реранкеру ${r.rerank_prompt_tokens}) ·
      ${r.tok_per_s} ток/с · перезагрузок модели ${r.reloads}<br>
      <b>${r.seconds} с</b>: эмбеддинг ${t.embed} · реранк ${t.rerank} · ответ ${t.answer} · загрузка модели ${t.load}</div></div>`;
}

$("out").addEventListener("click", (e) => { const q = e.target.closest("button[data-q]"); if (q) ask(q.dataset.q); });

$("f").onsubmit = async (e) => {
  e.preventDefault();
  const question = $("q").value.trim();
  if (!question) return;
  const params = {threshold: Number($("threshold").value), gate_min: Number($("gate_min").value)};
  $("go").disabled = true;
  $("out").innerHTML = '<div class="meta">ищу, проверяю цитаты… пресет до оптимизации отвечает до пары минут</div>';
  try {
    const res = await fetch("/api/ask", {method: "POST", headers: {"Content-Type": "application/json"},
                                         body: JSON.stringify({question, stack: $("stack").value, params})});
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const known = QUESTIONS.find(item => item.q === question);
    const expect = known ? `<div class="expect"><b>Ожидание:</b> ${esc(known.expect)}</div>` : "";
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
    questions = [{"q": x["q"], "expect": x["expect"]} for x in optimize_demo.QUESTIONS]
    labels = {name: presets.PRESETS[name]["label"] for name in presets.ORDER}
    options = "".join(f'<option value="{name}">{label}</option>' for name, label in labels.items())
    try:
        ollama = local_llm.version()
    except RuntimeError:
        ollama = "(не запущена — `ollama serve`)"
    return (PAGE
            .replace("__OLLAMA__", ollama)
            .replace("__STACK_OPTIONS__", options)
            .replace("__THRESHOLD__", str(retrieval.DEFAULTS["threshold"]))
            .replace("__GATE_MIN__", str(citations.GATE_MIN))
            .replace("__LABELS__", json.dumps(labels, ensure_ascii=False))
            .replace("__QUESTIONS__", json.dumps(questions, ensure_ascii=False)))


def parse_params(raw):
    if not isinstance(raw, dict):
        raise ValueError("params должен быть объектом")
    return {key: min(max(cast(raw[key]), low), high)
            for key, (low, high, cast) in LIMITS.items() if raw.get(key) is not None}


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
                choice = payload.get("stack") or "before_after"
                if choice not in GROUPS and choice not in presets.PRESETS:
                    raise ValueError(f"неизвестный пресет {choice!r}")
                stacks = GROUPS.get(choice, [choice])
                params = parse_params(payload.get("params") or {})
                with LOCK:          # одна модель за раз: на 8 ГБ две 7b в памяти не держатся
                    results = {s: BOTS[s].ask(question, **params) for s in stacks}
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
    print(f"Оптимизация LLM: {url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
