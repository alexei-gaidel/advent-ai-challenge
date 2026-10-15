"""Ревьюер: diff + контекст из RAG → DeepSeek → JSON замечаний → проверка кодом → Markdown.

Ответ модели — строгий JSON с тремя списками, которые просит задание:
    bugs           потенциальные баги (с серьёзностью)
    architecture   архитектурные проблемы — в том числе нарушения правил проекта
    recommendations рекомендации
У каждого замечания есть файл и строка. Код проверяет, что файл есть в diff, а строка —
в изменённом месте; замечания «мимо diff» не выбрасываются, а помечаются.
"""

import json
import re
import time

import diffparse
import providers

MODEL = "deepseek-chat"
MARKER = "<!-- ai-review:advent -->"
SEVERITY = {"high": "🔴", "medium": "🟠", "low": "🟡"}

PROMPT = """Ты — строгий ревьюер кода в репозитории Advent AI Challenge (Python, только
стандартная библиотека). Тебе дан diff pull request'а<<CTX_INTRO>>.

Найди в ИЗМЕНЁННОМ коде:
1. bugs — потенциальные баги: ошибки логики, необработанные исключения, уязвимости
   (инъекции, выход за пределы каталога, утечка секретов), гонки, неверные границы.
2. architecture — архитектурные проблемы: нарушения правил и устройства проекта,
   дублирование, смешение ответственности, лишние зависимости.
3. recommendations — что улучшить: читаемость, проверки, тесты, документация.

Правила:
- Только замечания к строкам с «+» в diff. Старый код не ревьюй.
- Указывай file (путь как в diff) и line (номер строки в НОВОЙ версии файла).
- Не выдумывай: если не уверен, что это баг, — это recommendation, не bug.
- Ссылайся на правило проекта, если замечание из него следует: "rule": "CLAUDE.md: …".
- Пустой список лучше натянутых замечаний.

Верни только JSON:
{"summary": "1–2 предложения о PR",
 "bugs": [{"file": "...", "line": 12, "severity": "high|medium|low", "title": "...", "detail": "...", "rule": ""}],
 "architecture": [{"file": "...", "line": 0, "title": "...", "detail": "...", "rule": ""}],
 "recommendations": [{"file": "...", "line": 0, "title": "...", "detail": ""}]}
<<CONTEXT>>
=== DIFF ===
<<DIFF>>"""


def build_prompt(diff_text, context):
    if context:
        blocks = "\n\n".join(f"[{c['kind']}] {c['source']} › {c['where']}\n{c['text']}"
                             for c in context)
        intro = " и фрагменты документации и кода проекта, найденные поиском"
        ctx = f"\n=== КОНТЕКСТ ПРОЕКТА (RAG) ===\n{blocks}\n"
    else:
        intro, ctx = "", ""
    # .replace, а не .format: в JSON-примере фигурные скобки (грабли из CLAUDE.md).
    return (PROMPT.replace("<<CTX_INTRO>>", intro).replace("<<CONTEXT>>", ctx)
            .replace("<<DIFF>>", diff_text))


def review(files, context, temperature=0.2):
    diff_text, cut = diffparse.for_prompt(files)
    if not diff_text.strip():
        return {"summary": "В PR нет файлов, которые имеет смысл ревьюить (данные, картинки).",
                "bugs": [], "architecture": [], "recommendations": [], "cut": cut,
                "prompt_tokens": 0, "completion_tokens": 0, "cost": 0, "seconds": 0}
    prompt = build_prompt(diff_text, context)
    started = time.monotonic()
    reply = providers.call(MODEL, [{"role": "user", "content": prompt}],
                           temperature=temperature, max_tokens=3000,
                           response_format={"type": "json_object"})
    data = _parse(reply["text"])
    by_path = {f["path"]: f for f in files}
    for section in ("bugs", "architecture", "recommendations"):
        items = [i for i in data.get(section) or [] if isinstance(i, dict)]
        for item in items:
            f = by_path.get(str(item.get("file", "")).strip())
            line = item.get("line") if isinstance(item.get("line"), int) else 0
            item["check"] = ("ok" if f and diffparse.near_change(f, line)
                             else "file" if f else "outside")
        data[section] = items
    data.update(cut=cut, prompt_tokens=reply["prompt_tokens"],
                completion_tokens=reply["completion_tokens"], cost=reply["cost"] or 0,
                seconds=round(time.monotonic() - started, 1), prompt_chars=len(prompt))
    return data


def _parse(text):
    match = re.search(r"\{.*\}", text, flags=re.S)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict) or not data:
        return {"summary": "Модель вернула не JSON — ревью не разобрано.", "raw": text[:2000]}
    return data


# --- Markdown для комментария к PR ------------------------------------------------

CHECK_NOTE = {"ok": "", "file": " · ⚠ строка вне изменений", "outside": " · ⚠ файла нет в diff"}


def _item(item, bug=False):
    where = f"`{item.get('file', '?')}:{item.get('line') or '—'}`"
    mark = SEVERITY.get(str(item.get("severity", "")).lower(), "") + " " if bug else ""
    rule = f"\n  _Правило: {item['rule']}_" if item.get("rule") else ""
    detail = f" — {item['detail']}" if item.get("detail") else ""
    return (f"- {mark}**{item.get('title', '')}** {where}{CHECK_NOTE.get(item.get('check'), '')}"
            f"{detail}{rule}")


def to_markdown(result, meta):
    lines = [MARKER, "## 🤖 AI-ревью", "", result.get("summary", ""), ""]
    for key, title in (("bugs", "Потенциальные баги"), ("architecture", "Архитектурные проблемы"),
                       ("recommendations", "Рекомендации")):
        items = result.get(key) or []
        lines += [f"### {title} ({len(items)})", ""]
        lines += [_item(i, bug=key == "bugs") for i in items] or ["_нет_"]
        lines.append("")
    if result.get("raw"):
        lines += ["```", result["raw"], "```", ""]
    ctx = meta.get("context") or []
    if ctx:
        lines += ["<details><summary>Контекст из RAG "
                  f"({meta['search']}, {len(ctx)} фрагментов)</summary>", ""]
        lines += [f"- `{c['source']}` › {c['where']} · {c['score']} · запрос: {c['query']}"
                  for c in ctx]
        lines += ["", "</details>", ""]
    if result.get("cut"):
        lines += [f"⚠ Не вошли в ревью или обрезаны: {', '.join(result['cut'])}", ""]
    skipped = meta.get("skipped") or []
    lines.append(
        f"<sub>{MODEL} · {meta.get('files', 0)} файлов в diff, пропущено {len(skipped)} "
        f"(данные/картинки) · поиск: {meta['search']} · "
        f"{result.get('prompt_tokens', 0)}+{result.get('completion_tokens', 0)} токенов · "
        f"${result.get('cost', 0):.4f} · {meta.get('seconds', 0)} с · "
        f"⚠ — замечание не привязано к изменённым строкам</sub>")
    return "\n".join(lines)
