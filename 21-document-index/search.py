"""Поиск по индексу из терминала.

    python3 search.py "почему Groq отдавал 403"
    python3 search.py "сколько сэкономил TOON" --strategy fixed -k 3
"""

import argparse
import sys

import chunking
import embeddings
import index_store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--strategy", choices=[*chunking.STRATEGIES, "all"], default="all")
    parser.add_argument("-k", type=int, default=3)
    args = parser.parse_args()

    vector, seconds = embeddings.embed_one(args.query)
    db = index_store.connect()
    names = list(chunking.STRATEGIES) if args.strategy == "all" else [args.strategy]
    print(f"запрос → вектор {len(vector)} за {seconds} с\n")
    for name in names:
        print(f"=== {name}")
        for hit in index_store.Index(db, name).search(vector, args.k):
            print(f"  {hit['score']:.3f}  {hit['chunk_id']}")
            print(f"         {hit['section']}  ({hit['n_chars']} симв.)")
            snippet = " ".join(hit["text"].split())[:160]
            print(f"         {snippet}…")
        print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        print(f"ошибка: {error}", file=sys.stderr)
        sys.exit(1)
