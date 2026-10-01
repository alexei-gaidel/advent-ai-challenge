"""Агент по Конституции РФ: RAG в четырёх режимах поиска.

    agent = Agent()
    agent.ask("Как Конституция определяет брак?", mode="rewrite+rerank")

Отбор чанков — retrieval.py, сборка промпта — rag.py, вызов модели — providers.py.
Если после порога и реранкера не осталось ни одного чанка, модель не вызывается:
агент сразу отвечает, что подходящих статей нет. Это и есть смысл фильтра —
не кормить модель мусором, на котором она начнёт додумывать.
"""

import re

import index
import providers
import rag
import retrieval

MODEL = "deepseek-chat"
MODES = retrieval.MODES
MODE_LABELS = retrieval.MODE_LABELS
EMPTY_ANSWER = "В Конституции не нашлось статей, относящихся к вопросу: все кандидаты отсечены фильтром."


def cited_articles(text):
    """Номера статей, на которые ссылается ответ: [ст.81], «статья 81», «ст. 67.1»."""
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

    def ask(self, question, mode="rerank", **params):
        """Один вопрос в одном режиме: ответ, trace отбора и расход по стадиям."""
        hits, trace = retrieval.retrieve(self.index, question, mode, **{**self.params, **params})
        stages = dict(trace["stages"])
        if hits:
            messages = rag.messages(question, hits)
            reply = providers.call(self.model, messages, temperature=self.temperature,
                                   max_tokens=600)
            answer = reply["text"]
            stages["answer"] = {"prompt_tokens": reply["prompt_tokens"],
                                "completion_tokens": reply["completion_tokens"],
                                "cost": reply["cost"] or 0, "seconds": reply["seconds"]}
            prompt_chars = sum(len(m["content"]) for m in messages)
        else:
            answer, prompt_chars = EMPTY_ANSWER, 0
        total = lambda key: sum(s[key] for s in stages.values())
        return {
            "mode": mode,
            "question": question,
            "answer": answer,
            "cited": cited_articles(answer),
            "hits": [{"chunk_id": h["chunk_id"], "article": h["article"], "score": h["score"],
                      "rerank": h.get("rerank"), "amended": h["amended"], "text": h["text"]}
                     for h in hits],
            "trace": trace,
            "stages": stages,
            "prompt_tokens": total("prompt_tokens"),
            "completion_tokens": total("completion_tokens"),
            "cost": round(total("cost"), 6),
            "seconds": round(total("seconds") + trace["embed_seconds"], 2),
            "context_chars": sum(len(h["text"]) for h in hits),
            "prompt_chars": prompt_chars,
        }

    def compare(self, question, modes=MODES, **params):
        return {mode: self.ask(question, mode, **params) for mode in modes}
