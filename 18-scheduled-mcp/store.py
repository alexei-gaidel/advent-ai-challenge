"""Хранилище: ряд наблюдений в JSONL и расписание в SQLite.

Два хранилища не прихоть, а следствие того, где всё крутится:

  * ряд наблюдений живёт в JSONL и коммитится в репозиторий — только так история
    переживает запуски GitHub Actions, где между вызовами ничего не сохраняется;
  * расписание и журнал запусков нужны локальному планировщику, им подходит SQLite,
    и в репозиторий они не едут.
"""

import json
import os
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent

# Пути можно переопределить окружением: MCP-сервер запускается отдельным процессом,
# и без этого он читал бы рабочие данные вместо тех, что готовит демонстрация.
SAMPLES_PATH = pathlib.Path(os.environ.get("ADVENT_SAMPLES")
                            or HERE / "data" / "samples.jsonl")
DB_PATH = pathlib.Path(os.environ.get("ADVENT_SCHEDULE_DB") or HERE / "schedule.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    name       TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,        -- collect | digest | remind
    every      INTEGER NOT NULL,     -- периодичность в секундах; 0 — одноразовое
    next_run   TEXT NOT NULL,
    params     TEXT NOT NULL DEFAULT '{}',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job      TEXT NOT NULL,
    kind     TEXT NOT NULL,
    at       TEXT NOT NULL,
    status   TEXT NOT NULL,          -- ok | error
    detail   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_at ON runs(at);
"""


def now():
    return datetime.now(timezone.utc)


def iso(moment):
    return moment.isoformat(timespec="seconds")


# --- ряд наблюдений (JSONL) -----------------------------------------------------

def append(samples):
    """Дозапись наблюдений. Файл построчный: одна строка — один JSON."""
    SAMPLES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SAMPLES_PATH.open("a", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
    return len(samples)


def read(since=None, symbol=None):
    """Читает наблюдения. since — datetime, всё раньше отбрасывается."""
    if not SAMPLES_PATH.exists():
        return []

    rows = []
    for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            sample = json.loads(line)
        except json.JSONDecodeError:
            continue           # битую строку пропускаем, а не роняем весь ряд
        if symbol and symbol.upper() not in {
            str(sample.get("title", "")).upper(),
            str(sample.get("coin", "")).upper(),
            str(sample.get("symbol", "")).upper(),   # ряды, снятые до смены источника
        }:
            continue
        if since and sample.get("at", "") < iso(since):
            continue
        rows.append(sample)
    return rows


def _short_path(path):
    try:
        return str(path.relative_to(HERE.parent))
    except ValueError:
        return str(path)


def samples_stats():
    rows = read()
    return {
        "count": len(rows),
        "first": rows[0]["at"] if rows else None,
        "last": rows[-1]["at"] if rows else None,
        # Путь показываем относительным, когда он внутри репозитория, иначе как есть.
        "path": _short_path(SAMPLES_PATH),
    }


# --- расписание (SQLite) ---------------------------------------------------------

def connect():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init():
    with connect() as connection:
        connection.executescript(SCHEMA)


def add_job(name, kind, every, params=None, first_run=None):
    """Заводит задание. every=0 — одноразовое, сработает и выключится."""
    moment = first_run or (now() + timedelta(seconds=every if every else 0))
    with connect() as connection:
        connection.execute(
            """INSERT INTO jobs (name, kind, every, next_run, params, active, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(name) DO UPDATE SET
                   kind = excluded.kind, every = excluded.every,
                   next_run = excluded.next_run, params = excluded.params, active = 1""",
            (name, kind, int(every), iso(moment),
             json.dumps(params or {}, ensure_ascii=False), iso(now())),
        )
    return name


def list_jobs(active_only=True):
    query = "SELECT * FROM jobs" + (" WHERE active = 1" if active_only else "")
    with connect() as connection:
        return [dict(row, params=json.loads(row["params"]))
                for row in connection.execute(query + " ORDER BY next_run")]


def due_jobs(moment=None):
    """Задания, у которых подошло время."""
    moment = moment or now()
    return [job for job in list_jobs() if job["next_run"] <= iso(moment)]


def reschedule(job, moment=None):
    """После запуска: периодическое сдвигаем вперёд, одноразовое выключаем."""
    moment = moment or now()
    with connect() as connection:
        if job["every"]:
            connection.execute("UPDATE jobs SET next_run = ? WHERE name = ?",
                               (iso(moment + timedelta(seconds=job["every"])), job["name"]))
        else:
            connection.execute("UPDATE jobs SET active = 0 WHERE name = ?", (job["name"],))


def cancel_job(name):
    with connect() as connection:
        connection.execute("UPDATE jobs SET active = 0 WHERE name = ?", (name,))


def log_run(job, kind, status, detail="", moment=None):
    with connect() as connection:
        connection.execute(
            "INSERT INTO runs (job, kind, at, status, detail) VALUES (?, ?, ?, ?, ?)",
            (job, kind, iso(moment or now()), status, detail[:400]))


def list_runs(limit=20):
    with connect() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT job, kind, at, status, detail FROM runs ORDER BY id DESC LIMIT ?",
            (int(limit),))]


def runs_stats():
    with connect() as connection:
        total = connection.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
        errors = connection.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE status = 'error'").fetchone()["n"]
    return {"total": total, "errors": errors}
