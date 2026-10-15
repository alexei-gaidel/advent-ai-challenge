"""Ассистент поддержки в терминале.

    python3 cli.py                         # список тикетов
    python3 cli.py T-101                   # черновик ответа по тикету (RAG + CRM)
    python3 cli.py "Как подключить SDK?"   # вопрос о продукте без тикета (только RAG)
"""

import re
import sys

import assistant as support

DIM, YELLOW, GREEN, VIOLET, RESET = "\033[2m", "\033[33m", "\033[32m", "\033[35m", "\033[0m"


def main():
    bot = support.SupportAssistant()
    arg = " ".join(sys.argv[1:]).strip()
    if not arg:
        for t in bot.tickets():
            print(f"{t['id']}  {t['status']:12} {t['user_id']}  {t['subject']}")
        return 0
    try:
        if re.fullmatch(r"[Tt]-\d+", arg):
            r = bot.answer(ticket_id=arg.upper())
        else:
            r = bot.answer(question=arg, mode="rag")
    except (ValueError, RuntimeError) as error:
        print(f"ошибка: {error}")
        return 1
    if r["diagnosis"]:
        print(f"{YELLOW}ДИАГНОЗ:{RESET} {r['diagnosis']}\n")
    print(f"{GREEN}ОТВЕТ КЛИЕНТУ:{RESET}\n{r['reply']}\n")
    for t in r["tools"]:
        print(f"{VIOLET if t['by'] == 'модель' else GREEN}  MCP {t['name']} · {t['by']}{RESET}")
    for s in r["sources"]:
        print(f"{DIM}  [{s['n']}] {s['source']} › {s['section'][:60]} · {s['score']}{RESET}")
    print(f"{DIM}  {r['prompt_tokens']}+{r['completion_tokens']} токенов · ${r['cost']} · {r['seconds']} с{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
