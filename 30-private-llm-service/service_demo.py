"""Проверка приватного LLM-сервиса по сети: доступ, ключи, нагрузка, rate limit, контекст.

Ходит на адрес в локальной сети (не localhost), как пошёл бы телефон или другой компьютер.
Сервер должен быть запущен: python3 server.py

    python3 service_demo.py                     # все блоки, ~5–8 мин
    python3 service_demo.py --only load --tag parallel2   # только нагрузка, отдельной меткой

Пишет data/service_result.json (дописывает блоки, не затирая чужие) и печатает таблицы.
"""

import argparse
import json
import pathlib
import statistics
import threading
import time
import urllib.error
import urllib.request

import limits
import local_llm
import server

RESULT = pathlib.Path(__file__).with_name("data") / "service_result.json"
QUESTION = ("Сколько будет 17 + 25? Объясни решение в двух-трёх предложениях "
            "и в последней строке напиши ответ числом.")
LOAD_TOKENS = 120
WAVES = [1, 4, 8, 12]
SEQUENTIAL = 30
SECRET = "ФИАЛКА-7392"


def call(url, key, body=None, path="/v1/chat/completions", timeout=300, raw=None):
    """→ (status, json, headers, seconds). Ошибки HTTP — тоже результат, не исключение."""
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url + path, data=data, headers=headers)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response), dict(response.headers), round(time.monotonic() - started, 2)
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            parsed = json.loads(payload)
        except ValueError:
            parsed = {"raw": payload[:200].decode(errors="replace")}
        return error.code, parsed, dict(error.headers), round(time.monotonic() - started, 2)


def err(resp):
    return resp.get("error", {}).get("type", "")


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else None


# ---------- 1. сеть ----------

def block_network(url, lan_ip):
    print(f"\n=== 1. Доступ по сети: {url}")
    out = {}
    status, body, _, sec = call(url, None, path="/health")
    out["health"] = {"status": status, "seconds": sec, "body": body}
    print(f"  GET {url}/health → {status} за {sec} с {body}")
    request = urllib.request.Request(url + "/")
    with urllib.request.urlopen(request, timeout=5) as r:
        status = r.status
    out["page"] = status
    print(f"  GET {url}/ (страница чата) → {status}")
    for target, label in [(f"http://{lan_ip}:11434", "ollama_lan"), ("http://127.0.0.1:11434", "ollama_local")]:
        try:
            with urllib.request.urlopen(target + "/api/version", timeout=3) as r:
                out[label] = f"открыт ({r.status})"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            out[label] = f"закрыт ({getattr(error, 'reason', error)})"
        print(f"  Ollama {target} → {out[label]}")
    return out


# ---------- 2. ключи ----------

def block_auth(url, key):
    print("\n=== 2. Ключи")
    body = {"messages": [{"role": "user", "content": "привет"}], "max_tokens": 5}
    cases = [("без ключа", None), ("чужой ключ", "sk-local-000000000000000000000000"), ("свой ключ", key)]
    out = []
    for label, k in cases:
        status, resp, _, sec = call(url, k, body)
        out.append({"case": label, "status": status, "error": err(resp)})
        print(f"  {label:12} → {status} {err(resp)}")
    status, resp, _, _ = call(url, None, path="/stats")
    out.append({"case": "/stats без ключа", "status": status, "error": err(resp)})
    print(f"  /stats без ключа → {status}")
    return out


# ---------- 3. нагрузка ----------

def wave(url, key, n):
    results = [None] * n
    peak = {"running": 0, "waiting": 0}
    stop = threading.Event()

    def worker(i):
        body = {"messages": [{"role": "user", "content": QUESTION}], "max_tokens": LOAD_TOKENS, "temperature": 0}
        results[i] = call(url, key, body)

    def watch():
        while not stop.is_set():
            status, s, _, _ = call(url, key, path="/stats", timeout=5)
            if status == 200:
                peak["running"] = max(peak["running"], s["running"])
                peak["waiting"] = max(peak["waiting"], s["waiting"])
            time.sleep(0.2)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    started = time.monotonic()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = round(time.monotonic() - started, 2)
    stop.set()
    ok = [r for r in results if r[0] == 200]
    lat = [r[3] for r in ok]
    waits = [r[1]["gateway"]["queue_wait_s"] for r in ok]
    right = sum("42" in r[1]["choices"][0]["message"]["content"] for r in ok)
    codes = {}
    for r in results:
        codes[r[0]] = codes.get(r[0], 0) + 1
    busy_s = [r[3] for r in results if r[0] == 503]
    return {"n": n, "codes": codes, "ok": len(ok), "right": right, "wall_s": wall,
            "p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "max": max(lat) if lat else None,
            "queue_wait_max": max(waits) if waits else None,
            "busy_reply_s": max(busy_s) if busy_s else None,
            "tok_per_s": round(statistics.mean(r[1]["gateway"]["tok_per_s"] for r in ok), 1) if ok else None,
            "peak_running": peak["running"], "peak_waiting": peak["waiting"],
            "rps": round(len(ok) / wall, 2)}


