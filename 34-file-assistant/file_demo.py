"""Замер файлового ассистента: 4 сценария × 3 прохода на живом API, temperature=0.

Каждый проход начинается со свежей песочницы (workspace.build(reset=True)), поэтому
прогоны независимы и воспроизводимы. Качество считается КОДОМ против эталона:

    usages      файлы с MCPClient (эталон — поиск по песочнице) → найдено / лишние
    invariants  7 нарушений из invariants.py → найдено в INVARIANTS_REPORT.md / лишние строки
    docs        6 проверок diff документации дня 31
    changelog   дни из папок слепка → покрыты / фантомные / дата совпала с коммитом «Задание NN»

    python3 file_demo.py              # ~10 мин → data/file_result.json, data/file_demo.log
    python3 file_demo.py docs 1       # один сценарий, один проход
"""

import hashlib
import json
import pathlib
import re
import subprocess
import sys
import time

import assistant as fa
import invariants
import workspace

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
ROOT = workspace.WORKSPACE
DRAFTS = ROOT / workspace.DRAFTS
PY_PATH = re.compile(r"\d\d-[\w-]+/[\w/.-]+\.py")


CREATED = {}     # новые файлы из diff — для пересчёта оценок без нового прогона


def draft(path):
    if path in CREATED:
        return CREATED[path]
    target = DRAFTS / path
    return target.read_text(encoding="utf-8") if target.is_file() else None


def created_from_diff(diff, path):
    """Текст нового файла из его diff (--- /dev/null): все строки «+» по порядку."""
    lines, inside = [], False
    for line in diff.splitlines():
        if line.startswith("+++ "):
            inside = line == f"+++ b/{path}"
        elif inside and line.startswith("+"):
            lines.append(line[1:])
    return "\n".join(lines) + "\n" if lines else None


def rescore():
    """Пересчитать оценки changelog и invariants по сохранённым diff (их файлы — новые)."""
    path = DATA / "file_result.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for row in data["runs"]:
        name = {"changelog": "CHANGELOG.md", "invariants": "INVARIANTS_REPORT.md"}.get(row["scenario"])
        if not name or "error" in row:
            continue
        CREATED.clear()
        text = created_from_diff(row["diff"], name)
        if text is not None:
            CREATED[name] = text
        workspace.build(reset=True)
        row.update(SCORERS[row["scenario"]]({"answer": row["answer"], "changed": row["changed"]}))
        print(f"{row['scenario']} #{row['attempt']}: {row['score']}/{row['of']}")
    for key, s in data["summary"].items():
        rows = [r for r in data["runs"] if r["scenario"] == key and "error" not in r]
        s["score"] = sum(r["score"] for r in rows)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# --- эталоны и оценки ---------------------------------------------------------

def score_usages(result):
    truth = sorted(p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.py")
                   if "MCPClient" in p.read_text(encoding="utf-8"))
    answer = result["answer"] or ""
    named = set(PY_PATH.findall(answer))
    # Таблица бывает и «день | файл» без полного пути: пара (NN, имя) в одной строке.
    pairs = set()
    for line in answer.splitlines():
        cells = [c.strip(" `*") for c in line.strip().strip("|").split("|")]
        days = {c[:2] for c in cells if re.match(r"\d\d(\b|-)", c)}
        files = {c for c in cells if re.fullmatch(r"[\w.]+\.py", c)}
        pairs |= {(d, f) for d in days for f in files}
    for path in truth:
        if (path[:2], path.split("/")[-1]) in pairs:
            named.add(path)
    found = [p for p in truth if p in named]
    extra = sorted(named - set(truth))
    return {"score": len(found), "of": len(truth), "extra": extra,
            "missed": [p for p in truth if p not in named]}


RULE_WORDS = {"port": r"порт|PORT|80\d\d", "stdlib": r"import|stdlib|станд",
              "boundary": r"URL|api\.|провайдер|границ", "gitignore": r"13-task-state"}


