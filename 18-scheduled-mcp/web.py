"""Веб-чат с агентами, моделью памяти и профилями пользователей.

Запуск:
    python3 web.py     → откроется http://localhost:8018

Сверху — профили: их несколько, каждый редактируется на месте. У каждой панели
свой выбор профиля, поэтому два агента рядом с разными профилями показывают,
как один и тот же вопрос отвечается по-разному.

Сервер намеренно тонкий: он не собирает запрос к LLM и не разбирает ответ,
а только передаёт текст нужному агенту и отдаёт то, что тот вернул.
"""

import json
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent as agents
import digest
import scheduler
import store
import profiles
import storage
import toon
from memory import LAYERS
from invariants import KINDS as INVARIANT_KINDS
from taskstate import DEFAULT_STAGES
from providers import CATALOG

PORT = 8018

PAGE = '''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Сводка по расписанию</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#6b7280;
          --card:#f4f4f5; --accent:#2563eb; --border:#e4e4e7; --ok:#16a34a; --bad:#ef4444;
          --warn:#f59e0b; --prof:#7c3aed; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#18181b; --fg:#f4f4f5; --muted:#a1a1aa;
            --card:#27272a; --accent:#3b82f6; --border:#3f3f46; --ok:#22c55e; --bad:#f87171;
            --warn:#fbbf24; --prof:#a78bfa; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 -apple-system,
         BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
  .wrap { max-width:1400px; margin:0 auto; padding:20px 16px 40px; }
  h1 { font-size:20px; margin:0 0 4px; }
  h2 { font-size:14px; margin:0; display:flex; align-items:center; gap:10px; }
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
  .layer.profile { border-color:var(--prof); }
  .layer h3 { font-size:12.5px; margin:0; display:flex; align-items:center; gap:7px; }
  .layer .hint { color:var(--muted); font-size:11.5px; margin-top:3px; }
  .inv { border:1px solid var(--bad); border-radius:9px; padding:9px 11px; }
  .inv.off { opacity:.45; border-color:var(--border); }
  .inv h3 { font-size:12.5px; margin:0 0 5px; display:flex; align-items:center; gap:7px; }
  .inv ol { margin:0; padding-left:18px; font-size:12px; }
  .inv li { padding:2px 0; }
  .inv .kind { color:var(--muted); }
  .inv .why { color:var(--muted); font-size:11.5px; }
  .inv .x { color:var(--muted); cursor:pointer; margin-left:5px; }
  .inv .x:hover { color:var(--bad); }
  .inv .add { display:grid; gap:5px; margin-top:7px; }
  .msg.blocked { border-left:3px solid var(--bad); }
  .violation { margin-top:6px; font-size:11.5px; color:var(--bad); }
  .violation details { margin-top:4px; }
  .violation summary { cursor:pointer; }
  .violation pre { white-space:pre-wrap; background:var(--bg); border:1px solid var(--border);
                   border-radius:7px; padding:8px; margin-top:5px; color:var(--muted); }
  .task { padding:10px 14px; border-bottom:1px solid var(--border); font-size:12.5px; }
  .task .row2 { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
  .stages { display:flex; gap:5px; flex-wrap:wrap; margin:7px 0; }
  .stages button { font-size:12px; padding:5px 10px; background:var(--bg);
                   color:var(--muted); border:1px solid var(--border); border-radius:7px; }
  .stages button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
  .stages button.past { color:var(--ok); border-color:var(--ok); }
  .stages button:disabled { opacity:.45; cursor:not-allowed; }
  .market { border:1px solid var(--border); border-radius:12px; background:var(--card);
            padding:14px 16px; margin-bottom:14px; }
  .market h2 { font-size:16px; margin:0 0 8px; display:flex; align-items:center; gap:8px; }
  .market pre { white-space:pre-wrap; font-size:13px; margin:0; }
  .market table { width:100%; border-collapse:collapse; font-size:12.5px; margin-top:8px; }
  .market td { padding:3px 6px; border-bottom:1px solid var(--border); }
  .market td.ok { color:var(--ok); } .market td.bad { color:var(--bad); }
  .market .cols { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  @media (max-width:800px) { .market .cols { grid-template-columns:1fr; } }
  .mcp { border:1px solid var(--border); border-radius:9px; padding:9px 11px; }
  .mcp.off { opacity:.45; }
  .mcp h3 { font-size:12.5px; margin:0 0 5px; display:flex; align-items:center; gap:7px; }
  .mcp .tool { font-size:12px; padding:2px 0; }
  .mcp .tool b { font-weight:600; }
  .mcp .args { color:var(--muted); }
  .toolcall { margin-top:6px; font-size:11.5px; color:var(--muted);
              border-left:2px solid var(--accent); padding-left:8px; }
  .toolcall summary { cursor:pointer; color:var(--accent); }
  .toolcall pre { white-space:pre-wrap; background:var(--bg); border:1px solid var(--border);
                  border-radius:7px; padding:8px; margin-top:5px; }
  .gates { margin:7px 0; font-size:12px; }
  .gates .ghead { color:var(--muted); margin-bottom:3px; }
  .gate.ok { color:var(--ok); }
  .gate.no { color:var(--warn); }
  .gate .why { color:var(--muted); }
  .task .expect { margin-top:5px; color:var(--muted); }
  .task .expect b { color:var(--fg); }
  .task.paused { background:color-mix(in srgb, var(--warn) 12%, transparent); }
  .steps { margin:7px 0 0; padding:0; list-style:none; }
  .steps li { display:flex; gap:7px; align-items:flex-start; padding:2px 0; }
  .steps li.done { color:var(--muted); text-decoration:line-through; }
  .steps li.now { color:var(--fg); font-weight:600; }
  .steps input { margin-top:3px; }
  .advice { margin-top:7px; border:1px solid var(--warn); border-radius:8px;
            padding:7px 9px; display:flex; gap:7px; align-items:center; }
  .advice .body { flex:1; }
  .props { padding:9px 14px; border-bottom:1px solid var(--border);
           display:flex; flex-direction:column; gap:6px; }
  .prop { display:flex; align-items:center; gap:7px; font-size:12.5px;
          background:var(--bg); border:1px solid var(--warn); border-radius:8px;
          padding:6px 9px; }
  .prop.profile { border-color:var(--prof); }
  .prop .body { flex:1; }
  .prop button { font-size:12px; padding:4px 9px; }
  .ask { display:flex; gap:7px; padding:11px 14px; border-top:1px solid var(--border); }
  .ask input { flex:1; padding:9px 12px; font:inherit; font-size:14px; border-radius:9px;
               border:1px solid var(--border); background:var(--bg); color:var(--fg); }
  .tools { display:flex; gap:6px; padding:0 14px 12px; }
  .tools button { font-size:12px; padding:6px 11px; }

  /* профили */
  .profiles { border:1px solid var(--prof); border-radius:12px; padding:12px 14px;
              margin-bottom:18px; background:var(--card); }
  .chips { display:flex; flex-wrap:wrap; gap:6px; margin:10px 0 0; }
  .chip { padding:5px 12px; border-radius:999px; font-size:13px; border:1px solid var(--border);
          background:var(--bg); color:var(--fg); cursor:pointer; }
  .chip.on { border-color:var(--prof); color:var(--prof); font-weight:600; }
  .chip.add { color:var(--muted); border-style:dashed; }
  .editor { display:none; margin-top:12px; gap:8px; }
  .editor.open { display:grid; }
  .editor .row label { min-width:110px; }
  .editor .row input[type=text] { flex:1; }
  .facts { display:grid; gap:5px; }
  .fact { display:flex; gap:6px; }
  .fact input { padding:5px 8px; font:inherit; font-size:12.5px; border-radius:6px;
                border:1px solid var(--border); background:var(--bg); color:var(--fg); }
  .fact input.fk { width:150px; }
  .fact input.fv { flex:1; }
  .fact button { padding:4px 9px; font-size:12px; }
  .preview { white-space:pre-wrap; font-size:12px; color:var(--muted); background:var(--bg);
             border:1px dashed var(--border); border-radius:8px; padding:8px 10px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Персонализированный ассистент</h1>
  <div class="sub" id="dbline">Профиль пользователя — стиль, формат, ограничения, факты — подмешивается в каждый запрос поверх трёх слоёв памяти</div>

  <div class="profiles">
    <h2>Профили пользователей <span style="color:var(--muted);font-weight:400;font-size:12.5px">— несколько; у каждой панели свой выбор</span></h2>
    <div class="chips" id="chips"></div>
    <div class="editor" id="editor"></div>
  </div>

  <div class="bar">
    <input id="broadcast" placeholder="Спросить сразу все панели — один вопрос под разными профилями…">
    <button id="send-all">Отправить всем</button>
    <button id="add" class="ghost">+ ещё чат</button>
  </div>

  <div class="market" id="market"><h2>Сводка собирается…</h2></div>
  <div class="grid" id="grid"></div>
</div>

<script>
  const CATALOG = __CATALOG__;
  const ROLES = __ROLES__;
  const SAMPLE = __SAMPLE__;
  const LAYERS = __LAYERS__;
  const DEFAULT_STAGES = __STAGES__;
  const INVARIANT_KINDS = __KINDS__;
  const FIELDS = __FIELDS__;
  const KINDS = __KINDS__;
  let PROFILES = __PROFILES__;

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
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  };

  // ---------------------------------------------------------------- профили --
  let editing = null;

  function profileOptions(current) {
    return '<option value="">— без профиля —</option>' + Object.values(PROFILES).map((p) =>
      `<option value="${p.id}" ${p.id === current ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  }

  function refreshProfiles() {
    // Профиль правится в одном месте, а подключён ко многим панелям — обновляем все.
    panels.forEach((state) => {
      state.panel.querySelector(".profile").innerHTML = profileOptions(state.settings.profile);
      api(`/api/agents/${state.id}/settings`, {}).then((snap) => applySnapshot(state, snap, false));
    });
  }

  function paintChips() {
    const box = document.getElementById("chips");
    box.innerHTML = Object.values(PROFILES).map((p) =>
      `<button class="chip ${p.id === editing ? "on" : ""}" data-id="${p.id}">${esc(p.name)}</button>`
    ).join("") + '<button class="chip add" data-id="+">+ новый профиль</button>';
    box.querySelectorAll(".chip").forEach((chip) => {
      chip.onclick = async () => {
        if (chip.dataset.id === "+") {
          const name = prompt("Имя нового профиля");
          if (!name) return;
          const res = await api("/api/profiles", { name });
          if (res.error) { alert(res.text); return; }
          PROFILES = res.profiles;
          editing = res.id;
          refreshProfiles();
        } else {
          editing = editing === chip.dataset.id ? null : chip.dataset.id;
        }
        paintChips();
        paintEditor();
      };
    });
  }

  function paintEditor() {
    const box = document.getElementById("editor");
    const p = PROFILES[editing];
    box.classList.toggle("open", Boolean(p));
    if (!p) { box.innerHTML = ""; return; }

    const facts = Object.entries(p.facts || {});
    box.innerHTML = `
      <div class="row"><label>Имя</label><input type="text" class="pf" data-k="name" value="${esc(p.name)}"></div>
      ${Object.entries(FIELDS).map(([k, f]) =>
        `<div class="row"><label title="${esc(f.hint)}">${f.title}</label>
         <input type="text" class="pf" data-k="${k}" value="${esc(p[k])}" placeholder="${esc(f.hint)}"></div>`).join("")}
      <div class="row" style="align-items:flex-start"><label>Факты</label>
        <div class="facts" style="flex:1">
          ${facts.map(([k, v]) => `<div class="fact"><input class="fk" value="${esc(k)}"><input class="fv" value="${esc(v)}"><button class="ghost fdel">✕</button></div>`).join("")}
          <div class="fact"><input class="fk" placeholder="ключ"><input class="fv" placeholder="значение"><button class="ghost fdel" style="visibility:hidden">✕</button></div>
        </div></div>
      <div class="row"><label>В промпт уйдёт</label><div class="preview" style="flex:1">${esc(p.block || "")}</div></div>
      <div class="row" style="gap:6px">
        <button class="psave">Сохранить</button>
        <button class="ghost pdel">Удалить профиль</button>
        <span style="color:var(--muted);font-size:12px">${p.block ? p.block.length + " симв. в каждом запросе" : ""}</span>
      </div>`;

    box.querySelectorAll(".fdel").forEach((b) => { b.onclick = () => b.closest(".fact").remove(); });
    box.querySelector(".psave").onclick = async () => {
      const fields = {};
      box.querySelectorAll(".pf").forEach((i) => { fields[i.dataset.k] = i.value; });
      fields.facts = {};
      box.querySelectorAll(".fact").forEach((row) => {
        const k = row.querySelector(".fk").value.trim();
        const v = row.querySelector(".fv").value.trim();
        if (k && v) fields.facts[k] = v;
      });
      const res = await api(`/api/profiles/${editing}`, fields);
      if (res.error) { alert(res.text); return; }
      PROFILES = res.profiles;
      paintChips();
      paintEditor();
      refreshProfiles();
    };
    box.querySelector(".pdel").onclick = async () => {
      if (!confirm(`Удалить профиль «${p.name}»?`)) return;
      const res = await api(`/api/profiles/${editing}/delete`);
      PROFILES = res.profiles;
      editing = null;
      panels.forEach((s) => { if (!PROFILES[s.settings.profile]) s.settings.profile = ""; });
      paintChips();
      paintEditor();
      refreshProfiles();
    };
  }

  // ------------------------------------------------------------------- роли --
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
    panels.forEach((state) => {
      state.panel.querySelector(".role").innerHTML = roleOptions(state.settings.role);
    });
  }

  function pushTo(state, settings) {
    Object.assign(state.settings, settings);
    return api(`/api/agents/${state.id}/settings`, settings);
  }

  // ----------------------------------------------------------------- панели --
  function statsLine(snapshot) {
    const s = snapshot.stats;
    const l = snapshot.layers;
    const profile = l.profile.item;
    return "вызовов: <b>" + s.calls + "</b> · токенов: <b>" +
      (s.prompt_tokens + s.completion_tokens) + "</b> · стоимость: <b>$" +
      s.cost.toFixed(6) + "</b>" +
      (s.tool_calls ? " · инструментов: <b>" + s.tool_calls + "</b> за " +
        s.tool_rounds + " кругов" : "") +
      (s.violations ? " · <b style=\"color:var(--bad)\">нарушений: " + s.violations +
        "</b>, переписано " + s.rewrites + " (" + s.audit_tokens + " токенов аудита)" : "") +
      (s.router_calls ? " · роутер: <b>" + s.router_calls + "</b> вызовов на " +
        s.router_tokens + " токенов" : "") +
      "<br>профиль: <b style='color:var(--prof)'>" + (profile ? esc(profile.name) : "нет") + "</b>" +
      (profile ? " (" + l.profile.chars + " симв.)" : "") +
      " · краткосрочная: <b>" + l.short.in_context + "</b> из " + l.short.count +
      " · рабочая: <b>" + Object.keys(l.working.items).length + "</b> зап." +
      " · долговременная: <b>" + Object.keys(l.long.items).length + "</b> зап.";
  }

  function memRows(items, layer) {
    const keys = Object.keys(items);
    if (!keys.length) return '<div class="hint">пусто</div>';
    return '<table class="memtable">' + keys.map((k) => {
      const item = items[k];
      const value = typeof item === "string" ? item : item.value;
      const kind = typeof item === "string" ? "" : item.kind;
      const promote = layer === "long" && kind === "решение"
        ? '<span class="x promote" data-key="' + esc(k) +
          '" title="Сделать инвариантом">⇪</span>' : "";
      return '<tr><td class="k">' + esc(k) + (kind ? " <i>· " + esc(kind) + "</i>" : "") +
        "</td><td>" + esc(value) + promote + '</td><td class="x" data-layer="' + layer +
        '" data-key="' + esc(k) + '">✕</td></tr>';
    }).join("") + "</table>";
  }

  function profileRows(profile) {
    if (!profile) return '<div class="hint">не подключён — ответы без персонализации</div>';
    const rows = Object.entries(FIELDS)
      .filter(([k]) => profile[k])
      .map(([k, f]) => '<tr><td class="k">' + f.title + "</td><td>" + esc(profile[k]) + "</td><td></td></tr>");
    const facts = Object.entries(profile.facts || {}).map(([k, v]) =>
      '<tr><td class="k">' + esc(k) + " <i>· факт</i></td><td>" + esc(v) +
      '</td><td class="x" data-layer="profile" data-key="' + esc(k) + '">✕</td></tr>');
    return '<table class="memtable">' + rows.join("") + facts.join("") + "</table>";
  }

  function showTools(state, snapshot) {
    const box = state.panel.querySelector(".mcpbox");
    const info = snapshot.tools || {};
    const list = info.tools || [];
    const st = snapshot.settings;

    box.innerHTML = `
      <div class="mcp ${st.use_tools ? "" : "off"}">
        <h3><input type="checkbox" class="usetools" ${st.use_tools ? "checked" : ""}>
          Инструменты MCP · ${list.length}
          <span style="flex:1"></span>
          <span class="args">${esc(info.server && info.server.name ? info.server.name : "—")}
            ${esc(info.protocol || "")}</span></h3>
        ${info.error ? '<div class="args" style="color:var(--bad)">' + esc(info.error) + "</div>" : ""}
        ${list.map((tool) => {
          const schema = tool.schema || {};
          const required = new Set(schema.required || []);
          const args = Object.entries(schema.properties || {})
            .map(([name, field]) => name + (required.has(name) ? "*" : "") +
                 ": " + (field.type || "?")).join(", ") || "—";
          return '<div class="tool"><b>' + esc(tool.name) + "</b>(" + esc(args) + ")" +
            '<div class="args">' + esc((tool.description || "").slice(0, 90)) + "</div></div>";
        }).join("")}
      </div>`;

    box.querySelector(".usetools").onchange = async (e) =>
      applySnapshot(state, await push(state, { use_tools: e.target.checked }), false);
  }

  function showInvariants(state, snapshot) {
    const box = state.panel.querySelector(".invs");
    const list = snapshot.invariants || [];
    const st = snapshot.settings;

    box.innerHTML = `
      <div class="inv ${st.use_invariants ? "" : "off"}">
        <h3><input type="checkbox" class="useinv" ${st.use_invariants ? "checked" : ""}>
          Инварианты проекта · ${list.length}
          <span style="flex:1"></span>
          <label class="kind" style="font-weight:400"><input type="checkbox" class="useaudit"
            ${st.audit ? "checked" : ""}> аудитор</label></h3>
        ${list.length ? "<ol>" + list.map((x) =>
          '<li><span class="kind">[' + esc(x.kind) + "]</span> " + esc(x.text) +
          '<span class="x" data-id="' + x.id + '">✕</span>' +
          (x.rationale ? '<div class="why">почему: ' + esc(x.rationale) + "</div>" : "") +
          "</li>").join("") + "</ol>"
          : '<div class="why">инвариантов нет — ассистент ничем не ограничен</div>'}
        <div class="add">
          <input type="text" class="itext" placeholder="Что нельзя нарушать">
          <div class="row" style="gap:5px">
            <select class="ikind" style="max-width:140px">
              ${INVARIANT_KINDS.map((k) => '<option>' + k + "</option>").join("")}
            </select>
            <input type="text" class="iwhy" placeholder="почему (необязательно)">
            <button class="iadd" style="padding:6px 12px">+</button>
          </div>
        </div>
      </div>`;

    box.querySelector(".useinv").onchange = async (e) =>
      applySnapshot(state, await push(state, { use_invariants: e.target.checked }), false);
    box.querySelector(".useaudit").onchange = async (e) =>
      applySnapshot(state, await push(state, { audit: e.target.checked }), false);
    box.querySelector(".iadd").onclick = async () => {
      const text = box.querySelector(".itext").value.trim();
      if (!text) return;
      const snap = await api(`/api/agents/${state.id}/invariants/add`, {
        text, kind: box.querySelector(".ikind").value,
        rationale: box.querySelector(".iwhy").value });
      if (snap.error) { alert(snap.text); return; }
      applySnapshot(state, snap, false);
    };
    box.querySelectorAll(".inv .x").forEach((cell) => {
      cell.onclick = async () => applySnapshot(state, await api(
        `/api/agents/${state.id}/invariants/drop`, { id: cell.dataset.id }), false);
    });
  }

  function showLayers(state, snapshot) {
    const box = state.panel.querySelector(".layers");
    const l = snapshot.layers;
    const st = snapshot.settings;
    const layer = (key, cls, body) => `
      <div class="layer ${cls} ${st["use_" + key] ? "" : "off"}">
        <h3><input type="checkbox" class="use" data-k="use_${key}" ${st["use_" + key] ? "checked" : ""}>
          ${LAYERS[key].title}</h3>${body}</div>`;
    box.innerHTML =
      layer("profile", "profile",
        `<div class="hint">${LAYERS.profile.scope} · ${LAYERS.profile.life}</div>` +
        profileRows(l.profile.item)) +
      layer("short", "",
        `<div class="hint">${LAYERS.short.holds} · в модель уходит ${l.short.in_context}
          из ${l.short.count} сообщений</div>`) +
      layer("working", "", `<div class="hint">${LAYERS.working.life}</div>` + memRows(l.working.items, "working")) +
      layer("long", "", `<div class="hint">${LAYERS.long.scope} · ${LAYERS.long.life}</div>` + memRows(l.long.items, "long"));

    box.querySelectorAll(".use").forEach((input) => {
      input.onchange = async () =>
        applySnapshot(state, await pushTo(state, { [input.dataset.k]: input.checked }), false);
    });
    box.querySelectorAll(".memtable .promote").forEach((cell) => {
      cell.onclick = async (event) => {
        event.stopPropagation();
        applySnapshot(state, await api(`/api/agents/${state.id}/invariants/promote`,
          { key: cell.dataset.key }), false);
      };
    });
    box.querySelectorAll(".memtable td.x").forEach((cell) => {
      cell.onclick = async () => {
        const snap = await api(`/api/agents/${state.id}/forget`,
          { layer: cell.dataset.layer, key: cell.dataset.key });
        if (cell.dataset.layer === "profile") await reloadProfiles();
        applySnapshot(state, snap, false);
      };
    });
  }

  async function reloadProfiles() {
    PROFILES = (await api("/api/profiles/list")).profiles;
    paintChips();
    paintEditor();
  }

  const ORDER = ["working", "long", "profile"];

  function gatesHtml(t) {
    const next = t.stages[t.stage_index + 1];
    if (!next) return "";
    const gates = (t.gates && t.gates[next.key]) || [];
    if (!gates.length) return "";
    const rows = gates.map((g) =>
      '<div class="gate ' + (g.ok ? "ok" : "no") + '">' + (g.ok ? "✓" : "○") + " " +
      esc(g.title) + (g.ok ? "" : ' <span class="why">— ' + esc(g.howto) + "</span>") +
      "</div>").join("");
    const blocked = gates.some((g) => !g.ok);
    return '<div class="gates"><div class="ghead">Условия входа в «' + esc(next.title) +
      "»" + (blocked ? " — переход закрыт" : " — выполнены") + "</div>" + rows + "</div>";
  }

  function showTask(state, snapshot) {
    const box = state.panel.querySelector(".task");
    const t = snapshot.task_state;

    if (!t) {
      box.className = "task";
      box.innerHTML = '<div class="row2"><input type="text" class="tt" ' +
        'placeholder="Название задачи" style="flex:1;padding:6px 9px;font:inherit;' +
        'font-size:13px;border-radius:7px;border:1px solid var(--border);' +
        'background:var(--bg);color:var(--fg)">' +
        '<button class="start">Начать задачу</button></div>';
      box.querySelector(".start").onclick = async () =>
        applySnapshot(state, await api(`/api/agents/${state.id}/task/start`,
          { title: box.querySelector(".tt").value || "Без названия" }), false);
      return;
    }

    box.className = "task" + (t.paused ? " paused" : "");
    const steps = t.steps.filter((x) => x.stage === t.stage);
    box.innerHTML = `
      <div class="row2">
        <b>${esc(t.title)}</b>
        <span style="color:var(--muted)">· состояние ${t.block_chars} симв.</span>
        <span style="flex:1"></span>
        <button class="ghost plan">План</button>
        <button class="ghost approve" ${t.plan_approved ? "disabled" : ""}>Утвердить план</button>
        ${t.stage === "validation" ? '<button class="ghost validated" ' +
          (t.validation_passed ? "disabled" : "") + ">Валидация пройдена</button>" : ""}
        <button class="ghost pause">${t.paused ? "Продолжить" : "Пауза"}</button>
        <button class="ghost drop" title="Закрыть задачу">✕</button>
      </div>
      <div class="stages">${t.stages.map((st, i) => {
        const here = st.key === t.stage;
        const past = i < t.stage_index;
        const can = t.allowed.includes(st.key);
        return `<button data-k="${st.key}" class="${here ? "on" : past ? "past" : ""}"
          ${here || !can ? "disabled" : ""} title="${esc(st.goal)}">${i + 1}. ${esc(st.title)}</button>`;
      }).join("")}</div>
      ${gatesHtml(t)}
      <div class="expect">Ожидается: <b>${t.expected.actor === "user" ? "от тебя" : "от агента"}</b>
        — ${esc(t.expected.action)}${t.paused ? " · <b>на паузе</b>" +
          (t.pause_note ? " (" + esc(t.pause_note) + ")" : "") : ""}</div>
      ${steps.length ? '<ul class="steps">' + steps.map((x) => {
        const now = x.text === t.current_step;
        return `<li class="${x.status === "done" ? "done" : now ? "now" : ""}">
          <input type="checkbox" data-t="${esc(x.text)}" ${x.status === "done" ? "checked" : ""}>
          <span>${esc(x.text)}</span></li>`;
      }).join("") + "</ul>" : '<div class="expect">плана ещё нет — нажми «План»</div>'}`;

    box.querySelectorAll(".stages button").forEach((button) => {
      button.onclick = async () => {
        const res = await api(`/api/agents/${state.id}/task/move`, { stage: button.dataset.k });
        // Принудительного перехода нет: показываем, какое условие не выполнено.
        if (res.error) { alert(res.text); return; }
        applySnapshot(state, res, false);
      };
    });
    box.querySelectorAll(".steps input").forEach((input) => {
      input.onchange = async () => applySnapshot(state, await api(
        `/api/agents/${state.id}/task/step`,
        { text: input.dataset.t, status: input.checked ? "done" : "pending" }), false);
    });
    const approve = box.querySelector(".approve");
    if (approve) approve.onclick = async () => {
      const res = await api(`/api/agents/${state.id}/task/approve`, {});
      if (res.error) { alert(res.text); return; }
      applySnapshot(state, res, false);
    };
    const validated = box.querySelector(".validated");
    if (validated) validated.onclick = async () => {
      const res = await api(`/api/agents/${state.id}/task/validated`, {});
      if (res.error) { alert(res.text); return; }
      applySnapshot(state, res, false);
    };
    box.querySelector(".plan").onclick = async () =>
      applySnapshot(state, await api(`/api/agents/${state.id}/task/plan`, {}), false);
    box.querySelector(".pause").onclick = async () => {
      const path = t.paused ? "resume" : "pause";
      const note = t.paused ? "" : (prompt("Заметка к паузе (необязательно):") || "");
      applySnapshot(state, await api(`/api/agents/${state.id}/task/${path}`, { note }), false);
    };
    box.querySelector(".drop").onclick = async () =>
      applySnapshot(state, await api(`/api/agents/${state.id}/task/drop`, {}), false);

    if (snapshot.task_proposal) {
      const a = snapshot.task_proposal;
      const parts = [];
      if (a.step_done && a.step) parts.push("закрыть шаг «" + esc(a.step) + "»");
      if (a.next_stage) parts.push("перейти на «" + esc(a.next_stage) + "»");
      if (a.expected_action) parts.push("ждать: " + esc(a.expected_action));
      const card = document.createElement("div");
      card.className = "advice";
      card.innerHTML = '<div class="body">агент предлагает: ' + parts.join(" · ") +
        (a.reason ? '<div style="color:var(--muted)">' + esc(a.reason) + "</div>" : "") +
        '</div><button data-do="yes">✓</button><button class="ghost" data-do="no">✕</button>';
      box.append(card);
      card.querySelectorAll("button").forEach((button) => {
        button.onclick = async () => applySnapshot(state, await api(
          `/api/agents/${state.id}/task/advice`,
          { accept: button.dataset.do === "yes" }), false);
      });
    }
  }

  function showProposals(state, snapshot) {
    const bar = state.panel.querySelector(".props");
    const list = snapshot.proposals || [];
    if (!list.length) { bar.style.display = "none"; return; }
    bar.style.display = "flex";
    bar.innerHTML = list.map((p) =>
      '<div class="prop ' + p.layer + '" data-id="' + p.id + '"><div class="body">→ ' +
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
        if (action === "move") {
          // ⇄ переставляет по кругу: рабочая → долговременная → профиль → рабочая.
          proposal.layer = ORDER[(ORDER.indexOf(proposal.layer) + 1) % ORDER.length];
          proposal.kind = KINDS[proposal.layer][0];
          showProposals(state, snapshot);
          return;
        }
        const body = { proposal: proposal.id, action, layer: proposal.layer };
        const snap = await api(`/api/agents/${state.id}/decide`, body);
        if (snap.error) { alert(snap.text); return; }
        if (proposal.layer === "profile" && action === "accept") await reloadProfiles();
        applySnapshot(state, snap, false);
      };
    });
  }

  function applySnapshot(state, snapshot, repaintFeed) {
    Object.assign(state.settings, snapshot.settings);
    state.panel.querySelector(".stats").innerHTML = statsLine(snapshot);
    state.panel.querySelector(".profile").value = snapshot.settings.profile || "";
    showTools(state, snapshot);
    showInvariants(state, snapshot);
    showLayers(state, snapshot);
    showProposals(state, snapshot);
    showTask(state, snapshot);
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
          <select class="profile" title="Чей профиль подключён" style="max-width:170px;padding:5px 8px;font:inherit;font-size:13px;border-radius:7px;border:1px solid var(--prof);background:var(--bg);color:var(--fg)">${profileOptions(st.profile)}</select>
          <button class="ghost close" title="Закрыть панель">✕</button>
        </div>
        <div class="stats">${statsLine(snapshot)}</div>
      </div>
      <details class="cfg">
        <summary>Настройки агента и память</summary>
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
            <span style="color:var(--muted);font-size:12.5px">предлагает, что запомнить — в память или в профиль</span></div>
          <div class="mcpbox"></div>
          <div class="invs"></div>
          <div class="layers"></div>
          <div class="row" style="gap:6px">
            <select class="rl" style="max-width:150px">
              <option value="working">в рабочую</option>
              <option value="long">в долговременную</option>
              <option value="profile">в профиль · факт</option>
              <option value="profile:стиль">в профиль · стиль</option>
              <option value="profile:формат">в профиль · формат</option>
              <option value="profile:ограничения">в профиль · ограничения</option>
            </select>
            <input type="text" class="rk" placeholder="ключ" style="max-width:110px">
            <input type="text" class="rv" placeholder="значение">
            <button class="msave" style="padding:6px 12px">+</button></div>
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
      <div class="task"></div>
      <div class="props" style="display:none"></div>
      <div class="feed"></div>
      <div class="tools">
        <button class="ghost reset">Сбросить диалог</button>
        <button class="ghost cloneme">Клонировать</button>
        <button class="ghost check" title="Судья: что из профиля учтено в последнем ответе">Что учтено?</button>
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

    q(".name").onchange = (e) => push({ name: e.target.value });
    q(".profile").onchange = async (e) => {
      const snap = await api(`/api/agents/${state.id}/profile`, { profile: e.target.value });
      if (snap.error) { alert(snap.text); return; }
      q(".feed").insertAdjacentHTML("beforeend",
        '<div class="meta" style="text-align:center">— профиль: ' +
        (PROFILES[e.target.value] ? esc(PROFILES[e.target.value].name) : "нет") + " —</div>");
      applySnapshot(state, snap, false);
    };
    q(".model").onchange = (e) => push({ model: e.target.value });
    q(".role").onchange = (e) => {
      const role = e.target.value;
      q(".system").value = ROLES[role].prompt;
      push({ role, system_prompt: ROLES[role].prompt });
    };
    q(".system").onchange = (e) => push({ system_prompt: e.target.value });

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
      push({ role: res.key, system_prompt: ROLES[res.key].prompt });
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
    q(".temp").onchange = (e) => push({ temperature: Number(e.target.value) });
    q(".maxtok").onchange = (e) => push({ max_tokens: Number(e.target.value) });
    q(".stop").onchange = (e) => push({ stop: e.target.value });
    q(".keep").onchange = (e) => push({ keep_last: Number(e.target.value) });
    q(".task").onchange = (e) => push({ task: e.target.value });
    q(".router").onchange = (e) => push({ router: e.target.checked });
    q(".newtask").onclick = async () => {
      const snap = await api(`/api/agents/${state.id}/new_task`, { title: q(".task").value });
      q(".feed").insertAdjacentHTML("beforeend",
        '<div class="meta" style="text-align:center">— новая задача: рабочая память очищена —</div>');
      applySnapshot(state, snap, false);
    };
    q(".msave").onclick = async () => {
      const [layer, kind] = q(".rl").value.split(":");
      const snap = await api(`/api/agents/${state.id}/remember`,
        { layer, kind: kind || "", key: q(".rk").value, value: q(".rv").value });
      if (snap.error) { alert(snap.text); return; }
      q(".rk").value = q(".rv").value = "";
      if (layer === "profile") await reloadProfiles();
      applySnapshot(state, snap, false);
    };
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
      applySnapshot(state, snap, false);
    };
    q(".cloneme").onclick = async () => build(await api("/api/agents", { clone_of: state.id }));
    q(".check").onclick = async () => {
      const feed = q(".feed");
      const note = document.createElement("div");
      note.className = "meta";
      note.textContent = "…судья читает последний ответ";
      feed.append(note);
      const res = await api(`/api/agents/${state.id}/check`);
      if (res.error) { note.textContent = res.text; return; }
      const ok = res.items.filter((i) => i.ok).length;
      note.innerHTML = "<b>учтено автоматически: " + ok + " из " + res.items.length + "</b>" +
        " (судья: " + res.tokens + " токенов)<br>" + res.items.map((i) =>
          '<span style="color:' + (i.ok ? "var(--ok)" : "var(--bad)") + '">' +
          (i.ok ? "✓" : "✗") + "</span> " + esc(i.requirement) +
          (i.evidence ? ' <span style="opacity:.7">— ' + esc(i.evidence) + "</span>" : "")).join("<br>");
      feed.scrollTop = feed.scrollHeight;
    };

    q(".go").onclick = () => ask(state.id, q(".q").value);
    q(".q").onkeydown = (e) => { if (e.key === "Enter") ask(state.id, q(".q").value); };

    paintFmt();
    showTools(state, snapshot);
    showInvariants(state, snapshot);
    showLayers(state, snapshot);
    showProposals(state, snapshot);
    showTask(state, snapshot);
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

    const m = data.measure;
    pending.innerHTML = esc(data.text) +
      '<div class="meta">' + data.seconds + " c · " + data.prompt_tokens + " → " +
      data.completion_tokens + " токенов" +
      (data.reasoning_tokens ? " (из них " + data.reasoning_tokens + " на рассуждение)" : "") +
      (data.cost === null ? "" : " · $" + data.cost) +
      (data.layer_sizes.profile ? " · профиль " + data.layer_sizes.profile + " симв." : "") +
      "<br>" + m.chars + " симв. · пунктов " + m.bullets + " · эмодзи " + m.emoji +
      " · латиницы " + m.latin + "% · обращение: " + m.address + "</div>";
    applySnapshot(state, data.agent, false);

    if (data.tool_calls && data.tool_calls.length) {
      const box = document.createElement("div");
      box.className = "toolcall";
      box.innerHTML = "🔧 вызовов инструментов: " + data.tool_calls.length +
        data.tool_calls.map((call) =>
          "<details><summary>" + esc(call.name) + "(" +
          esc(JSON.stringify(call.arguments)) + ")</summary><pre>" +
          esc(call.result) + "</pre></details>").join("");
      pending.append(box);
    }

    if (data.audit && data.audit.rewritten) {
      pending.classList.add("blocked");
      const box = document.createElement("div");
      box.className = "violation";
      box.innerHTML = "⛔ ответ переписан: нарушены инварианты " +
        data.audit.violations.map((v) => v.number).join(", ") +
        data.audit.violations.map((v) => "<div>" + esc(v.why) + "</div>").join("") +
        "<details><summary>что было забраковано</summary><pre>" +
        esc(data.audit.rejected) + "</pre></details>";
      pending.append(box);
    }

    if (data.proposed && data.proposed.length) {
      pending.insertAdjacentHTML("beforebegin",
        '<div class="meta" style="text-align:center">— роутер предлагает запомнить: ' +
        data.proposed.length + " —</div>");
    }
    feed.scrollTop = feed.scrollHeight;
  }

  async function refreshMarket() {
    const box = document.getElementById("market");
    let data;
    try {
      data = await (await fetch("/api/market")).json();
    } catch (err) {
      box.innerHTML = "<h2>Рынок</h2><div>не удалось получить данные: " + esc(err) + "</div>";
      return;
    }

    box.innerHTML = "<h2>Рынок по расписанию" +
      '<button class="ghost" id="collect" style="font-size:12px;padding:5px 10px">Снять наблюдение</button>' +
      '<span style="flex:1"></span><span style="color:var(--muted);font-size:12.5px">' +
      "наблюдений " + data.samples.count + (data.samples.last ? " · последнее " +
        esc(data.samples.last.slice(11, 19)) : "") + "</span></h2>" +
      '<div class="cols"><div><pre>' + esc(data.digest) + "</pre></div><div>" +
      "<b style='font-size:13px'>Задания</b><table>" + data.jobs.map((job) =>
        "<tr><td>" + esc(job.name) + "</td><td>" +
        (job.every ? "каждые " + job.every + " c" : "одноразовое") +
        "</td><td>" + esc(job.next_run.slice(11, 19)) + "</td></tr>").join("") +
      "</table><b style='font-size:13px'>Последние запуски</b><table>" +
      data.runs.map((run) =>
        "<tr><td>" + esc(run.at.slice(11, 19)) + "</td><td>" + esc(run.job) +
        '</td><td class="' + (run.status === "ok" ? "ok" : "bad") + '">' +
        esc(run.status) + "</td><td>" + esc(run.detail.slice(0, 48)) + "</td></tr>").join("") +
      "</table></div></div>";

    const button = document.getElementById("collect");
    if (button) button.onclick = async () => {
      button.disabled = true;
      await fetch("/api/market/collect", { method: "POST" });
      await refreshMarket();
    };
  }

  refreshMarket();
  setInterval(refreshMarket, 15000);

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
    paintChips();
    const state = await (await fetch("/api/state")).json();

    if (state.agents.length) {
      // Продолжаем с того же места: панели, настройки и переписка из прошлой сессии.
      for (const snapshot of state.agents) {
        paintHistory(build(snapshot), snapshot.history);
      }
      document.getElementById("dbline").textContent =
        "Восстановлено из " + state.db.path.split("/").pop() + ": агентов " +
        state.db.agents + ", сообщений " + state.db.messages + ", профилей " + state.db.profiles;
    } else {
      // Два свежих агента с разными профилями: разница в ответах видна с первого вопроса.
      const ids = Object.keys(PROFILES);
      build(await api("/api/agents", { settings: { profile: ids[0] || "" } }));
      build(await api("/api/agents", { settings: { profile: ids[1] || ids[0] || "" } }));
    }
  })();
</script>
</body>
</html>
'''


