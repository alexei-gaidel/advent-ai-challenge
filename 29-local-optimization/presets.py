"""Пресеты локальной модели: лесенка от «как в дне 28» до оптимизированной.

Каждый следующий пресет — предыдущий плюс одно изменение, поэтому вклад каждого шага
виден отдельно. `quant-only` — контроль: база дня 28, поменяно только квантование.

    model        тег в Ollama
    options      опции запроса; None — не слать ничего, всё зашито в Modelfile
    rerank_k     сколько лучших по косинусу кандидатов отдать реранкеру
    frag_chars   обрезка текста фрагмента в промпте реранка (0 — целиком)
    prompt       промпт ответа: v1 — день 24, v2 — «сначала цитата, потом вывод»
    embed_cpu    bge-m3 на CPU (num_gpu 0): GPU-память целиком отдаётся LLM, без перезагрузок
"""

Q4 = "qwen2.5:7b"                       # Q4_K_M, 4,7 ГБ — модель дня 28
Q3 = "qwen2.5:7b-instruct-q3_K_M"       # 3,8 ГБ
TUNED = "qwen-constitution"             # Q3 + Modelfile: system, num_ctx, temperature

PRESETS = {
    "base": {"label": "до: день 28 (Q4, ctx 8192)", "model": Q4,
             "options": {"num_ctx": 8192, "temperature": 0},
             "rerank_k": 20, "frag_chars": 0, "prompt": "v1",
             "max_tokens": {"rerank": None, "answer": 900}},
    "params": {"label": "+ параметры (ctx 4096, топ-10, эмб. на CPU)", "model": Q4,
               "options": {"num_ctx": 4096, "temperature": 0},
               "rerank_k": 10, "frag_chars": 600, "prompt": "v1", "embed_cpu": True,
               "max_tokens": {"rerank": None, "answer": 400}},
    "prompt": {"label": "+ промпт «цитата → вывод»", "model": Q4,
               "options": {"num_ctx": 4096, "temperature": 0},
               "rerank_k": 10, "frag_chars": 600, "prompt": "v2", "embed_cpu": True,
               "max_tokens": {"rerank": None, "answer": 400}},
    "quant": {"label": "после: + Q3_K_M (Modelfile)", "model": TUNED, "options": None,
              "rerank_k": 10, "frag_chars": 600, "prompt": "v2", "embed_cpu": True,
              "max_tokens": {"rerank": None, "answer": 400}},
    "quant-only": {"label": "контроль: день 28 на Q3", "model": Q3,
                   "options": {"num_ctx": 8192, "temperature": 0},
                   "rerank_k": 20, "frag_chars": 0, "prompt": "v1",
                   "max_tokens": {"rerank": None, "answer": 900}},
}
ORDER = ["base", "params", "prompt", "quant", "quant-only"]
BEFORE, AFTER = "base", "quant"
