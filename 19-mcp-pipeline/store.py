"""Хранилище пайплайна: снимки, отчёты, расписание и журнал прогонов.

Разделение то же, что в задании 18, и по той же причине:

  * снимки — JSONL в репозитории: только так история переживает запуски GitHub Actions,
    где между вызовами ничего не сохраняется;
  * расписание, отчёты и журнал — SQLite: нужны живому планировщику.

Отчёты дополнительно пишутся файлами в data/reports — это и есть «saveToFile»
из третьего шага пайплайна.
"""

import json
import os
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent

# Пути переопределяются окружением: MCP-сервер запускается отдельным процессом,
# и без этого он читал бы рабочие данные вместо тех, что готовит демонстрация.
SNAPSHOTS_PATH = pathlib.Path(os.environ.get("ADVENT_SNAPSHOTS")
                              or HERE / "data" / "snapshots.jsonl")
REPORTS_DIR = pathlib.Path(os.environ.get("ADVENT_REPORTS") or HERE / "data" / "reports")
DB_PATH = pathlib.Path(os.environ.get("ADVENT_PIPELINE_DB") or HERE / "pipeline.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    name       TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    every      INTEGER NOT NULL,
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
    status   TEXT NOT NULL,
    detail   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_at ON runs(at);

-- Отчёты второго шага: живут отдельно от снимков, потому что третий шаг
-- принимает именно report_id и ничего другого.
CREATE TABLE IF NOT EXISTS reports (
    id          TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    changed     INTEGER NOT NULL,
    summary     TEXT NOT NULL,
    changes     TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    saved_path  TEXT NOT NULL DEFAULT ''
);
"""


def now():
    return datetime.now(timezone.utc)


def iso(moment):
    return moment.isoformat(timespec="seconds")


def connect():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.executescript(SCHEMA)


# --- снимки (JSONL) -------------------------------------------------------------

def next_snapshot_id():
    """Идентификатор снимка: время плюс порядковый номер в пределах секунды."""
    stamp = now().strftime("%Y%m%dT%H%M%S")
    existing = {row["snapshot_id"] for row in read_snapshots()
                if str(row.get("snapshot_id", "")).startswith(f"snap-{stamp}")}
    return f"snap-{stamp}-{len(existing) + 1}"


def save_snapshot(snapshot):
    """Дозаписывает снимок и возвращает его идентификатор."""
    snapshot = dict(snapshot)
    snapshot["snapshot_id"] = snapshot.get("snapshot_id") or next_snapshot_id()
    SNAPSHOTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SNAPSHOTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    return snapshot["snapshot_id"]


def read_snapshots():
    if not SNAPSHOTS_PATH.exists():
        return []
    rows = []
    for line in SNAPSHOTS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue           # битую строку пропускаем, а не роняем весь ряд
    return rows


def get_snapshot(snapshot_id):
    for row in reversed(read_snapshots()):
        if row.get("snapshot_id") == snapshot_id:
            return row
    return None


def previous_snapshot(snapshot_id):
    """Снимок, сделанный непосредственно перед указанным."""
    rows = read_snapshots()
    for index, row in enumerate(rows):
        if row.get("snapshot_id") == snapshot_id:
            return rows[index - 1] if index else None
    return None


def snapshots_stats():
    rows = read_snapshots()
    return {"count": len(rows),
            "first": rows[0]["at"] if rows else None,
            "last": rows[-1]["at"] if rows else None}


# --- отчёты ---------------------------------------------------------------------

def save_report(report):
    """Сохраняет отчёт второго шага. Возвращает идентификатор."""
    report = dict(report)
    report["id"] = report.get("id") or f"rep-{now().strftime('%Y%m%dT%H%M%S')}"
    with connect() as connection:
        connection.execute(
            """INSERT INTO reports (id, snapshot_id, changed, summary, changes,
                                    created_at, saved_path)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   changed = excluded.changed, summary = excluded.summary,
                   changes = excluded.changes""",
            (report["id"], report["snapshot_id"], int(report["changed"]),
             report["summary"], json.dumps(report.get("changes", []), ensure_ascii=False),
             iso(now()), report.get("saved_path", "")))
    return report["id"]


def get_report(report_id):
    with connect() as connection:
        row = connection.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if not row:
        return None
    return dict(row, changed=bool(row["changed"]), changes=json.loads(row["changes"]))


def mark_report_saved(report_id, path):
    with connect() as connection:
        connection.execute("UPDATE reports SET saved_path = ? WHERE id = ?",
                           (str(path), report_id))


def list_reports(limit=10):
    with connect() as connection:
        return [dict(row, changed=bool(row["changed"]))
                for row in connection.execute(
                    "SELECT id, snapshot_id, changed, summary, created_at, saved_path "
                    "FROM reports ORDER BY created_at DESC LIMIT ?", (int(limit),))]


# --- расписание -----------------------------------------------------------------

def add_job(name, kind, every, params=None, first_run=None):
    moment = first_run or (now() + timedelta(seconds=every if every else 0))
    with connect() as connection:
        connection.execute(
            """INSERT INTO jobs (name, kind, every, next_run, params, active, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(name) DO UPDATE SET
                   kind = excluded.kind, every = excluded.every,
                   next_run = excluded.next_run, params = excluded.params, active = 1""",
            (name, kind, int(every), iso(moment),
             json.dumps(params or {}, ensure_ascii=False), iso(now())))
    return name


def list_jobs(active_only=True):
    query = "SELECT * FROM jobs" + (" WHERE active = 1" if active_only else "")
    with connect() as connection:
        return [dict(row, params=json.loads(row["params"]))
                for row in connection.execute(query + " ORDER BY next_run")]


def due_jobs(moment=None):
    moment = moment or now()
    return [job for job in list_jobs() if job["next_run"] <= iso(moment)]


def reschedule(job, moment=None):
    """Периодическое сдвигаем вперёд, одноразовое выключаем.

    Для заданий с выравниванием считаем следующий запуск не «через N секунд»,
    а на ближайшую нужную минуту часа — так проверка идёт стабильно в :02.
    """
    moment = moment or now()
    params = job.get("params") or {}
    at_minute = params.get("at_minute")

    with connect() as connection:
        if not job["every"]:
            connection.execute("UPDATE jobs SET active = 0 WHERE name = ?", (job["name"],))
            return

        if at_minute is not None:
            following = (moment + timedelta(hours=1)).replace(
                minute=int(at_minute), second=0, microsecond=0)
        else:
            following = moment + timedelta(seconds=job["every"])
        connection.execute("UPDATE jobs SET next_run = ? WHERE name = ?",
                           (iso(following), job["name"]))


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
