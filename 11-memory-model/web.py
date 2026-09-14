"""Веб-чат с агентами и явной моделью памяти из трёх слоёв.

Запуск:
    python3 web.py     → откроется http://localhost:8011

Сервер намеренно тонкий: он не собирает запрос к LLM и не разбирает ответ,
а только передаёт текст нужному агенту и отдаёт то, что тот вернул.
"""

import json
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent as agents
import storage
import toon
from memory import LAYERS
from providers import CATALOG

PORT = 8011

PAGE = '''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Модель памяти агента</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#6b7280;
          --card:#f4f4f5; --accent:#2563eb; --border:#e4e4e7; --ok:#16a34a; --bad:#ef4444; --warn:#f59e0b; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa;
            --card:#27272a; --accent:#3b82f6; --border:#3f3f46; --ok:#22c55e; --bad:#f87171; --warn:#fbbf24; }
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
  .memtable { width:100%; border-collapse:collapse; margin-top:6px; font-size:12px; }
  .memtable td { padding:3px 6px; border-bottom:1px solid var(--border); vertical-align:top; }
  .memtable td.k { color:var(--muted); white-space:nowrap; width:1%; }
  .memtable td.x { width:1%; color:var(--muted); cursor:pointer; }
  .memtable td.x:hover { color:var(--bad); }
  .layer { border:1px solid var(--border); border-radius:9px; padding:9px 11px; }
  .layer.off { opacity:.45; }
  .layer h3 { font-size:12.5px; margin:0; display:flex; align-items:center; gap:7px; }
  .layer .hint { color:var(--muted); font-size:11.5px; margin-top:3px; }
  .props { padding:9px 14px; border-bottom:1px solid var(--border);
           display:flex; flex-direction:column; gap:6px; }
  .prop { display:flex; align-items:center; gap:7px; font-size:12.5px;
          background:var(--bg); border:1px solid var(--warn); border-radius:8px;
          padding:6px 9px; }
  .prop .body { flex:1; }
  .prop button { font-size:12px; padding:4px 9px; }
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
  <div class="sub" id="dbline">Каждая панель — отдельный агент на сервере; три слоя памяти: краткосрочная, рабочая и долговременная — включаются независимо</div>

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
  const LAYERS = __LAYERS__;

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

  function roleOptions(current) {
    const group = (custom) =>
      Object.entries(ROLES)
        .filter(([, r]) => Boolean(r.custom) === custom)
        .map(([k, r]) => `<option value="${k}" ${k === current ? "selected" : ""}>${r.title}</option>`)
        .join("");
    const mine = group(true);
    return "<optgroup label='Встроенные'>" + group(false) + "</optgroup>" +
           (mine ? "<optgroup label='Мои роли'>" + mine + "</optgroup>" : "");
  }

  function refreshRoles() {
    // Роль, созданная в одной панели, должна появиться во всех остальных.
    panels.forEach((state) => {
      const select = state.panel.querySelector(".role");
      select.innerHTML = roleOptions(state.settings.role);
    });
  }

  function pushTo(state, settings) {
    Object.assign(state.settings, settings);
    return api(`/api/agents/${state.id}/settings`, settings);
  }
  const push = pushTo;

  const esc = (text) => {
    const d = document.createElement("div");
    d.textContent = text;
    return d.innerHTML;
  };

  function statsLine(snapshot) {
    const s = snapshot.stats;
    const l = snapshot.layers;
    return "вызовов: <b>" + s.calls + "</b> · токенов: <b>" +
      (s.prompt_tokens + s.completion_tokens) + "</b> · стоимость: <b>$" +
      s.cost.toFixed(6) + "</b>" +
      (s.router_calls ? " · роутер: <b>" + s.router_calls + "</b> вызовов на " +
        s.router_tokens + " токенов" : "") +
      "<br>память → краткосрочная: <b>" + l.short.in_context + "</b> из " + l.short.count +
      " сообщ. · рабочая: <b>" + Object.keys(l.working.items).length + "</b> зап. (" +
      l.working.chars + " симв.) · долговременная: <b>" +
      Object.keys(l.long.items).length + "</b> зап. (" + l.long.chars + " симв.)";
  }

  function memRows(items, layer, onDelete) {
    const keys = Object.keys(items);
    if (!keys.length) return '<div class="hint">пусто</div>';
    return '<table class="memtable">' + keys.map((k) => {
      const item = items[k];
      const value = typeof item === "string" ? item : item.value;
      const kind = typeof item === "string" ? "" : item.kind;
      return '<tr><td class="k">' + esc(k) + (kind ? " <i>· " + esc(kind) + "</i>" : "") +
        "</td><td>" + esc(value) + '</td><td class="x" data-layer="' + layer +
        '" data-key="' + esc(k) + '">✕</td></tr>';
    }).join("") + "</table>";
  }

  function showLayers(state, snapshot) {
    const box = state.panel.querySelector(".layers");
    const l = snapshot.layers;
    const st = snapshot.settings;
    box.innerHTML = `
      <div class="layer ${st.use_short ? "" : "off"}">
        <h3><input type="checkbox" class="use" data-k="use_short" ${st.use_short ? "checked" : ""}>
          ${LAYERS.short.title}</h3>
        <div class="hint">${LAYERS.short.holds} · в модель уходит ${l.short.in_context}
          из ${l.short.count} сообщений</div>
      </div>
      <div class="layer ${st.use_working ? "" : "off"}">
        <h3><input type="checkbox" class="use" data-k="use_working" ${st.use_working ? "checked" : ""}>
          ${LAYERS.working.title}</h3>
        <div class="hint">${LAYERS.working.life}</div>
        ${memRows(l.working.items, "working")}
      </div>
      <div class="layer ${st.use_long ? "" : "off"}">
        <h3><input type="checkbox" class="use" data-k="use_long" ${st.use_long ? "checked" : ""}>
          ${LAYERS.long.title}</h3>
        <div class="hint">${LAYERS.long.scope} · ${LAYERS.long.life}</div>
        ${memRows(l.long.items, "long")}
      </div>`;

    box.querySelectorAll(".use").forEach((input) => {
      input.onchange = async () =>
        applySnapshot(state, await push(state, { [input.dataset.k]: input.checked }), false);
    });
    box.querySelectorAll(".memtable td.x").forEach((cell) => {
      cell.onclick = async () => applySnapshot(state, await api(
        `/api/agents/${state.id}/forget`,
        { layer: cell.dataset.layer, key: cell.dataset.key }), false);
    });
  }

  function showProposals(state, snapshot) {
    const bar = state.panel.querySelector(".props");
    const list = snapshot.proposals || [];
    if (!list.length) { bar.style.display = "none"; return; }
    bar.style.display = "flex";
    bar.innerHTML = list.map((p) =>
      '<div class="prop" data-id="' + p.id + '"><div class="body">→ ' +
      LAYERS[p.layer].title.toLowerCase() + ": <b>" + esc(p.key) + "</b> = " +
      esc(p.value) + (p.kind ? " <i>(" + esc(p.kind) + ")</i>" : "") + "</div>" +
      '<button data-do="accept" title="Запомнить">✓</button>' +
      '<button class="ghost" data-do="move" title="Положить в другой слой">⇄</button>' +
      '<button class="ghost" data-do="reject" title="Не запоминать">✕</button></div>').join("");

    bar.querySelectorAll(".prop button").forEach((button) => {
      button.onclick = async () => {
        const card = button.closest(".prop");
        const proposal = list.find((p) => p.id === card.dataset.id);
        const action = button.dataset.do;
        const body = { proposal: proposal.id, action: action === "move" ? "accept" : action };
        if (action === "move") body.layer = proposal.layer === "long" ? "working" : "long";
        applySnapshot(state, await api(`/api/agents/${state.id}/decide`, body), false);
      };
    });
  }

  function applySnapshot(state, snapshot, repaintFeed) {
    Object.assign(state.settings, snapshot.settings);
    state.panel.querySelector(".stats").innerHTML = statsLine(snapshot);
    showLayers(state, snapshot);
    showProposals(state, snapshot);
    if (repaintFeed) {
      state.panel.querySelector(".feed").innerHTML = "";
      paintHistory(state, snapshot.history);
    }
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
          <div class="row"><label>Роль</label>
            <select class="role">${roleOptions(st.role)}</select>
            <button class="ghost newrole" title="Создать свою роль" style="padding:6px 10px">+</button>
            <button class="ghost delrole" title="Удалить свою роль" style="padding:6px 10px">✕</button></div>
          <div class="rolebox" style="display:none; gap:7px; padding:9px;
               border:1px solid var(--border); border-radius:8px">
            <input type="text" class="rt" placeholder="Название роли — например, «Рефакторинг»">
            <textarea class="rp" rows="3" placeholder="Описание для system_prompt: кем модель должна себя считать и как отвечать"></textarea>
            <div class="row" style="gap:6px">
              <button class="rsave">Создать роль</button>
              <button class="ghost rcancel">Отмена</button>
            </div>
          </div>
          <textarea class="system" rows="3" placeholder="system_prompt">${esc(st.system_prompt)}</textarea>
          <div class="row"><label>Температура</label>
            <input type="range" class="temp" min="0" max="2" step="0.1" value="${st.temperature}">
            <span class="temp-val">${st.temperature}</span></div>
          <div class="row"><label>max_tokens</label>
            <input type="number" class="maxtok" min="16" max="4000" value="${st.max_tokens}"></div>
          <div class="row"><label>stop</label>
            <input type="text" class="stop" value="${esc(st.stop)}" placeholder="через | "></div>
          <div class="row"><label>Задача</label>
            <input type="text" class="task" value="${esc(st.task)}" placeholder="название текущей задачи">
            <button class="ghost newtask" style="padding:6px 10px" title="Очистить рабочую память">Новая</button></div>
          <div class="row"><label>Окно диалога</label>
            <input type="number" class="keep" min="0" max="40" value="${st.keep_last}" style="max-width:70px">
            <span style="color:var(--muted);font-size:12.5px">последних сообщений</span></div>
          <div class="row"><label>Роутер</label>
            <input type="checkbox" class="router" ${st.router ? "checked" : ""}>
            <span style="color:var(--muted);font-size:12.5px">предлагает, что запомнить</span></div>
          <div class="layers"></div>
          <div class="row" style="gap:6px">
            <select class="rl" style="max-width:130px">
              <option value="working">в рабочую</option>
              <option value="long">в долговременную</option>
            </select>
            <input type="text" class="rk" placeholder="ключ" style="max-width:110px">
            <input type="text" class="rv" placeholder="значение">
            <button class="rsave" style="padding:6px 12px">+</button></div>
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
      <div class="props" style="display:none"></div>
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

    const push = (settings) => pushTo(state, settings);

    q(".name").onchange = (e) => push(state, { name: e.target.value });
    q(".model").onchange = (e) => push(state, { model: e.target.value });
    q(".role").onchange = (e) => {
      const role = e.target.value;
      q(".system").value = ROLES[role].prompt;
      push(state, { role, system_prompt: ROLES[role].prompt });
    };
    q(".system").onchange = (e) => push(state, { system_prompt: e.target.value });

    const box = q(".rolebox");
    q(".newrole").onclick = () => { box.style.display = box.style.display === "none" ? "grid" : "none"; };
    q(".rcancel").onclick = () => { box.style.display = "none"; };
    q(".rsave").onclick = async () => {
      const res = await api("/api/roles", { title: q(".rt").value, prompt: q(".rp").value });
      if (res.error) { alert(res.text); return; }
      Object.assign(ROLES, res.roles);
      q(".rt").value = q(".rp").value = "";
      box.style.display = "none";
      state.settings.role = res.key;
      refreshRoles();
      q(".system").value = ROLES[res.key].prompt;
      push(state, { role: res.key, system_prompt: ROLES[res.key].prompt });
    };
    q(".delrole").onclick = async () => {
      const role = q(".role").value;
      if (!ROLES[role] || !ROLES[role].custom) { alert("Удалять можно только свои роли"); return; }
      const res = await api("/api/roles/delete", { key: role });
      if (res.error) { alert(res.text); return; }
      for (const key of Object.keys(ROLES)) if (!res.roles[key]) delete ROLES[key];
      panels.forEach((s) => { if (s.settings.role === role) s.settings.role = "assistant"; });
      refreshRoles();
    };
    q(".temp").oninput = (e) => { q(".temp-val").textContent = e.target.value; };
    q(".temp").onchange = (e) => push(state, { temperature: Number(e.target.value) });
    q(".maxtok").onchange = (e) => push(state, { max_tokens: Number(e.target.value) });
    q(".stop").onchange = (e) => push(state, { stop: e.target.value });
    q(".mem").onchange = (e) => push(state, { memory: e.target.checked });
    q(".keep").onchange = (e) => push(state, { keep_last: Number(e.target.value) });
    q(".task").onchange = (e) => push(state, { task: e.target.value });
    q(".router").onchange = (e) => push(state, { router: e.target.checked });
    q(".newtask").onclick = async () => {
      const snap = await api(`/api/agents/${state.id}/new_task`, { title: q(".task").value });
      q(".feed").insertAdjacentHTML("beforeend",
        '<div class="meta" style="text-align:center">— новая задача: рабочая память очищена —</div>');
      applySnapshot(state, snap, false);
    };
    q(".rsave").onclick = async () => {
      const snap = await api(`/api/agents/${state.id}/remember`,
        { layer: q(".rl").value, key: q(".rk").value, value: q(".rv").value });
      if (snap.error) { alert(snap.text); return; }
      q(".rk").value = q(".rv").value = "";
      applySnapshot(state, snap, false);
    };
    q(".forkme").onclick = async () => {
      const snap = await api(`/api/agents/${state.id}/fork`, {});
      applySnapshot(state, snap, true);
    };
    q(".data").onchange = (e) => { push(state, { data: e.target.value }); showSavings(); };
    q(".sample").onclick = () => {
      q(".data").value = SAMPLE;
      push(state, { data: SAMPLE });
      showSavings();
    };
    panel.querySelectorAll(".fmt").forEach((b) => {
      b.onclick = () => { push(state, { data_format: b.dataset.fmt }); paintFmt(); };
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
    showLayers(state, snapshot);
    showProposals(state, snapshot);
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
    applySnapshot(state, data.agent, false);

    if (data.proposed && data.proposed.length) {
      pending.insertAdjacentHTML("beforebegin",
        '<div class="meta" style="text-align:center">— роутер предлагает запомнить: ' +
        data.proposed.length + " —</div>");
    }
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

  function paintHistory(state, history) {
    if (!history || !history.length) return;
    const feed = state.panel.querySelector(".feed");
    feed.insertAdjacentHTML("beforeend",
      '<div class="meta" style="text-align:center">— поднято из базы: ' +
      history.length + " сообщ. —</div>");
    for (const m of history) {
      feed.insertAdjacentHTML("beforeend",
        '<div class="msg ' + (m.role === "user" ? "user" : "bot") + '">' +
        esc(m.content) + "</div>");
    }
    feed.scrollTop = feed.scrollHeight;
  }

  (async () => {
    const state = await (await fetch("/api/state")).json();

    if (state.agents.length) {
      // Продолжаем с того же места: панели, настройки и переписка из прошлой сессии.
      for (const snapshot of state.agents) {
        paintHistory(build(snapshot), snapshot.history);
      }
      document.getElementById("dbline").textContent =
        "Восстановлено из " + state.db.path.split("/").pop() + ": агентов " +
        state.db.agents + ", сообщений " + state.db.messages;
    } else {
      build(await api("/api/agents"));
      build(await api("/api/agents"));
    }
  })();
</script>
</body>
</html>
'''


