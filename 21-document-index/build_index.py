"""Пайплайн индексации: корпус → чанки → эмбеддинги → SQLite.

    python3 build_index.py                    # обе стратегии
    python3 build_index.py --strategy fixed
"""

import argparse
import json
import sys
import time

import chunking
import corpus
import embeddings
import index_store


def build(strategy, documents, db, log=print):
    chunks = chunking.chunk_corpus(documents, strategy)
    stats = chunking.stats(documents, chunks)
    log(f"[{strategy}] {stats['chunks']} чанков, средний {stats['avg']} симв., "
        f"разрезано таблиц/кода: {stats['broken']}")

    started = time.monotonic()
    # Заголовок документа и раздела уходят в эмбеддинг вместе с текстом: без них чанк
    # «| 4 из 4 |» не знает, про какой он день. В базе текст хранится как есть.
    texts = [f"{c.title} › {c.section}\n\n{c.text}" for c in chunks]
    vectors = embeddings.embed(
        texts, on_batch=lambda done, total: print(f"\r  эмбеддинги {done}/{total}",
                                                  end="", file=sys.stderr))
    print(file=sys.stderr)
    seconds = round(time.monotonic() - started, 1)
    stats["embed_seconds"] = seconds
    stats["dim"] = len(vectors[0])

    index_store.save(db, strategy, chunks, vectors, embeddings.MODEL, seconds,
                     json.dumps(stats, ensure_ascii=False))
    log(f"[{strategy}] {len(vectors)} векторов × {stats['dim']} за {seconds} с → index.db")
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=[*chunking.STRATEGIES, "all"], default="all")
    args = parser.parse_args()

    embeddings.check()
    documents = corpus.load()
    total = sum(len(d["text"]) for d in documents)
    print(f"корпус: {len(documents)} документов, {total} символов ≈ {total / 1800:.0f} стр.")

    db = index_store.connect()
    names = list(chunking.STRATEGIES) if args.strategy == "all" else [args.strategy]
    for name in names:
        build(name, documents, db)
    size = index_store.DB_PATH.stat().st_size
    print(f"index.db: {size // 1024} КБ")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        print(f"ошибка: {error}", file=sys.stderr)
        sys.exit(1)
