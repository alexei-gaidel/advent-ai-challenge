"""Мини-чат: история в SQLite, RAG на каждом ходу, источники всегда, память задачи.

    chat = Chat.create("Экзамен", mode="memory")
    chat.send("Готовлюсь к экзамену, сравниваю Президента и премьера")

Один ход:
1. окно — последние WINDOW сообщений из базы (полная история хранится, но в промпт
   длинный диалог целиком не идёт — как в продакшене с ограниченным контекстом);
2. taskmemory.update → память задачи + самостоятельный поисковый запрос;
3. retrieval.retrieve(запрос) → порог + реранкер; citations.answer → гейт «не знаю»,
   JSON-ответ с цитатами, проверка цитат кодом;
4. реплика, ответ с источниками и снимок памяти → в базу.

Режимы: `memory` — с памятью задачи, `history` — только окно истории (абляция).
"""

import index
import storage
import taskmemory
import citations
import retrieval

WINDOW = 6
MODES = ("memory", "history")
MODE_LABELS = {"memory": "история + RAG + память задачи", "history": "история + RAG"}

_INDEX = None


def shared_index():
    """Индекс один на процесс: векторы читаются из базы один раз."""
    global _INDEX
    if _INDEX is None:
        _INDEX = index.Index()
    return _INDEX


class Chat:
    def __init__(self, chat_id, db=None):
        self.db = db or storage.connect()
        self.info = storage.chat(self.db, chat_id)
        self.id = chat_id
        self.mode = self.info["mode"]

    @classmethod
    def create(cls, title, mode="memory", db=None):
        if mode not in MODES:
            raise ValueError(f"неизвестный режим {mode!r}, есть {MODES}")
        db = db or storage.connect()
        return cls(storage.create_chat(db, title, mode, taskmemory.empty()), db)

    @property
    def state(self):
        return storage.state(self.db, self.id) or taskmemory.empty()

    def history(self):
        return storage.messages(self.db, self.id)

    def set_state(self, fields, locked=None):
        """Ручная правка памяти со страницы; locked — поля, которые модель не трогает."""
        state = self.state
        for key in taskmemory.FIELDS:
            if key in fields:
                state[key] = fields[key]
        if locked is not None:
            state["locked"] = [k for k in locked if k in taskmemory.FIELDS]
        turn = max([s["turn"] for s in storage.states(self.db, self.id)] or [0])
        storage.save_state(self.db, self.id, turn, taskmemory._clean(state, state))
        return self.state

    def send(self, message):
        message = message.strip()
        if not message:
            raise ValueError("пустое сообщение")
        past = self.history()
        turn = len([m for m in past if m["role"] == "user"]) + 1
        window = [{"role": m["role"], "content": m["content"]} for m in past[-WINDOW:]]

        state, query, side, prep = taskmemory.update(self.state, window, message,
                                                     with_state=self.mode == "memory")
        idx = shared_index()
        hits, trace = retrieval.retrieve(idx, query)
        memory = taskmemory.block(state) if self.mode == "memory" else ""
        result, replies = citations.answer(message, hits, trace, idx.info["source"],
                                           history=window, memory=memory)

        usages = [prep] + ([trace["stages"]["rerank"]] if "rerank" in trace["stages"] else []) + replies
        total = lambda key: sum(u.get(key) or 0 for u in usages)
        meta = {
            "query": query, "side_question": side,
            "unknown": result["unknown"], "reason": result.get("reason"),
            "sources": result["sources"], "quotes": result["quotes"],
            "rejected": result["rejected"], "clarify": result["clarify"],
            "candidates": [{k: c[k] for k in ("chunk_id", "article", "score", "rerank", "status")}
                           for c in trace["candidates"][:10]],
            "prompt_tokens": total("prompt_tokens"), "completion_tokens": total("completion_tokens"),
            "cost": round(total("cost"), 6),
            "seconds": round(total("seconds") + trace["embed_seconds"], 2),
            "llm_calls": len(usages), "memory_chars": len(memory),
        }
        storage.add_message(self.db, self.id, turn, "user", message)
        storage.add_message(self.db, self.id, turn, "assistant", result["answer"], meta)
        storage.save_state(self.db, self.id, turn, state)
        return {"turn": turn, "answer": result["answer"], "state": state, **meta}
