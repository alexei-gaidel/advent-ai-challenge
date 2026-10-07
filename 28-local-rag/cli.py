"""Локальный RAG в терминале: вопрос → ответ с цитатами, без сети и без ключей.

    python3 cli.py "Как Конституция определяет брак?"
    python3 cli.py --stack local-3b "..."
    python3 cli.py                       # вопросы по одному, /quit — выход
"""

import argparse
import sys

import agent
import embeddings
import llm


def show(r):
    print(f"\n{r['answer']}")
    for s in r["sources"]:
        print(f"  источник {s['chunk_id']:10} {s['source']}")
    for q in r["quotes"]:
        print(f"  ✓ «{q['text']}» [{q['chunk_id']}]")
    for q in r["rejected"]:
        print(f"  ✗ «{q['text']}» [{q['chunk_id']}] — {q['status']}")
    t = r["timing"]
    print(f"  ({r['seconds']} с: эмбеддинг {t['embed']} · реранк {t['rerank']} · ответ {t['answer']} · "
          f"загрузка {t['load']}; {'через сеть' if r['network'] else 'без сети'})")


def main():
    parser = argparse.ArgumentParser(description="RAG по Конституции РФ на локальной LLM")
    parser.add_argument("question", nargs="*")
    parser.add_argument("--stack", default=llm.DEFAULT_STACK, choices=list(llm.STACKS))
    args = parser.parse_args()
    try:
        embeddings.check()
        llm.check(args.stack)
    except RuntimeError as error:
        print(error)
        return 1
    bot = agent.Agent(args.stack)

    if args.question:
        show(bot.ask(" ".join(args.question)))
        return 0
    print(f"{llm.STACKS[args.stack]['label']}. /quit — выход")
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line == "/quit":
            return 0
        if line:
            try:
                show(bot.ask(line))
            except RuntimeError as error:
                print(error)


if __name__ == "__main__":
    sys.exit(main())
