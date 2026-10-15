"""Ассистент разработчика: команды чата, RAG по документации и MCP к репозиторию.

Команды:
    /help <вопрос>    ответ о проекте: документация (RAG) + git-контекст (MCP)
    /branch           текущая ветка — прямой вызов MCP, без модели
    /status           что не закоммичено
    /files [папка]    файлы под git
    /diff [путь]      незакоммиченные изменения
    /log [папка]      последние коммиты
    текст без «/»     то же, что /help

Как собирается ответ /help:
    1. git_branch вызывается кодом всегда — ветку модель не выбирает и не выдумывает;
    2. топ-6 чанков документации по косинусу bge-m3;
    3. DeepSeek получает ветку и фрагменты, а остальные инструменты MCP (status, files,
       diff, log, read_file) вызывает сама через function calling, если вопрос про
       текущее состояние кода, а не про документацию.
"""

import json
import re
import time

import index as rag
import providers
import tools as mcp_tools

MODEL = "deepseek-chat"
MAX_TOOL_ROUNDS = 4
TOOL_RESULT_CHARS = 6000

# Режимы нужны сравнению в help_demo.py; веб и CLI всегда работают в full.
MODES = {
    "llm": "только модель",
    "rag": "RAG без MCP",
    "full": "RAG + MCP",
}

SYSTEM = """Ты — ассистент разработчика в репозитории Advent AI Challenge.
Отвечай по-русски, коротко и по делу: 2–6 предложений или короткий список.

Источники правды, по убыванию:
1. Результаты инструментов (git, файлы) — это текущее состояние репозитория.
2. Фрагменты документации [1]…[N] — README, CLAUDE.md, docs/.
Ссылайся на фрагменты номерами в квадратных скобках: «порт 8025 [2]».
Называй конкретные пути к папкам и файлам, когда они известны.
Если ни фрагменты, ни инструменты ответа не дают — так и скажи, не выдумывай.
<<TOOLS_RULE>>"""

TOOLS_RULE = """Вопросы о текущем состоянии (что изменено, какие файлы есть, что в коде) —
вызывай инструменты, документация про это может устареть. Читай файл через read_file,
если документация не отвечает на вопрос о коде."""

LAST_ROUND = ("Лимит вызовов инструментов исчерпан, новых вызовов не будет. Ответь обычным "
              "текстом по уже полученным данным; чего не хватило — так и скажи.")
LEAK = re.compile(r"DSML|<\s*\|?\s*(tool_call|invoke)", re.I)

EXPLAIN = """Объясни, что делает файл {path} проекта Advent AI Challenge.
Верни JSON: {"summary": "...", "functions": ["..."]}

{code}"""

NO_CONTEXT ="""Ты — ассистент разработчика в репозитории Advent AI Challenge
(ежедневные задания по LLM на Python). Отвечай по-русски, коротко. Если не знаешь — скажи."""


