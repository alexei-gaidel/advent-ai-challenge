"""Ограничения приватного LLM-сервиса: ключи, rate limit, очередь, размер контекста.

Логика дня живёт здесь, server.py только вызывает её и переводит LimitError в HTTP-ответ.

    ключ          → кто ты (401)
    TokenBucket   → не чаще N запросов в минуту на ключ (429 + Retry-After)
    check_request → тело, сообщения, оценка токенов против окна модели (400 / 413)
    Gate          → в Ollama одновременно не больше MAX_INFLIGHT, ещё MAX_QUEUE ждут (503)

Окно контекста задаёт шлюз, а не клиент: num_ctx всегда MAX_CONTEXT. Это важно дважды.
Ollama молча обрезает промпт, который не влез в num_ctx (видно только в логе сервера), —
шлюз вместо этого отвечает 413. И разные num_ctx в запросах заставляют Ollama
перезагружать модель — клиент не должен уметь так сделать.
"""

import collections
import hmac
import os
import pathlib
import secrets
import statistics
import threading
import time

MAX_BODY = 256 * 1024       # байт в теле: json.dumps пишет кириллицу как \uXXXX — 6 байт на символ
MAX_MESSAGES = 40           # сообщений в истории
MAX_CONTEXT = 4096          # num_ctx модели: промпт + ответ
MAX_OUTPUT = 512            # потолок max_tokens
DEFAULT_OUTPUT = 256        # если клиент не указал max_tokens
MAX_INFLIGHT = int(os.environ.get("LLM_SERVICE_INFLIGHT", 2))   # одновременно в Ollama
MAX_QUEUE = int(os.environ.get("LLM_SERVICE_QUEUE", 6))         # ждут своей очереди
QUEUE_TIMEOUT = 120         # сколько секунд ждать места, потом 503
DEFAULT_RPM = 10            # запросов в минуту на ключ, если в .env не указано
ROLES = ("system", "user", "assistant")


class LimitError(Exception):
    """Запрос отклонён шлюзом до похода в модель."""

    def __init__(self, status, kind, message, retry_after=None):
        super().__init__(message)
        self.status, self.kind, self.retry_after = status, kind, retry_after


# ---------- ключи ----------

def find_env_file():
    """Ищет .env рядом со скриптом и выше по дереву — общий файл лежит в корне репо."""
    for folder in pathlib.Path(__file__).resolve().parents:
        candidate = folder / ".env"
        if candidate.exists():
            return candidate
    return None


def _env_value(name):
    if os.environ.get(name):
        return os.environ[name]
    env_file = find_env_file()
    if env_file:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def load_keys():
    """LLM_SERVICE_KEYS=имя:ключ[:запросов_в_минуту],… → {ключ: {"name", "rpm"}}."""
    keys = {}
    for item in _env_value("LLM_SERVICE_KEYS").split(","):
        parts = item.strip().split(":")
        if len(parts) >= 2 and parts[1]:
            rpm = int(parts[2]) if len(parts) > 2 and parts[2] else DEFAULT_RPM
            keys[parts[1]] = {"name": parts[0], "rpm": rpm}
    return keys


def init_keys():
    """Дописывает в корневой .env два ключа: личный (10/мин) и для нагрузочного теста (600/мин)."""
    if load_keys():
        return None
    env_file = find_env_file() or pathlib.Path(__file__).resolve().parent.parent / ".env"
    line = (f"LLM_SERVICE_KEYS=owner:sk-local-{secrets.token_hex(12)}:{DEFAULT_RPM},"
            f"loadtest:sk-local-{secrets.token_hex(12)}:600")
    text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    env_file.write_text(text.rstrip("\n") + ("\n" if text else "") + line + "\n", encoding="utf-8")
    return env_file


def authenticate(header, keys):
    """'Bearer sk-…' → запись ключа. Сравнение за постоянное время."""
    token = (header or "").removeprefix("Bearer ").strip()
    for key, info in keys.items():
        if token and hmac.compare_digest(token, key):
            return info
    raise LimitError(401, "invalid_api_key", "нет ключа или ключ неверный: заголовок "
                                              "Authorization: Bearer <ключ>")


def mask(key):
    return key[:9] + "…" + key[-3:] if len(key) > 14 else "…"


# ---------- rate limit ----------

