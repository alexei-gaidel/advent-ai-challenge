"""Хранилище: агенты, диалоги, три слоя памяти и профили пользователей в SQLite.

Слои лежат раздельно и по-разному живут:
    messages        — краткосрочная память, реплики диалога;
    working_memory  — рабочая, привязана к агенту, чистится при смене задачи;
    long_memory     — долговременная, общая на весь стенд, без agent_id;
    profiles        — профили пользователей: предпочтения и факты, несколько штук,
                      агент ссылается на один из них по id.
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

-- Профили пользователей: JSON с полями name/about/style/format/limits/facts.
-- Отдельная таблица, а не kind='профиль' в long_memory: профилей несколько,
-- и у каждого своя структура, а не плоские пары ключ-значение.
CREATE TABLE IF NOT EXISTS profiles (
    id         TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Очередь предложений роутера: ничего не попадает в память без решения пользователя.
-- Инварианты проекта: то, что ассистент не имеет права нарушать.
-- Общие на весь стенд, как и долговременная память: решение принято один раз
-- и действует для всех агентов.
CREATE TABLE IF NOT EXISTS invariants (
    id         TEXT PRIMARY KEY,
    text       TEXT NOT NULL,
    kind       TEXT NOT NULL,      -- архитектура | стек | бизнес-правило | процесс
    rationale  TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL,      -- manual | promoted
    created_at TEXT NOT NULL
);

-- Состояние задачи как конечный автомат: этап, шаг, ожидаемое действие.
CREATE TABLE IF NOT EXISTS tasks (
    id         TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    stages     TEXT NOT NULL,      -- JSON: набор этапов, настраивается под задачу
    stage      TEXT NOT NULL,      -- ключ текущего этапа
    expected   TEXT NOT NULL,      -- JSON: от кого ждём действия и какого
    paused     INTEGER NOT NULL DEFAULT 0,
    pause_note TEXT NOT NULL DEFAULT '',
    -- Условия входа: утверждения человека, без них жизненный цикл не восстановить.
    plan_approved     INTEGER NOT NULL DEFAULT 0,
    validation_passed INTEGER NOT NULL DEFAULT 0,
    open_questions    TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_steps (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    stage    TEXT NOT NULL,
    position INTEGER NOT NULL,
    text     TEXT NOT NULL,
    status   TEXT NOT NULL        -- pending | done | skipped
);
-- Журнал переходов: по нему видно, что автомат именно автомат, а не текстовое поле.
CREATE TABLE IF NOT EXISTS task_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,     -- plan | transition | pause | resume | step | expect
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

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


# --- профили -------------------------------------------------------------------

def save_profile(profile_id, data):
    with connect() as connection:
        connection.execute(
            """INSERT INTO profiles (id, data, created_at, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at""",
            (profile_id, json.dumps(data, ensure_ascii=False), now(), now()),
        )


def load_profiles():
    with connect() as connection:
        return {row["id"]: json.loads(row["data"]) for row in connection.execute(
            "SELECT id, data FROM profiles ORDER BY created_at")}


def delete_profile(profile_id):
    with connect() as connection:
        connection.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))


def max_profile_number():
    with connect() as connection:
        ids = [row["id"] for row in connection.execute("SELECT id FROM profiles")]
    return max((int(v[1:]) for v in ids if v[1:].isdigit()), default=0)


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

# --- инварианты ----------------------------------------------------------------

def save_invariant(invariant):
    with connect() as connection:
        connection.execute(
            """INSERT INTO invariants (id, text, kind, rationale, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   text = excluded.text, kind = excluded.kind,
                   rationale = excluded.rationale""",
            (invariant["id"], invariant["text"], invariant["kind"],
             invariant.get("rationale", ""), invariant.get("source", "manual"), now()),
        )


def delete_invariant(invariant_id):
    with connect() as connection:
        connection.execute("DELETE FROM invariants WHERE id = ?", (invariant_id,))


def load_invariants():
    """Инварианты общие: читаются отдельно от агента, как долговременная память."""
    with connect() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT id, text, kind, rationale, source FROM invariants ORDER BY created_at")]


def clear_invariants():
    with connect() as connection:
        connection.execute("DELETE FROM invariants")


# --- состояние задачи ----------------------------------------------------------

def save_task(task):
    """Создаёт или обновляет карточку задачи."""
    with connect() as connection:
        connection.execute(
            """INSERT INTO tasks (id, agent_id, title, stages, stage, expected,
                                  paused, pause_note, plan_approved, validation_passed,
                                  open_questions, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   title = excluded.title, stages = excluded.stages,
                   stage = excluded.stage, expected = excluded.expected,
                   paused = excluded.paused, pause_note = excluded.pause_note,
                   plan_approved = excluded.plan_approved,
                   validation_passed = excluded.validation_passed,
                   open_questions = excluded.open_questions,
                   updated_at = excluded.updated_at""",
            (task["id"], task["agent_id"], task["title"],
             json.dumps(task["stages"], ensure_ascii=False), task["stage"],
             json.dumps(task["expected"], ensure_ascii=False),
             int(task["paused"]), task["pause_note"],
             int(task.get("plan_approved", False)),
             int(task.get("validation_passed", False)),
             json.dumps(task.get("open_questions", []), ensure_ascii=False),
             now(), now()),
        )


def save_steps(task_id, steps):
    """Шаги переписываются целиком: их немного, а порядок и статусы меняются часто."""
    with connect() as connection:
        connection.execute("DELETE FROM task_steps WHERE task_id = ?", (task_id,))
        connection.executemany(
            """INSERT INTO task_steps (task_id, stage, position, text, status)
               VALUES (?, ?, ?, ?, ?)""",
            [(task_id, step["stage"], index, step["text"], step["status"])
             for index, step in enumerate(steps)],
        )


def add_event(task_id, kind, payload):
    with connect() as connection:
        connection.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
            (task_id, kind, json.dumps(payload, ensure_ascii=False), now()),
        )


def load_task(agent_id):
    """Активная задача агента вместе с шагами и журналом переходов."""
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM tasks WHERE agent_id = ? ORDER BY created_at DESC LIMIT 1",
            (agent_id,)).fetchone()
        if not row:
            return None
        steps = connection.execute(
            "SELECT stage, text, status FROM task_steps WHERE task_id = ? ORDER BY position",
            (row["id"],)).fetchall()
        events = connection.execute(
            """SELECT kind, payload, created_at FROM task_events WHERE task_id = ?
               ORDER BY id""", (row["id"],)).fetchall()

    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "title": row["title"],
        "stages": json.loads(row["stages"]),
        "stage": row["stage"],
        "expected": json.loads(row["expected"]),
        "paused": bool(row["paused"]),
        "pause_note": row["pause_note"],
        "plan_approved": bool(row["plan_approved"]),
        "validation_passed": bool(row["validation_passed"]),
        "open_questions": json.loads(row["open_questions"]),
        "steps": [dict(step) for step in steps],
        "events": [{"kind": e["kind"], "payload": json.loads(e["payload"]),
                    "at": e["created_at"]} for e in events],
    }


def delete_task(task_id):
    with connect() as connection:
        for table in ("task_events", "task_steps"):
            connection.execute(f"DELETE FROM {table} WHERE task_id = ?", (task_id,))
        connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


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
        connection.execute(
            """DELETE FROM task_events WHERE task_id IN
               (SELECT id FROM tasks WHERE agent_id = ?)""", (agent_id,))
        connection.execute(
            """DELETE FROM task_steps WHERE task_id IN
               (SELECT id FROM tasks WHERE agent_id = ?)""", (agent_id,))
        for table in ("messages", "working_memory", "memory_proposals", "tasks"):
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
            for table in ("agents", "messages", "working_memory", "long_memory", "profiles")
        }
    return {**counts, "path": str(DB_PATH)}
