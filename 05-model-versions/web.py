"""Веб-интерфейс к сравнению моделей: слабая / средняя / сильная.

Запуск:
    python3 web.py     → откроется http://localhost:8005
"""

import json
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from models import MODELS, PRESETS, judge, run_model

PORT = 8005

PAGE = '''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Слабая / средняя / сильная модель</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#6b7280;
          --card:#f4f4f5; --accent:#2563eb; --border:#e4e4e7;
          --weak:#94a3b8; --medium:#f59e0b; --strong:#16a34a; --bad:#ef4444; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa;
            --card:#27272a; --accent:#3b82f6; --border:#3f3f46;
            --weak:#94a3b8; --medium:#fbbf24; --strong:#22c55e; --bad:#f87171; }
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
  button { padding:11px 24px; font-size:15px; border:0; border-radius:10px;
           background:var(--accent); color:#fff; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  .grid { display:grid; grid-template-columns:repeat(3, 1fr); gap:14px; margin-top:22px; }
  @media (max-width:900px) { .grid { grid-template-columns:1fr; } }
  .col { border:1px solid var(--border); border-radius:12px; background:var(--card);
         padding:14px 15px; }
  .col.winner { border-color:var(--strong); box-shadow:0 0 0 1px var(--strong); }
  .col h2 { font-size:16px; margin:0 0 2px; }
  .col.weak h2 { color:var(--weak); }
  .col.medium h2 { color:var(--medium); }
  .col.strong h2 { color:var(--strong); }
  .col .note { color:var(--muted); font-size:12px; margin-bottom:10px; }
  .col .note a { color:inherit; }
  .answer { border-top:1px solid var(--border); padding-top:10px; font-size:14px;
            white-space:pre-wrap; word-wrap:break-word; }
  .mark { font-size:12px; padding:1px 7px; border-radius:999px; margin-left:6px;
          background:var(--strong); color:#fff; }
  .mark.bad { background:var(--bad); }
  details { margin-top:10px; font-size:13px; }
  details summary { cursor:pointer; color:var(--accent); }
  details pre { white-space:pre-wrap; word-wrap:break-word; background:var(--bg);
                border:1px solid var(--border); border-radius:8px; padding:10px;
                font-size:12px; margin-top:8px; }
  .metrics { border-top:1px solid var(--border); margin-top:10px; padding-top:9px;
             font-size:12.5px; color:var(--muted); }
  .metrics b { color:var(--fg); font-weight:600; }
  .verdict { margin-top:24px; border:1px solid var(--strong); border-radius:12px;
             padding:14px 16px; }
  .verdict h2 { font-size:15px; margin:0 0 8px; }
  .verdict.hidden { display:none; }
  table { width:100%; border-collapse:collapse; margin-top:26px; font-size:14px; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); }
  th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase;
       letter-spacing:.05em; }
  td.num, th.num { text-align:right; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Один запрос — три модели разного размера</h1>
  <div class="sub">Groq API &middot; 7B, 27B и 120B параметров &middot; качество оценивает независимый судья DeepSeek</div>

  <div class="opts" id="presets"></div>
  <textarea id="prompt"></textarea>
  <div class="opts">
    <label class="opt"><input type="checkbox" id="judge" checked> Позвать судью</label>
    <button id="run">Сравнить</button>
  </div>

  <div class="grid" id="grid"></div>
  <div class="verdict hidden" id="verdict"></div>
  <div id="table"></div>
</div>

<script>
  const MODELS = __MODELS__;
  const PRESETS = __PRESETS__;

  const presetsBox = document.getElementById("presets");
  const promptBox = document.getElementById("prompt");
  const grid = document.getElementById("grid");
  const verdictBox = document.getElementById("verdict");
  const tableBox = document.getElementById("table");
  const judgeBox = document.getElementById("judge");
  const runButton = document.getElementById("run");

  let preset = Object.keys(PRESETS)[0];

  for (const [key, meta] of Object.entries(PRESETS)) {
    const label = document.createElement("label");
    label.className = "opt";
    label.innerHTML = '<input type="radio" name="preset" value="' + key + '"' +
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

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function makeColumn(model) {
    const col = document.createElement("div");
    col.className = "col " + model.key;
    col.id = "col-" + model.key;
    col.innerHTML =
      "<h2>" + model.level + " · " + model.size + "</h2>" +
      '<div class="note"><a href="' + model.link + '" target="_blank">' + model.id +
      "</a> · " + model.owner + '</div><div class="answer">…думаю</div>';
    grid.append(col);
    return col;
  }

  function fillColumn(col, data) {
    const mark =
      data.ok === null || data.ok === undefined
        ? ""
        : '<span class="mark' + (data.ok ? "" : " bad") + '">' +
          (data.ok ? "эталон найден" : "эталон не найден") + "</span>";

    let html =
      col.querySelector("h2").outerHTML.replace("</h2>", mark + "</h2>") +
      col.querySelector(".note").outerHTML +
      '<div class="answer">' + escapeHtml(data.text) + "</div>";

    if (data.reasoning) {
      html += "<details><summary>Скрытое рассуждение (" + data.reasoning_tokens +
              " токенов)</summary><pre>" + escapeHtml(data.reasoning) + "</pre></details>";
    }

    html +=
      '<div class="metrics">' +
      "<div>время: <b>" + data.wall + " c</b> (модель " + data.total_time +
      " c + очередь " + data.queue_time + " c)</div>" +
      "<div>токенов: <b>" + data.completion_tokens + "</b> ответ" +
      (data.reasoning_tokens ? " (из них " + data.reasoning_tokens + " на рассуждение)" : "") +
      ", " + data.prompt_tokens + " запрос</div>" +
      "<div>стоимость: <b>" +
      (data.cost === null ? "— (тариф не задан)" : "$" + data.cost) + "</b></div>" +
      "</div>";

    col.innerHTML = html;
  }

  function showTable(results) {
    const rows = results
      .map((d) =>
        "<tr><td>" + d.level + " · " + d.size + "</td><td>" + d.id +
        '</td><td class="num">' + d.wall + '</td><td class="num">' + d.completion_tokens +
        '</td><td class="num">' + (d.reasoning_tokens || "—") +
        '</td><td class="num">' + (d.cost === null ? "—" : "$" + d.cost) +
        '</td><td class="num">' + (d.ok === null ? "—" : d.ok ? "эталон найден" : "нет эталона") + "</td></tr>"
      )
      .join("");
    tableBox.innerHTML =
      "<table><tr><th>Модель</th><th>ID</th><th class='num'>Секунд</th>" +
      "<th class='num'>Токенов</th><th class='num'>Из них рассуждение</th>" +
      "<th class='num'>Стоимость</th><th class='num'>Ответ</th></tr>" + rows + "</table>";
  }

  runButton.onclick = async () => {
    const prompt = promptBox.value.trim();
    if (!prompt) return;

    runButton.disabled = true;
    grid.innerHTML = "";
    tableBox.innerHTML = "";
    verdictBox.className = "verdict hidden";

    const results = [];
    await Promise.all(
      MODELS.map(async (model) => {
        const col = makeColumn(model);
        try {
          const res = await fetch("/api/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prompt, model: model.key, preset }),
          });
          const data = await res.json();
          if (data.error) {
            col.querySelector(".answer").textContent = data.text;
            return;
          }
          fillColumn(col, data);
          results.push(data);
        } catch (err) {
          col.querySelector(".answer").textContent = "Ошибка запроса: " + err;
        }
      })
    );

    const order = MODELS.map((m) => m.key);
    results.sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
    if (results.length) showTable(results);

    if (judgeBox.checked && results.length > 1) {
      verdictBox.className = "verdict";
      verdictBox.innerHTML = "<h2>Судья сравнивает ответы…</h2>";
      const verdict = await (
        await fetch("/api/judge", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prompt,
            answers: results.map((d) => ({ key: d.key, text: d.text })),
          }),
        })
      ).json();

      const names = Object.fromEntries(MODELS.map((m) => [m.key, m.level + " · " + m.size]));
      verdictBox.innerHTML =
        "<h2>Вердикт судьи (DeepSeek)" +
        (verdict.winner ? '<span class="mark">' + names[verdict.winner] + "</span>" : "") +
        "</h2>" +
        (verdict.ranking || []).map((k, i) => "<div>" + (i + 1) + ". " + names[k] + "</div>").join("") +
        '<div class="metrics">' + escapeHtml(verdict.comment || "") + "</div>";

      if (verdict.winner) {
        const col = document.getElementById("col-" + verdict.winner);
        if (col) col.classList.add("winner");
      }
    }

    runButton.disabled = false;
  };
</script>
</body>
</html>
'''


