"""RAG для ревью: документация проекта + код папок, которые трогает PR.

Корпус собирается заново на каждый PR, потому что код меняется вместе с ним:
    документы — README.md, CLAUDE.md, NN-*/README.md, 31-dev-assistant/docs/*.md;
    код       — *.py папок из diff, нарезанные по функциям и классам верхнего уровня.
Весь код репо (~60 тыс. строк) на CPU-раннере GitHub за разумное время не проэмбеддить,
а соседний код за пределами затронутых папок ревью почти не нужен.

Эмбеддинги — bge-m3 в Ollama, как в днях 21–31. Векторы кешируются в SQLite по sha256
текста: в CI кеш живёт между прогонами (actions/cache), и повторный PR эмбеддит только
новые чанки. Ollama нет — поиск переключается на BM25, и ревью пишет об этом в шапке.
"""

import array
import collections
import hashlib
import math
import pathlib
import re
import sqlite3
import time

import chunking
import embeddings

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = pathlib.Path(__file__).with_name("data") / "embed_cache.db"
CODE_MAX = 2500
PER_FILE = 4
LIMIT = 12
# Отдельный запрос за правилами проекта: diff сам по себе редко похож на текст правил.
# Разделы CLAUDE.md, которые при pin_rules идут в контекст всегда, без поиска.
PINNED = ("Жёсткие правила", "Наследование кода", "Грабли, на которые уже наступали")
RULES_QUERY = ("жёсткие правила проекта: только стандартная библиотека, ключи в .env, "
               "web.py не собирает messages, грабли и известные ошибки")


def doc_paths(root):
    found = [root / "README.md", root / "CLAUDE.md"]
    for folder in sorted(root.iterdir()):
        if folder.is_dir() and re.match(r"\d\d-", folder.name) and folder.name != "32-code-review":
            if (folder / "README.md").exists():
                found.append(folder / "README.md")
    found += sorted((root / "31-dev-assistant" / "docs").glob("*.md"))
    return [p for p in found if p.exists()]


def doc_chunks(root):
    chunks = []
    for path in doc_paths(root):
        text = path.read_text(encoding="utf-8")
        title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")),
                     path.parent.name)
        doc = {"source": str(path.relative_to(root)), "title": title, "text": text}
        for c in chunking.chunk_structure(doc):
            chunks.append({"id": c.chunk_id.replace("structure:", "doc:"), "kind": "doc",
                           "source": c.source, "where": c.section, "text": c.text,
                           "embed": f"{c.title} › {c.section}\n\n{c.text}"})
    return chunks


TOP = re.compile(r"^(def |class |async def |@)")


def code_chunks(root, folders):
    """Функции и классы верхнего уровня; шапка модуля (импорты, константы) — отдельным чанком."""
    chunks = []
    for folder in folders:
        for path in sorted((root / folder).glob("*.py")):
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            starts = [0]
            for i, line in enumerate(lines):
                # Декоратор начинает блок, а def под ним — уже нет.
                if TOP.match(line) and i and not lines[i - 1].startswith("@"):
                    starts.append(i)
            starts.append(len(lines))
            source = str(path.relative_to(root))
            for a, b in zip(starts, starts[1:]):
                block = lines[a:b]
                while block and not block[-1].strip():
                    block.pop()
                # Длинный блок режем по строкам, номер строки в id сохраняем.
                piece, first = [], a
                for offset, line in enumerate(block):
                    piece.append(line)
                    if sum(len(x) + 1 for x in piece) > CODE_MAX:
                        chunks.append(_code(source, first, piece))
                        piece, first = [], a + offset + 1
                if piece and any(x.strip() for x in piece):
                    chunks.append(_code(source, first, piece))
    return chunks


def _code(source, first, piece):
    start, end = first + 1, first + len(piece)
    name = next((l.split("(")[0].split(":")[0] for l in piece if TOP.match(l) and not
                 l.startswith("@")), "шапка модуля")
    text = "\n".join(piece)
    return {"id": f"code:{source}:{start}-{end}", "kind": "code", "source": source,
            "where": f"строки {start}–{end} · {name}", "text": text,
            "embed": f"{source} · {name}\n\n{text}"}


# --- поиск -----------------------------------------------------------------------

def _cache():
    CACHE.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(CACHE)
    db.execute("CREATE TABLE IF NOT EXISTS vectors (hash TEXT PRIMARY KEY, vector BLOB)")
    return db


