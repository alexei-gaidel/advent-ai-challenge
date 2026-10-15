# Схемы данных проекта (SQLite)

Сгенерировано `31-dev-assistant/gen_docs.py` из исходников: каждая таблица — это `CREATE TABLE` из кода. Базы лежат рядом с кодом дня и в git не попадают (`agents.db`, `index.db`, `chat.db` в `.gitignore`).

Всего таблиц: 23, вариантов схем: 31.

## Таблица `agents`

Схема — используется в: `07-persistent-context/storage.py`, `09-context-compression/storage.py`, `10-context-strategies/storage.py`, `11-memory-model/storage.py`, `12-personalization/storage.py`, `13-task-state/storage.py`, `14-invariants/storage.py`, `15-lifecycle/storage.py`, `17-mcp-tool/storage.py`, `18-scheduled-mcp/storage.py`, `19-mcp-pipeline/storage.py` (дни: 07, 09, 10, 11, 12, 13, 14, 15, 17, 18, 19).

```sql
CREATE TABLE agents (
    id         TEXT PRIMARY KEY,
    settings   TEXT NOT NULL,
    stats      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

## Таблица `branches`

Схема — используется в: `10-context-strategies/storage.py` (дни: 10).

```sql
CREATE TABLE branches (
    id         TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    parent_id  TEXT,
    fork_at    INTEGER NOT NULL,
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

## Таблица `build`

Вариант 1 — используется в: `22-first-rag/index.py`, `23-rerank-filter/index.py`, `24-citations/index.py`, `25-rag-chat/index.py`, `28-local-rag/index.py`, `29-local-optimization/index.py` (дни: 22, 23, 24, 25, 28, 29).

```sql
CREATE TABLE build (
    model TEXT, built_at TEXT, seconds REAL, source TEXT
);
```

Вариант 2 — используется в: `31-dev-assistant/index.py` (дни: 31).

```sql
CREATE TABLE build (
    fingerprint TEXT, documents INTEGER, chunks INTEGER, seconds REAL, built_at TEXT
);
```

## Таблица `builds`

Схема — используется в: `21-document-index/index_store.py` (дни: 21).

```sql
CREATE TABLE builds (
    strategy  TEXT PRIMARY KEY,
    model     TEXT NOT NULL,
    built_at  TEXT NOT NULL,
    seconds   REAL NOT NULL,       -- время эмбеддинга всех чанков
    stats     TEXT NOT NULL        -- JSON статистики нарезки
);
```

## Таблица `chats`

Схема — используется в: `25-rag-chat/storage.py` (дни: 25).

```sql
CREATE TABLE chats (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    mode       TEXT NOT NULL,          -- memory | history
    created_at TEXT NOT NULL
);
```

## Таблица `checkpoints`

Схема — используется в: `10-context-strategies/storage.py` (дни: 10).

```sql
CREATE TABLE checkpoints (
    id         TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    branch_id  TEXT NOT NULL,
    at         INTEGER NOT NULL,
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

## Таблица `chunks`

Вариант 1 — используется в: `21-document-index/index_store.py` (дни: 21).

```sql
CREATE TABLE chunks (
    strategy   TEXT NOT NULL,
    chunk_id   TEXT NOT NULL,
    source     TEXT NOT NULL,      -- путь файла от корня репо
    title      TEXT NOT NULL,      -- заголовок документа
    section    TEXT NOT NULL,      -- путь раздела «Сравнение › Токены»
    text       TEXT NOT NULL,
    start_char INTEGER NOT NULL,   -- где чанк лежит в исходном тексте
    end_char   INTEGER NOT NULL,
    n_chars    INTEGER NOT NULL,
    PRIMARY KEY (strategy, chunk_id)
);
```

Вариант 2 — используется в: `22-first-rag/index.py`, `23-rerank-filter/index.py`, `24-citations/index.py`, `25-rag-chat/index.py`, `28-local-rag/index.py`, `29-local-optimization/index.py` (дни: 22, 23, 24, 25, 28, 29).

```sql
CREATE TABLE chunks (
    chunk_id  TEXT PRIMARY KEY,    -- ст.81 | ст.72#2 (вторая часть длинной статьи)
    article   TEXT NOT NULL,       -- 81, 67.1, ЗП (заключительные положения)
    chapter   TEXT NOT NULL,       -- «Глава 4. Президент Российской Федерации»
    parts     TEXT NOT NULL,       -- какие части статьи внутри: «1–3» или «весь текст»
    amended   INTEGER NOT NULL,    -- 1 — статья затронута поправками 2020
    text      TEXT NOT NULL,
    n_chars   INTEGER NOT NULL,
    vector    BLOB NOT NULL        -- float32 × 1024, L2-нормирован
);
```

Вариант 3 — используется в: `31-dev-assistant/index.py` (дни: 31).

```sql
CREATE TABLE chunks (
    chunk_id  TEXT PRIMARY KEY,    -- structure:17-mcp-tool/README.md:004
    source    TEXT NOT NULL,       -- путь от корня репозитория
    kind      TEXT NOT NULL,       -- root | day | docs
    title     TEXT NOT NULL,       -- заголовок документа
    section   TEXT NOT NULL,       -- путь заголовков внутри документа
    text      TEXT NOT NULL,
    vector    BLOB NOT NULL        -- float32 × 1024, L2-нормирован
);
```

## Таблица `facts`

Схема — используется в: `10-context-strategies/storage.py` (дни: 10).

```sql
CREATE TABLE facts (
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, key)
);
```

## Таблица `invariants`

Схема — используется в: `14-invariants/storage.py`, `15-lifecycle/storage.py`, `17-mcp-tool/storage.py`, `18-scheduled-mcp/storage.py`, `19-mcp-pipeline/storage.py` (дни: 14, 15, 17, 18, 19).

```sql
CREATE TABLE invariants (
    id         TEXT PRIMARY KEY,
    text       TEXT NOT NULL,
    kind       TEXT NOT NULL,      -- архитектура | стек | бизнес-правило | процесс
    rationale  TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL,      -- manual | promoted
    created_at TEXT NOT NULL
);
```

## Таблица `jobs`

Вариант 1 — используется в: `18-scheduled-mcp/store.py` (дни: 18).

```sql
CREATE TABLE jobs (
    name       TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,        -- collect | digest | remind
    every      INTEGER NOT NULL,     -- периодичность в секундах; 0 — одноразовое
    next_run   TEXT NOT NULL,
    params     TEXT NOT NULL DEFAULT '{}',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
```

Вариант 2 — используется в: `19-mcp-pipeline/store.py` (дни: 19).

```sql
CREATE TABLE jobs (
    name       TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    every      INTEGER NOT NULL,
    next_run   TEXT NOT NULL,
    params     TEXT NOT NULL DEFAULT '{}',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
```

## Таблица `long_memory`

Схема — используется в: `11-memory-model/storage.py`, `12-personalization/storage.py`, `13-task-state/storage.py`, `14-invariants/storage.py`, `15-lifecycle/storage.py`, `17-mcp-tool/storage.py`, `18-scheduled-mcp/storage.py`, `19-mcp-pipeline/storage.py` (дни: 11, 12, 13, 14, 15, 17, 18, 19).

```sql
CREATE TABLE long_memory (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL,
    kind         TEXT NOT NULL,
    source_agent TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
```

## Таблица `memory_proposals`

Схема — используется в: `11-memory-model/storage.py`, `12-personalization/storage.py`, `13-task-state/storage.py`, `14-invariants/storage.py`, `15-lifecycle/storage.py`, `17-mcp-tool/storage.py`, `18-scheduled-mcp/storage.py`, `19-mcp-pipeline/storage.py` (дни: 11, 12, 13, 14, 15, 17, 18, 19).

```sql
CREATE TABLE memory_proposals (
    id         TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    layer      TEXT NOT NULL,
    kind       TEXT,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

## Таблица `messages`

Вариант 1 — используется в: `07-persistent-context/storage.py`, `09-context-compression/storage.py`, `11-memory-model/storage.py`, `12-personalization/storage.py`, `13-task-state/storage.py`, `14-invariants/storage.py`, `15-lifecycle/storage.py`, `17-mcp-tool/storage.py`, `18-scheduled-mcp/storage.py`, `19-mcp-pipeline/storage.py` (дни: 07, 09, 11, 12, 13, 14, 15, 17, 18, 19).

```sql
CREATE TABLE messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

Вариант 2 — используется в: `10-context-strategies/storage.py` (дни: 10).

```sql
CREATE TABLE messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    branch_id  TEXT NOT NULL DEFAULT 'main',
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

Вариант 3 — используется в: `25-rag-chat/storage.py` (дни: 25).

```sql
CREATE TABLE messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    turn       INTEGER NOT NULL,       -- номер хода: реплика пользователя и ответ — один ход
    role       TEXT NOT NULL,          -- user | assistant
    content    TEXT NOT NULL,
    meta       TEXT NOT NULL DEFAULT '{}',   -- у ответа: query, sources, quotes, unknown, расход
    created_at TEXT NOT NULL
);
```

## Таблица `profiles`

Схема — используется в: `12-personalization/storage.py`, `13-task-state/storage.py`, `14-invariants/storage.py`, `15-lifecycle/storage.py`, `17-mcp-tool/storage.py`, `18-scheduled-mcp/storage.py`, `19-mcp-pipeline/storage.py` (дни: 12, 13, 14, 15, 17, 18, 19).

```sql
CREATE TABLE profiles (
    id         TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

## Таблица `reports`

Схема — используется в: `19-mcp-pipeline/store.py` (дни: 19).

```sql
CREATE TABLE reports (
    id          TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    changed     INTEGER NOT NULL,
    summary     TEXT NOT NULL,
    changes     TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    saved_path  TEXT NOT NULL DEFAULT ''
);
```

## Таблица `runs`

Вариант 1 — используется в: `18-scheduled-mcp/store.py` (дни: 18).

```sql
CREATE TABLE runs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job      TEXT NOT NULL,
    kind     TEXT NOT NULL,
    at       TEXT NOT NULL,
    status   TEXT NOT NULL,          -- ok | error
    detail   TEXT NOT NULL DEFAULT ''
);
```

Вариант 2 — используется в: `19-mcp-pipeline/store.py` (дни: 19).

```sql
CREATE TABLE runs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job      TEXT NOT NULL,
    kind     TEXT NOT NULL,
    at       TEXT NOT NULL,
    status   TEXT NOT NULL,
    detail   TEXT NOT NULL DEFAULT ''
);
```

## Таблица `summaries`

Схема — используется в: `09-context-compression/storage.py` (дни: 09).

```sql
CREATE TABLE summaries (
    agent_id   TEXT PRIMARY KEY REFERENCES agents(id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    covered    INTEGER NOT NULL,   -- сколько первых сообщений истории свёрнуто
    updated_at TEXT NOT NULL
);
```

## Таблица `task_events`

Схема — используется в: `13-task-state/storage.py`, `14-invariants/storage.py`, `15-lifecycle/storage.py`, `17-mcp-tool/storage.py`, `18-scheduled-mcp/storage.py`, `19-mcp-pipeline/storage.py` (дни: 13, 14, 15, 17, 18, 19).

```sql
CREATE TABLE task_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,     -- plan | transition | pause | resume | step | expect
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

## Таблица `task_states`

Схема — используется в: `25-rag-chat/storage.py` (дни: 25).

```sql
CREATE TABLE task_states (
    chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    turn       INTEGER NOT NULL,       -- 0 — пустая память при создании чата
    state      TEXT NOT NULL,          -- JSON: goal, clarified, constraints, terms, locked
    created_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, turn)
);
```

## Таблица `task_steps`

Схема — используется в: `13-task-state/storage.py`, `14-invariants/storage.py`, `15-lifecycle/storage.py`, `17-mcp-tool/storage.py`, `18-scheduled-mcp/storage.py`, `19-mcp-pipeline/storage.py` (дни: 13, 14, 15, 17, 18, 19).

```sql
CREATE TABLE task_steps (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    stage    TEXT NOT NULL,
    position INTEGER NOT NULL,
    text     TEXT NOT NULL,
    status   TEXT NOT NULL        -- pending | done | skipped
);
```

## Таблица `tasks`

Вариант 1 — используется в: `13-task-state/storage.py`, `14-invariants/storage.py` (дни: 13, 14).

```sql
CREATE TABLE tasks (
    id         TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    stages     TEXT NOT NULL,      -- JSON: набор этапов, настраивается под задачу
    stage      TEXT NOT NULL,      -- ключ текущего этапа
    expected   TEXT NOT NULL,      -- JSON: от кого ждём действия и какого
    paused     INTEGER NOT NULL DEFAULT 0,
    pause_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Вариант 2 — используется в: `15-lifecycle/storage.py`, `17-mcp-tool/storage.py`, `18-scheduled-mcp/storage.py`, `19-mcp-pipeline/storage.py` (дни: 15, 17, 18, 19).

```sql
CREATE TABLE tasks (
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
```

## Таблица `vectors`

Схема — используется в: `21-document-index/index_store.py` (дни: 21).

```sql
CREATE TABLE vectors (
    strategy TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    dim      INTEGER NOT NULL,
    vector   BLOB NOT NULL,        -- float32, L2-нормирован
    PRIMARY KEY (strategy, chunk_id)
);
```

## Таблица `working_memory`

Схема — используется в: `11-memory-model/storage.py`, `12-personalization/storage.py`, `13-task-state/storage.py`, `14-invariants/storage.py`, `15-lifecycle/storage.py`, `17-mcp-tool/storage.py`, `18-scheduled-mcp/storage.py`, `19-mcp-pipeline/storage.py` (дни: 11, 12, 13, 14, 15, 17, 18, 19).

```sql
CREATE TABLE working_memory (
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, key)
);
```
