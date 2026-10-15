"""RAG по документации продукта: README шлюза дня 30 + docs/faq.md + docs/errors.md.

Нарезка по заголовкам (стратегия structure дня 21), эмбеддинги bge-m3 в Ollama, векторы в
SQLite. Корпус маленький (~70 чанков), поэтому поиск — косинус перебором. Отпечаток корпуса
хранится в базе: поменялся FAQ — ассистент пометит ответ «индекс устарел».

    python3 index.py              # собрать index.db (~10 с)
    python3 index.py "вопрос"     # топ-5 из терминала
"""

import array
import hashlib
import pathlib
import sqlite3
import sys
import time

import chunking
import embeddings

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
DB_PATH = HERE / "index.db"
TOP_K = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,   -- structure:33-support-assistant/docs/faq.md:003
    source   TEXT NOT NULL,      -- путь от корня репозитория
    section  TEXT NOT NULL,      -- путь заголовков
    text     TEXT NOT NULL,
    vector   BLOB NOT NULL       -- float32 × 1024, L2-нормирован
);
CREATE TABLE IF NOT EXISTS build (fingerprint TEXT, chunks INTEGER, seconds REAL, built_at TEXT);
"""


def paths():
    return [ROOT / "30-private-llm-service" / "README.md", *sorted((HERE / "docs").glob("*.md"))]


def documents():
    docs = []
    for path in paths():
        text = path.read_text(encoding="utf-8")
        title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), path.stem)
        docs.append({"source": str(path.relative_to(ROOT)), "title": title, "text": text})
    return docs


def fingerprint(docs=None):
    digest = hashlib.sha256()
    for d in docs or documents():
        digest.update(d["source"].encode() + d["text"].encode())
    return digest.hexdigest()[:16]


def connect():
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def build(log=print):
    embeddings.check()
    docs = documents()
    chunks = chunking.chunk_corpus(docs, "structure")
    started = time.monotonic()
    vectors = embeddings.embed([f"{c.title} › {c.section}\n\n{c.text}" for c in chunks])
    seconds = round(time.monotonic() - started, 1)
    db = connect()
    with db:
        db.execute("DELETE FROM chunks")
        db.execute("DELETE FROM build")
        db.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                       [(c.chunk_id, c.source, c.section, c.text, array.array("f", v).tobytes())
                        for c, v in zip(chunks, vectors)])
        db.execute("INSERT INTO build VALUES (?, ?, ?, datetime('now'))",
                   (fingerprint(docs), len(chunks), seconds))
    log(f"{len(docs)} документа → {len(chunks)} чанков за {seconds} с → {DB_PATH.name}")


class Index:
    def __init__(self):
        db = connect()
        self.rows = [dict(r) for r in db.execute("SELECT * FROM chunks")]
        for r in self.rows:
            r["vector"] = array.array("f", r["vector"])
        meta = db.execute("SELECT * FROM build").fetchone()
        self.meta = dict(meta) if meta else {}

    @property
    def stale(self):
        return not self.meta or self.meta["fingerprint"] != fingerprint()

    def section(self, title):
        """Чанк по заголовку раздела — для фрагментов, которые нужны всегда, без поиска."""
        for r in self.rows:
            if r["section"].split(" › ")[-1] == title:
                return {"source": r["source"], "section": r["section"], "text": r["text"],
                        "score": "закреплён"}
        return None

    def search(self, question, k=TOP_K):
        if not self.rows:
            raise RuntimeError("индекс пуст — собери его: python3 index.py")
        query, _ = embeddings.embed_one(question)
        scored = sorted(((sum(a * b for a, b in zip(query, r["vector"])), r) for r in self.rows),
                        key=lambda pair: pair[0], reverse=True)
        return [{"source": r["source"], "section": r["section"], "text": r["text"],
                 "score": round(s, 3)} for s, r in scored[:k]]


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            for h in Index().search(" ".join(sys.argv[1:])):
                print(f"{h['score']:.3f}  {h['source']} › {h['section'][:70]}")
        else:
            build()
    except RuntimeError as error:
        print(f"ошибка: {error}", file=sys.stderr)
        sys.exit(1)
