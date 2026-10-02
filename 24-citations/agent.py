"""Агент по Конституции РФ: обычный RAG дня 23 против RAG с цитатами и режимом «не знаю».

    agent = Agent()
    agent.ask("Как Конституция определяет брак?", mode="cited")

Отбор чанков у режимов общий (retrieval.py: порог + реранкер), различается только то,
что происходит после: `rag` отвечает свободным текстом со ссылками [ст.N], `cited`
проходит гейт релевантности, отвечает JSON-ом с цитатами, и цитаты проверяются кодом.
"""

import re

import citations
import index
import providers
import rag
import retrieval

MODEL = "deepseek-chat"
MODES = ("rag", "cited")
MODE_LABELS = {"rag": "RAG дня 23 (свободный текст)", "cited": "цитаты + «не знаю»"}


def cited_articles(text):
    """Номера статей, на которые ссылается свободный ответ: [ст.81], «статья 81»."""
    found = re.findall(r"(?:ст\.\s*|стать[яиейюёь]+\s+)(\d+(?:\.\d+)?)", text, flags=re.I)
    return sorted(set(found), key=lambda x: float(x))


class Agent:
    def __init__(self, model=MODEL, temperature=0, **params):
        self.model = model
        self.temperature = temperature
        self.params = params          # threshold, k_before, k_after, rerank_min
        self._index = None

    @property
    def index(self):
        if self._index is None:
            self._index = index.Index()
        return self._index

    def ask(self, question, mode="cited", gate_min=None, **params):
        if mode not in MODES:
            raise ValueError(f"неизвестный режим {mode!r}, есть {MODES}")
        hits, trace = retrieval.retrieve(self.index, question, **{**self.params, **params})
        url = self.index.info["source"]
        usages = [trace["stages"]["rerank"]] if "rerank" in trace["stages"] else []

        if mode == "rag":
            if hits:
                reply = providers.call(self.model, rag.messages(question, hits),
                                       temperature=self.temperature, max_tokens=600)
                usages.append(reply)
                text = reply["text"]
            else:
                text = "В найденных статьях ответа нет."
            result = {"unknown": False, "stage": "answer", "answer": text, "clarify": [],
                      "sources": [citations.source_info(h, url) for h in hits
                                  if h["article"] in cited_articles(text)],
                      "quotes": [], "rejected": []}
        else:
            result, replies = citations.answer(
                question, hits, trace, url, self.temperature,
                citations.GATE_MIN if gate_min is None else gate_min)
            usages += replies

        total = lambda key: sum(u.get(key) or 0 for u in usages)
        return {
            **result,
            "mode": mode,
            "question": question,
            "context": [{"chunk_id": h["chunk_id"], "article": h["article"], "score": h["score"],
                         "rerank": h.get("rerank"), "text": h["text"]} for h in hits],
            "candidates": [{k: c[k] for k in ("chunk_id", "article", "score", "rerank", "status")}
                           for c in trace["candidates"]],
            "best_rerank": max((h.get("rerank") or 0 for h in hits), default=None),
            "prompt_tokens": total("prompt_tokens"),
            "completion_tokens": total("completion_tokens"),
            "cost": round(total("cost"), 6),
            "seconds": round(total("seconds") + trace["embed_seconds"], 2),
            "llm_calls": len(usages),
        }

    def compare(self, question, **params):
        return {mode: self.ask(question, mode, **params) for mode in MODES}
