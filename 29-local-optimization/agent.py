"""Агент по Конституции РФ на локальной модели, настроенной пресетом (presets.py).

    Agent("base").ask("Как Конституция определяет брак?")    # как в дне 28
    Agent("quant").ask(...)                                    # оптимизированный

Пайплайн дня 28 (retrieval.py → citations.py). Пресет задаёт модель, опции Ollama, сколько
кандидатов видит реранкер, обрезку фрагментов и промпт ответа. После каждого вопроса
снимаются ресурсы: что в памяти, сколько на GPU, RSS процессов Ollama.
"""

import citations
import index
import local_llm
import presets
import retrieval

RELOAD_S = 1.0        # load_duration больше — модель грузилась с диска, а не была в памяти


class Agent:
    def __init__(self, preset=presets.AFTER, **params):
        if preset not in presets.PRESETS:
            raise ValueError(f"неизвестный пресет {preset!r}, есть {tuple(presets.PRESETS)}")
        self.name = preset
        self.preset = presets.PRESETS[preset]
        self.params = params          # threshold, k_before, k_after, rerank_min
        self._index = None

    @property
    def index(self):
        if self._index is None:
            self._index = index.Index()
        return self._index

    def ask(self, question, gate_min=None, **params):
        hits, trace = retrieval.retrieve(self.index, question, self.preset,
                                         **{**self.params, **params})
        url = self.index.info["source"]
        result, replies = citations.answer(
            question, hits, trace, url, self.preset,
            citations.GATE_MIN if gate_min is None else gate_min)

        rerank = trace["stages"].get("rerank")
        usages = ([rerank] if rerank else []) + replies
        total = lambda key, items=usages: sum(u.get(key) or 0 for u in items)
        try:
            memory = {"ps": local_llm.resources(), "rss_mb": local_llm.rss_mb()}
        except RuntimeError:
            memory = {"ps": [], "rss_mb": None}
        return {
            **result,
            "preset": self.name,
            "model": self.preset["model"],
            "question": question,
            "context": [{"chunk_id": h["chunk_id"], "article": h["article"], "score": h["score"],
                         "rerank": h.get("rerank"), "text": h["text"]} for h in hits],
            "final": trace["final"],
            "candidates": [{k: c[k] for k in ("chunk_id", "article", "score", "rerank", "status")}
                           for c in trace["candidates"]],
            "best_rerank": max((h.get("rerank") or 0 for h in hits), default=None),
            "rerank_failed": trace["rerank_failed"],
            "rerank_missing": trace.get("rerank_missing", 0),
            "rerank_prompt_tokens": rerank["prompt_tokens"] if rerank else 0,
            "json_failed": sum(not r.get("json_ok", True) for r in replies),
            "prompt_tokens": total("prompt_tokens"),
            "completion_tokens": total("completion_tokens"),
            "timing": {"embed": trace["embed_seconds"],
                       "rerank": round(rerank["seconds"], 2) if rerank else 0.0,
                       "answer": round(total("seconds", replies), 2),
                       "load": round(total("load_seconds"), 2)},
            "reloads": sum((u.get("load_seconds") or 0) > RELOAD_S for u in usages),
            "tok_per_s": max((u.get("tok_per_s") or 0 for u in usages), default=0),
            "prompt_tok_per_s": max((u.get("prompt_tok_per_s") or 0 for u in usages), default=0),
            "memory": memory,
            "seconds": round(total("seconds") + trace["embed_seconds"], 2),
            "llm_calls": len(usages),
            "network": False,
        }
