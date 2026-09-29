"""Две стратегии нарезки документа на чанки.

fixed     — окно фиксированного размера с перекрытием, про структуру не знает ничего.
structure — по заголовкам markdown, с двумя поправками, без которых она не работает:
            слишком длинный раздел делится по абзацам, слишком короткий приклеивается
            к следующему.

Обе стратегии возвращают одинаковые Chunk с метаданными, поэтому индекс и поиск
про стратегию не знают.
"""

import re
from dataclasses import dataclass, asdict

FIXED_SIZE = 800          # символов, ≈ 200 токенов русского текста
FIXED_OVERLAP = 120       # 15%
STRUCT_MAX = 2000         # длиннее — делим по абзацам
STRUCT_MIN = 150          # короче — приклеиваем к следующему разделу

HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


@dataclass
class Chunk:
    chunk_id: str
    strategy: str
    source: str
    title: str
    section: str
    text: str
    start_char: int
    end_char: int

    @property
    def n_chars(self):
        return len(self.text)

    def as_dict(self):
        row = asdict(self)
        row["n_chars"] = self.n_chars
        return row


def outline(text):
    """Заголовки документа: [(позиция, уровень, заголовок)].

    Строки внутри ``` — не заголовки: «# /etc/systemd/...» в README дня 18 это
    комментарий в блоке кода.
    """
    heads, pos, fenced = [], 0, False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced:
            match = HEADING.match(line)
            if match:
                heads.append((pos, len(match.group(1)), match.group(2)))
        pos += len(line)
    return heads


def section_path_at(heads, position):
    """Путь разделов «Сравнение › Токены» для позиции в тексте (без заголовка документа)."""
    stack = {}
    for pos, level, name in heads:
        if pos > position:
            break
        stack[level] = name
        for deeper in [lvl for lvl in stack if lvl > level]:
            del stack[deeper]
    parts = [stack[lvl] for lvl in sorted(stack) if lvl > 1]
    return " › ".join(parts) or "(вступление)"


def section_label(heads, start, end):
    """Метка чанка: путь раздела в начале плюс заголовки, которые попали внутрь
    (склеенные короткие разделы должны быть видны в метаданных)."""
    label = section_path_at(heads, start)
    inner = [name for pos, level, name in heads if start < pos < end and level > 1]
    return " + ".join([label] + inner) if inner else label


def protected_spans(text):
    """Диапазоны блоков кода и таблиц — внутри них резать нельзя."""
    spans = [(m.start(), m.end()) for m in re.finditer(r"```.*?```", text, flags=re.S)]
    spans += [(m.start(), m.end())
              for m in re.finditer(r"(?:^\|.*\|[ \t]*\n)+", text, flags=re.M)]
    return sorted(spans)


def cuts_protected(text, start, end):
    """Разрезает ли граница [start, end) таблицу или блок кода."""
    return any(s < start < e or s < end < e for s, e in protected_spans(text))


# ---------------------------------------------------------------- fixed

def chunk_fixed(doc, size=FIXED_SIZE, overlap=FIXED_OVERLAP):
    text, heads = doc["text"], outline(doc["text"])
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Сдвигаем границу назад до пробела, чтобы не рвать слово.
            space = max(text.rfind(" ", start, end), text.rfind("\n", start, end))
            if space > start + size // 2:
                end = space
        piece = text[start:end].strip()
        if piece:
            chunks.append(Chunk(
                chunk_id=f"fixed:{doc['source']}:{len(chunks):03d}",
                strategy="fixed", source=doc["source"], title=doc["title"],
                # Секция по первому заголовку перед окном: окно заголовков не видит,
                # но метаданные у него всё равно честные.
                section=section_path_at(heads, start),
                text=piece, start_char=start, end_char=end,
            ))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


# ---------------------------------------------------------------- structure

def split_long(text, limit=STRUCT_MAX):
    """Делит длинный раздел по пустым строкам, не разрывая блоки кода и таблицы.

    Возвращает [(смещение, кусок)]. Абзац длиннее лимита остаётся целым — лучше
    большой чанк, чем разрезанная таблица.
    """
    blocks, pos, fenced, current_start = [], 0, False, 0
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        pos += len(line)
        if not fenced and line.strip() == "":
            blocks.append((current_start, text[current_start:pos]))
            current_start = pos
    if current_start < len(text):
        blocks.append((current_start, text[current_start:]))

    pieces, buf_start, buf = [], 0, ""
    for offset, block in blocks:
        # Одинокий заголовок от тела не отрываем, даже если выйдем за лимит.
        if len(buf.strip()) >= STRUCT_MIN and len(buf) + len(block) > limit:
            pieces.append((buf_start, buf))
            buf_start, buf = offset, ""
        if not buf:
            buf_start = offset
        buf += block
    if buf.strip():
        pieces.append((buf_start, buf))
    return pieces


def chunk_structure(doc, max_chars=STRUCT_MAX, min_chars=STRUCT_MIN):
    text, heads = doc["text"], outline(doc["text"])
    bounds = [pos for pos, _, _ in heads if pos > 0]
    starts = [0] + bounds
    ends = bounds + [len(text)]

    # Разделы: (начало, конец). Короткие копим и отдаём вместе со следующим.
    sections, pending = [], None
    for start, end in zip(starts, ends):
        if pending is not None:
            start = pending
        if len(text[start:end].strip()) < min_chars and end < len(text):
            pending = start
            continue
        pending = None
        sections.append((start, end))
    # Короткий хвост документа приклеивать не к чему — отдаём предыдущему разделу.
    if len(sections) > 1 and len(text[sections[-1][0]:sections[-1][1]].strip()) < min_chars:
        tail = sections.pop()
        sections[-1] = (sections[-1][0], tail[1])

    chunks = []
    for start, end in sections:
        body = text[start:end]
        pieces = [(0, body)] if len(body) <= max_chars else split_long(body, max_chars)
        for offset, piece in pieces:
            if not piece.strip():
                continue
            begin = start + offset
            section = section_label(heads, begin + len(piece) - len(piece.lstrip()),
                                    begin + len(piece))
            chunks.append(Chunk(
                chunk_id=f"structure:{doc['source']}:{len(chunks):03d}",
                strategy="structure", source=doc["source"], title=doc["title"],
                section=section, text=piece.strip(),
                start_char=begin, end_char=begin + len(piece),
            ))
    return chunks


STRATEGIES = {"fixed": chunk_fixed, "structure": chunk_structure}


def chunk_corpus(documents, strategy):
    fn = STRATEGIES[strategy]
    return [chunk for doc in documents for chunk in fn(doc)]


def stats(documents, chunks):
    """Статистика нарезки: размеры и сколько чанков разрезали таблицу или код."""
    sizes = sorted(c.n_chars for c in chunks)
    texts = {d["source"]: d["text"] for d in documents}
    broken = sum(cuts_protected(texts[c.source], c.start_char, c.end_char) for c in chunks)
    return {
        "chunks": len(chunks),
        "avg": round(sum(sizes) / len(sizes)),
        "median": sizes[len(sizes) // 2],
        "min": sizes[0],
        "max": sizes[-1],
        "broken": broken,
        "broken_pct": round(100 * broken / len(chunks), 1),
        "total_chars": sum(sizes),
    }


if __name__ == "__main__":
    import corpus
    docs = corpus.load()
    for name in STRATEGIES:
        print(name, stats(docs, chunk_corpus(docs, name)))
