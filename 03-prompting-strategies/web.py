"""Веб-интерфейс к четырём способам решения задачи.

Запуск:
    python3 web.py     → откроется http://localhost:8003
"""

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from strategies import DEFAULT_QUESTION, MODEL, STRATEGIES, judge

PORT = 8003

PAGE = '''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Четыре способа решить задачу</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#6b7280;
          --card:#f4f4f5; --accent:#2563eb; --border:#e4e4e7; --win:#16a34a; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa;
            --card:#27272a; --accent:#3b82f6; --border:#3f3f46; --win:#22c55e; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 -apple-system,
         BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
  .wrap { max-width:1100px; margin:0 auto; padding:24px 16px 64px; }
  h1 { font-size:21px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:20px; }
  textarea { width:100%; min-height:80px; padding:12px 14px; font:inherit; resize:vertical;
             border-radius:10px; border:1px solid var(--border); background:var(--card);
             color:var(--fg); }
  textarea:focus { outline:2px solid var(--accent); outline-offset:-1px; }
  .opts { display:flex; flex-wrap:wrap; gap:10px; margin:14px 0; }
  .opt { display:flex; align-items:center; gap:7px; padding:8px 13px; border-radius:999px;
         border:1px solid var(--border); background:var(--card); cursor:pointer;
         font-size:14px; user-select:none; }
  .opt:has(input:checked) { border-color:var(--accent); box-shadow:inset 0 0 0 1px var(--accent); }
  .opt.judge:has(input:checked) { border-color:var(--win); box-shadow:inset 0 0 0 1px var(--win); }
  button { padding:11px 24px; font-size:15px; border:0; border-radius:10px;
           background:var(--accent); color:#fff; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(330px, 1fr));
          gap:14px; margin-top:24px; }
  .card { border:1px solid var(--border); border-radius:12px; padding:14px 16px;
          background:var(--card); }
  .card.winner { border-color:var(--win); box-shadow:0 0 0 1px var(--win); }
  .card h2 { font-size:15px; margin:0 0 2px; }
  .card .note { color:var(--muted); font-size:12px; margin-bottom:10px; }
  .answer { white-space:pre-wrap; word-wrap:break-word; font-size:14px;
            border-top:1px solid var(--border); padding-top:10px; }
  .metrics { color:var(--muted); font-size:12px; margin-top:10px;
             border-top:1px solid var(--border); padding-top:8px; }
  .badge { display:inline-block; background:var(--win); color:#fff; font-size:11px;
           padding:2px 8px; border-radius:999px; margin-left:6px; vertical-align:middle; }
  details { margin-top:10px; font-size:13px; }
  details summary { cursor:pointer; color:var(--accent); }
  details pre { white-space:pre-wrap; word-wrap:break-word; background:var(--bg);
                border:1px solid var(--border); border-radius:8px; padding:10px;
                font-size:12px; margin-top:8px; }
  .verdict { margin-top:24px; border:1px solid var(--win); border-radius:12px;
             padding:14px 16px; }
  .verdict h2 { font-size:15px; margin:0 0 8px; }
  table { width:100%; border-collapse:collapse; margin-top:24px; font-size:14px; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); }
  th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase;
       letter-spacing:.05em; }
  td.num, th.num { text-align:right; }
  .hidden { display:none; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Одна задача — четыре способа спросить</h1>
  <div class="sub">DeepSeek API &middot; каждый способ уходит отдельным запросом, ответы приходят параллельно</div>

  <textarea id="question"></textarea>

  <div class="opts" id="opts"></div>
  <div class="opts">
    <label class="opt judge"><input type="checkbox" id="judge" checked> Позвать судью (5-й вызов)</label>
    <button id="run">Решить</button>
  </div>

  <div class="grid" id="grid"></div>
  <div class="verdict hidden" id="verdict"></div>
  <div id="table"></div>
</div>

<script>
  const STRATEGIES = __STRATEGIES__;
  const DEFAULT_QUESTION = __QUESTION__;

  const optsBox = document.getElementById("opts");
  const grid = document.getElementById("grid");
  const verdictBox = document.getElementById("verdict");
  const tableBox = document.getElementById("table");
  const questionBox = document.getElementById("question");
  const judgeBox = document.getElementById("judge");
  const runButton = document.getElementById("run");

  questionBox.value = DEFAULT_QUESTION;

  for (const [key, meta] of Object.entries(STRATEGIES)) {
    const label = document.createElement("label");
    label.className = "opt";
    label.innerHTML = '<input type="checkbox" value="' + key + '" checked>';
    label.append(meta.title);
    optsBox.append(label);
  }

  const selected = () =>
    [...optsBox.querySelectorAll("input:checked")].map((input) => input.value);

  function makeCard(key) {
    const card = document.createElement("div");
    card.className = "card";
    card.id = "card-" + key;
    card.innerHTML =
      "<h2>" + STRATEGIES[key].title + "</h2>" +
      '<div class="note">' + STRATEGIES[key].note + "</div>" +
      '<div class="answer">…думаю</div>';
    grid.append(card);
    return card;
  }

  function fillCard(card, key, data) {
    card.querySelector(".answer").textContent = data.text;

    if (data.extra && data.extra.prompt) {
      const details = document.createElement("details");
      details.innerHTML =
        "<summary>Промпт, который сочинила модель</summary><pre></pre>";
      details.querySelector("pre").textContent = data.extra.prompt;
      card.append(details);
    }

    const metrics = document.createElement("div");
    metrics.className = "metrics";
    metrics.textContent =
      "токенов: " + data.tokens + "  ·  " + data.seconds + " c  ·  вызовов API: " + data.calls;
    card.append(metrics);
  }

  function showTable(results) {
    if (!results.length) return;
    const rows = results
      .map(
        ([key, data]) =>
          "<tr><td>" + STRATEGIES[key].title + '</td><td class="num">' + data.tokens +
          '</td><td class="num">' + data.seconds + '</td><td class="num">' + data.calls + "</td></tr>"
      )
      .join("");
    tableBox.innerHTML =
      "<table><tr><th>Способ</th><th class='num'>Токенов</th>" +
      "<th class='num'>Секунд</th><th class='num'>Вызовов API</th></tr>" + rows + "</table>";
  }

  async function post(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.json();
  }

  runButton.onclick = async () => {
    const question = questionBox.value.trim();
    const keys = selected();
    if (!question || !keys.length) return;

    runButton.disabled = true;
    grid.innerHTML = "";
    tableBox.innerHTML = "";
    verdictBox.className = "verdict hidden";

    const results = [];
    await Promise.all(
      keys.map(async (key) => {
        const card = makeCard(key);
        try {
          const data = await post("/api/solve", { question, strategy: key });
          fillCard(card, key, data);
          if (!data.error) results.push([key, data]);
        } catch (err) {
          card.querySelector(".answer").textContent = "Ошибка запроса: " + err;
        }
      })
    );

    results.sort((a, b) => keys.indexOf(a[0]) - keys.indexOf(b[0]));
    showTable(results);

    if (judgeBox.checked && results.length > 1) {
      verdictBox.className = "verdict";
      verdictBox.innerHTML = "<h2>Судья сравнивает ответы…</h2>";
      const verdict = await post("/api/judge", {
        question,
        answers: results.map(([key, data]) => ({ strategy: key, text: data.text })),
      });

      const place = (key, index) =>
        "<div>" + (index + 1) + ". " + STRATEGIES[key].title + "</div>";
      verdictBox.innerHTML =
        "<h2>Вердикт судьи" +
        (verdict.winner ? '<span class="badge">' + STRATEGIES[verdict.winner].title + "</span>" : "") +
        "</h2>" +
        (verdict.ranking || []).map(place).join("") +
        '<div class="metrics">' + (verdict.comment || verdict.raw || "") + "</div>";

      if (verdict.winner) {
        const card = document.getElementById("card-" + verdict.winner);
        if (card) card.classList.add("winner");
      }
    }

    runButton.disabled = false;
  };
</script>
</body>
</html>
'''


