"""Индекс Конституции: статьи → чанки → эмбеддинги bge-m3 → SQLite.

Нарезка по структуре, как победившая стратегия дня 21, только структура тут юридическая:
одна статья — один чанк. Статья длиннее MAX_CHARS (ст. 71, 72, 83, 125…) делится по
частям, а часть-перечень — по подпунктам «а)», «б)» с повторённой вводной фразой. В метаданных — номер статьи, глава, какие части вошли и была ли поправка 2020.

    python3 index.py              # собрать index.db (~20 с на M1)
    python3 index.py "вопрос"     # поиск топ-5 из терминала
"""

import array
import json
import pathlib
import re
import sqlite3
import sys
import time

import constitution
import embeddings

DB_PATH = pathlib.Path(__file__).with_name("index.db")
MAX_CHARS = 1500

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id  TEXT PRIMARY KEY,    -- ст.81 | ст.72#2 (вторая часть длинной статьи)
    article   TEXT NOT NULL,       -- 81, 67.1, ЗП (заключительные положения)
    chapter   TEXT NOT NULL,       -- «Глава 4. Президент Российской Федерации»
    parts     TEXT NOT NULL,       -- какие части статьи внутри: «1–3» или «весь текст»
    amended   INTEGER NOT NULL,    -- 1 — статья затронута поправками 2020
    text      TEXT NOT NULL,
    n_chars   INTEGER NOT NULL,
    vector    BLOB NOT NULL        -- float32 × 1024, L2-нормирован
);
CREATE TABLE IF NOT EXISTS build (
    model TEXT, built_at TEXT, seconds REAL, source TEXT
);
"""


def connect(path=DB_PATH):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def _part_number(paragraph):
    match = re.match(r"(\d+(?:\.\d+)?)\. ", paragraph)
    return match.group(1) if match else None


def _letter(paragraph):
    match = re.match(r"([а-я](?:\.\d+)?)\) ", paragraph)
    return match.group(1) if match else None


def _pack(units):
    """Жадно складывает единицы (списки абзацев) в группы не длиннее MAX_CHARS."""
    groups, current = [], []
    for unit in units:
        if current and sum(len(p) for p in current + unit) > MAX_CHARS:
            groups.append(current)
            current = []
        current += unit
    return groups + [current]


def _split(paragraphs):
    """Группы абзацев статьи: сначала по частям, а слишком длинную часть — по подпунктам.

    Подпункты «а)», «б)» без вводной фразы бессмысленны («назначает…» — кто?), поэтому
    вводная («Президент Российской Федерации:») повторяется в начале каждой группы.
    """
    units = []
    for p in paragraphs:
        if _part_number(p) or not units:
            units.append([p])
        else:
            units[-1].append(p)
    groups = []
    for unit in units:
        if sum(len(p) for p in unit) <= MAX_CHARS or len(unit) == 1:
            groups.append(("part", unit))
            continue
        lead, items = unit[0], [[p] for p in unit[1:]]
        for group in _pack(items):
            groups.append(("items", [lead] + group))
    # Короткие соседние части снова склеиваем, подпункты не трогаем.
    packed, current = [], []
    for kind, group in groups:
        if kind == "items":
            if current:
                packed.append(current)
                current = []
            packed.append(group)
        elif current and sum(len(p) for p in current + group) > MAX_CHARS:
            packed.append(current)
            current = group
        else:
            current = current + group
    return packed + ([current] if current else [])


def _label(group):
    numbers = [x for x in map(_part_number, group) if x]
    letters = [x for x in map(_letter, group) if x]
    parts = numbers[0] if len(numbers) == 1 else (f"{numbers[0]}–{numbers[-1]}" if numbers else "")
    if letters:
        span = letters[0] if len(letters) == 1 else f"{letters[0]}–{letters[-1]}"
        parts = f"{parts} п. {span}".strip()
    return parts


def chunk(articles):
    """Статья целиком или группы её частей / подпунктов не длиннее MAX_CHARS."""
    chunks = []
    for article in articles:
        paragraphs = article["paragraphs"]
        base = {"article": article["article"], "chapter": article["chapter"],
                "amended": int(article["amended"])}
        if sum(len(p) for p in paragraphs) <= MAX_CHARS:
            groups = [paragraphs]
        else:
            groups = _split(paragraphs)
        for n, group in enumerate(groups, 1):
            parts = "весь текст" if len(groups) == 1 else _label(group)
            chunk_id = f"ст.{article['article']}" + (f"#{n}" if len(groups) > 1 else "")
            header = article["title"] + ("" if len(groups) == 1 else f" ({parts})")
            text = f"{header}\n" + "\n".join(group)
            chunks.append({**base, "chunk_id": chunk_id, "parts": parts,
                           "text": text, "n_chars": len(text)})
    return chunks


def build(log=print):
    embeddings.check()
    data = constitution.load()
    chunks = chunk(data["articles"])
    sizes = sorted(c["n_chars"] for c in chunks)
    log(f"{len(data['articles'])} статей → {len(chunks)} чанков, "
        f"{sizes[0]}–{sizes[-1]} симв., медиана {sizes[len(sizes) // 2]}")
    started = time.monotonic()
    # Глава уходит в эмбеддинг вместе с текстом: «ч. 3» без неё не знает, о чём она.
    vectors = embeddings.embed(
        [f"Конституция РФ › {c['chapter'] or 'Заключительные положения'}\n{c['text']}"
         for c in chunks],
        on_batch=lambda done, total: print(f"\r  эмбеддинги {done}/{total}", end="",
                                           file=sys.stderr))
    print(file=sys.stderr)
    seconds = round(time.monotonic() - started, 1)
    db = connect()
    with db:
        db.execute("DELETE FROM chunks")
        db.execute("DELETE FROM build")
        for c, v in zip(chunks, vectors):
            db.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)",
                       (c["chunk_id"], c["article"], c["chapter"], c["parts"], c["amended"],
                        c["text"], c["n_chars"], array.array("f", v).tobytes()))
        db.execute("INSERT INTO build VALUES (?,?,?,?)",
                   (embeddings.MODEL, time.strftime("%Y-%m-%d %H:%M:%S"), seconds,
                    data["source"]))
    log(f"{len(vectors)} векторов × {len(vectors[0])} за {seconds} с → "
        f"{DB_PATH.name} ({DB_PATH.stat().st_size // 1024} КБ)")


class Index:
    """Все векторы в памяти, поиск — скалярное произведение с вектором вопроса."""

    def __init__(self, db=None):
        db = db or connect()
        self.rows = []
        for row in db.execute("SELECT * FROM chunks ORDER BY rowid"):
            item = dict(row)
            item["vector"] = array.array("f", item["vector"])
            self.rows.append(item)
        if not self.rows:
            raise RuntimeError("индекс пуст — python3 index.py")
        self.info = dict(db.execute("SELECT * FROM build").fetchone())

    def search(self, question, k=5):
        """Вопрос → топ-k чанков со score. Возвращает и время эмбеддинга вопроса."""
        vector, seconds = embeddings.embed_one(question)
        scored = sorted(((sum(a * b for a, b in zip(vector, r["vector"])), r)
                         for r in self.rows), key=lambda pair: pair[0], reverse=True)
        hits = [{**{key: val for key, val in r.items() if key != "vector"},
                 "score": round(score, 4)} for score, r in scored[:k]]
        return hits, seconds


def main():
    if len(sys.argv) > 1:
        hits, seconds = Index().search(" ".join(sys.argv[1:]))
        print(f"вектор вопроса за {seconds} с")
        for h in hits:
            first = h["text"].split("\n", 2)[1][:110]
            print(f"  {h['score']:.3f}  {h['chunk_id']:9} {'*' if h['amended'] else ' '} {first}…")
        return 0
    build()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        print(f"ошибка: {error}", file=sys.stderr)
        sys.exit(1)
