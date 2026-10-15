"""AI-ревью PR: diff → RAG (документация + код) → DeepSeek → комментарий в PR.

    python3 review.py --github                    # в GitHub Actions: событие PR → комментарий
    python3 review.py --base main --head HEAD     # локально: ревью ветки, Markdown в stdout
    python3 review.py --base main --no-rag        # то же без контекста проекта
    python3 review.py --base main --no-pin        # правила CLAUDE.md только через поиск
    python3 review.py --repo /tmp/wt              # рабочая копия против HEAD (временный worktree)

Ключ DeepSeek — DEEPSEEK_API_KEY из окружения (в Actions — секрет) или из корневого .env.
"""

import argparse
import json
import pathlib
import sys
import time

import diffparse
import github
import rag
import reviewer

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "data" / "last_review.json"


def run(repo, base=None, head=None, use_rag=True, search="auto", log=print,
        temperature=0.2, pin_rules=True):
    """Весь пайплайн без GitHub: → (result, meta)."""
    started = time.monotonic()
    files = diffparse.parse(diffparse.get_diff(repo, base, head))
    reviewable = [f for f in files if not f["skipped"]]
    folders = diffparse.folders(reviewable)
    log(f"diff: {len(files)} файлов, к ревью {len(reviewable)}, папки: {', '.join(folders) or '—'}")

    context, mode, rag_stats = [], "без RAG", {}
    if use_rag:
        retriever = rag.Retriever(pathlib.Path(repo), folders, mode=search, log=log)
        context = retriever.for_diff(files, pin_rules=pin_rules)
        mode, rag_stats = retriever.mode, retriever.stats
        log(f"RAG ({mode}): {rag_stats['docs']} чанков документации + {rag_stats['code']} кода, "
            f"выбрано {len(context)} за {rag_stats['seconds']} с")

    result = reviewer.review(files, context, temperature=temperature)
    meta = {"files": len(files), "skipped": [f["path"] for f in files if f["skipped"]],
            "folders": folders, "search": mode, "rag": rag_stats, "context": context,
            "seconds": round(time.monotonic() - started, 1)}
    log(f"ревью: багов {len(result.get('bugs', []))}, архитектура "
        f"{len(result.get('architecture', []))}, рекомендаций "
        f"{len(result.get('recommendations', []))} · {meta['seconds']} с")
    return result, meta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--github", action="store_true", help="режим GitHub Actions")
    parser.add_argument("--repo", default=str(HERE.parent))
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--no-rag", action="store_true")
    parser.add_argument("--search", choices=["auto", "embed", "bm25"], default="auto")
    parser.add_argument("--no-pin", action="store_true",
                        help="не закреплять правила CLAUDE.md, только поиск")
    args = parser.parse_args()

    log = lambda text: print(text, file=sys.stderr, flush=True)   # stdout — только Markdown
    pr = None
    if args.github:
        pr = github.event()
        args.base, args.head = pr["base"], pr["head"]
        log(f"PR #{pr['number']}: {pr['title']}")

    try:
        result, meta = run(args.repo, args.base, args.head, not args.no_rag, args.search, log,
                           pin_rules=not args.no_pin)
    except RuntimeError as error:
        markdown = f"{reviewer.MARKER}\n## 🤖 AI-ревью\n\n⚠ Ревью не выполнено: {error}"
        github.step_summary(markdown)
        print(markdown)
        return 1

    markdown = reviewer.to_markdown(result, meta)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"result": result, "meta": meta, "pr": pr}, ensure_ascii=False,
                              indent=1), encoding="utf-8")
    github.step_summary(markdown)
    if pr:
        url, action = github.upsert_comment(pr["repo"], pr["number"], markdown, reviewer.MARKER)
        log(f"комментарий {action}: {url}")
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
