"""GitHub REST на urllib: прочитать событие PR и опубликовать ревью одним комментарием.

Комментарий помечен невидимым маркером. На новый пуш в PR ищется комментарий с маркером
и переписывается (PATCH), а не добавляется новый — лента PR не забивается копиями.
"""

import json
import os
import urllib.error
import urllib.request

API = "https://api.github.com"


def event():
    """PR из события Actions: номер, репозиторий, base/head sha."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        raise RuntimeError("нет GITHUB_EVENT_PATH — это не запуск из GitHub Actions")
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    pr = data.get("pull_request")
    if not pr:
        raise RuntimeError("событие не pull_request")
    return {"number": pr["number"], "repo": data["repository"]["full_name"],
            "base": pr["base"]["sha"], "head": pr["head"]["sha"], "title": pr["title"],
            "url": pr["html_url"]}


def _request(method, url, body=None):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("нет GITHUB_TOKEN")
    request = urllib.request.Request(
        url, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "advent-ai-review"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"GitHub {error.code}: {error.read().decode()[:300]}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"GitHub недоступен: {error}") from error


def upsert_comment(repo, number, body, marker):
    """→ (url комментария, 'created' | 'updated')."""
    page = 1
    while True:
        comments = _request("GET", f"{API}/repos/{repo}/issues/{number}/comments"
                                   f"?per_page=100&page={page}")
        for comment in comments:
            if marker in (comment.get("body") or ""):
                updated = _request("PATCH", f"{API}/repos/{repo}/issues/comments/{comment['id']}",
                                   {"body": body})
                return updated["html_url"], "updated"
        if len(comments) < 100:
            break
        page += 1
    created = _request("POST", f"{API}/repos/{repo}/issues/{number}/comments", {"body": body})
    return created["html_url"], "created"


def step_summary(markdown):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