def build_page():
    """Подставляет в страницу список моделей и пресеты."""
    presets = {key: {"title": value["title"], "note": value["note"], "prompt": value["prompt"]}
               for key, value in PRESETS.items()}
    html = PAGE.replace("__MODELS__", json.dumps(MODELS, ensure_ascii=False))
    html = html.replace("__PRESETS__", json.dumps(presets, ensure_ascii=False))
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
        # Разбор запроса тоже под try: битый payload не должен ронять поток обработчика.
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))

            if self.path == "/api/run":
                model = next(m for m in MODELS if m["key"] == payload["model"])
                # Эталон берём из пресета, только если запрос не переписали руками.
                preset = PRESETS.get(payload.get("preset"), {})
                expect = preset.get("expect", []) if preset.get("prompt") == payload["prompt"] else []
                print(f"→ {model['level']}: {model['id']}")
                self._json(run_model(model, payload["prompt"], expect))

            elif self.path == "/api/judge":
                print("→ судья (DeepSeek)")
                self._json(judge(payload["prompt"], payload["answers"]))

            else:
                self._reply(404, "text/plain; charset=utf-8", b"not found")

        except Exception as error:
            traceback.print_exc()
            self._json({"error": True, "text": f"Ошибка: {error}"})

    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Модели: {', '.join(m['id'] for m in MODELS)}\nОткрой {url}  (Ctrl+C — остановить)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")


if __name__ == "__main__":
    main()