class TokenBucket:
    """Ведро на ключ: ёмкость burst, пополняется rpm жетонами в минуту."""

    def __init__(self):
        self.buckets = {}
        self.lock = threading.Lock()

    @staticmethod
    def burst(rpm):
        return max(3, rpm // 5)

    def take(self, name, rpm):
        now = time.monotonic()
        rate, cap = rpm / 60, self.burst(rpm)
        with self.lock:
            tokens, last = self.buckets.get(name, (cap, now))
            tokens = min(cap, tokens + (now - last) * rate)
            if tokens < 1:
                self.buckets[name] = (tokens, now)
                wait = (1 - tokens) / rate
                raise LimitError(429, "rate_limit_exceeded",
                                 f"лимит {rpm} запросов в минуту на ключ «{name}», "
                                 f"повторите через {wait:.1f} с", retry_after=max(1, round(wait + 0.5)))
            self.buckets[name] = (tokens - 1, now)
            return int(tokens - 1)


# ---------- размер запроса ----------

def estimate_tokens(messages):
    """Оценка токенов промпта без токенизатора (у Ollama его нет в API).

    Калибровано на qwen2.5:3b по prompt_eval_count Ollama (2026-10-12):
    - текст Конституции (85% кириллицы) — 3,66–3,85 символа на токен, английский — 3,7–3,8;
    - без system шаблон Qwen подставляет свой («You are Qwen…») — 30 токенов, со своим — ~9;
    - каждое сообщение — ещё ~6 токенов разметки.
    Берём 3,3 символа на токен: ошибка на 10–16% в большую сторону. Лучше лишний 413,
    чем молчаливая обрезка промпта.
    """
    has_system = any(m["role"] == "system" for m in messages)
    chars = sum(len(m["content"]) for m in messages)
    return (9 if has_system else 30) + 6 * len(messages) + int(chars / 3.3) + 1


def check_request(body):
    """Проверяет OpenAI-подобное тело и возвращает то, что пойдёт в Ollama.

    → {"messages", "options", "stream", "estimate", "max_tokens", "clipped"}
    """
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise LimitError(400, "invalid_request", "messages — непустой список")
    if len(messages) > MAX_MESSAGES:
        raise LimitError(413, "too_many_messages",
                         f"в истории {len(messages)} сообщений, максимум {MAX_MESSAGES}")
    clean = []
    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ROLES or not isinstance(m.get("content"), str):
            raise LimitError(400, "invalid_request",
                             "каждое сообщение — {role: system|user|assistant, content: строка}")
        clean.append({"role": m["role"], "content": m["content"]})

    asked = body.get("max_tokens")
    if asked is None:
        max_tokens = DEFAULT_OUTPUT
    elif isinstance(asked, int) and not isinstance(asked, bool) and asked > 0:
        max_tokens = min(asked, MAX_OUTPUT)
    else:
        raise LimitError(400, "invalid_request", "max_tokens — положительное целое")

    temperature = body.get("temperature", 0.7)
    if not isinstance(temperature, (int, float)) or isinstance(temperature, bool) or not 0 <= temperature <= 2:
        raise LimitError(400, "invalid_request", "temperature — число от 0 до 2")

    estimate = estimate_tokens(clean)
    if estimate + max_tokens > MAX_CONTEXT:
        raise LimitError(413, "context_length_exceeded",
                         f"промпт ≈{estimate} токенов + ответ {max_tokens} > окна {MAX_CONTEXT}. "
                         f"Сократите историю или текст (на промпт остаётся ≈{MAX_CONTEXT - max_tokens})")

    options = {"num_ctx": MAX_CONTEXT, "num_predict": max_tokens, "temperature": temperature}
    return {"messages": clean, "options": options, "stream": bool(body.get("stream")),
            "estimate": estimate, "max_tokens": max_tokens, "clipped": asked is not None and asked > MAX_OUTPUT}


# ---------- очередь ----------

class Gate:
    """Не больше MAX_INFLIGHT запросов в модели, не больше MAX_QUEUE в ожидании."""

    def __init__(self, inflight=MAX_INFLIGHT, queue=MAX_QUEUE, timeout=QUEUE_TIMEOUT):
        self.slots = threading.BoundedSemaphore(inflight)
        self.inflight_max, self.queue_max, self.timeout = inflight, queue, timeout
        self.lock = threading.Lock()
        self.waiting = 0
        self.running = 0

    def enter(self):
        """Ждёт место; возвращает, сколько секунд простоял в очереди."""
        start = time.monotonic()
        if self.slots.acquire(blocking=False):
            with self.lock:
                self.running += 1
            return 0.0
        with self.lock:
            if self.waiting >= self.queue_max:
                raise LimitError(503, "server_busy",
                                 f"сервер занят: {self.running} в работе, {self.waiting} в очереди "
                                 f"(максимум {self.queue_max})", retry_after=10)
            self.waiting += 1
        try:
            got = self.slots.acquire(timeout=self.timeout)
        finally:
            with self.lock:
                self.waiting -= 1
        if not got:
            raise LimitError(503, "server_busy", f"не дождались места за {self.timeout} с", retry_after=10)
        with self.lock:
            self.running += 1
        return round(time.monotonic() - start, 2)

    def leave(self):
        with self.lock:
            self.running -= 1
        self.slots.release()


# ---------- статистика ----------

class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.started = time.time()
        self.status = collections.Counter()
        self.latency = collections.deque(maxlen=500)
        self.queue_wait = collections.deque(maxlen=500)
        self.tokens = collections.Counter()
        self.estimate_ratio = collections.deque(maxlen=500)

    def record(self, status, seconds=None, wait=None, result=None, estimate=None):
        with self.lock:
            self.status[status] += 1
            if seconds is not None:
                self.latency.append(seconds)
            if wait is not None:
                self.queue_wait.append(wait)
            if result:
                self.tokens["prompt"] += result["prompt_tokens"]
                self.tokens["output"] += result["output_tokens"]
                if estimate and result["prompt_tokens"]:
                    self.estimate_ratio.append(estimate / result["prompt_tokens"])

    def snapshot(self, gate):
        def pct(values, q):
            values = sorted(values)
            return round(values[min(len(values) - 1, int(q * len(values)))], 2) if values else None
        with self.lock:
            return {
                "uptime_s": round(time.time() - self.started),
                "requests": dict(self.status),
                "running": gate.running, "waiting": gate.waiting,
                "limits": {"inflight": gate.inflight_max, "queue": gate.queue_max,
                           "context": MAX_CONTEXT, "max_output": MAX_OUTPUT, "body_bytes": MAX_BODY},
                "latency_p50": pct(self.latency, 0.5), "latency_p95": pct(self.latency, 0.95),
                "queue_wait_p95": pct(self.queue_wait, 0.95),
                "tokens": dict(self.tokens),
                "estimate_vs_real": round(statistics.mean(self.estimate_ratio), 2) if self.estimate_ratio else None,
            }
