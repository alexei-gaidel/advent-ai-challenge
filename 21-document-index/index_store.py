"""Индекс в SQLite: чанки с метаданными, их векторы и сведения о сборке.

Векторы лежат BLOB-ом float32 (array('f')) — 1024 × 4 байта на чанк. Для пары сотен
чанков отдельная векторная база не нужна: поиск грузит всё в память и считает
скалярное произведение с нормированным вектором запроса.
"""

import array
import pathlib
import sqlite3
import time

DB_PATH = pathlib.Path(__file__).with_name("index.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    strategy   TEXT NOT NULL,
    chunk_id   TEXT NOT NULL,
    source     TEXT NOT NULL,      -- путь файла от корня репо
    title      TEXT NOT NULL,      -- заголовок документа
    section    TEXT NOT NULL,      -- путь раздела «Сравнение › Токены»
    text       TEXT NOT NULL,
    start_char INTEGER NOT NULL,   -- где чанк лежит в исходном тексте
    end_char   INTEGER NOT NULL,
    n_chars    INTEGER NOT NULL,
    PRIMARY KEY (strategy, chunk_id)
);
CREATE TABLE IF NOT EXISTS vectors (
    strategy TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    dim      INTEGER NOT NULL,
    vector   BLOB NOT NULL,        -- float32, L2-нормирован
    PRIMARY KEY (strategy, chunk_id)
);
CREATE TABLE IF NOT EXISTS builds (
    strategy  TEXT PRIMARY KEY,
    model     TEXT NOT NULL,
    built_at  TEXT NOT NULL,
    seconds   REAL NOT NULL,       -- время эмбеддинга всех чанков
    stats     TEXT NOT NULL        -- JSON статистики нарезки
);
"""


def connect(path=DB_PATH):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def save(db, strategy, chunks, vectors, model, seconds, stats_json):
    """Перезаписывает индекс одной стратегии целиком."""
    with db:
        db.execute("DELETE FROM chunks WHERE strategy = ?", (strategy,))
        db.execute("DELETE FROM vectors WHERE strategy = ?", (strategy,))
        for chunk, vector in zip(chunks, vectors):
            row = chunk.as_dict()
            db.execute(
                "INSERT INTO chunks VALUES (:strategy, :chunk_id, :source, :title, :section,"
                " :text, :start_char, :end_char, :n_chars)", row)
            db.execute("INSERT INTO vectors VALUES (?, ?, ?, ?)",
                       (strategy, chunk.chunk_id, len(vector),
                        array.array("f", vector).tobytes()))
        db.execute("INSERT OR REPLACE INTO builds VALUES (?, ?, ?, ?, ?)",
                   (strategy, model, time.strftime("%Y-%m-%d %H:%M:%S"), seconds, stats_json))


class Index:
    """Векторы одной стратегии в памяти + поиск top-k по косинусу."""

    def __init__(self, db, strategy):
        self.strategy = strategy
        self.rows = []
        for row in db.execute(
                "SELECT c.*, v.vector FROM chunks c JOIN vectors v USING (strategy, chunk_id)"
                " WHERE c.strategy = ? ORDER BY c.rowid", (strategy,)):
            item = dict(row)
            item["vector"] = array.array("f", item["vector"])
            self.rows.append(item)
        if not self.rows:
            raise RuntimeError(f"индекс «{strategy}» пуст — python3 build_index.py")

    def search(self, query_vector, k=5):
        scored = [(sum(a * b for a, b in zip(query_vector, row["vector"])), row)
                  for row in self.rows]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [{**{key: val for key, val in row.items() if key != "vector"},
                 "score": round(score, 4)} for score, row in scored[:k]]


def builds(db):
    return [dict(row) for row in db.execute("SELECT * FROM builds ORDER BY strategy")]
