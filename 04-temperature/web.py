"""Веб-интерфейс к сравнению температур: три окна на 0, 0.7 и 1.2.

Запуск:
    python3 web.py     → откроется http://localhost:8004
"""

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from temperature import (
    MODEL,
    PRESETS,
    RUNS_PER_TEMPERATURE,
    TEMPERATURES,
    run_temperature,
)

PORT = 8004

PAGE = '''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Температура: 0 / 0.7 / 1.2</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#6b7280;
          --card:#f4f4f5; --accent:#2563eb; --border:#e4e4e7;
          --cold:#0ea5e9; --warm:#f59e0b; --hot:#ef4444; --ok:#16a34a; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa;
            --card:#27272a; --accent:#3b82f6; --border:#3f3f46;
            --cold:#38bdf8; --warm:#fbbf24; --hot:#f87171; --ok:#22c55e; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 -apple-system,
         BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
  .wrap { max-width:1200px; margin:0 auto; padding:24px 16px 64px; }
  h1 { font-size:21px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:20px; }
  textarea { width:100%; min-height:72px; padding:12px 14px; font:inherit; resize:vertical;
             border-radius:10px; border:1px solid var(--border); background:var(--card);
             color:var(--fg); }
  textarea:focus { outline:2px solid var(--accent); outline-offset:-1px; }
  .opts { display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin:14px 0; }
  .opt { display:flex; align-items:center; gap:7px; padding:8px 13px; border-radius:999px;
         border:1px solid var(--border); background:var(--card); cursor:pointer;
         font-size:14px; user-select:none; }
  .opt:has(input:checked) { border-color:var(--accent); box-shadow:inset 0 0 0 1px var(--accent); }
  .runs { color:var(--muted); font-size:14px; }
  .runs input { width:52px; padding:7px 9px; font:inherit; border-radius:8px;
                border:1px solid var(--border); background:var(--card); color:var(--fg); }
  button { padding:11px 24px; font-size:15px; border:0; border-radius:10px;
           background:var(--accent); color:#fff; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  .grid { display:grid; grid-template-columns:repeat(3, 1fr); gap:14px; margin-top:22px; }
  @media (max-width:900px) { .grid { grid-template-columns:1fr; } }
  .col { border:1px solid var(--border); border-radius:12px; background:var(--card);
         padding:14px 15px; }
  .col h2 { font-size:16px; margin:0 0 2px; }
  .col.t0 h2 { color:var(--cold); }
  .col.t07 h2 { color:var(--warm); }
  .col.t12 h2 { color:var(--hot); }
  .col .note { color:var(--muted); font-size:12px; margin-bottom:10px; }
  .run { border-top:1px solid var(--border); padding:9px 0; font-size:14px;
         white-space:pre-wrap; word-wrap:break-word; }
  .run .n { color:var(--muted); font-size:11px; text-transform:uppercase;
            letter-spacing:.05em; margin-bottom:3px; }
  .mark { font-size:12px; padding:1px 7px; border-radius:999px; margin-left:6px;
          background:var(--ok); color:#fff; }
  .mark.bad { background:var(--hot); }
  .metrics { border-top:1px solid var(--border); margin-top:10px; padding-top:9px;
             font-size:12.5px; color:var(--muted); }
  .metrics b { color:var(--fg); font-weight:600; }
  table { width:100%; border-collapse:collapse; margin-top:26px; font-size:14px; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); }
  th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase;
       letter-spacing:.05em; }
  td.num, th.num { text-align:right; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Одна и та же задача при разной температуре</h1>
  <div class="sub">DeepSeek API &middot; каждая температура прогоняется несколько раз — разброс виден между повторами, а не внутри одного ответа</div>

  <div class="opts" id="presets"></div>
  <textarea id="prompt"></textarea>
  <div class="opts">
    <label class="runs">прогонов на температуру: <input type="number" id="runs" min="1" max="5" value="__RUNS__"></label>
    <button id="run">Сравнить</button>
  </div>

  <div class="grid" id="grid"></div>
  <div id="table"></div>
</div>

<script>
  const PRESETS = __PRESETS__;
  const TEMPERATURES = __TEMPERATURES__;

  const presetsBox = document.getElementById("presets");
  const promptBox = document.getElementById("prompt");
  const runsBox = document.getElementById("runs");
  const grid = document.getElementById("grid");
  const tableBox = document.getElementById("table");
  const runButton = document.getElementById("run");

  let preset = Object.keys(PRESETS)[0];

  for (const [key, meta] of Object.entries(PRESETS)) {
    const label = document.createElement("label");
    label.className = "opt";
    label.innerHTML =
      '<input type="radio" name="preset" value="' + key + '"' +
      (key === preset ? " checked" : "") + ">";
    label.append(meta.title);
    label.title = meta.note;
    presetsBox.append(label);
  }
  promptBox.value = PRESETS[preset].prompt;

  presetsBox.onchange = (event) => {
    preset = event.target.value;
    promptBox.value = PRESETS[preset].prompt;
  };

  const cssClass = (t) => (t === 0 ? "t0" : t < 1 ? "t07" : "t12");

  function makeColumn(temperature) {
    const col = document.createElement("div");
    col.className = "col " + cssClass(temperature);
    col.id = "col-" + temperature;
    col.innerHTML =
      "<h2>temperature = " + temperature + "</h2>" +
      '<div class="note">' +
      (temperature === 0 ? "самый предсказуемый режим" :
       temperature < 1 ? "сбалансированный режим" : "свободный режим") +
      '</div><div class="run">…думаю</div>';
    grid.append(col);
    return col;
  }

  function fillColumn(col, data) {
    const runs = data.answers
      .map((answer, index) => {
        const mark =
          answer.ok === null || answer.ok === undefined
            ? ""
            : '<span class="mark' + (answer.ok ? "" : " bad") + '">' +
              (answer.ok ? "верно" : "ошибка") + "</span>";
        return '<div class="run"><div class="n">прогон ' + (index + 1) + mark + "</div>" +
               escapeHtml(answer.text) + "</div>";
      })
      .join("");

    const m = data.metrics;
    const accuracy =
      m.correct === null ? "" :
      "<div>точность: <b>" + m.correct + " из " + data.answers.length + "</b></div>";

    col.innerHTML =
      col.querySelector("h2").outerHTML +
      col.querySelector(".note").outerHTML +
      runs +
      '<div class="metrics">' +
      "<div>сходство повторов: <b>" + m.similarity + "%</b>" +
      (m.identical ? " (совпали дословно)" : "") + "</div>" +
      "<div>уникальных слов: <b>" + m.unique_words + "</b></div>" +
      "<div>в среднем: <b>" + m.avg_chars + "</b> симв., <b>" + m.avg_tokens + "</b> токенов</div>" +
      accuracy +
      "</div>";
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function showTable(results) {
    const rows = results
      .map(
        (data) =>
          "<tr><td>" + data.temperature + '</td><td class="num">' + data.metrics.similarity +
          '%</td><td class="num">' + data.metrics.unique_words +
          '</td><td class="num">' + data.metrics.avg_chars +
          '</td><td class="num">' +
          (data.metrics.correct === null ? "—" : data.metrics.correct + " / " + data.answers.length) +
          "</td></tr>"
      )
      .join("");
    tableBox.innerHTML =
      "<table><tr><th>Температура</th><th class='num'>Сходство повторов</th>" +
      "<th class='num'>Уникальных слов</th><th class='num'>Символов</th>" +
      "<th class='num'>Точность</th></tr>" + rows + "</table>";
  }

  runButton.onclick = async () => {
    const prompt = promptBox.value.trim();
    if (!prompt) return;

    runButton.disabled = true;
    grid.innerHTML = "";
    tableBox.innerHTML = "";

    const results = [];
    await Promise.all(
      TEMPERATURES.map(async (temperature) => {
        const col = makeColumn(temperature);
        try {
          const res = await fetch("/api/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              prompt,
              temperature,
              runs: Number(runsBox.value),
              preset,
            }),
          });
          const data = await res.json();
          if (data.error) {
            col.querySelector(".run").textContent = data.text;
            return;
          }
          fillColumn(col, data);
          results.push(data);
        } catch (err) {
          col.querySelector(".run").textContent = "Ошибка запроса: " + err;
        }
      })
    );

    results.sort((a, b) => a.temperature - b.temperature);
    if (results.length) showTable(results);
    runButton.disabled = false;
  };
</script>
</body>
</html>
'''


