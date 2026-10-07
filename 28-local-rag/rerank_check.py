"""Промпт реранкера дня 24 (v1) против нумерованного (v2) на локальных и облачной модели.

Только этап реранка, на 6 вопросах, где ответ в тексте есть. Для каждого: сколько id из
ответа модели не удалось сопоставить с кандидатами и какую оценку получила нужная статья
(гейт «не знаю» пропускает от 7). Один проход, temperature=0.

    python3 rerank_check.py      # ~15 минут на M1 8 ГБ
"""

import json
import pathlib
import sys

import citations
import index
import local_rag_demo
import retrieval

MODELS = ["deepseek-chat", "qwen2.5:7b", "qwen2.5:3b"]
RESULTS = pathlib.Path(__file__).with_name("data") / "rerank_check.json"


def main():
    ix = index.Index()
    questions = [q for q in local_rag_demo.QUESTIONS if q["type"] == "answer"]
    rows = []
    for model in MODELS:
        for prompt in ("v1", "v2"):
            for item in questions:
                hits, _ = ix.search(item["q"], retrieval.DEFAULTS["k_before"])
                alive = [h for h in hits if h["score"] >= retrieval.DEFAULTS["threshold"]]
                scores, usage = retrieval.rerank(item["q"], alive, model, prompt)
                needed = [s for cid, s in scores.items()
                          if cid.split("#")[0].replace("ст.", "") in item["sources"]]
                best_needed = max((s for s in needed if s is not None), default=None)
                position = next((i for i, h in enumerate(alive, 1)
                                 if h["article"] in item["sources"]), None)
                row = {"model": model, "prompt": prompt, "q": item["q"], "candidates": len(alive),
                       "needed_position": position,
                       "missing": sum(s is None for s in scores.values()),
                       "needed_score": best_needed,
                       "passes_gate": (best_needed or 0) >= citations.GATE_MIN,
                       "best_other": max((s for cid, s in scores.items() if s is not None and
                                          cid.split("#")[0].replace("ст.", "") not in item["sources"]),
                                         default=None),
                       "seconds": usage["seconds"], "prompt_tokens": usage["prompt_tokens"],
                       "raw": usage["raw"]}
                rows.append(row)
                print(f"{model:14} {prompt}  кандидатов {len(alive):>2} (нужная №{position})  "
                      f"не сопоставлено {row['missing']:>2}  нужная {str(best_needed):>4}  "
                      f"лучшая чужая {str(row['best_other']):>4}  {usage['seconds']:6.1f} с  "
                      f"{item['q'][:45]}")
    print()
    for model in MODELS:
        for prompt in ("v1", "v2"):
            part = [r for r in rows if r["model"] == model and r["prompt"] == prompt]
            print(f"{model:14} {prompt}: нужная статья ≥ {citations.GATE_MIN} в "
                  f"{sum(r['passes_gate'] for r in part)}/{len(part)}, не сопоставлено id "
                  f"{sum(r['missing'] for r in part)}/{sum(r['candidates'] for r in part)}, "
                  f"время {sum(r['seconds'] for r in part):.0f} с")
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {RESULTS.relative_to(pathlib.Path(__file__).parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
