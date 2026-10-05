"""Локальная модель в терминале.

    python3 cli.py "Сколько будет 17 * 23?"   # один запрос
    python3 cli.py                             # диалог с историей
    python3 cli.py --model llama3.2:3b         # другая скачанная модель

Команды диалога: /new — забыть историю, /models — список моделей, /quit — выход.
"""

import argparse
import sys

import local_llm


def main():
    parser = argparse.ArgumentParser(description="Чат с локальной LLM через Ollama")
    parser.add_argument("question", nargs="*")
    parser.add_argument("--model", default=local_llm.MODEL)
    parser.add_argument("--temperature", type=float)
    args = parser.parse_args()
    try:
        local_llm.check(args.model)
    except RuntimeError as error:
        print(error)
        return 1

    if args.question:
        result = local_llm.ask(" ".join(args.question), args.model, temperature=args.temperature)
        print(result["text"])
        print(f"  ({local_llm.metrics_line(result)})")
        return 0

    print(f"{args.model} локально через Ollama {local_llm.version()}. /new, /models, /quit")
    history = []
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line == "/quit":
            return 0
        if line == "/new":
            history = []
            print("история очищена")
            continue
        if line == "/models":
            for m in local_llm.models():
                print(f"  {m['name']:20} {m['params']:>6} {m['size_gb']} ГБ")
            continue
        history.append({"role": "user", "content": line})
        try:
            result = local_llm.chat(history, args.model, temperature=args.temperature)
        except RuntimeError as error:
            history.pop()
            print(error)
            continue
        history.append({"role": "assistant", "content": result["text"]})
        print(f"\n{result['text']}")
        print(f"  ({local_llm.metrics_line(result)})")


if __name__ == "__main__":
    sys.exit(main())
