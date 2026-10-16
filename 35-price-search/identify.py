"""Опознание товара: фото и/или описание → карточка для поиска.

Фото читает qwen/qwen3.8-27b на Groq — у DeepSeek зрения нет. Только текст — deepseek-chat.
Модель не ищет цены и не выбирает магазин: она говорит, ЧТО искать — бренд, модель, вариант
(цвет, память, размер), 1–3 поисковых запроса и признаки «не тот товар» (чехол, амбушюры,
б/у). Если по фото модель не определить, карточка честно говорит «уточните» и задаёт вопрос,
а не отправляет поиск наугад.

Большие фото уменьшаются системной утилитой macOS sips до 1024 px по длинной стороне:
у Groq есть лимит на размер картинки, а детали упаковки и надписи на 1024 px читаются.

    python3 identify.py "наушники сони с шумодавом пятое поколение"
    python3 identify.py photo.jpg ["уточнение текстом"]
"""

import base64
import json
import pathlib
import subprocess
import sys
import tempfile
import time

import providers

VISION_MODEL = "qwen/qwen3.8-27b"
TEXT_MODEL = "deepseek-chat"
MAX_SIDE = 1024
MAX_BYTES = 8 * 1024 * 1024

PROMPT = """Определи товар, который человек хочет купить<<SOURCE>>, чтобы найти ИМЕННО ЕГО на
Ozon, Wildberries и Яндекс.Маркете и сравнить цены.
<<TEXT>>
Ответь JSON:
{"brand": "бренд или null", "model": "точная модель или null",
 "variant": {"color": null, "memory": null, "size": null, "other": null},
 "category": "что это, по-русски, 1–3 слова",
 "title": "каноническое название товара для человека",
 "queries": ["1–3 поисковых запроса для маркетплейса: бренд + модель + важный вариант,
              без лишних слов; первый — самый точный"],
 "must": ["0–3 токена с цифрами в нижнем регистре, которые ВСЕ обязаны быть в названии
           того же товара: артикул модели, номер поколения, объём памяти («wh-1000xm5»,
           «16», «128»); пробелы и дефисы не учитываются; слова без цифр сюда не пиши"],
 "must_not": ["слова, которые выдают аксессуар или другой товар: чехол, кейс, амбушюры,
              плёнка, стекло, ремешок, запчасть, б/у, уценка, реплика, копия — по смыслу товара"],
 "confidence": "high | medium | low",
 "question": "если confidence = low — что спросить у человека, иначе null"}
Не выдумывай модель: если по <<WHAT>> её нельзя определить, ставь model = null,
confidence = low и задай вопрос. Если у товара есть поколения или версии, которые внешне
почти не отличаются (AirPods Pro 1/2/3, Kindle разных лет), а по <<WHAT>> не видно какая —
тоже confidence = low и спроси, какое поколение; без этого найдётся «не тот» товар."""


def shrink(image_bytes):
    """Уменьшить фото до MAX_SIDE по длинной стороне (sips — встроен в macOS) → JPEG."""
    if len(image_bytes) > MAX_BYTES:
        raise ValueError(f"фото больше {MAX_BYTES // 1024 // 1024} МБ")
    with tempfile.TemporaryDirectory() as folder:
        source = pathlib.Path(folder) / "in"
        target = pathlib.Path(folder) / "out.jpg"
        source.write_bytes(image_bytes)
        result = subprocess.run(["sips", "-s", "format", "jpeg", "-Z", str(MAX_SIDE),
                                 str(source), "--out", str(target)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode != 0 or not target.exists():
            raise ValueError("не удалось прочитать картинку — нужен JPEG, PNG или HEIC")
        return target.read_bytes()


def identify(text=None, image=None, temperature=0.0):
    """→ {card, model, prompt_tokens, completion_tokens, cost, seconds}."""
    text = (text or "").strip()
    if not text and not image:
        raise ValueError("нужно фото или описание товара")
    started = time.monotonic()
    if image:
        jpeg = shrink(image)
        prompt = (PROMPT.replace("<<SOURCE>>", " (по фото" + (" и описанию)" if text else ")"))
                  .replace("<<TEXT>>", f"Описание от человека: {text}\n" if text else "")
                  .replace("<<WHAT>>", "фото"))
        content = [{"type": "text", "text": prompt},
                   {"type": "image_url", "image_url": {
                       "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}]
        model = VISION_MODEL
    else:
        content = (PROMPT.replace("<<SOURCE>>", " (по описанию)")
                   .replace("<<TEXT>>", f"Описание от человека: {text}\n")
                   .replace("<<WHAT>>", "описанию"))
        model = TEXT_MODEL
    reply = providers.call(model, [{"role": "user", "content": content}],
                           temperature=temperature, max_tokens=1500,
                           response_format={"type": "json_object"})
    try:
        card = json.loads(reply["text"])
    except json.JSONDecodeError:
        raise RuntimeError(f"модель вернула не JSON: {reply['text'][:200]}")
    card = normalize(card)
    return {"card": card, "model": model, "prompt_tokens": reply["prompt_tokens"],
            "completion_tokens": reply["completion_tokens"], "cost": reply["cost"] or 0.0,
            "seconds": round(time.monotonic() - started, 2)}


def normalize(card):
    """Привести ответ модели к форме, на которую рассчитан код."""
    card = card if isinstance(card, dict) else {}
    variant = card.get("variant") if isinstance(card.get("variant"), dict) else {}
    as_list = lambda v: [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
    queries = as_list(card.get("queries"))[:3]
    if not queries and card.get("title"):
        queries = [str(card["title"])]
    confidence = card.get("confidence") if card.get("confidence") in ("high", "medium", "low") else "low"
    return {
        "brand": card.get("brand") or None, "model": card.get("model") or None,
        "variant": {k: variant.get(k) or None for k in ("color", "memory", "size", "other")},
        "category": card.get("category") or "", "title": card.get("title") or "",
        "queries": queries, "must": [m.lower() for m in as_list(card.get("must"))][:4],
        "must_not": [m.lower() for m in as_list(card.get("must_not"))][:15],
        "confidence": confidence,
        "question": card.get("question") if confidence == "low" else None,
    }


if __name__ == "__main__":
    args = sys.argv[1:] or ["наушники сони с шумодавом пятое поколение"]
    path = pathlib.Path(args[0])
    if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".heic", ".webp") and path.exists():
        result = identify(" ".join(args[1:]), path.read_bytes())
    else:
        result = identify(" ".join(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
