"""Индекс документации проекта: чанки по заголовкам → bge-m3 → SQLite.

Нарезка — стратегия structure дня 21 (она там выиграла у фиксированного окна).
В эмбеддинг вместе с текстом уходит «заголовок документа › раздел»: без него чанк
«| 8025 |» не знает, про какой он день.

    python3 index.py              # собрать index.db (~30 с на M1)
    python3 index.py "вопрос"     # топ-6 из терминала
"""

import array
import pathlib
import sqlite3
import sys
import time

import chunking
import corpus
import embeddings

DB_PATH = pathlib.Path(__file__).with_name("index.db")
TOP_K = 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id  TEXT PRIMARY KEY,    -- structure:17-mcp-tool/README.md:004
    source    TEXT NOT NULL,       -- путь от корня репозитория
    kind      TEXT NOT NULL,       -- root | day | docs
    title     TEXT NOT NULL,       -- заголовок документа
    section   TEXT NOT NULL,       -- путь заголовков внутри документа
    text      TEXT NOT NULL,
    vector    BLOB NOT NULL        -- float32 × 1024, L2-нормирован
);
CREATE TABLE IF NOT EXISTS build (
    fingerprint TEXT, documents INTEGER, chunks INTEGER, seconds REAL, built_at TEXT
);
"""


def connect(path=DB_PATH):
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def build(db=None, log=print):
    embeddings.check()
    db = db or connect()
    documents = corpus.load()
    chunks = chunking.chunk_corpus(documents, "structure")
    kinds = {d["source"]: d["kind"] for d in documents}
    log(f"корпус: {len(documents)} документов → {len(chunks)} чанков")

    started = time.monotonic()
    texts = [f"{c.title} › {c.section}\n\n{c.text}" for c in chunks]
    vectors = embeddings.embed(texts, on_batch=lambda done, total: print(
        f"\r  эмбеддинги {done}/{total}", end="", file=sys.stderr))
    print(file=sys.stderr)
    seconds = round(time.monotonic() - started, 1)

    with db:
        db.execute("DELETE FROM chunks")
        db.execute("DELETE FROM build")
        db.executemany(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(c.chunk_id, c.source, kinds[c.source], c.title, c.section, c.text,
              array.array("f", v).tobytes()) for c, v in zip(chunks, vectors)])
        db.execute("INSERT INTO build VALUES (?, ?, ?, ?, datetime('now'))",
                   (corpus.fingerprint(documents), len(documents), len(chunks), seconds))
    log(f"{len(chunks)} векторов за {seconds} с → {DB_PATH.name}")
    return {"documents": len(documents), "chunks": len(chunks), "seconds": seconds}


class Index:
    """Векторы в памяти: 300 чанков × 1024 — косинус перебором быстрее любого ANN."""

    def __init__(self, db=None):
        self.db = db or connect()
        self.reload()

    def reload(self):
        rows = self.db.execute("SELECT * FROM chunks").fetchall()
        self.rows = [dict(row) for row in rows]
        for row in self.rows:
            row["vector"] = array.array("f", row["vector"])
        meta = self.db.execute("SELECT * FROM build").fetchone()
        self.meta = dict(meta) if meta else {}

    @property
    def stale(self):
        """Документы изменились после сборки индекса."""
        return not self.meta or self.meta["fingerprint"] != corpus.fingerprint()

    def search(self, question, k=TOP_K):
        if not self.rows:
            raise RuntimeError("индекс пуст — собери его: python3 index.py")
        query, seconds = embeddings.embed_one(question)
        scored = []
        for row in self.rows:
            score = sum(a * b for a, b in zip(query, row["vector"]))
            scored.append((score, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        hits = [{k_: v for k_, v in row.items() if k_ != "vector"} | {"score": round(score, 3)}
                for score, row in scored[:k]]
        return hits, seconds


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            hits, seconds = Index().search(" ".join(sys.argv[1:]))
            print(f"эмбеддинг вопроса: {seconds} с")
            for h in hits:
                print(f"{h['score']:.3f}  {h['source']} › {h['section'][:60]}")
        else:
            build()
    except RuntimeError as error:
        print(f"ошибка: {error}", file=sys.stderr)
        sys.exit(1)
