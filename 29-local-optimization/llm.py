"""Единый вызов локальной LLM по пресету (presets.py). Облака в дне 29 нет — только судья.

    llm.call(presets.PRESETS["quant"], messages, "answer", json_mode=True)

Реранкер и генератор не знают ни модели, ни опций: всё берётся из пресета.
"""

import local_llm
import presets


def call(preset, messages, role, json_mode=False, max_tokens=None):
    """role — "rerank" или "answer": от неё зависит лимит выдачи из пресета."""
    limit = max_tokens or preset["max_tokens"].get(role)
    r = local_llm.chat(messages, preset["model"], max_tokens=limit, json_mode=json_mode,
                       options=preset["options"])
    return {"text": r["text"], "prompt_tokens": r["prompt_tokens"],
            "completion_tokens": r["output_tokens"], "seconds": r["total_s"],
            "load_seconds": r["load_s"], "prompt_seconds": r["prompt_s"],
            "eval_seconds": r["eval_s"], "tok_per_s": r["tok_per_s"],
            "prompt_tok_per_s": r["prompt_tok_per_s"], "finish_reason": r["done_reason"],
            "cost": 0.0, "local": True}


def check(name):
    local_llm.check(presets.PRESETS[name]["model"])