def build_page():
    """Подставляет в страницу пресеты, список температур и число прогонов."""
    presets = {key: {"title": value["title"], "note": value["note"], "prompt": value["prompt"]}
               for key, value in PRESETS.items()}
    html = PAGE.replace("__PRESETS__", json.dumps(presets, ensure_ascii=False))
    html = html.replace("__TEMPERATURES__", json.dumps(TEMPERATURES))
    html = html.replace("__RUNS__", str(RUNS_PER_TEMPERATURE))
    return html.encode("utf-8")


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
        if self.path != "/api/run":
            self._reply(404, "text/plain; charset=utf-8", b"not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length))
        temperature = float(payload["temperature"])
        runs = max(1, min(5, int(payload.get("runs", RUNS_PER_TEMPERATURE))))

        # Эталон берём из пресета, но только если запрос не переписали руками:
        # для своего текста проверять точность не по чему.
        preset = PRESETS.get(payload.get("preset"), {})
        expect = preset.get("expect", []) if preset.get("prompt") == payload["prompt"] else []

        print(f"→ temperature={temperature}, прогонов: {runs}")
        try:
            self._json(run_temperature(payload["prompt"], temperature, runs, expect))
        except (RuntimeError, SystemExit) as error:
            self._json({"error": True, "text": f"Ошибка: {error}"})

    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Модель: {MODEL}\nОткрой {url}  (Ctrl+C — остановить)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")


if __name__ == "__main__":
    main()