class Retriever:
    def __init__(self, root, folders, mode="auto", log=print):
        started = time.monotonic()
        self.chunks = doc_chunks(root) + code_chunks(root, folders)
        self.mode = mode
        self.stats = {"docs": sum(c["kind"] == "doc" for c in self.chunks),
                      "code": sum(c["kind"] == "code" for c in self.chunks),
                      "embedded": 0, "cached": 0}
        if mode in ("auto", "embed"):
            try:
                embeddings.check()
                self._embed(log)
                self.mode = "bge-m3"
            except RuntimeError as error:
                if mode == "embed":
                    raise
                log(f"Ollama недоступна ({error}) — поиск BM25")
                self.mode = "bm25"
        if self.mode == "bm25":
            self._bm25_index()
        self.stats["seconds"] = round(time.monotonic() - started, 1)

    def _embed(self, log):
        db = _cache()
        hashes = [hashlib.sha256(c["embed"].encode()).hexdigest() for c in self.chunks]
        known = {}
        for i in range(0, len(hashes), 500):
            part = hashes[i:i + 500]
            rows = db.execute(f"SELECT hash, vector FROM vectors WHERE hash IN "
                              f"({','.join('?' * len(part))})", part).fetchall()
            known.update({h: array.array("f", v) for h, v in rows})
        missing = [i for i, h in enumerate(hashes) if h not in known]
        if missing:
            log(f"эмбеддинги: новых чанков {len(missing)} из {len(hashes)}")
            vectors = embeddings.embed([self.chunks[i]["embed"] for i in missing])
            with db:
                for i, v in zip(missing, vectors):
                    known[hashes[i]] = array.array("f", v)
                    db.execute("INSERT OR REPLACE INTO vectors VALUES (?, ?)",
                               (hashes[i], array.array("f", v).tobytes()))
        db.close()
        self.stats["embedded"], self.stats["cached"] = len(missing), len(hashes) - len(missing)
        for c, h in zip(self.chunks, hashes):
            c["vector"] = known[h]

    def _bm25_index(self):
        self.tokens = [collections.Counter(_tokens(c["embed"])) for c in self.chunks]
        self.lengths = [sum(t.values()) for t in self.tokens]
        self.avg = sum(self.lengths) / max(len(self.lengths), 1)
        df = collections.Counter(w for t in self.tokens for w in t)
        n = len(self.chunks)
        self.idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}

    def search(self, query, k):
        if self.mode == "bge-m3":
            q = embeddings.embed([query])[0]
            scored = [(sum(a * b for a, b in zip(q, c["vector"])), c) for c in self.chunks]
        else:
            words = _tokens(query)
            scored = []
            for c, t, length in zip(self.chunks, self.tokens, self.lengths):
                s = sum(self.idf.get(w, 0) * t[w] * 2.2 /
                        (t[w] + 1.2 * (0.25 + 0.75 * length / self.avg)) for w in words if w in t)
                scored.append((s, c))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [(round(s, 3), c) for s, c in scored[:k]]

    def pinned(self):
        """Правила проекта из CLAUDE.md целиком — без поиска."""
        return [{**{k: v for k, v in c.items() if k not in ("vector", "embed")},
                 "score": 1.0, "query": "закреплено"}
                for c in self.chunks if c["source"] == "CLAUDE.md"
                and c["where"].split(" › ")[-1] in PINNED]

    def for_diff(self, files, pin_rules=False):
        """Запрос на каждый изменённый файл + запрос за правилами → до LIMIT чанков.
        pin_rules — разделы правил CLAUDE.md сверх лимита, а запрос за правилами не нужен."""
        picked = self.pinned() if pin_rules else []
        seen = {c["id"] for c in picked}
        queries = [(f["path"], f["path"] + "\n" + "\n".join(f["added"].values())[:1500])
                   for f in files if not f["skipped"] and f["added"]]
        if not pin_rules:
            queries.append(("правила проекта", RULES_QUERY))
        results = [(label, self.search(query, PER_FILE)) for label, query in queries]
        budget = len(picked) + LIMIT          # закреплённые правила в лимит не входят
        # По кругу: первый результат каждого запроса, потом вторые… — так ни один файл
        # и ни правила не вытесняются запросом с более высокими косинусами.
        for rank in range(PER_FILE):
            for label, hits in results:
                if rank < len(hits) and len(picked) < budget:
                    score, c = hits[rank]
                    if c["id"] not in seen:
                        seen.add(c["id"])
                        picked.append({**{k: v for k, v in c.items()
                                          if k not in ("vector", "embed")},
                                       "score": score, "query": label})
        return picked


def _tokens(text):
    return re.findall(r"[a-zа-яё0-9_]{2,}", text.lower())
