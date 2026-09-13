"""Хранилище агентов, веток диалога и фактов в SQLite.

В отличие от дня 9 здесь нет сводок: история не сжимается, а управляется тремя
стратегиями. Реплики принадлежат ветке (branch_id), факты лежат парами ключ-значение,
чекпоинты помечают точки, от которых можно ответвиться.
"""

import json
import pathlib
import sqlite3
from datetime import datetime, timezone

DB_PATH = pathlib.Path(__file__).with_name("agents.db")
MAIN_BRANCH = "main"

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id         TEXT PRIMARY KEY,
    settings   TEXT NOT NULL,
    stats      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    branch_id  TEXT NOT NULL DEFAULT 'main',
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_branch ON messages(agent_id, branch_id, id);

-- Sticky Facts: важные данные диалога парами ключ-значение.
CREATE TABLE IF NOT EXISTS facts (
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, key)
);

-- Branching: дерево веток. fork_at — сколько сообщений родителя унаследовано.
CREATE TABLE IF NOT EXISTS branches (
    id         TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    parent_id  TEXT,
    fork_at    INTEGER NOT NULL,
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Именованные точки, от которых создаются ветки.
CREATE TABLE IF NOT EXISTS checkpoints (
    id         TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    branch_id  TEXT NOT NULL,
    at         INTEGER NOT NULL,
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init():
    """Создаёт таблицы. WAL — чтобы чтение не блокировалось параллельной записью."""
    with connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)


def save_agent(agent_id, settings, stats):
    with connect() as connection:
        connection.execute(
            """INSERT INTO agents (id, settings, stats, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   settings = excluded.settings,
                   stats = excluded.stats,
                   updated_at = excluded.updated_at""",
            (agent_id, json.dumps(settings, ensure_ascii=False),
             json.dumps(stats, ensure_ascii=False), now(), now()),
        )


def append_message(agent_id, branch_id, role, content):
    with connect() as connection:
        connection.execute(
            """INSERT INTO messages (agent_id, branch_id, role, content, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (agent_id, branch_id, role, content, now()),
        )


def save_facts(agent_id, facts):
    """Сливает новые факты в хранилище. Пустое значение удаляет ключ."""
    with connect() as connection:
        for key, value in facts.items():
            if str(value).strip():
                connection.execute(
                    """INSERT INTO facts (agent_id, key, value, updated_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(agent_id, key) DO UPDATE SET
                           value = excluded.value, updated_at = excluded.updated_at""",
                    (agent_id, key, str(value), now()),
                )
            else:
                connection.execute(
                    "DELETE FROM facts WHERE agent_id = ? AND key = ?", (agent_id, key))


def clear_facts(agent_id):
    with connect() as connection:
        connection.execute("DELETE FROM facts WHERE agent_id = ?", (agent_id,))


def save_branch(branch_id, agent_id, parent_id, fork_at, title):
    with connect() as connection:
        connection.execute(
            """INSERT INTO branches (id, agent_id, parent_id, fork_at, title, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET title = excluded.title""",
            (branch_id, agent_id, parent_id, fork_at, title, now()),
        )


def save_checkpoint(checkpoint_id, agent_id, branch_id, at, title):
    with connect() as connection:
        connection.execute(
            """INSERT INTO checkpoints (id, agent_id, branch_id, at, title, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (checkpoint_id, agent_id, branch_id, at, title, now()),
        )


def load_all():
    """Все агенты со ветками, репликами, фактами и чекпоинтами."""
    with connect() as connection:
        agents = connection.execute(
            "SELECT id, settings, stats FROM agents ORDER BY created_at").fetchall()
        messages = connection.execute(
            "SELECT agent_id, branch_id, role, content FROM messages ORDER BY id").fetchall()
        branches = connection.execute(
            "SELECT id, agent_id, parent_id, fork_at, title FROM branches "
            "ORDER BY created_at").fetchall()
        facts = connection.execute(
            "SELECT agent_id, key, value FROM facts ORDER BY updated_at").fetchall()
        checkpoints = connection.execute(
            "SELECT id, agent_id, branch_id, at, title FROM checkpoints "
            "ORDER BY created_at").fetchall()

    by_agent = {}
    for agent in agents:
        by_agent[agent["id"]] = {
            "id": agent["id"],
            "settings": json.loads(agent["settings"]),
            "stats": json.loads(agent["stats"]),
            "messages": {},
            "branches": {},
            "facts": {},
            "checkpoints": [],
        }

    for row in messages:
        agent = by_agent.get(row["agent_id"])
        if agent is not None:
            agent["messages"].setdefault(row["branch_id"], []).append(
                {"role": row["role"], "content": row["content"]})

    for row in branches:
        agent = by_agent.get(row["agent_id"])
        if agent is not None:
            agent["branches"][row["id"]] = {
                "parent_id": row["parent_id"], "fork_at": row["fork_at"],
                "title": row["title"]}

    for row in facts:
        agent = by_agent.get(row["agent_id"])
        if agent is not None:
            agent["facts"][row["key"]] = row["value"]

    for row in checkpoints:
        agent = by_agent.get(row["agent_id"])
        if agent is not None:
            agent["checkpoints"].append(
                {"id": row["id"], "branch_id": row["branch_id"],
                 "at": row["at"], "title": row["title"]})

    return list(by_agent.values())


def clear_history(agent_id):
    """Полный сброс диалога: реплики, ветки, чекпоинты и факты."""
    with connect() as connection:
        for table in ("messages", "checkpoints", "branches", "facts"):
            connection.execute(f"DELETE FROM {table} WHERE agent_id = ?", (agent_id,))


def delete_agent(agent_id):
    with connect() as connection:
        for table in ("messages", "checkpoints", "branches", "facts"):
            connection.execute(f"DELETE FROM {table} WHERE agent_id = ?", (agent_id,))
        connection.execute("DELETE FROM agents WHERE id = ?", (agent_id,))


def max_agent_number():
    """Наибольший номер в id вида a12 — чтобы счётчик продолжился, а не начался заново."""
    with connect() as connection:
        ids = [row["id"] for row in connection.execute("SELECT id FROM agents")]
    numbers = [int(value[1:]) for value in ids if value[1:].isdigit()]
    return max(numbers, default=0)


def stats():
    with connect() as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in ("agents", "messages", "branches", "facts")
        }
    return {**counts, "path": str(DB_PATH)}
