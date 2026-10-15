"""Доказательство дня: сколько подсаженных проблем находит ревью с RAG и без.

Сценарий: временный git worktree от HEAD, в него накладывается demo/seeded.patch —
правка дня 31 с 7 заранее известными проблемами (5 багов, 2 архитектурные). Это тот же
diff, что уходит в демо-PR на GitHub. Ревью гоняется в трёх режимах:

    no-rag   только diff;
    bm25     diff + RAG с лексическим поиском (как в CI без Ollama);
    bge-m3   diff + RAG на эмбеддингах (как в CI с Ollama и локально).

Пять из семи проблем — нарушения правил и «граблей» из CLAUDE.md: без контекста проекта
модель о них может знать только по общему опыту. Это и проверяется.

Засчитывает судья — openai/gpt-oss-120b на Groq (провайдер не участвует в ревью): по
списку подсаженных проблем и списку замечаний он отмечает, какое замечание какую
проблему нашло, а остальные замечания делит на «по делу» и «ложные». Режимы судья не
видит. Подсказок вида «тут баг» в diff нет.

    python3 review_demo.py              # 3 прохода × 4 режима, ~4 мин
    python3 review_demo.py --passes 1
"""

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import time

import providers
import review

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SEEDED = json.loads((HERE / "demo" / "seeded.json").read_text(encoding="utf-8"))
OUT = HERE / "data" / "review_result.json"
JUDGE = "openai/gpt-oss-120b"
MODES = {"no-rag": "без RAG", "bm25": "RAG · BM25", "bge-m3": "RAG · bge-m3",
         "pinned": "bge-m3 + правила"}

JUDGE_PROMPT = """Ты проверяешь работу автоматического ревьюера кода.
В pull request намеренно заложены проблемы (список A). Ревьюер выдал замечания (список B).

Для каждой проблемы из A укажи номер замечания из B, которое её находит по существу
(то же место и та же суть; формулировка может отличаться), или null.
Для каждого замечания из B, не совпавшего ни с одной проблемой A, оцени: "valid" — реальная
проблема или разумная рекомендация к изменённому коду, "wrong" — неверно, выдумано или не
относится к diff.

Верни только JSON:
{"found": {"<id проблемы>": <номер из B или null>, ...},
 "extra": {"<номер из B>": "valid" | "wrong", ...}}

A — заложенные проблемы:
<<SEEDED>>

B — замечания ревьюера:
<<FINDINGS>>"""


def worktree():
    path = pathlib.Path(tempfile.mkdtemp(prefix="seeded-"))
    subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "-q", "--detach", str(path), "HEAD"],
                   check=True)
    subprocess.run(["git", "-C", str(path), "apply", str(HERE / "demo" / "seeded.patch")],
                   check=True)
    return path


def drop_worktree(path):
    subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(path)],
                   check=False)
    shutil.rmtree(path, ignore_errors=True)


def seeded_lines(path):
    """Где в новом коде стоит каждая проблема — для README и для проверки строк."""
    lines = {}
    for issue in SEEDED["issues"]:
        text = (path / issue["file"]).read_text(encoding="utf-8").splitlines()
        lines[issue["id"]] = next((n for n, l in enumerate(text, 1) if issue["needle"] in l), None)
    return lines


def findings(result):
    out = []
    for section in ("bugs", "architecture", "recommendations"):
        for item in result.get(section) or []:
            out.append({"section": section, "file": item.get("file"), "line": item.get("line"),
                        "title": item.get("title", ""), "detail": item.get("detail", ""),
                        "check": item.get("check")})
    return out


def judge(items, lines):
    seeded = "\n".join(f"- {i['id']}: {i['file']}:{lines[i['id']]} — {i['what']}"
                       for i in SEEDED["issues"])
    block = "\n".join(f"{n}. [{f['section']}] {f['file']}:{f['line']} — {f['title']}. {f['detail']}"
                      for n, f in enumerate(items, 1)) or "(замечаний нет)"
    prompt = JUDGE_PROMPT.replace("<<SEEDED>>", seeded).replace("<<FINDINGS>>", block)
    for attempt in range(6):
        try:
            reply = providers.call(JUDGE, [{"role": "user", "content": prompt}], temperature=0,
                                   max_tokens=4000, response_format={"type": "json_object"})
            break
        except RuntimeError as error:
            # Groq на бесплатном тарифе — 8000 токенов в минуту, судья упирается в лимит.
            if "429" not in str(error) or attempt == 5:
                raise
            time.sleep(20)
    match = re.search(r"\{.*\}", reply["text"], flags=re.S)
    data = json.loads(match.group(0)) if match else {}
    found = {i["id"]: data.get("found", {}).get(i["id"]) for i in SEEDED["issues"]}
    extra = {str(k): v for k, v in (data.get("extra") or {}).items()}
    return found, extra


