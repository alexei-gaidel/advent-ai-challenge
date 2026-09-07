"""Веб-чат с агентами: панели рядом, у каждой свои настройки и своя память.

Запуск:
    python3 web.py     → откроется http://localhost:8006

Сервер намеренно тонкий: он не собирает запрос к LLM и не разбирает ответ,
а только передаёт текст нужному агенту и отдаёт то, что тот вернул.
"""

import json
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent as agents
import toon
from providers import CATALOG

PORT = 8006

PAGE = '''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Агенты — веб-чат</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#6b7280;
          --card:#f4f4f5; --accent:#2563eb; --border:#e4e4e7; --ok:#16a34a; --bad:#ef4444; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa;
            --card:#27272a; --accent:#3b82f6; --border:#3f3f46; --ok:#22c55e; --bad:#f87171; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 -apple-system,
         BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
  .wrap { max-width:1400px; margin:0 auto; padding:20px 16px 40px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:16px; }
  .bar { display:flex; gap:8px; margin-bottom:18px; }
  .bar input { flex:1; padding:11px 14px; font:inherit; border-radius:10px;
               border:1px solid var(--border); background:var(--card); color:var(--fg); }
  .bar input:focus { outline:2px solid var(--accent); outline-offset:-1px; }
  button { padding:10px 18px; font-size:14px; border:0; border-radius:9px;
           background:var(--accent); color:#fff; cursor:pointer; white-space:nowrap; }
  button.ghost { background:transparent; color:var(--muted); border:1px solid var(--border); }
  button:disabled { opacity:.5; cursor:default; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(380px, 1fr)); gap:14px; }
  .panel { border:1px solid var(--border); border-radius:12px; background:var(--card);
           display:flex; flex-direction:column; min-height:420px; }
  .head { padding:12px 14px; border-bottom:1px solid var(--border); }
  .head .title { display:flex; align-items:center; gap:8px; }
  .head .title input { flex:1; font:inherit; font-weight:600; background:transparent;
                       border:0; color:var(--fg); padding:2px 0; }
  .head .title input:focus { outline:none; border-bottom:1px solid var(--accent); }
  .head .stats { color:var(--muted); font-size:12px; margin-top:5px; }
  .head .stats b { color:var(--fg); font-weight:600; }
  details.cfg { border-bottom:1px solid var(--border); font-size:13px; }
  details.cfg summary { cursor:pointer; padding:9px 14px; color:var(--accent); }
  .cfg .body { padding:0 14px 12px; display:grid; gap:9px; }
  .row { display:flex; align-items:center; gap:8px; }
  .row label { color:var(--muted); min-width:98px; font-size:12.5px; }
  .row select, .row input[type=number], .row input[type=text] {
      flex:1; padding:6px 9px; font:inherit; font-size:13px; border-radius:7px;
      border:1px solid var(--border); background:var(--bg); color:var(--fg); }
  .row input[type=range] { flex:1; }
  textarea { width:100%; padding:8px 10px; font:inherit; font-size:13px; resize:vertical;
             border-radius:8px; border:1px solid var(--border); background:var(--bg);
             color:var(--fg); }
  .seg { display:flex; border:1px solid var(--border); border-radius:7px; overflow:hidden; }
  .seg button { flex:1; background:var(--bg); color:var(--muted); border-radius:0;
                padding:6px 0; font-size:13px; }
  .seg button.on { background:var(--accent); color:#fff; }
  .feed { flex:1; overflow-y:auto; padding:12px 14px; display:flex; flex-direction:column;
          gap:10px; max-height:520px; }
  .msg { padding:9px 12px; border-radius:10px; white-space:pre-wrap; word-wrap:break-word;
         font-size:14px; }
  .msg.user { background:var(--accent); color:#fff; margin-left:12%; }
  .msg.bot { background:var(--bg); border:1px solid var(--border); margin-right:6%; }
  .msg.err { background:var(--bg); border:1px solid var(--bad); color:var(--bad); }
  .meta { color:var(--muted); font-size:11.5px; margin-top:6px; }
  .ask { display:flex; gap:7px; padding:11px 14px; border-top:1px solid var(--border); }
  .ask input { flex:1; padding:9px 12px; font:inherit; font-size:14px; border-radius:9px;
               border:1px solid var(--border); background:var(--bg); color:var(--fg); }
  .tools { display:flex; gap:6px; padding:0 14px 12px; }
  .tools button { font-size:12px; padding:6px 11px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Веб-чат с агентами</h1>
  <div class="sub">Каждая панель — отдельный агент на сервере: своя память, свои настройки, свои счётчики</div>

  <div class="bar">
    <input id="broadcast" placeholder="Спросить сразу все панели…">
    <button id="send-all">Отправить всем</button>
    <button id="add" class="ghost">+ ещё чат</button>
  </div>

  <div class="grid" id="grid"></div>
</div>

<script>
  const CATALOG = __CATALOG__;
  const ROLES = __ROLES__;
  const SAMPLE = __SAMPLE__;

  const grid = document.getElementById("grid");
  const panels = new Map();

  const api = async (path, body) => {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return res.json();
  };

  const esc = (text) => {
    const d = document.createElement("div");
    d.textContent = text;
    return d.innerHTML;
  };

  function statsLine(snapshot) {
    const s = snapshot.stats;
    return "вызовов: <b>" + s.calls + "</b> · токенов: <b>" +
      (s.prompt_tokens + s.completion_tokens) + "</b> (" + s.prompt_tokens + " в запрос, " +
      s.completion_tokens + " в ответ) · стоимость: <b>$" + s.cost.toFixed(6) +
      "</b> · в памяти: <b>" + snapshot.messages + "</b> сообщ.";
  }

  function build(snapshot) {
    const st = snapshot.settings;
    const panel = document.createElement("div");
    panel.className = "panel";
    panel.innerHTML = `
      <div class="head">
        <div class="title">
          <input class="name" value="${esc(st.name)}">
          <button class="ghost close" title="Закрыть панель">✕</button>
        </div>
        <div class="stats">${statsLine(snapshot)}</div>
      </div>
      <details class="cfg">
        <summary>Настройки агента</summary>
        <div class="body">
          <div class="row"><label>Модель</label><select class="model">
            ${CATALOG.map((m) => `<option value="${m.id}" ${m.id === st.model ? "selected" : ""}>${m.label}${m.size !== "—" ? " · " + m.size : ""}</option>`).join("")}
          </select></div>
          <div class="row"><label>Роль</label><select class="role">
            ${Object.entries(ROLES).map(([k, r]) => `<option value="${k}" ${k === st.role ? "selected" : ""}>${r.title}</option>`).join("")}
          </select></div>
          <textarea class="system" rows="3" placeholder="system_prompt">${esc(st.system_prompt)}</textarea>
          <div class="row"><label>Температура</label>
            <input type="range" class="temp" min="0" max="2" step="0.1" value="${st.temperature}">
            <span class="temp-val">${st.temperature}</span></div>
          <div class="row"><label>max_tokens</label>
            <input type="number" class="maxtok" min="16" max="4000" value="${st.max_tokens}"></div>
          <div class="row"><label>stop</label>
            <input type="text" class="stop" value="${esc(st.stop)}" placeholder="через | "></div>
          <div class="row"><label>Память</label>
            <input type="checkbox" class="mem" ${st.memory ? "checked" : ""}>
            <span style="color:var(--muted);font-size:12.5px">последние</span>
            <input type="number" class="depth" min="0" max="40" value="${st.memory_depth}" style="max-width:70px">
            <span style="color:var(--muted);font-size:12.5px">сообщ.</span></div>
          <div class="row"><label>Данные</label>
            <div class="seg">
              <button class="fmt" data-fmt="json">JSON</button>
              <button class="fmt" data-fmt="toon">TOON</button>
            </div>
            <button class="ghost sample" style="font-size:12px;padding:6px 10px">пример</button></div>
          <textarea class="data" rows="3" placeholder="JSON, который уйдёт в промпт как контекст">${esc(st.data)}</textarea>
          <div class="savings" style="color:var(--muted);font-size:12px"></div>
        </div>
      </details>
      <div class="feed"></div>
      <div class="tools">
        <button class="ghost reset">Сбросить диалог</button>
        <button class="ghost cloneme">Клонировать</button>
      </div>
      <div class="ask">
        <input class="q" placeholder="Спросить этого агента…">
        <button class="go">→</button>
      </div>`;
    grid.append(panel);

    const q = (sel) => panel.querySelector(sel);
    const state = { id: snapshot.id, panel, settings: { ...st } };
    panels.set(snapshot.id, state);

    const paintFmt = () => {
      panel.querySelectorAll(".fmt").forEach((b) =>
        b.classList.toggle("on", b.dataset.fmt === state.settings.data_format));
      showSavings();
    };

    function showSavings() {
      const box = q(".savings");
      const raw = q(".data").value.trim();
      if (!raw) { box.textContent = ""; return; }
      try {
        JSON.parse(raw);
      } catch (e) {
        box.innerHTML = '<span style="color:var(--bad)">не разбирается как JSON</span>';
        return;
      }
      api("/api/savings", { data: raw }).then((s) => {
        if (s.error) { box.textContent = ""; return; }
        box.textContent = "TOON " + s.toon_chars + " симв. против JSON " + s.json_chars +
          " (компактный " + s.json_compact_chars + ") — короче на " + s.percent +
          "% / " + s.percent_compact + "%";
      });
    }

    const push = (settings) => {
      Object.assign(state.settings, settings);
      return api(`/api/agents/${state.id}/settings`, settings);
    };

    q(".name").onchange = (e) => push({ name: e.target.value });
    q(".model").onchange = (e) => push({ model: e.target.value });
    q(".role").onchange = (e) => {
      const role = e.target.value;
      q(".system").value = ROLES[role].prompt;
      push({ role, system_prompt: ROLES[role].prompt });
    };
    q(".system").onchange = (e) => push({ system_prompt: e.target.value });
    q(".temp").oninput = (e) => { q(".temp-val").textContent = e.target.value; };
    q(".temp").onchange = (e) => push({ temperature: Number(e.target.value) });
    q(".maxtok").onchange = (e) => push({ max_tokens: Number(e.target.value) });
    q(".stop").onchange = (e) => push({ stop: e.target.value });
    q(".mem").onchange = (e) => push({ memory: e.target.checked });
    q(".depth").onchange = (e) => push({ memory_depth: Number(e.target.value) });
    q(".data").onchange = (e) => { push({ data: e.target.value }); showSavings(); };
    q(".sample").onclick = () => {
      q(".data").value = SAMPLE;
      push({ data: SAMPLE });
      showSavings();
    };
    panel.querySelectorAll(".fmt").forEach((b) => {
      b.onclick = () => { push({ data_format: b.dataset.fmt }); paintFmt(); };
    });

    q(".close").onclick = () => {
      api(`/api/agents/${state.id}/close`);
      panels.delete(state.id);
      panel.remove();
    };
    q(".reset").onclick = async () => {
      const snap = await api(`/api/agents/${state.id}/reset`);
      q(".feed").innerHTML = "";
      q(".stats").innerHTML = statsLine(snap);
    };
    q(".cloneme").onclick = async () => build(await api("/api/agents", { clone_of: state.id }));

    q(".go").onclick = () => ask(state.id, q(".q").value);
    q(".q").onkeydown = (e) => { if (e.key === "Enter") ask(state.id, q(".q").value); };

    paintFmt();
    return state;
  }

  async function ask(id, text) {
    text = (text || "").trim();
    if (!text) return;
    const state = panels.get(id);
    const panel = state.panel;
    const feed = panel.querySelector(".feed");
    panel.querySelector(".q").value = "";

    feed.insertAdjacentHTML("beforeend", '<div class="msg user">' + esc(text) + "</div>");
    const pending = document.createElement("div");
    pending.className = "msg bot";
    pending.textContent = "…думаю";
    feed.append(pending);
    feed.scrollTop = feed.scrollHeight;

    let data;
    try {
      data = await api(`/api/agents/${id}/ask`, { text });
    } catch (err) {
      pending.className = "msg err";
      pending.textContent = "Ошибка запроса: " + err;
      return;
    }

    if (data.error) {
      pending.className = "msg err";
      pending.textContent = data.text;
      return;
    }

    pending.innerHTML = esc(data.text) +
      '<div class="meta">' + data.seconds + " c · " + data.prompt_tokens + " → " +
      data.completion_tokens + " токенов" +
      (data.reasoning_tokens ? " (из них " + data.reasoning_tokens + " на рассуждение)" : "") +
      (data.cost === null ? "" : " · $" + data.cost) + "</div>";
    panel.querySelector(".stats").innerHTML = statsLine(data.agent);
    feed.scrollTop = feed.scrollHeight;
  }

  document.getElementById("add").onclick = async () => build(await api("/api/agents"));
  document.getElementById("send-all").onclick = () => {
    const input = document.getElementById("broadcast");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    [...panels.keys()].forEach((id) => ask(id, text));
  };
  document.getElementById("broadcast").onkeydown = (e) => {
    if (e.key === "Enter") document.getElementById("send-all").click();
  };

  (async () => {
    build(await api("/api/agents"));
    build(await api("/api/agents"));
  })();
</script>
</body>
</html>
'''


