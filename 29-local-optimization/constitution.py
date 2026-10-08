"""Корпус дня 22: Конституция РФ с официального сайта kremlin.ru.

Страница kremlin.ru/acts/constitution/item — полный текст с поправками 2020 года.
Скрипт скачивает её urllib-ом, разбирает html.parser-ом на главы, статьи и части
и сохраняет в data/constitution.json. JSON лежит в репозитории, чтобы прогон
повторялся без сети; `--refresh` скачивает заново.

    python3 constitution.py            # статистика по сохранённому тексту
    python3 constitution.py --refresh  # скачать и разобрать заново
"""

import html.parser
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

URL = "http://www.kremlin.ru/acts/constitution/item"
PATH = pathlib.Path(__file__).with_name("data") / "constitution.json"


class _Parser(html.parser.HTMLParser):
    """Собирает текст из <h2> (раздел/глава), <h3> (статья) и <p> (часть статьи).

    Поправки 2020 года kremlin.ru оборачивает в <a class="reference" href="#reference-2020-…">
    и помечает сноской <sup>*</sup> — отмечаем флагом amended. Цифра в <sup> — это номер
    вставленной статьи или части: «Статья 67<sup>1</sup>» → «Статья 67.1», «2<sup>1</sup>.» → «2.1.».
    """

    def __init__(self):
        super().__init__()
        self.blocks = []          # (tag, text, amended)
        self.tag = None
        self.text = []
        self.amended = False
        self.sup = None           # текст внутри <sup>, пока он открыт

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("h2", "h3", "p") and self.tag is None:
            self.tag, self.text, self.amended = tag, [], False
        elif tag == "a" and self.tag and "reference-2020" in (attrs.get("href") or ""):
            self.amended = True
        elif tag == "sup" and self.tag:
            self.sup = []
        elif tag == "br" and self.tag:
            self.text.append(" ")

    def handle_endtag(self, tag):
        if tag == "sup" and self.sup is not None:
            mark, self.sup = "".join(self.sup), None
            digits = re.sub(r"\D", "", mark)
            if digits:
                self.text.append("." + digits)
            if "*" in mark:
                self.amended = True
        elif tag == self.tag:
            text = re.sub(r"\s+", " ", "".join(self.text).replace("\xa0", " ")).strip()
            text = re.sub(r"^(\d+\.\d+)\.?", r"\1.", text)      # «2.1.» без двойной точки
            if text:
                self.blocks.append((tag, text, self.amended))
            self.tag = None

    def handle_data(self, data):
        if self.sup is not None:
            self.sup.append(data)
        elif self.tag:
            self.text.append(data)


def download():
    request = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 advent-ai-challenge"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"не удалось скачать {URL}: {error}") from error


def parse(page):
    """HTML → список статей: {article, title, chapter, part, amended, paragraphs}."""
    parser = _Parser()
    parser.feed(page)
    articles, part, chapter, current = [], "", "", None
    for tag, text, amended in parser.blocks:
        if tag == "h2":
            # «Раздел первый Глава 1. Основы…» приходит одной строкой.
            match = re.match(r"(Раздел \S+)\s*(.*)", text)
            if match:
                part, text = match.group(1), match.group(2)
            if text.startswith("Глава"):
                chapter = text
            current = None
        elif tag == "h3":
            number = re.match(r"Статья (\d+(?:\.\d+)?)", text)
            if number:
                current = {"article": number.group(1), "title": f"Статья {number.group(1)}",
                           "chapter": chapter, "part": part, "amended": amended,
                           "paragraphs": []}
            elif text.startswith("Заключительные"):
                current = {"article": "ЗП", "title": text, "chapter": "", "part": part,
                           "amended": amended, "paragraphs": []}
            else:
                current = None
            if current:
                articles.append(current)
        elif tag == "p" and current is not None:
            current["paragraphs"].append(text)
            current["amended"] = current["amended"] or amended
    return [a for a in articles if a["paragraphs"]]


def load():
    if not PATH.exists():
        raise RuntimeError(f"нет {PATH.name} — python3 constitution.py --refresh")
    return json.loads(PATH.read_text(encoding="utf-8"))


def main():
    if "--refresh" in sys.argv or not PATH.exists():
        articles = parse(download())
        PATH.parent.mkdir(exist_ok=True)
        PATH.write_text(json.dumps({"source": URL, "articles": articles},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"скачано и разобрано → {PATH.relative_to(PATH.parent.parent)}")
    data = load()
    articles = data["articles"]
    chars = sum(len(p) for a in articles for p in a["paragraphs"])
    chapters = sorted({a["chapter"] for a in articles if a["chapter"]},
                      key=lambda c: int(re.search(r"\d+", c).group()))
    print(f"источник: {data['source']}")
    print(f"{len(chapters)} глав, {len(articles)} статей, "
          f"{sum(a['amended'] for a in articles)} с поправками 2020, "
          f"{chars} символов ≈ {chars / 1800:.0f} стр.")
    for chapter in chapters:
        count = sum(a["chapter"] == chapter for a in articles)
        print(f"  {count:>3}  {chapter}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        print(f"ошибка: {error}", file=sys.stderr)
        sys.exit(1)