def score_invariants(result):
    truth = invariants.check()
    report = draft("INVARIANTS_REPORT.md")
    if report is None:
        return {"score": 0, "of": len(truth), "extra": [], "missed": truth,
                "note": "отчёт не записан"}
    rows = [line for line in report.splitlines()
            if line.startswith("|") and not re.match(r"\|\s*-", line)
            and not re.match(r"\|\s*файл", line, re.I)]
    used, found = set(), []
    for v in truth:
        for i, row in enumerate(rows):
            cell = row.strip("|").split("|")[0].strip(" `")
            if cell == v["path"] and re.search(RULE_WORDS[v["rule"]], row):
                used.add(i)
                found.append(v)
                break
    missed = [f"{v['path']}:{v['line']} {v['rule']}" for v in truth if v not in found]
    extra = [rows[i].strip()[:200] for i in range(len(rows)) if i not in used]
    return {"score": len(found), "of": len(truth), "extra": extra, "missed": missed}


def score_docs(result):
    readme = draft("31-dev-assistant/README.md") or ""
    api = draft("31-dev-assistant/docs/mcp-api.md") or ""
    arch = draft("31-dev-assistant/docs/architecture.md") or ""
    original = (ROOT / "31-dev-assistant/README.md").read_text(encoding="utf-8")

    def line_with(text, needle):
        return next((line for line in text.splitlines() if needle in line), "")

    def history(text):
        start = text.find("### Первый прогон")
        return text[start:text.find("![demo]", start)]

    table = line_with(readme, "| `project_server.py` |")
    scheme = line_with(readme, "git_status ·")
    checks = {
        "README: таблица «Файлы»": "list_files" in table and "git_blame" in table
                                   and "git_files" not in table,
        "README: схема /help": "list_files" in scheme and "git_blame" in scheme,
        "mcp-api.md: list_files и git_blame": "`list_files`" in api and "`git_blame`" in api
                                              and "`git_files`" not in api,
        "architecture.md": "git_blame" in arch and "git_files" not in arch,
        "код не тронут": not any(p.endswith(".py") for p in result["changed"]),
        "история не переписана": bool(readme) and history(readme) == history(original),
    }
    return {"score": sum(checks.values()), "of": len(checks), "checks": checks,
            "missed": [k for k, ok in checks.items() if not ok], "extra": []}


def day_dates():
    """Даты коммитов «Задание NN: …» до слепка: {NN: {даты}}."""
    log = subprocess.run(["git", "-C", str(workspace.REPO), "log", workspace.SNAPSHOT,
                          "--date=short", "--pretty=format:%ad %s"],
                         capture_output=True, text=True, check=True).stdout
    dates = {}
    for line in log.splitlines():
        match = re.match(r"(\S+) Задание (\d\d)", line)
        if match:
            dates.setdefault(match.group(2), set()).add(match.group(1))
    return dates


def score_changelog(result):
    days = sorted(p.name[:2] for p in ROOT.iterdir()
                  if p.is_dir() and re.match(r"\d\d-", p.name))
    text = draft("CHANGELOG.md")
    if text is None:
        return {"score": 0, "of": len(days), "extra": [], "missed": days,
                "note": "CHANGELOG.md не создан"}
    # «## 33 — …», «## [33] …» и «## Задание 33 — …» — формат заголовка модель выбирает сама.
    headings = re.findall(r"^#{2,3}\s+(?:Задание\s+|День\s+)?\[?(\d\d)\b(.*)$", text, re.M)
    found = [n for n, _ in headings]
    dates = day_dates()
    wrong_dates = []
    for number, rest in headings:
        date = re.search(r"\d{4}-\d\d-\d\d", rest)
        if not date or date.group(0) not in dates.get(number, set()):
            wrong_dates.append(f"{number}: {date.group(0) if date else 'нет даты'}")
    order = found == sorted(found, reverse=True)
    return {"score": len(set(found) & set(days)), "of": len(days),
            "missed": [d for d in days if d not in found],
            "extra": sorted(set(found) - set(days)), "wrong_dates": wrong_dates,
            "order_ok": order}


SCORERS = {"usages": score_usages, "invariants": score_invariants,
           "docs": score_docs, "changelog": score_changelog}


# --- прогон ---------------------------------------------------------------------