def build_page():
    """Подставляет в страницу список способов и задачу по умолчанию из strategies.py."""
    meta = {key: {"title": value["title"], "note": value["note"]}
            for key, value in STRATEGIES.items()}
    html = PAGE.replace("__STRATEGIES__", json.dumps(meta, ensure_ascii=False))
    html = html.replace("__QUESTION__", json.dumps(DEFAULT_QUESTION, ensure_ascii=False))
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
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length))

        if self.path == "/api/solve":
            strategy = STRATEGIES.get(payload.get("strategy"))
            if not strategy:
                self._json({"error": True, "text": "Неизвестный способ", "tokens": 0,
                            "seconds": 0, "calls": 0, "extra": {}})
                return
            print(f"→ {strategy['title']}")
            try:
                self._json(strategy["run"](payload["question"]))
            except (RuntimeError, SystemExit) as error:   # ключ/сеть/API — показываем в карточке
                self._json({"error": True, "text": f"Ошибка: {error}", "tokens": 0,
                            "seconds": 0, "calls": 0, "extra": {}})

        elif self.path == "/api/judge":
            print("→ судья")
            try:
                self._json(judge(payload["question"], payload["answers"]))
            except (RuntimeError, SystemExit) as error:
                self._json({"winner": None, "ranking": [], "comment": f"Ошибка: {error}"})

        else:
            self._reply(404, "text/plain; charset=utf-8", b"not found")

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
