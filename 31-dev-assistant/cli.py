"""Ассистент разработчика в терминале.

    python3 cli.py                          # REPL: /help …, /branch, /status, /files, /diff, /log
    python3 cli.py "/help где лежит MCP-клиент?"   # одна команда и выход
"""

import sys

import assistant as dev

DIM, BLUE, GREEN, VIOLET, RESET = "\033[2m", "\033[34m", "\033[32m", "\033[35m", "\033[0m"


def show(result):
    print(result["answer"])
    for tool in result.get("tools", []):
        color = VIOLET if tool.get("by") == "модель" else GREEN
        args = f" {tool['arguments']}" if tool.get("arguments") else ""
        print(f"{color}  MCP {tool['name']}{args} · {tool.get('by', '')}{RESET}")
    for s in result.get("sources", []):
        print(f"{DIM}  [{s['n']}] {s['source']} › {s['section'][:50]} · {s['score']}{RESET}")
    if result.get("kind") == "answer":
        print(f"{DIM}  {result['prompt_tokens']}+{result['completion_tokens']} токенов · "
              f"${result['cost']} · {result['seconds']} с"
              f"{' · индекс устарел: python3 index.py' if result.get('stale') else ''}{RESET}")


def main():
    bot = dev.DevAssistant()
    if len(sys.argv) > 1:
        try:
            show(bot.handle(" ".join(sys.argv[1:])))
        except (ValueError, RuntimeError) as error:
            print(f"ошибка: {error}")
            sys.exit(1)
        return
    print(f"Ассистент разработчика. {dev.DevAssistant.usage()}\nCtrl+D — выход.")
    while True:
        try:
            text = input(f"{BLUE}› {RESET}")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text.strip():
            continue
        try:
            show(bot.handle(text))
        except (ValueError, RuntimeError) as error:
            print(f"ошибка: {error}")


if __name__ == "__main__":
    main()