def block_load(url, key):
    print(f"\n=== 3. Стабильность: волны параллельных запросов (ключ loadtest), ответ ≤{LOAD_TOKENS} токенов")
    call(url, key, {"messages": [{"role": "user", "content": "ок"}], "max_tokens": 1})   # прогрев модели
    out = {"waves": []}
    print(f"  {'парал.':>6} {'коды':>18} {'«42»':>5} {'стена':>7} {'p50':>6} {'p95':>6} {'max':>6} "
          f"{'очередь≤':>9} {'пик раб/ожид':>13} {'ток/с':>6}")
    for n in WAVES:
        w = wave(url, key, n)
        out["waves"].append(w)
        codes = " ".join(f"{k}×{v}" for k, v in sorted(w["codes"].items()))
        print(f"  {n:>6} {codes:>18} {w['right']:>3}/{w['ok']:<1} {w['wall_s']:>6}с {w['p50']:>6} {w['p95']:>6} "
              f"{w['max']:>6} {w['queue_wait_max']:>8}с {str(w['peak_running']) + '/' + str(w['peak_waiting']):>13} "
              f"{w['tok_per_s']:>6}")
        time.sleep(1)

    print(f"  {SEQUENTIAL} запросов подряд:")
    lat, codes, right = [], {}, 0
    for i in range(SEQUENTIAL):
        status, resp, _, sec = call(url, key, {"messages": [{"role": "user", "content": QUESTION}],
                                               "max_tokens": LOAD_TOKENS, "temperature": 0})
        codes[status] = codes.get(status, 0) + 1
        if status == 200:
            lat.append(sec)
            right += "42" in resp["choices"][0]["message"]["content"]
    first, last = lat[:10], lat[-10:]
    out["sequential"] = {"n": SEQUENTIAL, "codes": codes, "right": right, "p50": pct(lat, 0.5),
                         "max": max(lat), "first10_mean": round(statistics.mean(first), 2),
                         "last10_mean": round(statistics.mean(last), 2)}
    s = out["sequential"]
    print(f"    коды {codes}, «42» {right}/{SEQUENTIAL}, p50 {s['p50']} с, max {s['max']} с, "
          f"первые 10 — {s['first10_mean']} с, последние 10 — {s['last10_mean']} с")
    return out


# ---------- 4. rate limit ----------

def block_rate(url, key, rpm):
    burst = limits.TokenBucket.burst(rpm)
    print(f"\n=== 4. Rate limit: ключ owner, {rpm}/мин, burst {burst}. Ждём, пока ведро наполнится…")
    time.sleep(burst * 60 / rpm + 1)
    body = {"messages": [{"role": "user", "content": "1+1?"}], "max_tokens": 3}
    rows = []
    started = time.monotonic()
    for i in range(15):
        status, resp, headers, sec = call(url, key, body)
        rows.append({"i": i + 1, "t": round(time.monotonic() - started, 2), "status": status,
                     "retry_after": headers.get("Retry-After"), "remaining": headers.get("X-RateLimit-Remaining")})
    line = " ".join(str(r["status"]) for r in rows)
    print(f"  15 запросов за {rows[-1]['t']} с: {line}")
    retry = int(next(r["retry_after"] for r in rows if r["status"] == 429))
    print(f"  первый 429 просит подождать {retry} с; ждём и повторяем…")
    time.sleep(retry)
    status, _, _, _ = call(url, key, body)
    print(f"  после паузы → {status}")
    return {"rpm": rpm, "burst": burst, "rows": rows, "ok": sum(r["status"] == 200 for r in rows),
            "limited": sum(r["status"] == 429 for r in rows), "retry_after": retry, "after_wait": status}


# ---------- 5. контекст ----------

def long_text(target_tokens):
    """Текст Конституции нужной длины (≈3,7 символа на токен) с секретом в самом начале."""
    path = pathlib.Path(__file__).resolve().parent.parent / "29-local-optimization" / "data" / "constitution.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    text = "\n".join(p if isinstance(p, str) else str(p.get("text", p))
                     for a in data["articles"] for p in a["paragraphs"])
    head = f"Кодовое слово: {SECRET}. Запомни его.\n\n"
    return head + text[:int(target_tokens * 3.7)]


def ask_secret(text):
    return [{"role": "user", "content": text + f"\n\nКакое кодовое слово было названо в самом начале? "
                                              f"Ответь только кодовым словом."}]


