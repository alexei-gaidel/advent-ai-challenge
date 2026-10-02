"""SQLite chat.db: чаты, сообщения с источниками и снимки памяти задачи по ходам.

Полная история лежит здесь; в промпт уходит только окно последних сообщений
(chat.py, WINDOW). Снимок памяти пишется на каждом ходу — по ним видно, когда
модель зафиксировала цель, уточнение или термин и не потеряла ли их потом.
"""

import json
import pathlib
import sqlite3
import time

DB_PATH = pathlib.Path(__file__).with_name("chat.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    mode       TEXT NOT NULL,          -- memory | history
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    turn       INTEGER NOT NULL,       -- номер хода: реплика пользователя и ответ — один ход
    role       TEXT NOT NULL,          -- user | assistant
    content    TEXT NOT NULL,
    meta       TEXT NOT NULL DEFAULT '{}',   -- у ответа: query, sources, quotes, unknown, расход
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS task_states (
    chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    turn       INTEGER NOT NULL,       -- 0 — пустая память при создании чата
    state      TEXT NOT NULL,          -- JSON: goal, clarified, constraints, terms, locked
    created_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, turn)
);
"""


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def connect(path=DB_PATH):
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(SCHEMA)
    return db


def create_chat(db, title, mode, state):
    # id продолжается с максимума в базе: после перезапуска новый чат не затрёт старый.
    with db:
        cursor = db.execute("INSERT INTO chats (title, mode, created_at) VALUES (?, ?, ?)",
                            (title, mode, _now()))
        db.execute("INSERT INTO task_states VALUES (?, 0, ?, ?)",
                   (cursor.lastrowid, json.dumps(state, ensure_ascii=False), _now()))
    return cursor.lastrowid


def chats(db):
    return [dict(row) for row in db.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.chat_id = c.id AND m.role = 'user')"
        " AS turns FROM chats c ORDER BY c.id DESC")]


def chat(db, chat_id):
    row = db.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    if not row:
        raise ValueError(f"нет чата {chat_id}")
    return dict(row)


def add_message(db, chat_id, turn, role, content, meta=None):
    with db:
        db.execute("INSERT INTO messages (chat_id, turn, role, content, meta, created_at)"
                   " VALUES (?, ?, ?, ?, ?, ?)",
                   (chat_id, turn, role, content, json.dumps(meta or {}, ensure_ascii=False), _now()))


def messages(db, chat_id, last=None):
    rows = [dict(r) for r in db.execute(
        "SELECT * FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,))]
    for r in rows:
        r["meta"] = json.loads(r["meta"])
    return rows[-last:] if last else rows


def save_state(db, chat_id, turn, state):
    with db:
        db.execute("INSERT OR REPLACE INTO task_states VALUES (?, ?, ?, ?)",
                   (chat_id, turn, json.dumps(state, ensure_ascii=False), _now()))


def state(db, chat_id):
    row = db.execute("SELECT state FROM task_states WHERE chat_id = ? ORDER BY turn DESC LIMIT 1",
                     (chat_id,)).fetchone()
    return json.loads(row["state"]) if row else None


def states(db, chat_id):
    return [{"turn": r["turn"], "state": json.loads(r["state"])} for r in db.execute(
        "SELECT turn, state FROM task_states WHERE chat_id = ? ORDER BY turn", (chat_id,))]


def delete_chat(db, chat_id):
    with db:
        db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