def build_page():
    """Подставляет в страницу каталог моделей, роли и пример данных."""
    roles = {key: {"title": value["title"], "prompt": value["prompt"]}
             for key, value in agents.ROLES.items()}
    html = PAGE.replace("__CATALOG__", json.dumps(CATALOG, ensure_ascii=False))
    html = html.replace("__ROLES__", json.dumps(roles, ensure_ascii=False))
    html = html.replace("__SAMPLE__", json.dumps(
        json.dumps(toon.SAMPLE, ensure_ascii=False, indent=2), ensure_ascii=False))
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
            payload = json.loads(self.rfile.read(length) or b"{}")
            parts = self.path.strip("/").split("/")   # api / agents / {id} / {action}

            if self.path == "/api/agents":
                if payload.get("clone_of"):
                    created = agents.clone(payload["clone_of"])
                else:
                    created = agents.create(payload.get("settings"))
                print(f"→ создан {created.id} ({created.settings['name']})")
                self._json(created.snapshot())

            elif self.path == "/api/savings":
                self._json(toon.savings(json.loads(payload["data"])))

            elif len(parts) == 4 and parts[0] == "api" and parts[1] == "agents":
                agent = agents.get(parts[2])
                action = parts[3]

                if action == "ask":
                    print(f"→ {agent.settings['name']}: {agent.settings['model']}")
                    self._json(agent.ask(payload["text"]))
                elif action == "settings":
                    self._json(agent.update(payload))
                elif action == "reset":
                    self._json(agent.reset())
                elif action == "close":
                    agents.close(parts[2])
                    self._json({"closed": parts[2]})
                else:
                    self._reply(404, "text/plain; charset=utf-8", b"not found")

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
    print(f"Моделей в каталоге: {len(CATALOG)}\nОткрой {url}  (Ctrl+C — остановить)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")


if __name__ == "__main__":
    main()