class DevAssistant:
    def __init__(self, index=None, bridge=None, model=MODEL):
        self.index = index or rag.Index()
        self.bridge = bridge or mcp_tools.ToolBridge()
        self.model = model

    # --- команды --------------------------------------------------------------

    def handle(self, text):
        """Разбор строки чата → результат-словарь для веба и CLI."""
        text = (text or "").strip()
        if not text:
            raise ValueError("пустая команда — попробуй /help как устроен проект")
        if not text.startswith("/"):
            return self.help(text)
        command, _, rest = text.partition(" ")
        rest = rest.strip()
        command = command.lower()
        if command == "/help":
            if not rest:
                return {"command": "/help", "answer": self.usage(), "kind": "usage"}
            return self.help(rest)
        direct = {
            "/branch": ("git_branch", {}),
            "/status": ("git_status", {}),
            "/files": ("git_files", {"path": rest} if rest else {}),
            "/diff": ("git_diff", {"path": rest} if rest else {}),
            "/log": ("git_log", {"path": rest} if rest else {}),
            "/grep": ("grep", {"query": rest}),
        }
        if command == "/explain":
            return self.explain(rest)
        if command not in direct:
            raise ValueError(f"неизвестная команда {command} — список: /help")
        name, arguments = direct[command]
        started = time.monotonic()
        output = self.bridge.call(name, arguments)
        return {"command": command, "kind": "tool", "answer": output,
                "tools": [{"name": name, "arguments": arguments, "by": "команда"}],
                "seconds": round(time.monotonic() - started, 2)}

    def explain(self, path):
        """/explain путь — краткое объяснение файла моделью."""
        code = self.bridge.call("read_file", {"path": path, "lines": 400})
        print(f"[explain] {path}, ключ {providers.get_api_key('DEEPSEEK_API_KEY')}")
        prompt = EXPLAIN.format(path=path, code=code)
        reply = providers.call(self.model, [{"role": "user", "content": prompt}],
                               temperature=0.2, max_tokens=600)
        return {"command": "/explain", "kind": "answer", "answer": reply["text"],
                "prompt_tokens": reply["prompt_tokens"],
                "completion_tokens": reply["completion_tokens"], "cost": reply["cost"],
                "seconds": reply["seconds"], "sources": [], "tools": []}

    @staticmethod
    def usage():
        return ("/help <вопрос> — спросить о проекте (документация + git)\n"
                "/branch — текущая ветка\n/status — что не закоммичено\n"
                "/files [папка] — файлы под git\n/diff [путь] — изменения\n"
                "/log [папка] — последние коммиты")

    # --- /help ----------------------------------------------------------------

    def help(self, question, mode="full", temperature=0.2):
        started = time.monotonic()
        result = {"command": "/help", "kind": "answer", "question": question, "mode": mode,
                  "branch": None, "sources": [], "tools": [], "stale": False,
                  "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0, "rounds": 0}

        if mode == "llm":
            messages = [{"role": "system", "content": NO_CONTEXT},
                        {"role": "user", "content": question}]
            return self._finish(result, messages, [], temperature, started)

        hits, embed_seconds = self.index.search(question)
        result["stale"] = self.index.stale
        result["embed_seconds"] = embed_seconds
        result["sources"] = [{"n": n, "source": h["source"], "section": h["section"],
                              "score": h["score"]} for n, h in enumerate(hits, 1)]
        fragments = "\n\n".join(f"[{n}] {h['source']} › {h['section']}\n{h['text']}"
                                for n, h in enumerate(hits, 1))

        specs, context = [], ""
        if mode == "full":
            branch = self.bridge.call("git_branch", {})
            result["branch"] = branch.splitlines()[0].replace("Ветка: ", "")
            result["tools"].append({"name": "git_branch", "arguments": {}, "by": "код",
                                    "result": branch})
            context = f"Git-контекст (MCP git_branch, получен только что):\n{branch}\n\n"
            specs = self.bridge.specs(exclude=("git_branch",))

        system = SYSTEM.replace("<<TOOLS_RULE>>", TOOLS_RULE if specs else "")
        user = f"{context}Фрагменты документации:\n\n{fragments}\n\nВопрос: {question}"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        return self._finish(result, messages, specs, temperature, started)

    def _finish(self, result, messages, specs, temperature, started):
        """Цикл function calling: модель зовёт инструменты MCP, пока не ответит текстом."""
        conversation = list(messages)
        for round_number in range(MAX_TOOL_ROUNDS + 1):
            last = round_number == MAX_TOOL_ROUNDS
            if last and specs:
                # Без этой фразы DeepSeek на последнем круге, где инструментов уже нет,
                # печатал вызов текстом — сырую разметку <｜DSML｜invoke…> (прогон v1).
                conversation.append({"role": "user", "content": LAST_ROUND})
            reply = providers.call(self.model, conversation, temperature=temperature,
                                   max_tokens=900, tools=None if (last or not specs) else specs)
            result["prompt_tokens"] += reply["prompt_tokens"]
            result["completion_tokens"] += reply["completion_tokens"]
            result["cost"] = round(result["cost"] + (reply["cost"] or 0), 6)
            if not reply["tool_calls"]:
                break
            result["rounds"] += 1
            conversation.append(reply["message"])
            for call in reply["tool_calls"]:
                function = call.get("function") or {}
                name = function.get("name", "")
                arguments = mcp_tools.parse_arguments(function.get("arguments"))
                output = self.bridge.call(name, arguments)
                result["tools"].append({"name": name, "arguments": arguments, "by": "модель",
                                        "result": output[:1500]})
                conversation.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                     "content": output[:TOOL_RESULT_CHARS]})
        result["answer"] = reply["text"]
        if LEAK.search(result["answer"]):
            result["answer"] = ("(модель вместо ответа выдала разметку вызова инструмента — "
                                "ответа нет, переформулируй вопрос)")
            result["leak"] = True
        result["seconds"] = round(time.monotonic() - started, 2)
        return result


if __name__ == "__main__":
    import sys
    print(json.dumps(DevAssistant().handle(" ".join(sys.argv[1:]) or "/branch"),
                     ensure_ascii=False, indent=2))