def profiles_payload():
    """Профили для страницы, каждый вместе с готовым текстом для промпта."""
    return {pid: {**p, "block": profiles.block(p)} for pid, p in profiles.load_all().items()}


def build_page():
    """Подставляет в страницу каталог моделей, роли, профили и пример данных."""
    html = PAGE.replace("__CATALOG__", json.dumps(CATALOG, ensure_ascii=False))
    html = html.replace("__ROLES__", json.dumps(agents.all_roles(), ensure_ascii=False))
    html = html.replace("__LAYERS__", json.dumps(LAYERS, ensure_ascii=False))
    html = html.replace("__STAGES__", json.dumps(DEFAULT_STAGES, ensure_ascii=False))
    html = html.replace("__KINDS__", json.dumps(INVARIANT_KINDS, ensure_ascii=False))
    html = html.replace("__FIELDS__", json.dumps(profiles.FIELDS, ensure_ascii=False))
    html = html.replace("__KINDS__", json.dumps(
        {"working": ["данные задачи"], "long": ["решение", "знание"],
         "profile": list(profiles.KINDS)}, ensure_ascii=False))
    html = html.replace("__PROFILES__", json.dumps(profiles_payload(), ensure_ascii=False))
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

    def _market_state(self):
        store.init()
        return {
            "digest": digest.as_text(digest.build("24h")),
            "jobs": store.list_jobs(),
            "runs": store.list_runs(8),
            "samples": store.samples_stats(),
        }

    def do_GET(self):
        if self.path == "/api/market":
            self._json(self._market_state())
            return
        if self.path in ("/", "/index.html"):
            self._reply(200, "text/html; charset=utf-8", build_page())
        elif self.path == "/api/state":
            self._json({
                "agents": [a.snapshot() for a in agents.REGISTRY.values()],
                "db": storage.stats(),
            })
        else:
            self._reply(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        if self.path == "/api/market/collect":
            try:
                done = scheduler.run_job({"kind": "collect", "params": {}, "name": "ручной"})
                store.log_run("ручной", "collect", "ok", done)
                self._json({"ok": True, "detail": done})
            except Exception as error:
                traceback.print_exc()
                self._json({"error": True, "text": str(error)})
            return

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
                self._json(agents.add_role(payload["title"], payload["prompt"]))

            elif self.path == "/api/roles/delete":
                self._json(agents.delete_role(payload["key"]))

            # --- профили: несколько штук, живут отдельно от агентов ---
            elif self.path == "/api/profiles":
                created = profiles.create({"name": payload.get("name", "")})
                print(f"→ новый профиль {created['id']}: {created['name']}")
                self._json({"id": created["id"], "profiles": profiles_payload()})

            elif self.path == "/api/profiles/list":
                self._json({"profiles": profiles_payload()})

            elif len(parts) == 3 and parts[:2] == ["api", "profiles"]:
                profiles.update(parts[2], payload)
                self._json({"profiles": profiles_payload()})

            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "delete":
                profiles.delete(parts[2])
                for agent in agents.REGISTRY.values():
                    if agent.settings["profile"] == parts[2]:
                        agent.set_profile("")
                self._json({"profiles": profiles_payload()})

            elif (len(parts) == 5 and parts[:2] == ["api", "agents"]
                  and parts[3] == "invariants"):
                agent = agents.get(parts[2])
                action = parts[4]
                print(f"→ инварианты {agent.settings['name']}: {action}")

                if action == "add":
                    self._json(agent.add_invariant(payload["text"],
                                                   payload.get("kind", "архитектура"),
                                                   payload.get("rationale", "")))
                elif action == "drop":
                    self._json(agent.drop_invariant(payload["id"]))
                elif action == "promote":
                    self._json(agent.promote_decision(payload["key"]))
                else:
                    self._reply(404, "text/plain; charset=utf-8", b"not found")

            elif (len(parts) == 5 and parts[:2] == ["api", "agents"]
                  and parts[3] == "task"):
                agent = agents.get(parts[2])
                action = parts[4]
                print(f"→ задача {agent.settings['name']}: {action}")

                if action == "start":
                    self._json(agent.start_task(payload.get("title", "Без названия"),
                                                payload.get("stages")))
                elif action == "plan":
                    self._json(agent.plan_task(payload.get("context", "")))
                elif action == "move":
                    self._json(agent.move(payload["stage"]))
                elif action == "approve":
                    self._json(agent.approve_plan())
                elif action == "validated":
                    self._json(agent.pass_validation())
                elif action == "question":
                    self._json(agent.add_question(payload["text"]))
                elif action == "resolve":
                    self._json(agent.resolve_question(payload["text"]))
                elif action == "pause":
                    self._json(agent.pause_task(payload.get("note", "")))
                elif action == "resume":
                    self._json(agent.resume_task())
                elif action == "step":
                    self._json(agent.mark_step(payload["text"],
                                               payload.get("status", "done")))
                elif action == "expect":
                    self._json(agent.set_expected(payload["actor"], payload["action"]))
                elif action == "steps":
                    self._json(agent.set_steps(payload["steps"]))
                elif action == "stages":
                    self._json(agent.set_stages(payload["stages"]))
                elif action == "advice":
                    if payload.get("accept"):
                        self._json(agent.apply_advice())
                    else:
                        agent.task_proposal = None
                        self._json(agent.snapshot())
                elif action == "drop":
                    self._json(agent.drop_task())
                else:
                    self._reply(404, "text/plain; charset=utf-8", b"not found")

            elif len(parts) == 4 and parts[0] == "api" and parts[1] == "agents":
                agent = agents.get(parts[2])
                action = parts[3]

                if action == "ask":
                    profile = agent.profile
                    print(f"→ {agent.settings['name']} · профиль: "
                          f"{profile['name'] if profile else 'нет'}")
                    result = agent.ask(payload["text"])
                    self._json({**result, "measure": profiles.measure(result["text"])})
                elif action == "settings":
                    self._json(agent.update(payload))
                elif action == "profile":
                    self._json(agent.set_profile(payload.get("profile", "")))
                elif action == "check":
                    # Судья смотрит на последнюю пару вопрос-ответ и профиль агента.
                    if len(agent.history) < 2 or not agent.profile:
                        raise RuntimeError("Нужны подключённый профиль и хотя бы один ответ")
                    items, tokens = profiles.check(
                        agent.settings["model"], agent.profile,
                        agent.history[-2]["content"], agent.history[-1]["content"])
                    self._json({"items": items, "tokens": tokens})
                elif action == "reset":
                    self._json(agent.reset())
                elif action == "decide":
                    self._json(agent.decide(payload["proposal"], payload["action"],
                                            payload.get("layer")))
                elif action == "remember":
                    self._json(agent.remember(payload["layer"], payload["key"],
                                              payload["value"], payload.get("kind", "")))
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
          f"сообщений: {restored['messages']}, профилей: {restored['profiles']}")
    print(f"Моделей в каталоге: {len(CATALOG)}\nОткрой {url}  (Ctrl+C — остановить)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")


if __name__ == "__main__":
    main()
