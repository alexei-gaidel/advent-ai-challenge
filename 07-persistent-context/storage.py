"""Хранилище агентов и их диалогов в SQLite.

Модуль sqlite3 входит в стандартную библиотеку, так что зависимостей по-прежнему нет.
Соединение открывается на каждую операцию: панели пишут параллельно (ThreadingHTTPServer),
а одно общее соединение между потоками sqlite3 не разрешает.
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
    """Создаёт или обновляет строку агента."""
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
    """Дописывает одну реплику. Вызывается сразу после ответа модели."""
    with connect() as connection:
        connection.execute(
            "INSERT INTO messages (agent_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (agent_id, role, content, now()),
        )


def load_all():
    """Все агенты с их историей — этим наполняется реестр при старте."""
    with connect() as connection:
        agents = connection.execute(
            "SELECT id, settings, stats FROM agents ORDER BY created_at"
        ).fetchall()
        rows = connection.execute(
            "SELECT agent_id, role, content FROM messages ORDER BY id"
        ).fetchall()

    history = {}
    for row in rows:
        history.setdefault(row["agent_id"], []).append(
            {"role": row["role"], "content": row["content"]}
        )

    return [
        {
            "id": agent["id"],
            "settings": json.loads(agent["settings"]),
            "stats": json.loads(agent["stats"]),
            "history": history.get(agent["id"], []),
        }
        for agent in agents
    ]


def clear_history(agent_id):
    with connect() as connection:
        connection.execute("DELETE FROM messages WHERE agent_id = ?", (agent_id,))


def delete_agent(agent_id):
    """Закрыл панель — удалили агента вместе с перепиской."""
    with connect() as connection:
        connection.execute("DELETE FROM messages WHERE agent_id = ?", (agent_id,))
        connection.execute("DELETE FROM agents WHERE id = ?", (agent_id,))


def max_agent_number():
    """Наибольший номер в id вида a12 — чтобы счётчик продолжился, а не начался заново."""
    with connect() as connection:
        ids = [row["id"] for row in connection.execute("SELECT id FROM agents")]
    numbers = [int(value[1:]) for value in ids if value[1:].isdigit()]
    return max(numbers, default=0)


def stats():
    """Сводка для интерфейса и README."""
    with connect() as connection:
        agents = connection.execute("SELECT COUNT(*) AS n FROM agents").fetchone()["n"]
        messages = connection.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
    return {"agents": agents, "messages": messages, "path": str(DB_PATH)}