def block_context(url, key):
    print(f"\n=== 5. Контекст: окно {limits.MAX_CONTEXT}, ответ ≤{limits.MAX_OUTPUT}")
    out = {}

    big = ask_secret(long_text(5000))
    r = local_llm.chat(big, options={"num_ctx": limits.MAX_CONTEXT, "num_predict": 20, "temperature": 0})
    out["ollama_direct"] = {"chars": len(big[0]["content"]), "prompt_tokens": r["prompt_tokens"],
                            "answer": r["text"].strip(), "found": SECRET in r["text"], "seconds": r["total_s"]}
    print(f"  Ollama напрямую, ~5000 токенов в окно 4096: prompt_eval_count={r['prompt_tokens']}, "
          f"ответ «{r['text'].strip()[:60]}» — секрет {'найден' if SECRET in r['text'] else 'ПОТЕРЯН'}, ошибки нет")

    status, resp, _, sec = call(url, key, {"messages": big, "max_tokens": 20, "temperature": 0})
    out["gateway_big"] = {"status": status, "error": err(resp), "seconds": sec,
                          "message": resp.get("error", {}).get("message")}
    print(f"  тот же текст через шлюз → {status} {err(resp)} за {sec} с: {out['gateway_big']['message']}")

    fit = ask_secret(long_text(3300))
    status, resp, _, sec = call(url, key, {"messages": fit, "max_tokens": 20, "temperature": 0})
    if status == 200:
        g, u = resp["gateway"], resp["usage"]
        answer = resp["choices"][0]["message"]["content"].strip()
        out["gateway_fit"] = {"status": 200, "estimate": g["estimate"], "real": u["prompt_tokens"],
                              "answer": answer, "found": SECRET in answer, "seconds": sec}
        print(f"  ~3300 токенов через шлюз → 200 за {sec} с, оценка {g['estimate']} / реально {u['prompt_tokens']}, "
              f"ответ «{answer[:40]}» — секрет {'найден' if SECRET in answer else 'не найден'}")
    else:
        out["gateway_fit"] = {"status": status, "error": err(resp)}
        print(f"  ~3300 токенов через шлюз → {status} {err(resp)}")

    status, resp, _, sec = call(url, key, {"messages": [{"role": "user", "content":
                                 "Напиши подробное эссе на 3000 слов об истории вычислительной техники."}],
                                 "max_tokens": 5000, "temperature": 0})
    g = resp.get("gateway", {})
    out["max_tokens"] = {"status": status, "asked": 5000, "got": resp.get("usage", {}).get("completion_tokens"),
                         "clipped": g.get("max_tokens_clipped"),
                         "finish": resp.get("choices", [{}])[0].get("finish_reason"), "seconds": sec}
    m = out["max_tokens"]
    print(f"  max_tokens=5000 → {status}, сгенерировано {m['got']}, обрезано до {limits.MAX_OUTPUT}: {m['clipped']}, "
          f"finish_reason={m['finish']}, {sec} с")

    many = [{"role": "user" if i % 2 == 0 else "assistant", "content": "ок"} for i in range(41)]
    status, resp, _, _ = call(url, key, {"messages": many})
    out["many_messages"] = {"status": status, "error": err(resp)}
    print(f"  41 сообщение → {status} {err(resp)}")

    status, resp, _, _ = call(url, key, raw=b'{"messages": [{"role": "user", "content": "' + b"a" * 300000 + b'"}]}')
    out["big_body"] = {"status": status, "error": err(resp)}
    print(f"  тело 300 КБ → {status} {err(resp)}")

    status, resp, _, _ = call(url, key, raw=b"{broken")
    out["broken_json"] = {"status": status, "error": err(resp)}
    print(f"  битый JSON → {status} {err(resp)}")
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["network", "auth", "load", "rate", "context"])
    parser.add_argument("--tag", default="", help="метка для блока нагрузки (например parallel2)")
    args = parser.parse_args()

    ips = server.lan_addresses()
    lan_ip = ips[0] if ips else "127.0.0.1"
    url = f"http://{lan_ip}:{server.PORT}"
    keys = {v["name"]: (k, v["rpm"]) for k, v in limits.load_keys().items()}
    owner, load = keys["owner"], keys["loadtest"]

    data = json.loads(RESULT.read_text(encoding="utf-8")) if RESULT.exists() else {}
    data.update({"date": time.strftime("%Y-%m-%d %H:%M"), "url": url, "model": local_llm.MODEL,
                 "ollama": local_llm.version(),
                 "limits": {"context": limits.MAX_CONTEXT, "max_output": limits.MAX_OUTPUT,
                            "inflight": limits.MAX_INFLIGHT, "queue": limits.MAX_QUEUE,
                            "owner_rpm": owner[1], "loadtest_rpm": load[1]}})
    steps = {
        "network": lambda: block_network(url, lan_ip),
        "auth": lambda: block_auth(url, owner[0]),
        "load": lambda: block_load(url, load[0]),
        "rate": lambda: block_rate(url, owner[0], owner[1]),
        "context": lambda: block_context(url, load[0]),
    }
    for name, step in steps.items():
        if args.only and name != args.only:
            continue
        data[name + (f"_{args.tag}" if args.tag else "")] = step()
        RESULT.parent.mkdir(exist_ok=True)
        RESULT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    status, s, _, _ = call(url, owner[0], path="/stats")
    print(f"\n/stats сервера: {json.dumps(s, ensure_ascii=False)}")
    print(f"→ {RESULT}")


if __name__ == "__main__":
    main()
