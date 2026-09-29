"""Корпус для индексации: README дней 01–20 и CLAUDE.md из корня репозитория.

README самого дня 21 в корпус не входит — иначе индекс менялся бы от собственного
отчёта о нём. Список фиксированный, а не «все README»: новый день не должен молча
сдвигать цифры сравнения.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
LAST_DAY = 20


def paths():
    """Файлы корпуса в стабильном порядке: дни по номеру, затем CLAUDE.md."""
    found = []
    for folder in sorted(ROOT.iterdir()):
        match = re.match(r"(\d\d)-", folder.name)
        if folder.is_dir() and match and int(match.group(1)) <= LAST_DAY:
            readme = folder / "README.md"
            if readme.exists():
                found.append(readme)
    found.append(ROOT / "CLAUDE.md")
    return found


def normalize(text):
    """Выкидывает то, что не несёт смысла для поиска: картинки и HTML-теги."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)       # ![demo](demo.svg)
    text = re.sub(r"<img[^>]*>", "", text)
    text = re.sub(r"</?(p|div|details|summary|br)[^>]*>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def load():
    """Список документов: source (путь от корня репо), title, text."""
    documents = []
    for path in paths():
        text = normalize(path.read_text(encoding="utf-8"))
        title = next((line[2:].strip() for line in text.splitlines()
                      if line.startswith("# ")), path.parent.name)
        documents.append({
            "source": str(path.relative_to(ROOT)),
            "title": title,
            "text": text,
        })
    return documents


if __name__ == "__main__":
    docs = load()
    total = sum(len(d["text"]) for d in docs)
    for d in docs:
        print(f"{len(d['text']):>7}  {d['source']:42} {d['title'][:50]}")
    # «Страница» — 1800 знаков, стандартная машинописная.
    print(f"\n{len(docs)} документов, {total} символов ≈ {total / 1800:.0f} страниц")