def run(passes):
    path = worktree()
    rows = []
    try:
        lines = seeded_lines(path)
        print("подсажено:", ", ".join(f"{k}@{v}" for k, v in lines.items()), flush=True)
        for number in range(1, passes + 1):
            for mode in MODES:
                result, meta = review.run(path, use_rag=mode != "no-rag",
                                          search="bm25" if mode == "bm25" else "embed",
                                          log=lambda *_: None, temperature=0,
                                          pin_rules=mode == "pinned")
                items = findings(result)
                found, extra = judge(items, lines)
                hit = {k: v for k, v in found.items() if isinstance(v, int)}
                matched = set(hit.values())
                row = {
                    "pass": number, "mode": mode, "found": found,
                    "recall": len(hit),
                    "recall_rule": sum(1 for i in SEEDED["issues"] if i["rule"] and i["id"] in hit),
                    "findings": len(items),
                    "extra_valid": sum(1 for n, v in extra.items() if v == "valid"
                                       and int(n) not in matched),
                    "extra_wrong": sum(1 for n, v in extra.items() if v == "wrong"
                                       and int(n) not in matched),
                    "line_ok": sum(f["check"] == "ok" for f in items),
                    "tokens": result["prompt_tokens"] + result["completion_tokens"],
                    "cost": result["cost"], "seconds": meta["seconds"],
                    "context": [f"{c['source']} › {c['where']}" for c in meta["context"]],
                    "result": result,
                }
                rows.append(row)
                print(f"[{number}] {MODES[mode]:13} найдено {row['recall']}/7 "
                      f"(правила {row['recall_rule']}/5) · замечаний {row['findings']}, "
                      f"сверх: по делу {row['extra_valid']}, ложных {row['extra_wrong']} · "
                      f"{row['tokens']} ток · {row['seconds']} с · "
                      f"{' '.join(k for k, v in found.items() if isinstance(v, int))}",
                      flush=True)
    finally:
        drop_worktree(path)
    return rows, lines


def summary(rows, passes):
    print(f"\nИтог за {passes} прохода:")
    table = []
    for mode in MODES:
        sel = [r for r in rows if r["mode"] == mode]
        per_issue = {i["id"]: sum(isinstance(r["found"][i["id"]], int) for r in sel)
                     for i in SEEDED["issues"]}
        line = {"mode": mode, "recall": [r["recall"] for r in sel],
                "recall_rule": [r["recall_rule"] for r in sel], "per_issue": per_issue,
                "findings": [r["findings"] for r in sel],
                "extra_valid": sum(r["extra_valid"] for r in sel),
                "extra_wrong": sum(r["extra_wrong"] for r in sel),
                "line_ok": sum(r["line_ok"] for r in sel),
                "findings_total": sum(r["findings"] for r in sel),
                "tokens": round(sum(r["tokens"] for r in sel) / len(sel)),
                "cost": round(sum(r["cost"] for r in sel), 4),
                "seconds": round(sum(r["seconds"] for r in sel) / len(sel), 1)}
        table.append(line)
        print(f"{MODES[mode]:13} найдено {line['recall']} из 7 · правила {line['recall_rule']} "
              f"из 5 · сверх по делу {line['extra_valid']}, ложных {line['extra_wrong']} · "
              f"строки в изменениях {line['line_ok']}/{line['findings_total']} · "
              f"{line['tokens']} ток/ревью · {line['seconds']} с")
        print("   " + ", ".join(f"{k} {v}/{len(sel)}" for k, v in per_issue.items()))
    return table


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--passes", type=int, default=3)
    args = parser.parse_args()
    started = time.monotonic()
    rows, lines = run(args.passes)
    table = summary(rows, args.passes)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"passes": args.passes, "judge": JUDGE, "seeded_lines": lines,
                               "summary": table, "rows": rows,
                               "minutes": round((time.monotonic() - started) / 60, 1)},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ data/review_result.json за {(time.monotonic() - started) / 60:.1f} мин")


if __name__ == "__main__":
    main()