def run_once(bot, scenario, attempt, log):
    workspace.build(reset=True)
    started = time.monotonic()
    try:
        result = bot.run(scenario, temperature=0)
    except (RuntimeError, ValueError) as error:
        log(f"  {scenario} #{attempt}: ОШИБКА {error}")
        return {"scenario": scenario, "attempt": attempt, "error": str(error)}
    score = SCORERS[scenario](result)
    calls = {}
    for tool in result["tools"]:
        calls[tool["name"]] = calls.get(tool["name"], 0) + 1
    row = {
        "scenario": scenario, "attempt": attempt, **score,
        "calls": len(result["tools"]), "by_tool": calls, "rounds": result["rounds"],
        "tool_errors": result["errors"], "files_read": len(result["read"]),
        "read": result["read"], "changed": result["changed"],
        "budget_warning": bool(result.get("budget_warning")), "leak": bool(result.get("leak")),
        "prompt_tokens": result["prompt_tokens"], "completion_tokens": result["completion_tokens"],
        "cost": result["cost"], "seconds": round(time.monotonic() - started, 1),
        "diff_sha": hashlib.sha256(result["diff"].encode()).hexdigest()[:10],
        "diff_lines": result["diff"].count("\n"),
        "answer": result["answer"], "diff": result["diff"],
        "trace": [{k: t[k] for k in ("round", "name", "arguments", "ok")} for t in result["tools"]],
    }
    log(f"  {scenario} #{attempt}: {score['score']}/{score['of']} · вызовов {row['calls']} "
        f"(ошибок {row['tool_errors']}) · кругов {row['rounds']} · прочитано {row['files_read']} "
        f"· изменено {len(row['changed'])} · {row['prompt_tokens']}+{row['completion_tokens']} ток. "
        f"· ${row['cost']} · {row['seconds']} с · diff {row['diff_sha']}")
    if score.get("missed"):
        log(f"      пропущено: {score['missed']}")
    if score.get("extra"):
        log(f"      лишнее: {score['extra']}")
    if score.get("wrong_dates"):
        log(f"      даты не совпали: {score['wrong_dates']}")
    return row


def main():
    if "--rescore" in sys.argv:
        return rescore()
    scenarios = [a for a in sys.argv[1:] if a in SCORERS] or list(SCORERS)
    passes = next((int(a) for a in sys.argv[1:] if a.isdigit()), 3)
    DATA.mkdir(exist_ok=True)
    full = scenarios == list(SCORERS) and passes == 3
    log_file = (DATA / ("file_demo.log" if full else "file_demo_partial.log")).open(
        "w", encoding="utf-8")

    def log(text):
        print(text, flush=True)
        log_file.write(text + "\n")
        log_file.flush()

    bot = fa.FileAssistant()
    log(f"слепок {workspace.SNAPSHOT}, модель {bot.model}, temperature=0, "
        f"проходов {passes}, лимит кругов {fa.MAX_TOOL_ROUNDS}")
    rows = []
    for scenario in scenarios:
        log(f"\n{fa.PRESETS[scenario]['title']}")
        for attempt in range(1, passes + 1):
            rows.append(run_once(bot, scenario, attempt, log))

    log("\nИтог (сумма за проходы):")
    log(f"{'сценарий':12} {'качество':>10} {'вызовов':>8} {'ошибок':>7} {'прочит.':>8} "
        f"{'токенов':>9} {'$':>8} {'с':>6}  разных diff")
    summary = {}
    for scenario in scenarios:
        ok = [r for r in rows if r["scenario"] == scenario and "error" not in r]
        if not ok:
            continue
        s = {"score": sum(r["score"] for r in ok), "of": sum(r["of"] for r in ok),
             "calls": sum(r["calls"] for r in ok), "tool_errors": sum(r["tool_errors"] for r in ok),
             "files_read": sum(r["files_read"] for r in ok),
             "tokens": sum(r["prompt_tokens"] + r["completion_tokens"] for r in ok),
             "cost": round(sum(r["cost"] for r in ok), 4),
             "seconds": round(sum(r["seconds"] for r in ok), 1),
             "distinct_diffs": len({r["diff_sha"] for r in ok}), "runs": len(ok)}
        summary[scenario] = s
        log(f"{scenario:12} {s['score']:>4}/{s['of']:<5} {s['calls']:>8} {s['tool_errors']:>7} "
            f"{s['files_read']:>8} {s['tokens']:>9} {s['cost']:>8} {s['seconds']:>6}  "
            f"{s['distinct_diffs']} из {s['runs']}")

    out = DATA / ("file_result.json" if full else "file_result_partial.json")
    out.write_text(json.dumps({"snapshot": workspace.SNAPSHOT, "model": bot.model,
                               "max_rounds": fa.MAX_TOOL_ROUNDS, "summary": summary,
                               "runs": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n→ {out.relative_to(HERE)}")
    workspace.build(reset=True)
    bot.bridge.close()


if __name__ == "__main__":
    main()
