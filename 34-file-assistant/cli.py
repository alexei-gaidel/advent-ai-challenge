"""Файловый ассистент в терминале.

    python3 cli.py                     # список сценариев
    python3 cli.py docs                # сценарий по ключу: usages, invariants, docs, changelog
    python3 cli.py "найди, где ..."    # своя цель
    python3 cli.py --diff              # что лежит в черновике
    python3 cli.py --apply             # перенести черновик в файлы песочницы
    python3 cli.py --discard           # выбросить черновик
    python3 cli.py --reset             # пересобрать песочницу из слепка
"""

import sys

import assistant as fa

DIM, YELLOW, GREEN, RED, CYAN, VIOLET, RESET = ("\033[2m", "\033[33m", "\033[32m", "\033[31m",
                                                "\033[36m", "\033[35m", "\033[0m")


def colored_diff(text):
    for line in text.splitlines():
        color = (YELLOW if line.startswith(("+++", "---")) else CYAN if line.startswith("@@")
                 else GREEN if line.startswith("+") else RED if line.startswith("-") else "")
        print(f"{color}{line}{RESET}" if color else line)


def main():
    bot = fa.FileAssistant()
    arg = " ".join(sys.argv[1:]).strip()
    if not arg:
        for key, preset in fa.PRESETS.items():
            print(f"{key:11} {preset['title']}\n{DIM}            {preset['goal']}{RESET}")
        return 0
    commands = {"--diff": lambda: colored_diff(bot.diff()),
                "--apply": lambda: print(bot.apply()),
                "--discard": lambda: print(bot.discard()),
                "--reset": lambda: print(bot.reset())}
    if arg in commands:
        commands[arg]()
        return 0
    try:
        r = bot.run(arg)
    except (ValueError, RuntimeError) as error:
        print(f"ошибка: {error}")
        return 1
    for t in r["tools"]:
        args = " ".join(f"{k}={v!r}" for k, v in t["arguments"].items())
        mark = "" if t["ok"] else f" {RED}✗ {t['result'][:120]}{RESET}"
        color = YELLOW if t["name"] in ("write_file", "edit_file") else VIOLET
        print(f"{DIM}{t['round']:>2}{RESET} {color}{t['name']}{RESET} {DIM}{args[:150]}{RESET}{mark}")
    print(f"\n{r['answer']}\n")
    if r["changed"]:
        colored_diff(r["diff"])
        print(f"\n{YELLOW}изменения в черновике — применить: python3 cli.py --apply{RESET}")
    print(f"{DIM}вызовов {len(r['tools'])} · кругов {r['rounds']} · ошибок {r['errors']} · "
          f"{r['prompt_tokens']}+{r['completion_tokens']} токенов · ${r['cost']} · {r['seconds']} с{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
