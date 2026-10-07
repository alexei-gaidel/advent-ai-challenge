"""Агент по Конституции РФ: RAG с цитатами дня 24, где каждая модель может быть локальной.

    agent = Agent("local-7b")            # bge-m3 + qwen2.5:7b — всё в Ollama на этом Mac
    agent.ask("Как Конституция определяет брак?")
    Agent("cloud").ask(...)              # тот же пайплайн, реранкер и ответ — deepseek-chat

Пайплайн одинаковый у всех стеков (retrieval.py → citations.py), различаются только модели.
Режим «свободный текст» дня 24 выброшен: сравниваем модели, а не форматы ответа.
"""

import citations
import index
import llm
import retrieval


class Agent:
    def __init__(self, stack=llm.DEFAULT_STACK, temperature=0, **params):
        if stack not in llm.STACKS:
            raise ValueError(f"неизвестный стек {stack!r}, есть {tuple(llm.STACKS)}")
        self.stack = stack
        self.models = llm.STACKS[stack]
        self.temperature = temperature
        self.params = params          # threshold, k_before, k_after, rerank_min
        self._index = None

    @property
    def index(self):
        if self._index is None:
            self._index = index.Index()
        return self._index

    def ask(self, question, gate_min=None, **params):
        hits, trace = retrieval.retrieve(self.index, question, self.models["rerank"],
                                         **{**self.params, **params})
        url = self.index.info["source"]
        result, replies = citations.answer(
            question, hits, trace, url, self.models["answer"], self.temperature,
            citations.GATE_MIN if gate_min is None else gate_min)

        rerank = trace["stages"].get("rerank")
        usages = ([rerank] if rerank else []) + replies
        total = lambda key, items=usages: sum(u.get(key) or 0 for u in items)
        return {
            **result,
            "stack": self.stack,
            "models": self.models,
            "question": question,
            "context": [{"chunk_id": h["chunk_id"], "article": h["article"], "score": h["score"],
                         "rerank": h.get("rerank"), "text": h["text"]} for h in hits],
            "final": trace["final"],
            "candidates": [{k: c[k] for k in ("chunk_id", "article", "score", "rerank", "status")}
                           for c in trace["candidates"]],
            "best_rerank": max((h.get("rerank") or 0 for h in hits), default=None),
            "rerank_failed": trace["rerank_failed"],
            "rerank_missing": trace.get("rerank_missing", 0),
            "json_failed": sum(not r.get("json_ok", True) for r in replies),
            "prompt_tokens": total("prompt_tokens"),
            "completion_tokens": total("completion_tokens"),
            "cost": round(total("cost"), 6),
            "timing": {"embed": trace["embed_seconds"],
                       "rerank": round(rerank["seconds"], 2) if rerank else 0.0,
                       "answer": round(total("seconds", replies), 2),
                       "load": round(total("load_seconds"), 2)},
            "seconds": round(total("seconds") + trace["embed_seconds"], 2),
            "llm_calls": len(usages),
            "network": any(not u.get("local") for u in usages),
        }
