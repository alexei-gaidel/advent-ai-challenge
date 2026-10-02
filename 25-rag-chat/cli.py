"""Тот же чат в терминале.

    python3 cli.py                 # новый чат с памятью задачи
    python3 cli.py --mode history  # без памяти (только окно истории)
    python3 cli.py --chat 3        # продолжить чат из chat.db

Команды: /memory — показать память задачи, /new — новый чат, /quit — выход.
"""

import argparse
import json
import sys

import chat


def show(result):
    print(f"\n{result['answer']}")
    if result["sources"]:
        for s in result["sources"]:
            print(f"  источник: {s['chunk_id']:9} {s['source']}")
    else:
        print("  источников нет — контекст слабый")
    for q in result["quotes"]:
        print(f"  ✓ «{q['text'][:140]}»")
    print(f"  (поиск: «{result['query']}», {result['llm_calls']} вызова LLM, ${result['cost']:.5f})\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=chat.MODES, default="memory")
    parser.add_argument("--chat", type=int)
    args = parser.parse_args()
    current = chat.Chat(args.chat) if args.chat else chat.Chat.create("CLI", args.mode)
    print(f"чат #{current.id} — {chat.MODE_LABELS[current.mode]}. /memory /new /quit")
    for line in sys.stdin if not sys.stdin.isatty() else iter(lambda: input("> "), None):
        line = line.strip()
        if not line:
            continue
        if line == "/quit":
            break
        if line == "/memory":
            print(json.dumps(current.state, ensure_ascii=False, indent=1))
            continue
        if line == "/new":
            current = chat.Chat.create("CLI", current.mode)
            print(f"чат #{current.id}")
            continue
        if not sys.stdin.isatty():
            print(f"> {line}")
        try:
            show(current.send(line))
        except RuntimeError as error:
            print(f"ошибка: {error}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print()
