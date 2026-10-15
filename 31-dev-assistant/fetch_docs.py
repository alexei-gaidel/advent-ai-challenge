"""Подтягивает README зависимостей с GitHub, чтобы добавить их в RAG."""

import json
import urllib.error
import urllib.request

import requests

SOURCES = {
    "ollama": "https://raw.githubusercontent.com/ollama/ollama/main/docs/api.md",
    "mcp": "https://raw.githubusercontent.com/modelcontextprotocol/specification/main/README.md",
}


def fetch(name):
    url = SOURCES[name]
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.read().decode("utf-8")
    except urllib.error.URLError as error:
        print(f"{name}: сеть недоступна ({error.reason})")
        return ""


def fetch_all():
    return {name: fetch(name) for name in SOURCES}


def latest_release(repo):
    data = requests.get(f"https://api.github.com/repos/{repo}/releases/latest", timeout=10)
    return data.json().get("tag_name", "?")


if __name__ == "__main__":
    print(json.dumps({k: len(v) for k, v in fetch_all().items()}, ensure_ascii=False))
