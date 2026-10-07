"""Единый вызов LLM для пайплайна: локальная модель через Ollama или облачная через API.

Реранкер и генератор ответа зовут только llm.call() и не знают, где крутится модель.
Стек — пара (реранкер, генератор); в локальном стеке обе модели в Ollama на этом Mac,
эмбеддинги bge-m3 — там же, поэтому полностью локальный RAG не трогает сеть и .env.

    llm.call("qwen2.5:7b", messages, json_mode=True)   # → localhost:11434
    llm.call("deepseek-chat", messages, json_mode=True) # → api.deepseek.com
"""

import local_llm
import providers

STACKS = {
    "cloud":    {"label": "облако: deepseek-chat", "rerank": "deepseek-chat", "answer": "deepseek-chat"},
    "local-7b": {"label": "локально: qwen2.5:7b", "rerank": "qwen2.5:7b", "answer": "qwen2.5:7b"},
    "local-3b": {"label": "локально: qwen2.5:3b", "rerank": "qwen2.5:3b", "answer": "qwen2.5:3b"},
}
DEFAULT_STACK = "local-7b"


def is_local(model):
    return model not in providers.BY_ID


def is_local_stack(stack):
    s = STACKS[stack]
    return is_local(s["rerank"]) and is_local(s["answer"])


def call(model, messages, temperature=0, max_tokens=800, json_mode=False):
    """→ {text, prompt_tokens, completion_tokens, seconds, load_seconds, cost, local}."""
    if is_local(model):
        r = local_llm.chat(messages, model, temperature=temperature, max_tokens=max_tokens,
                           json_mode=json_mode)
        return {"text": r["text"], "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["output_tokens"], "seconds": r["total_s"],
                "load_seconds": r["load_s"], "tok_per_s": r["tok_per_s"],
                "finish_reason": r["done_reason"], "cost": 0.0, "local": True}
    r = providers.call(model, messages, temperature=temperature, max_tokens=max_tokens,
                       response_format={"type": "json_object"} if json_mode else None)
    return {"text": r["text"], "prompt_tokens": r["prompt_tokens"],
            "completion_tokens": r["completion_tokens"], "seconds": r["seconds"],
            "load_seconds": 0.0, "tok_per_s": None, "finish_reason": r["finish_reason"],
            "cost": r["cost"] or 0.0, "local": False}


def check(stack):
    """Нужные локальные модели скачаны — иначе понятная ошибка до первого вопроса."""
    for model in {STACKS[stack]["rerank"], STACKS[stack]["answer"]}:
        if is_local(model):
            local_llm.check(model)
