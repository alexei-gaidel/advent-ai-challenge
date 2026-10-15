"""Документация проекта для RAG: README, CLAUDE.md и docs/.

В корпус входят:
    README.md                 оглавление репозитория
    CLAUDE.md                 правила и порядок работы над днём
    NN-*/README.md            описание каждого дня, кроме самого дня 31
    31-dev-assistant/docs/*   архитектура и справочники, собранные из кода (gen_docs.py)

README дня 31 не индексируется: в нём лежат результаты прогона ассистента, и ассистент
отвечал бы цифрами из отчёта о самом себе.
"""

import hashlib
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


def paths():
    found = [ROOT / "README.md", ROOT / "CLAUDE.md"]
    for folder in sorted(ROOT.iterdir()):
        if folder.is_dir() and re.match(r"\d\d-", folder.name) and folder != HERE:
            readme = folder / "README.md"
            if readme.exists():
                found.append(readme)
    found += sorted((HERE / "docs").glob("*.md"))
    return found


def normalize(text):
    """Картинки и HTML-теги для поиска шум."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"<img[^>]*>", "", text)
    text = re.sub(r"</?(p|div|details|summary|br)[^>]*>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def kind_of(source):
    if source.startswith("31-dev-assistant/docs/"):
        return "docs"
    if source in ("README.md", "CLAUDE.md"):
        return "root"
    return "day"


def load():
    """Документы: source (путь от корня), kind, title, text."""
    documents = []
    for path in paths():
        text = normalize(path.read_text(encoding="utf-8"))
        source = str(path.relative_to(ROOT))
        title = next((line[2:].strip() for line in text.splitlines()
                      if line.startswith("# ")), path.parent.name)
        documents.append({"source": source, "kind": kind_of(source),
                          "title": title, "text": text})
    return documents


def fingerprint(documents=None):
    """Отпечаток корпуса: изменился любой файл — индекс устарел."""
    digest = hashlib.sha256()
    for doc in documents or load():
        digest.update(doc["source"].encode())
        digest.update(doc["text"].encode())
    return digest.hexdigest()[:16]


if __name__ == "__main__":
    docs = load()
    total = sum(len(d["text"]) for d in docs)
    for d in docs:
        print(f"{len(d['text']):>7}  {d['kind']:5} {d['source']}")
    print(f"\n{len(docs)} документов, {total} символов ≈ {total / 1800:.0f} страниц, "
          f"отпечаток {fingerprint(docs)}")
