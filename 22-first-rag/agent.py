"""Агент с двумя режимами: без RAG (модель отвечает по памяти) и с RAG (по найденным статьям).

    agent = Agent()
    agent.ask("Сколько судей в Конституционном суде?", mode="rag")

web.py и rag_demo.py работают только через этот класс: сообщения собирает rag.py,
модель вызывает providers.py, поиск — index.py.
"""

import re

import index
import providers
import rag

MODEL = "deepseek-chat"
MODES = ("plain", "rag")
MODE_LABELS = {"plain": "без RAG", "rag": "с RAG"}


def cited_articles(text):
    """Номера статей, на которые ссылается ответ: [ст.81], «статья 81», «ст. 67.1»."""
    found = re.findall(r"(?:ст\.\s*|стать[яиейюёь]+\s+)(\d+(?:\.\d+)?)", text, flags=re.I)
    return sorted(set(found), key=lambda x: float(x))


class Agent:
    def __init__(self, k=5, model=MODEL, temperature=0):
        self.k = k
        self.model = model
        self.temperature = temperature
        self._index = None

    @property
    def index(self):
        if self._index is None:
            self._index = index.Index()
        return self._index

    def retrieve(self, question):
        return self.index.search(question, self.k)

    def ask(self, question, mode="rag"):
        """Один вопрос в одном режиме. Возвращает ответ, найденные чанки и расход."""
        if mode not in MODES:
            raise ValueError(f"неизвестный режим {mode!r}, есть {MODES}")
        hits, embed_seconds = self.retrieve(question) if mode == "rag" else (None, 0)
        messages = rag.messages(question, hits)
        reply = providers.call(self.model, messages, temperature=self.temperature,
                               max_tokens=600)
        return {
            "mode": mode,
            "question": question,
            "answer": reply["text"],
            "cited": cited_articles(reply["text"]),
            "hits": [{"chunk_id": h["chunk_id"], "article": h["article"], "score": h["score"],
                      "parts": h["parts"], "amended": h["amended"], "text": h["text"]}
                     for h in hits or []],
            "prompt_tokens": reply["prompt_tokens"],
            "completion_tokens": reply["completion_tokens"],
            "cost": reply["cost"],
            "seconds": round(reply["seconds"] + embed_seconds, 2),
            "prompt_chars": sum(len(m["content"]) for m in messages),
        }

    def compare(self, question):
        return {mode: self.ask(question, mode) for mode in MODES}