def build_page():
    """Подставляет в страницу каталог моделей, роли и пример данных."""
    roles = agents.all_roles()
    html = PAGE.replace("__CATALOG__", json.dumps(CATALOG, ensure_ascii=False))
    html = html.replace("__ROLES__", json.dumps(roles, ensure_ascii=False))
    html = html.replace("__LAYERS__", json.dumps(LAYERS, ensure_ascii=False))
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
        elif self.path == "/api/state":
            # Что удалось поднять из базы: панели вместе с прошлой перепиской.
            self._json({
                "agents": [a.snapshot() for a in agents.REGISTRY.values()],
                "db": storage.stats(),
            })
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

            elif self.path == "/api/roles":
                print(f"→ новая роль: {payload.get('title')}")
                self._json(agents.add_role(payload["title"], payload["prompt"]))

            elif self.path == "/api/roles/delete":
                self._json(agents.delete_role(payload["key"]))

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
                elif action == "decide":
                    self._json(agent.decide(payload["proposal"], payload["action"],
                                            payload.get("layer")))
                elif action == "remember":
                    self._json(agent.remember(payload["layer"], payload["key"],
                                              payload["value"], payload.get("kind", "знание")))
                elif action == "forget":
                    self._json(agent.forget(payload["layer"], payload["key"]))
                elif action == "new_task":
                    self._json(agent.new_task(payload.get("title", "")))
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
    restored = agents.restore()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"База: {storage.DB_PATH.name} — восстановлено агентов: {restored['agents']}, "
          f"сообщений: {restored['messages']}")
    print(f"Моделей в каталоге: {len(CATALOG)}\nОткрой {url}  (Ctrl+C — остановить)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")


if __name__ == "__main__":
    main()
