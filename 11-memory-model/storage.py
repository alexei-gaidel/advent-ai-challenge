"""Хранилище: агенты, диалоги и три слоя памяти в SQLite.

Слои лежат раздельно и по-разному живут:
    messages        — краткосрочная память, реплики диалога;
    working_memory  — рабочая, привязана к агенту, чистится при смене задачи;
    long_memory     — долговременная, общая на весь стенд, без agent_id.
"""

import json
import pathlib
import sqlite3
from datetime import datetime, timezone

DB_PATH = pathlib.Path(__file__).with_name("agents.db")

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
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_agent ON messages(agent_id, id);

-- Рабочая память: данные текущей задачи, своя у каждого агента.
CREATE TABLE IF NOT EXISTS working_memory (
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, key)
);

-- Долговременная память: профиль, решения, знания. Общая на весь стенд,
-- поэтому здесь нет agent_id — новый агент видит её сразу.
CREATE TABLE IF NOT EXISTS long_memory (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL,
    kind         TEXT NOT NULL,
    source_agent TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- Очередь предложений роутера: ничего не попадает в память без решения пользователя.
CREATE TABLE IF NOT EXISTS memory_proposals (
    id         TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    layer      TEXT NOT NULL,
    kind       TEXT,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    status     TEXT NOT NULL,
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


def append_message(agent_id, role, content):
    with connect() as connection:
        connection.execute(
            "INSERT INTO messages (agent_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (agent_id, role, content, now()),
        )


# --- рабочая память ------------------------------------------------------------

def save_working(agent_id, key, value):
    with connect() as connection:
        connection.execute(
            """INSERT INTO working_memory (agent_id, key, value, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(agent_id, key) DO UPDATE SET
                   value = excluded.value, updated_at = excluded.updated_at""",
            (agent_id, key, value, now()),
        )


def delete_working(agent_id, key):
    with connect() as connection:
        connection.execute(
            "DELETE FROM working_memory WHERE agent_id = ? AND key = ?", (agent_id, key))


def clear_working(agent_id):
    """Смена задачи: рабочая память обнуляется, остальные слои не трогаем."""
    with connect() as connection:
        connection.execute("DELETE FROM working_memory WHERE agent_id = ?", (agent_id,))


# --- долговременная память -----------------------------------------------------

def save_long(key, value, kind, source_agent=None):
    with connect() as connection:
        connection.execute(
            """INSERT INTO long_memory (key, value, kind, source_agent, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value, kind = excluded.kind,
                   updated_at = excluded.updated_at""",
            (key, value, kind, source_agent, now(), now()),
        )


def delete_long(key):
    with connect() as connection:
        connection.execute("DELETE FROM long_memory WHERE key = ?", (key,))


def load_long():
    """Долговременная память общая, поэтому читается отдельно от агентов."""
    with connect() as connection:
        return {
            row["key"]: {"value": row["value"], "kind": row["kind"],
                         "source_agent": row["source_agent"]}
            for row in connection.execute(
                "SELECT key, value, kind, source_agent FROM long_memory ORDER BY created_at")
        }


def clear_long():
    with connect() as connection:
        connection.execute("DELETE FROM long_memory")


# --- предложения роутера -------------------------------------------------------

def save_proposal(proposal_id, agent_id, layer, kind, key, value, status="pending"):
    with connect() as connection:
        connection.execute(
            """INSERT INTO memory_proposals
                   (id, agent_id, layer, kind, key, value, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   layer = excluded.layer, status = excluded.status""",
            (proposal_id, agent_id, layer, kind, key, value, status, now()),
        )


def update_proposal(proposal_id, status, layer=None):
    with connect() as connection:
        if layer:
            connection.execute(
                "UPDATE memory_proposals SET status = ?, layer = ? WHERE id = ?",
                (status, layer, proposal_id))
        else:
            connection.execute(
                "UPDATE memory_proposals SET status = ? WHERE id = ?", (status, proposal_id))


# --- загрузка и обслуживание ---------------------------------------------------

def load_all():
    """Агенты с их диалогом, рабочей памятью и висящими предложениями."""
    with connect() as connection:
        agents = connection.execute(
            "SELECT id, settings, stats FROM agents ORDER BY created_at").fetchall()
        messages = connection.execute(
            "SELECT agent_id, role, content FROM messages ORDER BY id").fetchall()
        working = connection.execute(
            "SELECT agent_id, key, value FROM working_memory ORDER BY updated_at").fetchall()
        proposals = connection.execute(
            """SELECT id, agent_id, layer, kind, key, value, status FROM memory_proposals
               WHERE status = 'pending' ORDER BY created_at""").fetchall()

    by_agent = {
        agent["id"]: {
            "id": agent["id"],
            "settings": json.loads(agent["settings"]),
            "stats": json.loads(agent["stats"]),
            "history": [],
            "working": {},
            "proposals": [],
        }
        for agent in agents
    }

    for row in messages:
        if row["agent_id"] in by_agent:
            by_agent[row["agent_id"]]["history"].append(
                {"role": row["role"], "content": row["content"]})
    for row in working:
        if row["agent_id"] in by_agent:
            by_agent[row["agent_id"]]["working"][row["key"]] = row["value"]
    for row in proposals:
        if row["agent_id"] in by_agent:
            by_agent[row["agent_id"]]["proposals"].append(dict(row))

    return list(by_agent.values())


def clear_history(agent_id):
    """Сброс диалога: краткосрочная и рабочая память агента, висящие предложения."""
    with connect() as connection:
        for table in ("messages", "working_memory", "memory_proposals"):
            connection.execute(f"DELETE FROM {table} WHERE agent_id = ?", (agent_id,))


def delete_agent(agent_id):
    """Долговременную память не трогаем: она общая и агента переживает."""
    with connect() as connection:
        for table in ("messages", "working_memory", "memory_proposals"):
            connection.execute(f"DELETE FROM {table} WHERE agent_id = ?", (agent_id,))
        connection.execute("DELETE FROM agents WHERE id = ?", (agent_id,))


def max_agent_number():
    with connect() as connection:
        ids = [row["id"] for row in connection.execute("SELECT id FROM agents")]
    numbers = [int(value[1:]) for value in ids if value[1:].isdigit()]
    return max(numbers, default=0)


def stats():
    with connect() as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in ("agents", "messages", "working_memory", "long_memory")
        }
    return {**counts, "path": str(DB_PATH)}
